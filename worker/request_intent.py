"""Small deterministic guards for the latest user request.

The model still owns creative judgment.  These predicates cover the places
where judgment is not needed: an explicit "no captions" must never turn into
add_captions, a preservation brief must not be treated as permission to
rebuild the video, and a style reference is not missing source footage.
"""

import re


_NO_CAPTIONS = re.compile(
    r"(?ix)(?:"
    r"\b(?:no|without)\s+(?:captions?|subtitles?)\b|"
    r"\b(?:remove|delete|disable|turn\s+off)\b.{0,24}"
    r"\b(?:captions?|subtitles?)\b|"
    r"\bdo\s+not\s+(?:add|use|include)\b.{0,18}"
    r"\b(?:captions?|subtitles?)\b|"
    r"\bcaption[- ]?free\b|"
    r"\bsin\s+subt[ií]tulos\b|\bsem\s+legendas\b|"
    r"\b(?:quitar?|remover?)\b.{0,20}\b(?:subt[ií]tulos|legendas)\b|"
    r"字幕(?:なし|無し)|字幕.{0,10}(?:入れない|削除|消して|外して)|"
    r"テロップ(?:なし|無し)"
    r")")

_PRESERVE = re.compile(
    r"(?ix)(?:"
    r"\bkeep\b.{0,40}\b(?:original|unchanged|intact|same)\b|"
    r"\bpreserv(?:e|ing)\b|\bunchanged\b|"
    r"\bdo\s+not\s+(?:replace|rewrite|restructure|reorder|cut|crop)\b|"
    r"\bimprove\s+the\s+existing\s+footage\s+only\b|"
    r"\bonly\s+(?:fix|adjust|correct|improve|change)\b"
    r")")

_RESET = re.compile(
    r"(?ix)(?:\breset\b|\bstart\s+(?:again|over)\b|"
    r"\bclear\s+(?:it|everything|all)\b|\bfrom\s+scratch\b|"
    r"\bdesde\s+cero\b|\bdo\s+zero\b|全部クリア|やり直し)")

_SOURCE_CHECK = re.compile(
    r"(?ix)(?:"
    r"\b(?:my|the)\s+(?:uploaded\s+)?(?:clips|footage|source\s+clips)\b|"
    r"\b(?:video|clip)\b.{0,24}\b(?:reference|referencia|refer[eê]ncia)\b|"
    r"\b(?:use|using|usar|usando)\b.{0,24}\b(?:as\s+)?(?:a\s+)?"
    r"(?:reference|referencia|refer[eê]ncia)\b|"
    r"\b(?:create|build|make)\b.{0,35}\bfrom\s+scratch\b|"
    r"\b(?:crear|hacer)\b.{0,35}\bdesde\s+cero\b"
    r")")


def no_captions(text):
    return bool(_NO_CAPTIONS.search(text or ""))


def preservation_requested(text):
    return bool(_PRESERVE.search(text or ""))


def reset_requested(text):
    return bool(_RESET.search(text or ""))


def source_inventory_must_be_checked(text):
    return bool(_SOURCE_CHECK.search(text or ""))


def request_contract(text):
    """A short system anchor placed immediately beside the current request."""
    lines = [
        "CURRENT REQUEST CONTRACT — the final user message has highest "
        "priority. Earlier messages supply missing context only; anything "
        "the latest message removes, reverses or narrows is superseded."
    ]
    if no_captions(text):
        lines.append(
            "NO-CAPTIONS LOCK: the user explicitly wants no captions or "
            "subtitles. Remove existing transcript captions if present and "
            "do not add, restyle or regenerate any."
        )
    if preservation_requested(text):
        lines.append(
            "PRESERVATION LOCK: keep the original footage, voice, wording, "
            "order and timing except for changes the user explicitly named. "
            "If the current EDL already violates that lock, reset it first; "
            "do not add generic polish, captions, cuts, music or effects."
        )
    if reset_requested(text):
        lines.append(
            "RESET FIRST: the user asked to start over. Reset the existing "
            "EDL before applying any explicitly requested replacement edit; "
            "do not preserve decorations from the abandoned version."
        )
    if source_inventory_must_be_checked(text):
        lines.append(
            "SOURCE-MATERIAL CHECK: verify the media inventory contains the "
            "actual source clips the user named. A SHORTS STYLE REFERENCE is "
            "an example only and never counts as source footage. If the "
            "required clips are absent, ask for them before editing the "
            "reference itself."
        )
    return "\n".join(lines)
