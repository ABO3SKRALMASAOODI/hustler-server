"""Independent semantic finishing review for speech-led edits.

The visual critic can prove that a crop, caption or cutaway looks wrong and
the audio reviewer can hear the finished mix.  Neither can decide whether a
podcast answer lost its question, whether two retained thoughts join
coherently, or whether the ending resolves the promise made by the opening.
This module gives a fresh, tool-free model only the *assembled program*
transcript plus narrow source context around edit boundaries.  It is scoped to
meaningful speech cuts so a caption/color/crop-only turn buys no extra call.

The critic never writes an EDL.  Malformed, unavailable or low-confidence
judgment degrades to no finding, and subjective hook notes require more
evidence than a concrete missing-context defect before they can trigger the
agent's one repair decision.
"""

import json
import math

import llm
from timeline import Timeline, insert_windows


STORY_CRITIC_VERSION = 3

_CATEGORIES = {
    "missing_context", "abrupt_open", "unresolved_end",
    "incoherent_sequence", "redundant_thought", "weak_hook",
    "broken_question_answer", "referent_without_antecedent",
    "speaker_turn_confusion",
    "instruction_miss", "other",
}
_SEVERITIES = {"blocker", "major", "minor"}
_STORY_FAMILIES = {
    "podcast_conversation", "talking_head_social",
    "product_demo_explainer", "narrative_story", "voiceover_montage",
}


