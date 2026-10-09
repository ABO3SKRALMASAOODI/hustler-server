"""Static validation for the model-visible editorial skill library.

This module intentionally uses only the Python standard library so deployment
CI can run it before installing the worker's production dependencies.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SKILLS_DIR = ROOT / "skills"
TOOLS_SOURCE = ROOT / "agent_tools.py"
REQUIRED_SECTIONS = (
    "editorial decision principles",
    "evidence to inspect",
    "strong treatment patterns",
    "common failure modes",
    "verification procedure",
    "repair ladder",
)

# These are schema fields or named renderer treatments which deliberately use
# function-like notation in prose. Everything else containing an underscore
# and followed by '(' must be an advertised agent tool.
NON_TOOL_CALLS = {
    "dip_black", "dip_white", "duration_s", "max_words_per_caption",
    "moment_in_program", "size_scale", "whip_right", "zoom_punch",
}
DATED_HISTORY = (
    (re.compile(r"\b20\d\d-\d\d-\d\d\b"), "dated incident"),
    (re.compile(r"\bproject\s+#?\d+\b", re.I), "project incident"),
    (re.compile(r"\bround\s+#?\d+\b", re.I), "round history"),
    (re.compile(r"\breal session\b|\bliteral complaint\b", re.I),
     "user/session history"),
)


def _dict_literal_keys(path: Path, name: str, _seen: frozenset = frozenset()
                       ) -> set[str]:
    """String keys of the module-level dict literal ``name`` in ``path``.
    ``{**OTHER, ...}`` merges of another literal in the same module are
    followed; anything else that cannot be read statically raises."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(isinstance(target, ast.Name) and target.id == name
                   for target in targets):
            continue
        if not isinstance(node.value, ast.Dict):
            break
        keys = set()
        for key, value in zip(node.value.keys, node.value.values):
            if key is None and isinstance(value, ast.Name) and value.id not in _seen:
                keys |= _dict_literal_keys(path, value.id, _seen | {name})
            elif isinstance(key, ast.Constant) and isinstance(key.value, str):
                keys.add(key.value)
        return keys
    raise ValueError(f"Could not statically locate {name} in {path}")


def tool_names(path: Path = TOOLS_SOURCE) -> set[str]:
    """Extract public tool keys without importing the worker dependency tree."""
    return _dict_literal_keys(path, "TOOLS")


def registered_tool_names(path: Path = TOOLS_SOURCE) -> set[str]:
    """Every name agent_tools registers, statically: the TOOLS literal, each
    ``TOOLS.update(<module>.<SPECS>)`` merge (read from ``<module>.py`` next
    to it) and each ``TOOLS["name"] = ...`` assignment."""
    names = tool_names(path)
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "update"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "TOOLS" and len(node.args) == 1):
            arg = node.args[0]
            if (isinstance(arg, ast.Attribute)
                    and isinstance(arg.value, ast.Name)):
                names |= _dict_literal_keys(path.parent / f"{arg.value.id}.py",
                                            arg.attr)
            elif isinstance(arg, ast.Name):
                names |= _dict_literal_keys(path, arg.id)
            else:
                raise ValueError("Cannot statically read TOOLS.update("
                                 f"{ast.unparse(arg)}) in {path}")
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if (isinstance(target, ast.Subscript)
                        and isinstance(target.value, ast.Name)
                        and target.value.id == "TOOLS"
                        and isinstance(target.slice, ast.Constant)
                        and isinstance(target.slice.value, str)):
                    names.add(target.slice.value)
    return names


