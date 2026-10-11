"""Device card schema (INTERFACES.md §5): one validator, used by the loader,
the compiler and the tests. Pure standard library."""

import re

from . import (BINDS_TO, BUILTIN_SUBSTITUTES, CARD_OPTIONAL, CARD_REQUIRED,
               FAMILIES, INTENSITIES, LANE_FEATURES, OWNER_FLAGS, RUNGS,
               SIDECAR_KINDS, STAGES, STATUSES, valid_id)

NEEDS_KEYS = ("sidecars", "lane_features", "footage")
BUDGET_KEYS = ("per_short", "min_gap_s", "per_section", "per_s")
COST_LANES = ("interactive", "batch", "vision", "local")
# Internal reference notation only (ROADMAP: "22@≈8.43", "05@0.23–12.23").
EXEMPLAR_RE = re.compile(r"^\d{2}@≈?\d+(\.\d+)?([–-]\d+(\.\d+)?)?$")
# "land_err_f<=1", "fill_iou>=0.95", "flash_risk"
VERIFY_RE = re.compile(r"^([a-z][a-z0-9_]*)\s*(?:(<=|>=|<|>|==)\s*"
                       r"-?\d+(\.\d+)?)?$")
_BIND_BASES = {b.split(".", 1)[0] for b in BINDS_TO}
_FLAG_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def verify_metric(entry):
    m = VERIFY_RE.match(str(entry or "").strip())
    return m.group(1) if m else None


