"""
task_tools.py — Task tracking tools for the AI agent.

Allows the agent to create, update, and complete tasks during a build.
Tasks are stored in tasks.json in the job workspace folder and polled
by the frontend alongside progress data.

Usage in Agent5.py:
    from task_tools import create_task_tools
    task_map, task_defs = create_task_tools(workspace)
    tool_map.update(task_map)
    all_tools.extend(task_defs)
"""

import os
import json
import time
import uuid


def create_task_tools(workspace):
    """
    Create task-tracking tool functions and their schema definitions.

    Returns: (tool_map_additions: dict, tool_definitions: list)
    """

    tasks_path = os.path.join(workspace, "tasks.json") if workspace else None

    def _load_tasks():
        if not tasks_path or not os.path.exists(tasks_path):
            return []
        try:
            with open(tasks_path) as f:
                return json.load(f)
        except Exception:
            return []

    def _save_tasks(tasks):
        if not tasks_path:
            return
        with open(tasks_path, "w") as f:
            json.dump(tasks, f, ensure_ascii=False)

    # ── create_task ───────────────────────────────────────────────

    def create_task(title: str, description: str = "") -> str:
        """Create a new task. Returns the task ID."""
        tasks = _load_tasks()
        task_id = f"task_{uuid.uuid4().hex[:6]}"
        task = {
            "id": task_id,
            "title": title.strip(),
            "description": description.strip(),
            "status": "todo",       # todo | in_progress | done
            "created_at": time.time(),
            "updated_at": time.time(),
            "notes": [],
        }
        tasks.append(task)
        _save_tasks(tasks)
        print(f"[tasks] Created: {task_id} — {title}")
        return f"TASK_CREATED: id={task_id}, title={title}"

    # ── set_task_status ───────────────────────────────────────────

    def set_task_status(task_id: str, status: str) -> str:
        """Move a task to todo, in_progress, or done."""
        if status not in ("todo", "in_progress", "done"):
            return f"TASK_ERROR: Invalid status '{status}'. Use todo, in_progress, or done."
        tasks = _load_tasks()
        for task in tasks:
            if task["id"] == task_id:
                task["status"] = status
                task["updated_at"] = time.time()
                _save_tasks(tasks)
                print(f"[tasks] {task_id} → {status}")
                return f"TASK_UPDATED: {task_id} is now {status}"
        return f"TASK_ERROR: Task {task_id} not found."

    # ── add_task_note ─────────────────────────────────────────────

    def add_task_note(task_id: str, note: str) -> str:
        """Attach a note to a task (discovery, blocker, decision)."""
        tasks = _load_tasks()
        for task in tasks:
            if task["id"] == task_id:
                task["notes"].append({
                    "text": note.strip(),
                    "ts": time.time(),
                })
                task["updated_at"] = time.time()
                _save_tasks(tasks)
                return f"NOTE_ADDED to {task_id}"
        return f"TASK_ERROR: Task {task_id} not found."

    # ── get_tasks ─────────────────────────────────────────────────

    def get_tasks() -> str:
        """Get the full task list with statuses."""
        tasks = _load_tasks()
        if not tasks:
            return "NO_TASKS: No tasks created yet."
        lines = []
        status_icon = {"todo": "[ ]", "in_progress": "[→]", "done": "[✓]"}
        for t in tasks:
            icon = status_icon.get(t["status"], "[ ]")
            line = f"{icon} {t['title']} (id={t['id']})"
            if t.get("description"):
                line += f"\n    {t['description']}"
            if t.get("notes"):
                for n in t["notes"][-2:]:  # Show last 2 notes
                    line += f"\n    note: {n['text']}"
            lines.append(line)
        return "\n".join(lines)

    # ── delete_task ───────────────────────────────────────────────

    def delete_task(task_id: str) -> str:
        """Remove a task from the list."""
        tasks = _load_tasks()
        original_len = len(tasks)
        tasks = [t for t in tasks if t["id"] != task_id]
        if len(tasks) == original_len:
            return f"TASK_ERROR: Task {task_id} not found."
        _save_tasks(tasks)
        print(f"[tasks] Deleted: {task_id}")
        return f"TASK_DELETED: {task_id}"

    # ── Tool map and definitions ──────────────────────────────────

    tool_map = {
        "create_task":     create_task,
        "set_task_status": set_task_status,
        "add_task_note":   add_task_note,
        "get_tasks":       get_tasks,
        "delete_task":     delete_task,
    }

    tool_definitions = [
        {
            "name": "create_task",
            "description": (
                "Create a new task to track your work. Use for multi-step implementations.\n"
                "Keep titles short (≤6 words, verb-led). Create tasks BEFORE starting work.\n"
                "Do NOT create tasks for trivial single-file edits or pure Q&A."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "title":       {"type": "string", "description": "Short task title, e.g. 'Create auth login page'"},
                    "description": {"type": "string", "description": "One sentence describing the work"},
                },
                "required": ["title"]
            }
        },
        {
            "name": "set_task_status",
            "description": (
                "Move a task between statuses: todo, in_progress, done.\n"
                "Keep at most one task in_progress at a time.\n"
                "Mark tasks done as you complete them."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "The task ID returned by create_task"},
                    "status":  {"type": "string", "description": "todo, in_progress, or done"},
                },
                "required": ["task_id", "status"]
            }
        },
        {
            "name": "add_task_note",
            "description": "Attach a brief note to a task — findings, blockers, or decisions.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "note":    {"type": "string", "description": "Progress note or decision"},
                },
                "required": ["task_id", "note"]
            }
        },
        {
            "name": "get_tasks",
            "description": "Display the current task list with statuses and notes. Use for planning.",
            "input_schema": {"type": "object", "properties": {}}
        },
        {
            "name": "delete_task",
            "description": "Remove a task from the list.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                },
                "required": ["task_id"]
            }
        },
    ]

    return tool_map, tool_definitions