def retired_tool_names(path: Path = TOOLS_SOURCE) -> set[str]:
    """Names popped from TOOLS by agent_tools' `for _retired_tool in (...)`."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if (isinstance(node, ast.For) and isinstance(node.target, ast.Name)
                and node.target.id == "_retired_tool"
                and isinstance(node.iter, (ast.Tuple, ast.List))):
            return {elt.value for elt in node.iter.elts
                    if isinstance(elt, ast.Constant)
                    and isinstance(elt.value, str)}
    return set()


def live_tool_names(path: Path = TOOLS_SOURCE) -> set[str]:
    """The live TOOLS registry, statically: every registered name minus the
    retired ones. CI runs this before worker dependencies are installed;
    tests compare it with the imported registry."""
    return registered_tool_names(path) - retired_tool_names(path)


_IDENTIFIER = re.compile(r"(?<![\w.\-/])([a-z][a-z0-9]*(?:_[a-z0-9]+)+)(?!\w)")
_CALL = re.compile(r"(?<![\w.])([a-z][a-z0-9_]*_[a-z0-9_]+)\s*\(")


def stale_tool_references(skills_dir: Path = SKILLS_DIR,
                          tools_path: Path = TOOLS_SOURCE,
                          live: set[str] | None = None
                          ) -> dict[str, list[str]]:
    """{skill file: sorted tool names it references that TOOLS lacks}.

    A reference is a function-shaped name (`name(`) or any bare mention of
    a name that is or was a registered tool (retired tools included), so
    prose like "use research_music" is caught even without parentheses.
    ``live`` overrides the static registry (tests pass the imported TOOLS).
    """
    live = live_tool_names(tools_path) if live is None else set(live)
    tool_like = (registered_tool_names(tools_path)
                 | retired_tool_names(tools_path) | live)
    found: dict[str, list[str]] = {}
    for path in sorted(skills_dir.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        named = {m.group(1) for m in _IDENTIFIER.finditer(text)} & tool_like
        called = set(_CALL.findall(text)) - NON_TOOL_CALLS
        stale = sorted((named | called) - live)
        if stale:
            found[path.name] = stale
    return found


def _sections(text: str) -> list[tuple[str, int, str]]:
    found: list[tuple[str, int, str]] = []
    for line_number, line in enumerate(text.splitlines(), 1):
        match = re.match(r"^##\s+(.+?)\s*$", line)
        if match:
            found.append((match.group(1).strip().lower(), line_number, line))
    return found


def validate_skill(path: Path, public_tools: set[str],
                   known_skills: set[str]) -> list[str]:
    text = path.read_text(encoding="utf-8")
    errors: list[str] = []
    if not text.startswith(f"# {path.stem} —"):
        errors.append("first heading must be '# <filename> — <description>'")

    sections = _sections(text)
    section_names = [name for name, _line, _raw in sections]
    for expected in REQUIRED_SECTIONS:
        count = section_names.count(expected)
        if count != 1:
            errors.append(f"section '{expected}' must occur exactly once (got {count})")
    positions = [section_names.index(name) for name in REQUIRED_SECTIONS
                 if name in section_names]
    if len(positions) == len(REQUIRED_SECTIONS) and positions != sorted(positions):
        errors.append("required sections must use the standard order")
    lines = text.splitlines()
    for section_index, (name, line_number, _raw) in enumerate(sections):
        if name not in REQUIRED_SECTIONS:
            continue
        next_line = (sections[section_index + 1][1] - 1
                     if section_index + 1 < len(sections) else len(lines))
        following = lines[line_number:next_line]
        body = next((line.strip() for line in following if line.strip()), "")
        if not body:
            errors.append(f"section '{name}' has no guidance")

    for pattern, label in DATED_HISTORY:
        match = pattern.search(text)
        if match:
            errors.append(f"contains {label}: {match.group(0)!r}")

    # Function-shaped underscore names are overwhelmingly Valmera tools. This
    # catches renamed/removed tools without mistaking ordinary parentheticals
    # or ffmpeg filter prose for capabilities.
    calls = set(re.findall(r"(?<![\w.])([a-z][a-z0-9_]*_[a-z0-9_]+)\s*\(", text))
    for name in sorted(calls - public_tools - NON_TOOL_CALLS):
        errors.append(f"references unavailable tool/capability '{name}'")

    for match in re.finditer(r"\bread_skill(?:\([^)]*\)|\s+)([a-z][a-z0-9-]+)",
                             text, re.I):
        target = match.group(1).lower()
        if target not in known_skills:
            errors.append(f"references unavailable skill '{target}'")
    return errors


def validate_all(skills_dir: Path = SKILLS_DIR,
                 tools_path: Path = TOOLS_SOURCE) -> dict:
    paths = sorted(skills_dir.glob("*.md"))
    known_skills = {path.stem for path in paths}
    # Structural check: every registered name (merged tool modules
    # included). Retired names are reported by stale_tool_references().
    public_tools = registered_tool_names(tools_path)
    failures = {}
    for path in paths:
        try:
            label = str(path.relative_to(ROOT))
        except ValueError:
            label = f"{skills_dir.name}/{path.name}"
        failures[label] = validate_skill(path, public_tools, known_skills)
    failures = {path: errors for path, errors in failures.items() if errors}
    return {
        "ok": not failures,
        "skills": len(paths),
        "tools": len(public_tools),
        "failures": failures,
        # Informational here (tests ratchet it): tool names a skill mentions
        # that the live TOOLS registry no longer serves.
        "stale_tool_references": stale_tool_references(skills_dir, tools_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = validate_all()
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    elif result["ok"]:
        print(f"Validated {result['skills']} skills against "
              f"{result['tools']} public tools")
        for path, names in result["stale_tool_references"].items():
            print(f"warning: skills/{path} names tools missing from TOOLS: "
                  + ", ".join(names))
    else:
        for path, errors in result["failures"].items():
            for error in errors:
                print(f"{path}: {error}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
