"""One dialect for every tool: a call whose meaning is clear works.

Each refusal costs the customer a full model call (60-80k prompt tokens on a
real project) and, in the 14 days to 6 Nov 2026, 424 customer tool calls were
refused of which only 48 were right to refuse. Most were the same root: the
model sent a reasonable value in a slightly different spelling and the tool
said no. This module repairs that spelling, using nothing but the tool's own
signature and schema, and says what it did so the model can read it in the
result.

It reads machine syntax only: argument names and enum values the model wrote
against our schemas. It never reads, matches or interprets a customer's words.

``repair`` never raises and never refuses. A call that is still impossible
after it (a required argument nobody can supply) gets ``exact_call``: the one
call that will work, so the refusal does not cost a second one.
"""
import difflib
import inspect
import json


_NOTHING = object()
NONE_WORDS = frozenset(("none", "null", "nil", "off", "no", "n/a", ""))
TRUE_WORDS = frozenset(("true", "1", "yes", "on"))
FALSE_WORDS = frozenset(("false", "0", "no", "off"))
_NAME_CUTOFF = 0.74
_ENUM_CUTOFF = 0.8
# A name that ends in one of these is the same argument in another unit word.
_UNIT_SUFFIXES = ("_seconds", "_secs", "_sec", "_s", "_ms")

# Names no spelling distance can find. Tool -> {what the model wrote: name}.
ALIASES = {
    "find_footage": {"topic": "query", "text": "query", "description": "query",
                     "prompt": "query", "q": "query"},
    "find_silences": {"min_silence_s": "min_seconds",
                      "min_silence": "min_seconds",
                      "min_silence_seconds": "min_seconds",
                      "minimum": "min_seconds", "min_s": "min_seconds"},
    "get_words": {"output_start": "start", "output_end": "end",
                  "from": "start", "to": "end"},
    "set_caption_mutes": {"mutes": "spans", "ranges": "spans"},
    "set_volume": {"gain": "gain_db", "volume_db": "gain_db", "db": "gain_db"},
}


def parameters(fn):
    """{name: inspect.Parameter} a tool takes, without ctx; None when the
    function cannot be read."""
    try:
        params = dict(inspect.signature(fn).parameters)
    except (TypeError, ValueError):
        return None
    params.pop("ctx", None)
    if any(p.kind == p.VAR_KEYWORD for p in params.values()):
        return None
    return params


def required(fn):
    params = parameters(fn) or {}
    return [name for name, p in params.items()
            if p.default is inspect.Parameter.empty
            and p.kind != p.VAR_POSITIONAL]


def _stem(name):
    low = str(name).strip().lower().replace("-", "_")
    for suffix in _UNIT_SUFFIXES:
        if low.endswith(suffix) and len(low) > len(suffix) + 1:
            return low[:-len(suffix)]
    return low


def _spelling(tool, key, params, args):
    """The argument a name the tool does not take was meant to be, or None."""
    table = ALIASES.get(tool) or {}
    target = table.get(key)
    if target in params and target not in args:
        return target
    free = [name for name in params if name not in args]
    low = str(key).strip().lower().replace("-", "_")
    if low in free:
        return low
    stem = _stem(key)
    for name in free:
        if _stem(name) == stem:
            return name
    # text_id, zoom_id, item_id: the id of the thing this tool acts on
    if low.endswith("_id") and "id" in free:
        return "id"
    near = difflib.get_close_matches(low, free, n=1, cutoff=_NAME_CUTOFF)
    return near[0] if near else None


def _enum_value(value, enum, optional, notes, key):
    """(value, keep): a string fitted to its enum. keep=False drops the
    argument so the tool's own default stands."""
    if not isinstance(value, str) or value in enum:
        return value, True
    words = [item for item in enum if isinstance(item, str)]
    fold = value.strip().lower().replace("-", "_").replace(" ", "_")
    for item in words:
        if item.lower().replace("-", "_").replace(" ", "_") == fold:
            notes.append(f"{key} {value!r} -> {item!r}")
            return item, True
    if fold in NONE_WORDS and optional:
        notes.append(f"{key} {value!r}: nothing to set, left out")
        return value, False
    near = difflib.get_close_matches(fold, words, n=1, cutoff=_ENUM_CUTOFF)
    if near:
        notes.append(f"{key} {value!r} -> {near[0]!r}")
        return near[0], True
    return value, True


