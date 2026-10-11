"""Compile device cards: devices/cards/<id>.yaml -> devices/cards/<id>.json.

PyYAML reaches the worker images only transitively (through faster-whisper's
dependencies), so no lane loads YAML at runtime (INTERFACES.md §5): the
YAML is the authoring format, the JSON beside it is what ``load_cards``
reads. One JSON per card keeps parallel tracks from colliding on a shared
compiled file.

    cd worker && python -m devices.compiler          # write every JSON
    cd worker && python -m devices.compiler --check  # exit 1 when stale

tests/test_w0c_cards.py runs the check, so a card edited without
recompiling fails CI.
"""

import json
import os
import sys

from . import CARDS_DIR, schema


def dumps(card):
    """The canonical JSON a card compiles to (stable bytes, newline-ended)."""
    return json.dumps(card, indent=2, ensure_ascii=False, sort_keys=False) \
        + "\n"


def load_yaml(path):
    import yaml   # authoring-time only
    with open(path, encoding="utf-8") as f:
        card = yaml.safe_load(f)
    if not isinstance(card, dict):
        raise ValueError(f"{os.path.basename(path)}: a card is a mapping")
    return card


def compile_dir(cards_dir=None, check=False):
    """{stem: (json_path, ok)}; raises ValueError on any invalid card.
    With ``check`` nothing is written and a stale/missing JSON is not ok."""
    cards_dir = cards_dir or CARDS_DIR
    sources = {}
    for fname in sorted(os.listdir(cards_dir)):
        stem, ext = os.path.splitext(fname)
        if ext in (".yaml", ".yml") and not fname.startswith((".", "_")):
            sources[stem] = load_yaml(os.path.join(cards_dir, fname))
    errors = []
    for stem, card in sources.items():
        if card.get("id") != stem:
            errors.append(f"{stem}: id must equal the file name")
        errors += [f"{stem}: {e}" for e in schema.validate(card, sources)]
    if errors:
        raise ValueError("invalid device cards: " + "; ".join(errors))
    out = {}
    for stem, card in sources.items():
        path = os.path.join(cards_dir, stem + ".json")
        text = dumps(card)
        try:
            with open(path, encoding="utf-8") as f:
                current = f.read()
        except OSError:
            current = None
        if check:
            out[stem] = (path, current == text)
        else:
            if current != text:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(text)
            out[stem] = (path, True)
    return out


def orphans(cards_dir=None):
    """Compiled JSON with no YAML source — allowed (a card may be authored
    as JSON), reported so a deleted YAML does not leave a live card."""
    cards_dir = cards_dir or CARDS_DIR
    names = set(os.listdir(cards_dir))
    return sorted(n for n in names if n.endswith(".json")
                  and n[:-5] + ".yaml" not in names
                  and n[:-5] + ".yml" not in names)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    check = "--check" in argv
    try:
        result = compile_dir(check=check)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    stale = [os.path.basename(p) for p, ok in result.values() if not ok]
    if stale:
        print("stale compiled cards: " + ", ".join(stale), file=sys.stderr)
        return 1
    print(f"{len(result)} card(s) {'checked' if check else 'compiled'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
