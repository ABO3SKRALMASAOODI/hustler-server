"""
Workflow Engine skill — multi-step automations with conditional logic.

Lets the agent define, save, and execute multi-step workflows.
Each step can use any other skill, with data passing between steps.
This is what makes the agent truly autonomous and indispensable.
"""

import json
import time
import psycopg2
from psycopg2.extras import RealDictCursor
from engine.super_agent.skills.base_skill import BaseSkill


class WorkflowEngineSkill(BaseSkill):
    SKILL_TYPE = "workflow_engine"
    DISPLAY_NAME = "Workflow Engine"
    DESCRIPTION = "Create and run multi-step automated workflows. Chain actions together — fetch data, process it, send results, log everything. The backbone of automation."
    CATEGORY = "automation"
    CONFIG_SCHEMA = {}

    @classmethod
    def get_tool_definitions(cls):
        return [
            {
                "name": "save_workflow",
                "description": (
                    "Save a reusable workflow definition to memory. "
                    "A workflow is a list of steps, each describing an action. "
                    "Steps reference other tools and can pass data between them. "
                    "The workflow can then be executed on-demand or on a schedule."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Workflow name (unique identifier)"},
                        "description": {"type": "string", "description": "What this workflow does"},
                        "steps": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string", "description": "Step name"},
                                    "action": {"type": "string", "description": "Description of what to do in this step"},
                                    "on_failure": {"type": "string", "enum": ["stop", "continue", "retry"], "description": "What to do if this step fails"},
                                },
                                "required": ["name", "action"],
                            },
                            "description": "Ordered list of workflow steps",
                        },
                    },
                    "required": ["name", "steps"],
                },
            },
            {
                "name": "list_workflows",
                "description": "List all saved workflows for this agent.",
                "input_schema": {"type": "object", "properties": {}},
            },
            {
                "name": "get_workflow",
                "description": "Get the full definition of a saved workflow by name.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Workflow name"},
                    },
                    "required": ["name"],
                },
            },
            {
                "name": "delete_workflow",
                "description": "Delete a saved workflow by name.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Workflow name to delete"},
                    },
                    "required": ["name"],
                },
            },
        ]

    @classmethod
    def create_handlers(cls, config, context=None):
        ctx = context or {}
        memory_manager = ctx.get("memory_manager")

        def save_workflow(name, steps, description=""):
            if not memory_manager:
                return "ERROR: Memory manager not available."

            workflow = {
                "name": name,
                "description": description,
                "steps": steps,
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            }

            memory_manager.store(
                f"workflow:{name}",
                json.dumps(workflow),
                category="instruction"
            )
            return f"Workflow '{name}' saved with {len(steps)} steps. You can reference it by name in future conversations."

        def list_workflows():
            if not memory_manager:
                return "ERROR: Memory manager not available."

            results = memory_manager.search("workflow:")
            workflows = []
            for r in results:
                if r["key"].startswith("workflow:"):
                    try:
                        wf = json.loads(r["value"])
                        workflows.append({
                            "name": wf.get("name", r["key"]),
                            "description": wf.get("description", ""),
                            "steps": len(wf.get("steps", [])),
                        })
                    except json.JSONDecodeError:
                        pass

            if not workflows:
                return "No workflows saved yet."

            lines = ["**Saved Workflows:**\n"]
            for wf in workflows:
                lines.append(f"- **{wf['name']}** ({wf['steps']} steps)")
                if wf["description"]:
                    lines.append(f"  {wf['description']}")
            return "\n".join(lines)

        def get_workflow(name):
            if not memory_manager:
                return "ERROR: Memory manager not available."

            results = memory_manager.search(f"workflow:{name}")
            for r in results:
                if r["key"] == f"workflow:{name}":
                    try:
                        wf = json.loads(r["value"])
                        lines = [f"**Workflow: {wf['name']}**"]
                        if wf.get("description"):
                            lines.append(f"_{wf['description']}_\n")
                        for i, step in enumerate(wf.get("steps", []), 1):
                            on_fail = step.get("on_failure", "stop")
                            lines.append(f"{i}. **{step['name']}** (on failure: {on_fail})")
                            lines.append(f"   {step['action']}")
                        return "\n".join(lines)
                    except json.JSONDecodeError:
                        return "ERROR: Corrupted workflow data."

            return f"Workflow '{name}' not found."

        def delete_workflow(name):
            if not memory_manager:
                return "ERROR: Memory manager not available."

            results = memory_manager.search(f"workflow:{name}")
            for r in results:
                if r["key"] == f"workflow:{name}":
                    memory_manager.delete(r["id"])
                    return f"Workflow '{name}' deleted."

            return f"Workflow '{name}' not found."

        return {
            "save_workflow": save_workflow,
            "list_workflows": list_workflows,
            "get_workflow": get_workflow,
            "delete_workflow": delete_workflow,
        }