def _typed(value, kind, key, notes):
    """A value in the JSON type the schema names, when the conversion is
    exact; otherwise the value unchanged (the tool has the last word)."""
    if kind in ("array", "object") and isinstance(value, str):
        text = value.strip()
        if text[:1] in ("[", "{"):
            try:
                # raw_decode: a model's string can run on past its value
                # (p5520: a complete operations list, then a stray tail).
                parsed, stop = json.JSONDecoder().raw_decode(text)
            except ValueError:
                return value
            if isinstance(parsed, (list if kind == "array" else dict)):
                notes.append(f"{key} sent as text: read as JSON"
                             + ("" if stop == len(text)
                                else ", the text after it left out"))
                return parsed
        elif kind == "array" and text:
            return [text]
        return value
    if kind == "array" and isinstance(value, (int, float)) \
            and not isinstance(value, bool):
        return [value]
    if kind == "array" and isinstance(value, dict):
        return [value]
    if kind in ("number", "integer") and isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            return value
        if kind == "integer" and number.is_integer():
            return int(number)
        return number if kind == "number" else value
    if kind == "integer" and isinstance(value, float) and value.is_integer():
        return int(value)
    if kind == "boolean" and isinstance(value, str):
        word = value.strip().lower()
        if word in TRUE_WORDS:
            return True
        if word in FALSE_WORDS:
            return False
    return value


def repair(tool, args, fn, props, notes):
    """Fit one call's argument names and values to the tool. Mutates and
    returns ``args``; every change that alters the call lands in ``notes``."""
    params = parameters(fn)
    if params is None:
        return args
    for key in list(args):
        if key in params:
            continue
        if key == "ids" and "id" in params:
            continue                 # one call per id, in the dispatcher
        if key == "id" and "ids" in params:
            args["ids"] = [args.pop(key)]
            continue
        target = _spelling(tool, key, params, args)
        value = args.pop(key)
        if target:
            args[target] = value
            notes.append(f"{key} -> {target}")
        else:
            notes.append(f"{key} ignored: {tool} does not take it")
    props = props if isinstance(props, dict) else {}
    for key in list(args):
        spec = props.get(key)
        if not isinstance(spec, dict):
            continue
        value = args[key]
        if value is None:
            continue
        kind = spec.get("type")
        if isinstance(kind, str):
            value = _typed(value, kind, key, notes)
        elif isinstance(value, str) and value.strip()[:1] in ("[", "{"):
            # no single type named (a tool that takes an id or a list): JSON
            # text is still a list or an object, never a name
            value = _typed(value, "array" if value.strip()[0] == "[" else
                           "object", key, notes)
        enum = spec.get("enum")
        keep = True
        if isinstance(enum, (list, tuple)) and enum:
            default = params[key].default if key in params else _NOTHING
            optional = default is None
            value, keep = _enum_value(value, enum, optional, notes, key)
        if keep:
            args[key] = value
        else:
            args.pop(key)
    return args


def missing(fn, args):
    """Required arguments this call still lacks."""
    return [name for name in required(fn)
            if name not in args and not (name == "id" and "ids" in args)]


def exact_call(tool, fn, props, args=None):
    """The call that will work, written out: the refusal's whole job."""
    props = props if isinstance(props, dict) else {}
    parts = []
    given = args or {}
    for name in required(fn):
        if name in given:
            parts.append(f"{name}={json.dumps(given[name], default=str)}")
            continue
        spec = props.get(name)
        kind = spec.get("type") if isinstance(spec, dict) else None
        enum = spec.get("enum") if isinstance(spec, dict) else None
        if enum:
            kind = "|".join(str(item) for item in enum[:8])
        parts.append(f"{name}=<{kind or 'value'}>")
    return f"{tool}({', '.join(parts)})"


def closest(value, choices, cutoff=0.6):
    """The one choice a mistyped machine token (a key, an id, a tool name) was
    meant to be, or None."""
    choices = [str(item) for item in choices if item]
    near = difflib.get_close_matches(str(value), choices, n=1, cutoff=cutoff)
    return near[0] if near else None


def clean_key(value):
    """A storage key with what our own sentences leave stuck to it removed: a
    key copied from 'added clips/5/a.mp4.' arrives with the full stop."""
    if not isinstance(value, str):
        return value
    return value.strip().strip("`'\"<>()[]{}.,;:!? ")
