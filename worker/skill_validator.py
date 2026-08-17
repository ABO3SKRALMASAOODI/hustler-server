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


def tool_names(path: Path = TOOLS_SOURCE) -> set[str]:
    """Extract public tool keys without importing the worker dependency tree."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(isinstance(target, ast.Name) and target.id == "TOOLS"
                   for target in targets):
            continue
        if not isinstance(node.value, ast.Dict):
            break
        return {
            key.value for key in node.value.keys
            if isinstance(key, ast.Constant) and isinstance(key.value, str)
        }
    raise ValueError(f"Could not statically locate TOOLS in {path}")


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
    public_tools = tool_names(tools_path)
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
    else:
        for path, errors in result["failures"].items():
            for error in errors:
                print(f"{path}: {error}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