def _float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _json_object(text):
    if not isinstance(text, str):
        return None
    decoder = json.JSONDecoder()
    for i, char in enumerate(text):
        if char != "{":
            continue
        try:
            value, _end = decoder.raw_decode(text[i:])
        except (ValueError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            return value
    return None


def parse_report(answer):
    """Normalize the small critic contract; discard ungrounded claims."""
    raw = _json_object(answer)
    if raw is None:
        return None
    verdict = str(raw.get("verdict") or "").strip().lower()
    if verdict not in {"pass", "repair"}:
        return None
    findings = []
    for item in (raw.get("findings") or [])[:6]:
        if not isinstance(item, dict):
            continue
        severity = str(item.get("severity") or "").strip().lower()
        category = str(item.get("category") or "other").strip().lower()
        evidence = str(item.get("evidence") or "").strip()
        repair = str(item.get("repair") or "").strip()
        try:
            confidence = float(item.get("confidence"))
            if not math.isfinite(confidence):
                continue
            confidence = min(max(confidence, 0.0), 1.0)
        except (TypeError, ValueError):
            continue
        try:
            program_s = float(item["program_s"])
            if not math.isfinite(program_s) or program_s < 0:
                program_s = None
        except (KeyError, TypeError, ValueError):
            program_s = None
        target_id = str(item.get("target_id") or "").strip()[:120] or None
        suggested_start = _optional_nonnegative_float(
            item.get("suggested_source_start_s"))
        suggested_end = _optional_nonnegative_float(
            item.get("suggested_source_end_s"))
        if suggested_start is None or suggested_end is None or \
                suggested_end <= suggested_start:
            suggested_start = suggested_end = None
        if severity not in _SEVERITIES or not evidence or not repair:
            continue
        if category not in _CATEGORIES:
            category = "other"
        findings.append({
            "severity": severity,
            "category": category,
            "program_s": program_s,
            "target_id": target_id,
            "suggested_source_start_s": suggested_start,
            "suggested_source_end_s": suggested_end,
            "evidence": evidence[:320],
            "repair": repair[:260],
            "confidence": round(confidence, 3),
        })
    if any(row["severity"] in {"blocker", "major"} and
           row["confidence"] >= 0.82 for row in findings):
        verdict = "repair"
    return {"verdict": verdict, "findings": findings,
            "summary": str(raw.get("summary") or "")[:300]}


def should_review(edl, index, family, asset_indexes=None):
    """Whether a final edit made a semantic decision worth another call.

    This is an attention allocator, not a creative restriction.  We review
    only speech-rich narrative families whose EDL actually removed or joined
    material.  A full-length video with a color grade or captions has no new
    story decision for an independent model to judge.
    """
    asset_indexes = asset_indexes or {}
    words = list((index or {}).get("words") or [])
    audible_inserts = []
    for item in (edl or {}).get("inserts") or []:
        if not isinstance(item, dict) or item.get("mute") or \
                not item.get("asset_key"):
            continue
        clip_index = asset_indexes.get(item["asset_key"]) or {}
        clip_words = clip_index.get("words") or []
        if clip_words:
            words.extend(clip_words)
            audible_inserts.append(item)
    if family not in _STORY_FAMILIES or len(words) < 24:
        return False
    keep = (edl or {}).get("keep") or []
    # Two audible speech clips joined in one output are a semantic edit even
    # on a canvas project with no main source. A single trimmed insert also
    # deserves review when its chosen window removed a meaningful share.
    if len(audible_inserts) >= 2:
        return True
    for item in audible_inserts:
        clip_index = asset_indexes.get(item["asset_key"]) or {}
        duration = _float((clip_index.get("video") or {}).get("duration"))
        consumed = _float(item.get("duration_s")) * max(
            .01, _float(item.get("rate"), 1.0))
        if duration > 0 and consumed / duration <= .92:
            return True
    if not keep:
        return False
    source_duration = _float(((index or {}).get("video") or {}).get(
        "duration"))
    kept = sum(max(0.0, _float(row[1]) - _float(row[0]))
               for row in keep if isinstance(row, (list, tuple)) and
               len(row) >= 2)
    removed_share = (max(0.0, 1.0 - kept / source_duration)
                     if source_duration > 0 else 0.0)
    return len(keep) >= 2 or removed_share >= 0.08


def _sentences(index):
    rows = []
    for raw in (index or {}).get("sentences") or []:
        text = " ".join(str(raw.get("text") or "").split())
        try:
            t0, t1 = float(raw["t0"]), float(raw["t1"])
        except (KeyError, TypeError, ValueError):
            continue
        if text and t1 > t0:
            rows.append({"t0": t0, "t1": t1, "text": text,
                         "speaker": raw.get("speaker")})
    rows.sort(key=lambda row: row["t0"])
    return rows


def _words(index):
    rows = []
    for raw in (index or {}).get("words") or []:
        text = " ".join(str(raw.get("w") or "").split())
        try:
            t0, t1 = float(raw["t0"]), float(raw["t1"])
        except (KeyError, TypeError, ValueError):
            continue
        if text and t1 > t0:
            rows.append({"w": text, "t0": t0, "t1": t1})
    rows.sort(key=lambda row: row["t0"])
    return rows


def _optional_nonnegative_float(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and value >= 0 else None


def _sentence_numbers_for_words(sentences, words):
    """Linear assignment for long podcast indexes (both inputs are sorted)."""
    out = []
    sentence_n = 0
    for word in words:
        midpoint = (word["t0"] + word["t1"]) / 2.0
        while sentence_n < len(sentences) and \
                sentences[sentence_n]["t1"] + .01 < midpoint:
            sentence_n += 1
        if sentence_n < len(sentences) and \
                sentences[sentence_n]["t0"] - .01 <= midpoint <= \
                sentences[sentence_n]["t1"] + .01:
            out.append(sentence_n)
        else:
            out.append(None)
    return out


def _word_fragments(mapped_words, sentences, source_words, source_label,
                    sentence_clock):
    """Exact surviving utterance fragments, never midpoint-invented prose."""
    sentence_word_ns = {}
    sentence_numbers = _sentence_numbers_for_words(sentences, source_words)
    source_word_lookup = {}
    for word_n, (word, sentence_n) in enumerate(zip(
            source_words, sentence_numbers)):
        source_word_lookup[(round(word["t0"], 5),
                            round(word["t1"], 5))] = word_n
        if sentence_n is not None:
            sentence_word_ns.setdefault(sentence_n, []).append(word_n)
    assigned = []
    for word in sorted(mapped_words, key=lambda row: row["out_t0"]):
        source_word_n = source_word_lookup.get((
            round(word["src_t0"], 5), round(word["src_t1"], 5)))
        sentence_n = (sentence_numbers[source_word_n]
                      if source_word_n is not None else None)
        assigned.append(dict(word, sentence_n=sentence_n,
                             source_word_n=source_word_n))
    groups = []
    for word in assigned:
        prior_word_n = groups[-1][-1].get("source_word_n") if groups else None
        consecutive = (prior_word_n is None or
                       word.get("source_word_n") is None or
                       word["source_word_n"] == prior_word_n + 1)
        if groups and consecutive \
                and groups[-1][-1]["sentence_n"] == word["sentence_n"] \
                and word["out_t0"] - groups[-1][-1]["out_t1"] <= 1.0 \
                and word["src_t0"] - groups[-1][-1]["src_t1"] <= 1.0:
            groups[-1].append(word)
        else:
            groups.append([word])
    out = []
    for group in groups:
        sentence_n = group[0]["sentence_n"]
        sentence = (sentences[sentence_n]
                    if sentence_n is not None else None)
        expected = sentence_word_ns.get(sentence_n) or []
        cut_in = bool(sentence and expected and
                      group[0].get("source_word_n") != expected[0])
        cut_off = bool(sentence and expected and
                       group[-1].get("source_word_n") != expected[-1])
        complete = bool(sentence and not cut_in and not cut_off)
        if complete:
            program_s = sentence_clock(sentence)
        else:
            program_s = (group[0]["out_t0"] + group[-1]["out_t1"]) / 2.0
        if program_s is None:
            program_s = group[0]["out_t0"]
        out.append({
            "t0": group[0]["src_t0"], "t1": group[-1]["src_t1"],
            "program_s": round(program_s, 2),
            "program_t0": round(group[0]["out_t0"], 2),
            "program_t1": round(group[-1]["out_t1"], 2),
            "text": " ".join(word["w"] for word in group),
            "speaker": sentence.get("speaker") if sentence else None,
            "source_label": source_label,
            "cut_in_inside_sentence": cut_in,
            "cut_off_inside_sentence": cut_off,
            "sentence_source_span": ([sentence["t0"], sentence["t1"]]
                                     if sentence else None),
        })
    return out


def _legacy_sentence_program(source, source_label, sentence_clock,
                             clip_start=None, clip_end=None):
    """Sentence midpoint fallback for legacy indexes without word timings."""
    out = []
    for row in source:
        midpoint = (row["t0"] + row["t1"]) / 2.0
        if clip_start is not None and not clip_start <= midpoint <= clip_end:
            continue
        program_s = sentence_clock(row)
        if program_s is None:
            continue
        out.append(dict(row, program_s=round(program_s, 2),
                        program_t0=round(program_s, 2),
                        program_t1=round(program_s, 2),
                        source_label=source_label,
                        cut_in_inside_sentence=False,
                        cut_off_inside_sentence=False,
                        sentence_source_span=[row["t0"], row["t1"]]))
    return out


def _repair_targets(edl, timeline, asset_indexes, main_duration,
                    assembled_joins, visible_ids=None):
    """Stable, editable identities the critic may bind a repair to."""
    targets = []
    restrict_to_visible = visible_ids is not None
    visible_ids = set(visible_ids or [])
    keeps = edl.get("keep") or []
    for n, keep in enumerate(keeps, 1):
        try:
            start, end = float(keep[0]), float(keep[1])
        except (TypeError, ValueError, IndexError):
            continue
        target_id = f"keep-{n}"
        if restrict_to_visible and target_id not in visible_ids:
            continue
        targets.append({
            "id": target_id, "type": "main_keep",
            "source_start_s": round(start, 3),
            "source_end_s": round(end, 3),
            "source_duration_s": round(main_duration, 3),
        })
    windows = insert_windows(edl.get("inserts") or [], timeline)
    for item in edl.get("inserts") or []:
        if not isinstance(item, dict) or not item.get("id") or \
                item.get("mute"):
            continue
        clip_index = asset_indexes.get(item.get("asset_key")) or {}
        if not (_words(clip_index) or _sentences(clip_index)):
            continue
        duration = _float((clip_index.get("video") or {}).get("duration"))
        source_start = _float(item.get("source_start_s"), 0.0)
        rate = max(.01, _float(item.get("rate"), 1.0))
        source_end = source_start + _float(item.get("duration_s")) * rate
        window = windows.get(item["id"])
        target_id = f"insert:{item['id']}"
        if restrict_to_visible and target_id not in visible_ids:
            continue
        targets.append({
            "id": target_id, "type": "insert",
            "asset_key": item.get("asset_key"),
            "source_start_s": round(source_start, 3),
            "source_end_s": round(source_end, 3),
            "source_duration_s": round(duration, 3),
            "program_start_s": window[0] if window else None,
            "program_end_s": window[1] if window else None,
        })
    for row in assembled_joins:
        if restrict_to_visible and row["target_id"] not in visible_ids:
            continue
        both_main = (str(row["left_target_id"]).startswith("keep-") and
                     str(row["right_target_id"]).startswith("keep-"))
        targets.append({
            "id": row["target_id"], "type": "assembled_speech_join",
            "left_target_id": row["left_target_id"],
            "right_target_id": row["right_target_id"],
            "program_s": row["program_s"],
            "source_duration_s": (round(main_duration, 3)
                                  if both_main else 0.0),
        })
    return targets


def _assembled_joins(program):
    """Every actual speech-bearing edit boundary on the output clock."""
    out = []
    for left, right in zip(program, program[1:]):
        left_target = left.get("edit_target_id")
        right_target = right.get("edit_target_id")
        if not left_target or not right_target or left_target == right_target:
            continue
        program_s = round(_float(right.get("program_t0"),
                                 right.get("program_s")), 2)
        target_id = f"join:{left_target}->{right_target}"
        out.append({
            "target_id": target_id,
            "left_target_id": left_target,
            "right_target_id": right_target,
            "program_s": program_s,
            "left": left,
            "right": right,
        })
    return out


def _join_line(row):
    left, right = row["left"], row["right"]
    left_speaker = (f"S{left['speaker']}" if left.get("speaker") is not None
                    else "speaker unknown")
    right_speaker = (f"S{right['speaker']}"
                     if right.get("speaker") is not None else
                     "speaker unknown")
    return (
        f"[program {row['program_s']:.2f}s | target={row['target_id']}] "
        f"LEFT {row['left_target_id']} {left_speaker} "
        f"{left['source_label']} {left['t0']:.2f}-{left['t1']:.2f}: "
        f"{left['text'][-220:]} -> RIGHT {row['right_target_id']} "
        f"{right_speaker} {right['source_label']} "
        f"{right['t0']:.2f}-{right['t1']:.2f}: {right['text'][:220]}")


def _spread(rows, count):
    if len(rows) <= count:
        return list(rows)
    if count <= 1:
        return [rows[len(rows) // 2]]
    return [rows[round(i * (len(rows) - 1) / (count - 1))]
            for i in range(count)]


def build_evidence(edl, index, family, user_request="", plan=None,
                   asset_indexes=None):
    """Build bounded program-order speech plus source boundary context."""
    timeline = Timeline(edl.get("keep") or [], edl.get("inserts") or [],
                        edl.get("speed") or [])
    asset_indexes = asset_indexes or {}
    source = _sentences(index)
    main_words = _words(index)
    mapped_main_words = [{
        "w": word["w"], "src_t0": word["src_t0"],
        "src_t1": word["src_t1"], "out_t0": word["t0"],
        "out_t1": word["t1"],
    } for word in timeline.kept_words(main_words)]
    if main_words:
        program = _word_fragments(
            mapped_main_words, source, main_words, "main source",
            lambda sentence: timeline.src_to_out(
                (sentence["t0"] + sentence["t1"]) / 2.0))
    else:
        program = _legacy_sentence_program(
            source, "main source",
            lambda sentence: timeline.src_to_out(
                (sentence["t0"] + sentence["t1"]) / 2.0))
    for row in program:
        midpoint = (row["t0"] + row["t1"]) / 2.0
        row["edit_target_id"] = next((
            f"keep-{n}" for n, keep in enumerate(edl.get("keep") or [], 1)
            if _float(keep[0]) - .01 <= midpoint <=
            _float(keep[1]) + .01), None)

    # Inserted video is real program speech, not B-roll metadata. Reconstruct
    # it on the exact output clock from the insert window, file-local source
    # start and playback rate. This is the story equivalent of asset-scoped
    # sequence evidence: two clips can both have sentence id 1 and never
    # contaminate each other's clocks.
    windows = insert_windows(edl.get("inserts") or [], timeline)
    insert_context = []
    for item in edl.get("inserts") or []:
        if not isinstance(item, dict) or item.get("mute") or \
                not item.get("asset_key"):
            continue
        clip_index = asset_indexes.get(item["asset_key"]) or {}
        clip_source = _sentences(clip_index)
        clip_words = _words(clip_index)
        window = windows.get(item.get("id"))
        if not clip_source or not window:
            continue
        clip_start = _float(item.get("source_start_s"), 0.0)
        rate = max(.01, _float(item.get("rate"), 1.0))
        clip_end = clip_start + _float(item.get("duration_s")) * rate
        label = f"insert {item.get('id') or 'unknown'} CLIP"
        if clip_words:
            mapped_clip_words = []
            for word in clip_words:
                midpoint = (word["t0"] + word["t1"]) / 2.0
                if not clip_start <= midpoint <= clip_end:
                    continue
                src_t0 = max(word["t0"], clip_start)
                src_t1 = min(word["t1"], clip_end)
                mapped_clip_words.append({
                    "w": word["w"], "src_t0": word["t0"],
                    "src_t1": word["t1"],
                    "out_t0": window[0] + (src_t0 - clip_start) / rate,
                    "out_t1": window[0] + (src_t1 - clip_start) / rate,
                })
            clip_program = _word_fragments(
                mapped_clip_words, clip_source, clip_words, label,
                lambda sentence, _start=clip_start, _rate=rate,
                _window=window: (_window[0] +
                                 (((sentence["t0"] + sentence["t1"]) / 2.0)
                                  - _start) / _rate))
        else:
            clip_program = _legacy_sentence_program(
                clip_source, label,
                lambda sentence, _start=clip_start, _rate=rate,
                _window=window: (_window[0] +
                                 (((sentence["t0"] + sentence["t1"]) / 2.0)
                                  - _start) / _rate),
                clip_start, clip_end)
        for row in clip_program:
            row["edit_target_id"] = f"insert:{item.get('id')}"
        program.extend(clip_program)

        # Context on both sides of the chosen clip window lets the reviewer
        # catch a missing setup/payoff instead of seeing only the excerpt the
        # original editor selected.
        for row in clip_source:
            near = (abs(row["t1"] - clip_start) <= 12.0 or
                    abs(row["t0"] - clip_end) <= 12.0)
            if not near:
                continue
            midpoint = (row["t0"] + row["t1"]) / 2.0
            if clip_start <= midpoint <= clip_end:
                out = window[0] + (midpoint - clip_start) / rate
                fully_kept = (row["t0"] >= clip_start - .01 and
                              row["t1"] <= clip_end + .01)
                state = (f"KEPT at program {out:.2f}s" if fully_kept else
                         "PARTIAL; exact surviving words are in program")
            else:
                state = "CUT"
            insert_context.append(
                f"[{label} chosen={clip_start:.2f}-{clip_end:.2f}; "
                f"clip source {row['t0']:.2f}-{row['t1']:.2f}; {state}] "
                f"{row['text']}")
    program.sort(key=lambda row: (row["program_s"], row["t0"]))
    assembled_joins = _assembled_joins(program)

    # Whole-program evidence stays chronological.  Very long outputs are
    # sampled evenly and explicitly labeled; joins themselves remain fully
    # represented in boundary context below, so the reviewer cannot mistake
    # an omitted middle for a real jump in the edit.
    sampled = len(program) > 140
    program_rows = _spread(program, 140)
    program_lines = []
    for row in program_rows:
        speaker = (f" S{row['speaker']}" if row.get("speaker") is not None
                   else "")
        markers = []
        if row.get("cut_in_inside_sentence"):
            markers.append("CUT-IN INSIDE SENTENCE")
        if row.get("cut_off_inside_sentence"):
            markers.append("CUT-OFF INSIDE SENTENCE")
        marker = " | " + ", ".join(markers) if markers else ""
        target = (f" | target={row['edit_target_id']}"
                  if row.get("edit_target_id") else "")
        program_lines.append(
            f"[program {row['program_s']:.2f}s | {row['source_label']} "
            f"{row['t0']:.2f}-"
            f"{row['t1']:.2f}{speaker}{target}{marker}] "
            f"{row['text'][:320]}")

    boundary_rows = []
    seen = set()
    for boundary_n, keep in enumerate(edl.get("keep") or [], 1):
        try:
            start, end = float(keep[0]), float(keep[1])
        except (TypeError, ValueError, IndexError):
            continue
        near = [row for row in source
                if row["t1"] >= start - 12.0 and row["t0"] <= end + 12.0
                and (abs(row["t1"] - start) <= 12.0 or
                     abs(row["t0"] - end) <= 12.0)]
        for row in near:
            key = (boundary_n, row["t0"], row["t1"])
            if key in seen:
                continue
            seen.add(key)
            kept = timeline.src_to_out((row["t0"] + row["t1"]) / 2.0)
            fully_kept = any(float(segment[0]) <= row["t0"] + .01 and
                             float(segment[1]) >= row["t1"] - .01
                             for segment in edl.get("keep") or [])
            overlaps = any(float(segment[1]) > row["t0"] and
                           float(segment[0]) < row["t1"]
                           for segment in edl.get("keep") or [])
            if fully_kept and kept is not None:
                state = "KEPT at program " + format(kept, ".2f") + "s"
            elif overlaps:
                state = "PARTIAL; exact surviving words are in program"
            else:
                state = "CUT"
            boundary_rows.append(
                f"[keep {boundary_n}={start:.2f}-{end:.2f}; "
                f"source {row['t0']:.2f}-{row['t1']:.2f}; "
                f"{state}] "
                f"{row['text'][:320]}")
    # Pathological micro-cut timelines can create hundreds of boundaries.
    # Preserve evidence across the whole edit rather than the first page.
    boundary_rows = _spread(boundary_rows + insert_context, 120)

    direction = plan or {}
    anchors = {
        key: direction.get(key) for key in (
            "brief", "treatment", "format", "intent", "decision_basis",
            "reference_transfer", "coherence_rules",
            "alternatives_rejected", "narrative_arc", "sequence_map",
            "must_keep", "must_avoid", "acceptance_checks")
        if direction.get(key)
    }
    main_duration = _float(((index or {}).get("video") or {}).get(
        "duration"))
    join_sampled = len(assembled_joins) > 160
    join_rows = _spread(assembled_joins, 160)
    visible_target_ids = {
        row.get("edit_target_id") for row in program_rows
        if row.get("edit_target_id")
    }
    for row in join_rows:
        visible_target_ids.update((row["target_id"],
                                   row["left_target_id"],
                                   row["right_target_id"]))
    for line in boundary_rows:
        if line.startswith("[keep "):
            keep_n = line[len("[keep "):].split("=", 1)[0]
            if keep_n.isdigit():
                visible_target_ids.add(f"keep-{keep_n}")
        elif line.startswith("[insert ") and " CLIP" in line:
            insert_id = line[len("[insert "):].split(" CLIP", 1)[0]
            if insert_id:
                visible_target_ids.add(f"insert:{insert_id}")
    repair_targets = _repair_targets(
        edl, timeline, asset_indexes, main_duration, join_rows,
        visible_target_ids)
    total_target_count = (len(edl.get("keep") or []) +
                          len(assembled_joins) +
                          sum(1 for item in edl.get("inserts") or []
                              if isinstance(item, dict) and
                              not item.get("mute") and item.get("id")))
    return {
        "family": family,
        "user_request": str(user_request or "")[:1200],
        "direction": anchors,
        "program_duration_s": round(timeline.out_duration, 2),
        "program_sentence_count": len(program),
        "program_sampled_evenly": sampled,
        "program_transcript": "\n".join(program_lines),
        "assembled_edit_joins": "\n".join(
            _join_line(row) for row in join_rows),
        "assembled_edit_joins_sampled_evenly": join_sampled,
        "repair_targets": repair_targets,
        "repair_targets_sampled_to_shown_evidence": (
            len(repair_targets) < total_target_count),
        "source_context_around_every_keep_boundary": "\n".join(
            boundary_rows),
    }


def _budgeted_lines(text, count, line_chars):
    lines = [line for line in str(text or "").splitlines() if line]
    return [line[:line_chars] for line in _spread(lines, count)]


def _visible_ids_from_lines(program_lines, join_lines, context_lines):
    ids = set()
    for line in program_lines + join_lines:
        if "target=" not in line:
            continue
        target_id = line.split("target=", 1)[1].split("]", 1)[0]
        target_id = target_id.split(" |", 1)[0].strip()
        if target_id:
            ids.add(target_id)
    for line in context_lines:
        if line.startswith("[keep "):
            keep_n = line[len("[keep "):].split("=", 1)[0]
            if keep_n.isdigit():
                ids.add(f"keep-{keep_n}")
        elif line.startswith("[insert ") and " CLIP" in line:
            insert_id = line[len("[insert "):].split(" CLIP", 1)[0]
            if insert_id:
                ids.add(f"insert:{insert_id}")
    return ids


def _packed_review_evidence(evidence, max_chars=30000):
    """Priority-pack story proof; never raw-truncate JSON or hide sampling."""
    program_lines = _budgeted_lines(
        evidence.get("program_transcript"), 28, 240)
    join_lines = _budgeted_lines(
        evidence.get("assembled_edit_joins"), 18, 400)
    context_lines = _budgeted_lines(
        evidence.get("source_context_around_every_keep_boundary"), 22, 240)
    visible_ids = _visible_ids_from_lines(
        program_lines, join_lines, context_lines)
    compact_targets = []
    target_fields = (
        "id", "type", "source_start_s", "source_end_s", "program_s",
        "program_start_s", "program_end_s", "left_target_id",
        "right_target_id", "asset_key", "source_duration_s")
    for target in evidence.get("repair_targets") or []:
        if target.get("id") not in visible_ids:
            continue
        compact_targets.append({
            key: target[key] for key in target_fields
            if target.get(key) is not None
        })
    direction_json = json.dumps(
        evidence.get("direction") or {}, ensure_ascii=False,
        separators=(",", ":"))
    packed = {
        "family": evidence.get("family"),
        "user_request": str(evidence.get("user_request") or "")[:900],
        "direction": (evidence.get("direction") if len(direction_json) <= 2600
                      else {"bounded_direction_json": direction_json[:2600],
                            "truncated": True}),
        "program_duration_s": evidence.get("program_duration_s"),
        "program_sentence_count": evidence.get("program_sentence_count"),
        "program_sampled_evenly": (
            evidence.get("program_sampled_evenly") or
            len(str(evidence.get("program_transcript") or "").splitlines()) >
            len(program_lines)),
        "program_transcript": "\n".join(program_lines),
        "assembled_edit_joins": "\n".join(join_lines),
        "assembled_edit_joins_sampled_evenly": (
            evidence.get("assembled_edit_joins_sampled_evenly") or
            len(str(evidence.get("assembled_edit_joins") or "").splitlines()) >
            len(join_lines)),
        "source_context_around_edit_boundaries": "\n".join(context_lines),
        "boundary_context_sampled_evenly": (
            len(str(evidence.get(
                "source_context_around_every_keep_boundary") or
                    "").splitlines()) > len(context_lines)),
        "repair_targets": compact_targets,
        "repair_targets_sampled_to_shown_evidence": (
            evidence.get("repair_targets_sampled_to_shown_evidence") or
            len(compact_targets) < len(evidence.get("repair_targets") or [])),
    }
    encoded = json.dumps(packed, ensure_ascii=False, separators=(",", ":"))
    # The fixed field budgets should already fit. This final deterministic
    # squeeze protects against unusually long ids without invalid JSON.
    if len(encoded) > max_chars:
        packed["direction"] = {"truncated": True}
        packed["user_request"] = packed["user_request"][:400]
        packed["source_context_around_edit_boundaries"] = "\n".join(
            _budgeted_lines(
                packed["source_context_around_edit_boundaries"], 12, 180))
        encoded = json.dumps(
            packed, ensure_ascii=False, separators=(",", ":"))
    if len(encoded) > max_chars:
        packed["program_transcript"] = "\n".join(
            _budgeted_lines(packed["program_transcript"], 18, 190))
        packed["assembled_edit_joins"] = "\n".join(
            _budgeted_lines(packed["assembled_edit_joins"], 12, 300))
        visible_ids = _visible_ids_from_lines(
            packed["program_transcript"].splitlines(),
            packed["assembled_edit_joins"].splitlines(),
            packed["source_context_around_edit_boundaries"].splitlines())
        packed["repair_targets"] = [
            target for target in packed["repair_targets"]
            if target.get("id") in visible_ids]
    return packed


_SYSTEM = """You are an independent senior story editor reviewing the exact
assembled transcript of a finished speech-led video. Another editor made the
cut. Be adversarial but evidence-bound: judge whether a viewer can understand
the opening, each join, the progression, and the ending without context that
was cut away. For interviews/podcasts, an answer needs its nearby question or
setup when the answer is not independently intelligible. Preserve intentional
open loops, rhetorical repetition and jump-cut energy when they work. Do not
reward feature count and do not rewrite the speaker's ideas merely to make them
generic. Do not infer a defect from evenly sampled omissions: use the complete
assembled_edit_joins ledger and boundary context to judge joins. Each join
shows the literal last surviving words on the left and first surviving words
on the right, including speakers. If a ledger is marked sampled, abstain on an
unshown join. User instructions outrank your preference.

Return JSON only:
{"verdict":"pass|repair","summary":"one sentence","findings":[
{"severity":"blocker|major|minor","category":"missing_context|abrupt_open|unresolved_end|incoherent_sequence|redundant_thought|weak_hook|broken_question_answer|referent_without_antecedent|speaker_turn_confusion|instruction_miss|other","program_s":12.3,"target_id":"one exact id from repair_targets","suggested_source_start_s":10.0,"suggested_source_end_s":22.0,"evidence":"exact words/times proving the issue","repair":"one source-boundary-aware action","confidence":0.0}]}
At most four findings. A major means a likely viewer rejection, not optional
polish. A merely less-than-perfect hook is minor unless the brief explicitly
requires a high-retention short and the first words visibly fail that promise.
For every proposed repair, copy one exact target_id from repair_targets. Use
the optional suggested source range only when the evidence proves exact safer
boundaries; otherwise omit both range fields. Treat CUT-IN/CUT-OFF markers as
literal missing speech, not as full sentences. Never invent an editable id.
Never claim to have seen pixels or heard audio."""


def _ground_repair_targets(report, evidence):
    """Keep observations, but strip any repair authority that was invented."""
    targets = {row["id"]: row for row in evidence.get("repair_targets") or []}
    duration_by_target = {
        target_id: _float(target.get("source_duration_s"))
        for target_id, target in targets.items()
    }
    for finding in report.get("findings") or []:
        target_id = finding.get("target_id")
        program_s = finding.get("program_s")
        program_duration = _float(evidence.get("program_duration_s"))
        if target_id not in targets or program_s is None or \
                program_s > program_duration + .05:
            finding["target_id"] = None
            finding["suggested_source_start_s"] = None
            finding["suggested_source_end_s"] = None
            continue
        start = finding.get("suggested_source_start_s")
        end = finding.get("suggested_source_end_s")
        duration = duration_by_target[target_id]
        if start is not None and end is not None and duration > 0 and \
                end > duration + .05:
            finding["suggested_source_start_s"] = None
            finding["suggested_source_end_s"] = None
    return report


def review(edl, index, family, user_request="", plan=None,
           asset_indexes=None):
    """Run the bounded independent story review, best effort."""
    if not should_review(edl, index, family, asset_indexes):
        return None
    evidence = build_evidence(
        edl, index, family, user_request, plan, asset_indexes)
    packed_evidence = _packed_review_evidence(evidence)
    answer = llm.ask_text(
        _SYSTEM, json.dumps(
            packed_evidence, ensure_ascii=False, separators=(",", ":")),
        max_tokens=1100, temperature=0.2,
        purpose="independent_story_critic")
    report = parse_report((answer or {}).get("text")) if answer else None
    if report is not None:
        report = _ground_repair_targets(report, packed_evidence)
        report["story_critic_v"] = STORY_CRITIC_VERSION
        report["program_sentence_count"] = evidence[
            "program_sentence_count"]
        report["sampled_evenly"] = evidence["program_sampled_evenly"]
    return report


def repair_lines(report):
    """High-confidence semantic defects eligible for one repair decision."""
    if not report:
        return []
    lines = []
    for finding in report.get("findings") or []:
        if finding.get("severity") not in {"blocker", "major"}:
            continue
        threshold = 0.90 if finding.get("category") in {
            "weak_hook", "redundant_thought"} else 0.82
        if float(finding.get("confidence") or 0.0) < threshold:
            continue
        target_id = finding.get("target_id")
        if not target_id or finding.get("program_s") is None:
            continue
        at = finding.get("program_s")
        where = f" at program {at:.1f}s" if at is not None else ""
        suggested = ""
        if finding.get("suggested_source_start_s") is not None and \
                finding.get("suggested_source_end_s") is not None:
            suggested = (" suggested_source="
                         f"{finding['suggested_source_start_s']:.2f}-"
                         f"{finding['suggested_source_end_s']:.2f}s")
        lines.append(
            f"independent story review [{finding.get('category')}]"
            f"{where} target={target_id}{suggested}: "
            f"{finding.get('evidence')} Repair: "
            f"{finding.get('repair')}")
    return lines


def summary_line(report):
    if report is None:
        return ""
    lines = repair_lines(report)
    if lines:
        return " INDEPENDENT STORY REVIEW: repair — " + "; ".join(lines[:3])
    summary = str(report.get("summary") or "").strip()
    if report.get("verdict") == "pass":
        return (" INDEPENDENT STORY REVIEW: pass"
                + (" — " + summary if summary else "."))
    findings = report.get("findings") or []
    if findings:
        return (" INDEPENDENT STORY REVIEW: advisory — "
                + str(findings[0].get("evidence") or "")[:260])
    return ""