def validate(card, cards=None):
    """[error, ...] for one card; empty when it honours the contract.
    ``cards`` (the whole set) lets a substitute rung name another card."""
    errs = []
    if not isinstance(card, dict):
        return ["a card is a mapping"]
    for key in CARD_REQUIRED:
        if key not in card:
            errs.append(f"missing {key}")
    extra = set(card) - set(CARD_REQUIRED) - set(CARD_OPTIONAL)
    if extra:
        errs.append("unknown field(s) " + ", ".join(sorted(extra)))
    if errs:
        return errs
    if not valid_id(card["id"]):
        errs.append("id must match ^[a-z][a-z0-9_]{2,47}$")
    if card["family"] not in FAMILIES:
        errs.append(f"family {card['family']!r} not in {FAMILIES}")
    if not (isinstance(card["verb"], str) and
            re.fullmatch(r"[a-z][a-z0-9_]{2,63}", card["verb"])):
        errs.append("verb must be a tool name")
    if card["stage"] not in STAGES:
        errs.append(f"stage {card['stage']!r} not in {STAGES}")
    if card.get("fx_type") is not None and not (
            isinstance(card["fx_type"], str) and card["fx_type"]):
        errs.append("fx_type must be a non-empty string")
    if not isinstance(card["params"], dict):
        errs.append("params must be a mapping")
    needs = card["needs"]
    if not isinstance(needs, dict):
        errs.append("needs must be a mapping")
    else:
        bad = set(needs) - set(NEEDS_KEYS)
        if bad:
            errs.append("needs: unknown key(s) " + ", ".join(sorted(bad)))
        for kind in needs.get("sidecars") or ():
            if kind not in SIDECAR_KINDS:
                errs.append(f"needs.sidecars: {kind!r} is not a pinned "
                            "sidecar kind")
        for feat in needs.get("lane_features") or ():
            if not (feat in LANE_FEATURES or (
                    isinstance(feat, str) and feat.startswith("ffmpeg:")
                    and re.fullmatch(r"ffmpeg:[a-z0-9_]+", feat))):
                errs.append(f"needs.lane_features: unknown {feat!r}")
        if "footage" in needs and not isinstance(needs["footage"], str):
            errs.append("needs.footage is human-readable text")
    binds = card["binds_to"]
    if not isinstance(binds, list) or not binds:
        errs.append("binds_to must be a non-empty list")
    else:
        for b in binds:
            if not (isinstance(b, str) and
                    (b in BINDS_TO or b.split(".", 1)[0] in _BIND_BASES)):
                errs.append(f"binds_to: unknown moment {b!r}")
    budget = card["budget"]
    if not isinstance(budget, dict) or set(budget) != set(INTENSITIES):
        errs.append(f"budget must give exactly {INTENSITIES}")
    else:
        for level, row in budget.items():
            if not isinstance(row, dict) or not row or \
                    set(row) - set(BUDGET_KEYS):
                errs.append(f"budget.{level}: keys from {BUDGET_KEYS}")
                continue
            for k, v in row.items():
                if not _num(v) or v < 0:
                    errs.append(f"budget.{level}.{k} must be >= 0")
        try:
            ladder = [budget[i].get("per_short") for i in INTENSITIES]
            if all(_num(v) for v in ladder) and not \
                    ladder[0] <= ladder[1] <= ladder[2]:
                errs.append("budget per_short must not shrink from calm to "
                            "charged")
        except AttributeError:
            pass
    never = card.get("never")
    if never is not None and not (isinstance(never, list) and
                                  all(isinstance(n, str) for n in never)):
        errs.append("never must be a list of strings")
    flags = card["owner_flags"]
    if not isinstance(flags, dict) or set(flags) != set(OWNER_FLAGS) or \
            not all(isinstance(v, bool) for v in flags.values()):
        errs.append(f"owner_flags must give all of {OWNER_FLAGS} as "
                    "booleans")
    errs += _rung_errors(card["rungs"], card.get("id"), cards)
    cost = card["cost"]
    if not isinstance(cost, dict) or set(cost) - {"ms_per_frame",
                                                  "fixed_ms"}:
        errs.append("cost must be {ms_per_frame: {lane: ms}, fixed_ms}")
    else:
        per = cost.get("ms_per_frame") or {}
        if not isinstance(per, dict) or set(per) - set(COST_LANES) or \
                not all(_num(v) and v >= 0 for v in per.values()):
            errs.append(f"cost.ms_per_frame lanes from {COST_LANES}, ms >= 0")
        if "fixed_ms" in cost and not (_num(cost["fixed_ms"])
                                       and cost["fixed_ms"] >= 0):
            errs.append("cost.fixed_ms must be >= 0")
    ex = card["exemplars"]
    if not isinstance(ex, list) or not all(
            isinstance(e, str) and EXEMPLAR_RE.match(e) for e in ex):
        errs.append("exemplars use the internal reference notation "
                    "('22@≈8.43')")
    ver = card["verify"]
    if not isinstance(ver, list) or not all(verify_metric(v) for v in ver):
        errs.append("verify entries are qa_metrics names with an optional "
                    "bar ('land_err_f<=1')")
    if card["status"] not in STATUSES:
        errs.append(f"status {card['status']!r} not in {STATUSES}")
    flag = card.get("flag")
    if flag is not None and not (isinstance(flag, str) and
                                 _FLAG_RE.match(flag)):
        errs.append("flag names a config/env flag (UPPER_SNAKE)")
    desc = card.get("description")
    if desc is not None and not (isinstance(desc, str) and
                                 0 < len(desc) <= 200):
        errs.append("description is one line (<= 200 chars)")
    skill = card.get("skill")
    if skill is not None and not (isinstance(skill, str) and
                                  re.fullmatch(r"[a-z0-9][a-z0-9-]*", skill)):
        errs.append("skill is a skills/<stem>.md stem")
    return errs


def _rung_errors(rungs, card_id, cards):
    errs = []
    if not isinstance(rungs, list) or not rungs:
        return ["rungs must be a non-empty list"]
    names = []
    for r in rungs:
        if isinstance(r, str):
            if r not in RUNGS or r == "substitute":
                errs.append(f"rung {r!r}: use full, reduced, omit or "
                            "{substitute: <device>}")
                continue
            names.append(r)
        elif isinstance(r, dict):
            keys = [k for k in RUNGS if k in r]
            if len(keys) != 1 or keys[0] in ("full", "omit"):
                errs.append("a rung mapping carries exactly one of reduced / "
                            "substitute")
                continue
            names.append(keys[0])
            if keys[0] == "substitute":
                sub = r["substitute"]
                if sub == card_id:
                    errs.append("a card cannot substitute itself")
                elif sub not in BUILTIN_SUBSTITUTES and (
                        cards is None or sub not in cards):
                    errs.append(f"substitute {sub!r} is neither a card nor a "
                                "built-in substitute")
        else:
            errs.append("a rung is a name or a mapping")
    if names and names[-1] != "omit":
        errs.append("rungs end with omit")
    if names.count("omit") > 1 or names.count("full") > 1:
        errs.append("full and omit appear once")
    order = [RUNGS.index(n) for n in names]
    if order != sorted(order):
        errs.append("rungs are ordered best-first (full, reduced, "
                    "substitute, omit)")
    return errs
