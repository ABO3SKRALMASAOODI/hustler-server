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

_COMMERCIAL_USE = re.compile(
    r"(?ix)(?:"
    r"\b(?:ad|advert|advertisement|promo|promotional|brand|branded|business|"
    r"company|corporate|client|marketing|product|startup|moneti[sz]ed|"
    r"sponsored|campaign|sales?)\b|"
    r"\b(?:instagram|social|video)\s+ad\b|"
    r"\bfor\s+(?:our|my|the)\s+(?:company|business|brand|client|product)\b"
    r")")

_BROAD_POLISH = re.compile(
    r"(?ix)(?:"
    r"\b(?:polish(?:ed)?|professional|pro[- ]?quality|publication[- ]?ready|"
    r"social[- ]?ready|tight(?:en|ened)?|engaging)\b.{0,48}"
    r"\b(?:clip|video|edit|reel|short|social|tiktok|instagram|podcast|"
    r"interview|talking[- ]?head)\b|"
    r"\b(?:clip|video|edit|reel|short|social|tiktok|instagram|podcast|"
    r"interview|talking[- ]?head)\b.{0,48}"
    r"\b(?:polish(?:ed)?|professional|pro[- ]?quality|publication[- ]?ready|"
    r"social[- ]?ready|tight(?:en|ened)?|engaging)\b"
    r")")


def no_captions(text):
    return bool(_NO_CAPTIONS.search(text or ""))


def preservation_requested(text):
    return bool(_PRESERVE.search(text or ""))


def reset_requested(text):
    return bool(_RESET.search(text or ""))


def source_inventory_must_be_checked(text):
    return bool(_SOURCE_CHECK.search(text or ""))


def commercial_use(text):
    """True when the latest brief clearly describes business/monetized use.

    This is a safety floor for catalog licensing, not a creative classifier:
    false means unknown/personal, while true means an NC track must not be
    silently baked into a file the user cannot lawfully publish as requested.
    """
    return bool(_COMMERCIAL_USE.search(text or ""))


def broad_polish_requested(text):
    """True when the user asks for an editorial outcome, not one local tweak.

    This is deliberately narrower than matching the word ``nice``. The
    contract below authorizes standard format finishing only when the user
    actually asked for a polished/professional/tight edit; preservation
    requests remain a stronger lock.
    """
    return bool(_BROAD_POLISH.search(text or ""))


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
    elif broad_polish_requested(text):
        lines.append(
            "BROAD-POLISH CONTRACT: this is an outcome request, so it DOES "
            "authorize the standard load-bearing finish for the footage's "
            "recognized format; it does not authorize random decoration. "
            "For a speech-led social/podcast/interview edit, remove indexed "
            "timed filler sounds and genuinely dead pauses when their cuts "
            "are word-safe, and include social loudness mastering in the "
            "FIRST atomic recipe. Skip either move when the user asks for "
            "natural/raw/uncut delivery or preserved levels. Never infer "
            "music, SFX, transitions or extra zooms from the word 'polish'."
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
