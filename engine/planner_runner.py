"""
planner_runner.py — Background runner for the Requirements Gatherer (Planner) agent.

Runs as a persistent background thread (NOT a subprocess with timeout).
Communicates with the frontend via file-based signaling in the job workspace.

Credits handling:
  - Deducted immediately after each planner turn via direct DB call
  - NOT written to deduct_credits.json (avoids double-deduction when builder runs)
  - On quit/error mid-run, reads partial_deduction.json left by BaseAgent and deducts that
  - Uses negative turn numbers (-1, -2, -3...) to distinguish from builder turns (1, 2, 3...)

Files used:
  planner_state.json       — current planner status
  planner_messages.jsonl   — planner conversation log
  planner_answer.json      — frontend writes user answers here, runner picks them up
  planner_spec.json        — the final approved spec
  planner_quit.json        — signal to quit
  planner_turn_counter.txt — tracks negative turn numbers for credit entries
"""

import argparse
import json
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

import anthropic
from dotenv import load_dotenv
load_dotenv()

from AAgent import BaseAgent, StopAgent

try:
    from credits import tokens_to_credits, deduct_credits as _deduct_credits_fn
except ImportError:
    _deduct_credits_fn = None
    def tokens_to_credits(input_tokens, output_tokens, cache_write_tokens, cache_read_tokens, model="hb-6-pro"):
        pricing = {
            "hb-6":     {"input": 1.00, "output": 5.00,  "cache_write": 1.25, "cache_read": 0.10},
            "hb-6-pro": {"input": 3.00, "output": 15.00, "cache_write": 3.75, "cache_read": 0.30},
            "hb-7":     {"input": 5.00, "output": 25.00, "cache_write": 6.25, "cache_read": 0.50},
        }
        p = pricing.get(model, pricing["hb-6-pro"])
        cost_dollars = (
            (input_tokens       * p["input"]) +
            (output_tokens      * p["output"]) +
            (cache_write_tokens * p["cache_write"]) +
            (cache_read_tokens  * p["cache_read"])
        ) / 1_000_000
        return round(cost_dollars / 0.01, 2)


ANTHROPIC_TO_HB = {
    "claude-haiku-4-5-20251001": "hb-6",
    "claude-sonnet-4-6":         "hb-6-pro",
    "claude-opus-4-6":           "hb-7",
}

MODEL_MAP = {
    "hb-6":     "claude-haiku-4-5-20251001",
    "hb-6-pro": "claude-sonnet-4-6",
    "hb-7":     "claude-opus-4-6",
}

PLANNER_TURN_FILE = "planner_turn_counter.txt"

client = anthropic.Anthropic()


# ══════════════════════════════════════════════════════════════════════════════
#  FILE I/O HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def write_planner_state(workspace, state, extra=None):
    data = {"state": state, "updated_at": time.time()}
    if extra:
        data.update(extra)
    with open(os.path.join(workspace, "planner_state.json"), "w") as f:
        json.dump(data, f)


def append_main_message(workspace, role, text, extra=None):
    """Write planner messages to the main messages.jsonl so they persist across refresh."""
    entry = {"role": role, "text": text, "ts": time.time(), "source": "planner"}
    if extra:
        entry.update(extra)
    with open(os.path.join(workspace, "messages.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def append_planner_message(workspace, role, text, extra=None):
    entry = {"role": role, "text": text, "ts": time.time()}
    if extra:
        entry.update(extra)
    with open(os.path.join(workspace, "planner_messages.jsonl"), "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def get_next_planner_turn(workspace):
    """Get next planner turn number (negative: -1, -2, -3...) to avoid collision with builder turns."""
    path = os.path.join(workspace, PLANNER_TURN_FILE)
    current = 0
    if os.path.exists(path):
        try:
            with open(path) as f:
                current = int(f.read().strip())
        except Exception:
            current = 0
    next_turn = current - 1
    with open(path, "w") as f:
        f.write(str(next_turn))
    return next_turn


def check_quit(workspace):
    quit_path = os.path.join(workspace, "planner_quit.json")
    if os.path.exists(quit_path):
        try:
            os.remove(quit_path)
        except Exception:
            pass
        return True
    return False


def wait_for_answer(workspace, timeout=7200):
    answer_path = os.path.join(workspace, "planner_answer.json")
    quit_path = os.path.join(workspace, "planner_quit.json")
    elapsed = 0
    while elapsed < timeout:
        if os.path.exists(quit_path):
            try:
                os.remove(quit_path)
            except Exception:
                pass
            raise PlannerQuit()

        if os.path.exists(answer_path):
            try:
                with open(answer_path) as f:
                    data = json.load(f)
                os.remove(answer_path)
                return data
            except Exception:
                pass

        time.sleep(1.5)
        elapsed += 1.5

    raise TimeoutError("No answer from user within timeout")


class PlannerQuit(Exception):
    pass


# ══════════════════════════════════════════════════════════════════════════════
#  CREDITS — immediate deduction, no deduct_credits.json
# ══════════════════════════════════════════════════════════════════════════════

def _get_db_connection():
    try:
        import psycopg2
        from psycopg2.extras import RealDictCursor
        db_url = os.getenv("DATABASE_URL")
        if not db_url:
            return None
        return psycopg2.connect(db_url, cursor_factory=RealDictCursor)
    except Exception as e:
        print(f"[planner] DB connection failed: {e}")
        return None


def _get_user_id(workspace):
    meta_path = os.path.join(workspace, "meta.json")
    if not os.path.exists(meta_path):
        return None
    try:
        with open(meta_path) as f:
            meta = json.load(f)
        uid = meta.get("user_id")
        return int(uid) if uid else None
    except Exception:
        return None


def deduct_planner_credits(workspace, token_breakdown, hb_model):
    """
    Deduct planner credits immediately from the user's balance.
    Uses negative turn numbers to avoid collision with builder turns.
    Does NOT write to deduct_credits.json — avoids double-deduction.
    Returns credits_used amount.
    """
    if not _deduct_credits_fn:
        print("[planner] deduct_credits not available, skipping direct deduction")
        return 0

    user_id = _get_user_id(workspace)
    if not user_id:
        print("[planner] No user_id found, skipping deduction")
        return 0

    job_id = os.path.basename(workspace)
    turn = get_next_planner_turn(workspace)

    conn = _get_db_connection()
    if not conn:
        print("[planner] No DB connection, skipping deduction")
        return 0

    try:
        credits_used = _deduct_credits_fn(
            conn,
            user_id=user_id,
            job_id=job_id,
            turn=turn,
            tokens_used=sum(token_breakdown.values()),
            input_tokens=token_breakdown.get("input", 0),
            output_tokens=token_breakdown.get("output", 0),
            cache_write_tokens=token_breakdown.get("cache_write", 0),
            cache_read_tokens=token_breakdown.get("cache_read", 0),
            model=hb_model,
        )
        print(f"[planner] Deducted {credits_used} credits (turn={turn})")
        return credits_used
    except Exception as e:
        print(f"[planner] Direct deduction failed: {e}")
        return 0
    finally:
        conn.close()


def deduct_from_partial(workspace, hb_model):
    """
    Read partial_deduction.json (written by BaseAgent mid-run) and deduct.
    Called on quit/error when the agent was interrupted mid-API-call.
    Cleans up the file after deduction.
    """
    partial_path = os.path.join(workspace, "partial_deduction.json")
    if not os.path.exists(partial_path):
        return

    try:
        with open(partial_path) as f:
            entries = json.load(f)
        if not entries:
            return

        entry = entries[-1] if isinstance(entries, list) else entries
        token_breakdown = {
            "input": int(entry.get("input_tokens", 0)),
            "output": int(entry.get("output_tokens", 0)),
            "cache_write": int(entry.get("cache_write_tokens", 0)),
            "cache_read": int(entry.get("cache_read_tokens", 0)),
        }

        total_tokens = sum(token_breakdown.values())
        if total_tokens == 0:
            return

        credits = deduct_planner_credits(workspace, token_breakdown, hb_model)
        print(f"[planner] Deducted {credits} from partial (quit/error mid-run)")

        os.remove(partial_path)
    except Exception as e:
        print(f"[planner] Failed to deduct from partial: {e}")


# ══════════════════════════════════════════════════════════════════════════════
#  SYSTEM PROMPT
# ══════════════════════════════════════════════════════════════════════════════

REQUIREMENTS_AGENT_SYSTEM_PROMPT = """You are the Requirements Gatherer — the first agent in a multi-agent web development pipeline.

Your ONLY job is to extract a complete, unambiguous specification from the user's request before handing it off to the Builder agent. The Builder works autonomously with zero user interaction, so every gap you leave becomes a wrong assumption baked into the final product.

IMPORTANT: Users may start with casual messages like "hi", "hello", or general questions. That's perfectly fine — respond conversationally and naturally. Ask what they'd like to build. You don't need to immediately jump into structured questions. Build rapport, understand their idea, THEN start gathering requirements.

────────────────────────────────────────────────────────
YOUR THREE TOOLS
────────────────────────────────────────────────────────
1. ask_user     — Present a numbered list of questions to the user. Use whenever critical information is missing.
2. propose_spec — Submit the completed specification for user review.
3. edit_spec    — Apply targeted patches when the user requests changes after propose_spec.

FLOW:
  Conversation (natural back-and-forth) ->
  ask_user (loop until complete) -> propose_spec ->
    approved : stop (tool returns APPROVED via StopAgent)
    rejected : continue gathering with rejection feedback
    edit     : call edit_spec with patches ->
                 approved : stop (tool returns APPROVED)
                 rejected : continue with rejection feedback
                 more edits : tool returns "EDIT REQUEST: ..." — call edit_spec again

IMPORTANT:
- You CAN respond with plain text (no tool call) for casual conversation, greetings, clarifications, or when the user hasn't given enough context for structured questions yet.
- After propose_spec returns an edit request, ALWAYS call edit_spec. Never call propose_spec again.
- After any tool returns APPROVED, stop immediately — do not say anything.
- After rejection, you get the rejection feedback — use it to ask better questions or revise.
- Never reconstruct the full spec from memory. edit_spec patches the live spec.

────────────────────────────────────────────────────────
THE STACK (MANDATORY — Builder uses this exact stack)
────────────────────────────────────────────────────────
- Framework: React + Vite + TypeScript
- Styling: Tailwind CSS with CSS variable design tokens in index.css
- Animation: Framer Motion
- Routing: React Router
- Backend (optional): Supabase (auth + postgres database + RLS)
- Payments (optional): Stripe via hosted proxy
- AI (optional): Claude via hosted proxy
- Fonts: Google Fonts (display font for headings, sans for body)
- Images: AI-generated via Replicate and saved to src/assets/
- Components: one primary component per file, src/pages/ for pages, src/components/ for reusable UI

────────────────────────────────────────────────────────
WHAT YOU MUST ALWAYS CLARIFY
────────────────────────────────────────────────────────
Before calling propose_spec, you must know:

PRODUCT — core purpose, target user, key features (ranked), pages/screens
DESIGN — aesthetic direction, color preferences, font style, motion style
DATA & AUTH — user accounts needed?, what data to save, real backend or localStorage
CONTENT — real content to appear, image descriptions
SCOPE — what is out of scope, third-party APIs needed

────────────────────────────────────────────────────────
HOW TO ASK QUESTIONS
────────────────────────────────────────────────────────
Call ask_user with a numbered list. Group related questions. Be direct.
Batch ALL questions for a given round into one call.
Do NOT ask more than 8 questions per round.
The user is on a web interface — keep it scannable.

────────────────────────────────────────────────────────
SPEC QUALITY RULES
────────────────────────────────────────────────────────
- Every page gets its own full object — no page may be skipped.
- Use specific hex values, font names, and motion descriptions.
- Every action must state exactly what happens.
- Image descriptions must be detailed enough for AI generation.
- Never infer or assume values not explicitly given — ask instead.

────────────────────────────────────────────────────────
TONE
────────────────────────────────────────────────────────
Professional, direct, efficient. Friendly but focused. No filler phrases. No emojis."""


# ══════════════════════════════════════════════════════════════════════════════
#  TOOL DEFINITIONS
# ══════════════════════════════════════════════════════════════════════════════

REQUIREMENTS_AGENT_TOOLS = [
    {
        "name": "ask_user",
        "description": (
            "Present a numbered list of questions to the user to fill gaps in the requirements. "
            "Batch ALL questions for this round into one call. Max 8 questions per round."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "questions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of specific, answerable questions."
                },
                "context": {
                    "type": "string",
                    "description": "One sentence shown above the questions explaining what they unlock."
                }
            },
            "required": ["questions", "context"]
        }
    },
    {
        "name": "propose_spec",
        "description": (
            "Submit the completed requirements spec for user review. "
            "Returns APPROVED (stop), REJECTED with feedback, or EDIT REQUEST with instructions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "3-5 sentence summary of what will be built."
                },
                "spec": {
                    "type": "object",
                    "description": "The full structured specification.",
                    "properties": {
                        "project": {
                            "type": "object",
                            "properties": {
                                "name":        {"type": "string"},
                                "purpose":     {"type": "string"},
                                "target_user": {"type": "string"}
                            },
                            "required": ["name", "purpose", "target_user"]
                        },
                        "pages": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "name":            {"type": "string"},
                                    "route":           {"type": "string"},
                                    "description":     {"type": "string"},
                                    "sections":        {"type": "array", "items": {"type": "string"}},
                                    "interactions":    {"type": "array", "items": {"type": "string"}},
                                    "states":          {"type": "array", "items": {"type": "string"}},
                                    "auth_visibility": {"type": "string"}
                                },
                                "required": ["name", "route", "description"]
                            }
                        },
                        "design": {
                            "type": "object",
                            "properties": {
                                "aesthetic": {"type": "string"},
                                "colors": {"type": "object"},
                                "typography": {"type": "object"},
                                "motion": {"type": "string"}
                            },
                            "required": ["aesthetic", "colors", "typography", "motion"]
                        },
                        "data": {
                            "type": "object",
                            "properties": {
                                "backend":       {"type": "string"},
                                "auth_required": {"type": "boolean"},
                                "tables":        {"type": "array", "items": {"type": "object"}},
                                "data_flow":     {"type": "string"}
                            },
                            "required": ["backend", "auth_required", "data_flow"]
                        },
                        "content": {
                            "type": "object",
                            "properties": {
                                "copy":   {"type": "object"},
                                "images": {"type": "array", "items": {"type": "object"}}
                            },
                            "required": ["copy", "images"]
                        },
                        "out_of_scope": {"type": "array", "items": {"type": "string"}},
                        "assumptions":  {"type": "array", "items": {"type": "string"}},
                        "notes":        {"type": "string"}
                    },
                    "required": ["project", "pages", "design", "data", "content", "out_of_scope", "assumptions", "notes"]
                }
            },
            "required": ["summary", "spec"]
        }
    },
    {
        "name": "edit_spec",
        "description": (
            "Apply targeted patches to the live spec. "
            "Returns APPROVED, REJECTED with feedback, or EDIT REQUEST with more instructions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "edits": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "path":   {"type": "string", "description": "Dot-notation path (e.g. 'design.colors.accent')"},
                            "value":  {"description": "New value (any JSON type)"},
                            "reason": {"type": "string"}
                        },
                        "required": ["path", "value", "reason"]
                    }
                },
                "summary": {"type": "string", "description": "Updated summary reflecting edits."}
            },
            "required": ["edits", "summary"]
        }
    }
]


# ══════════════════════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _set_nested(d, path, value):
    parts = path.split(".")
    target = d
    for part in parts[:-1]:
        key = int(part) if isinstance(target, list) else part
        target = target[key]
    last = parts[-1]
    if isinstance(target, list):
        target[int(last)] = value
    else:
        target[last] = value


def resolve_model_from_meta(workspace, fallback=None):
    meta_path = os.path.join(workspace, "meta.json")
    if os.path.exists(meta_path):
        try:
            with open(meta_path) as f:
                meta = json.load(f)
            hb_model = meta.get("model", "hb-6")
            return MODEL_MAP.get(hb_model, "claude-haiku-4-5-20251001")
        except Exception:
            pass
    return fallback or "claude-haiku-4-5-20251001"


# ══════════════════════════════════════════════════════════════════════════════
#  TOOL HANDLERS
# ══════════════════════════════════════════════════════════════════════════════

def make_tool_handlers(workspace):
    current_spec = {}
    current_summary = ""

    def handle_ask_user(questions, context):
        nonlocal current_spec, current_summary

        write_planner_state(workspace, "waiting_questions", {
            "questions": questions,
            "context": context,
        })

        append_planner_message(workspace, "planner", json.dumps({
            "type": "questions",
            "context": context,
            "questions": questions,
        }))
        append_main_message(workspace, "assistant", json.dumps({
            "type": "questions",
            "context": context,
            "questions": questions,
        }))

        print(f"[planner] Asking {len(questions)} questions, waiting for user...")

        answer_data = wait_for_answer(workspace)
        answer_text = answer_data.get("answer", "")

        append_planner_message(workspace, "user", answer_text)
        append_main_message(workspace, "user", answer_text)
        print(f"[planner] Got answer: {answer_text[:100]}...")

        write_planner_state(workspace, "thinking")
        return answer_text

    def handle_propose_spec(spec, summary):
        nonlocal current_spec, current_summary

        current_spec.clear()
        current_spec.update(spec)
        current_summary = summary

        write_planner_state(workspace, "waiting_spec", {
            "spec": spec,
            "summary": summary,
        })

        append_planner_message(workspace, "planner", json.dumps({
            "type": "spec",
            "spec": spec,
            "summary": summary,
        }))
        append_main_message(workspace, "assistant", json.dumps({
            "type": "spec",
            "summary": summary,
        }))

        print(f"[planner] Spec proposed, waiting for user decision...")

        answer_data = wait_for_answer(workspace)
        decision = answer_data.get("decision", "").lower()
        detail = answer_data.get("detail", "")

        append_planner_message(workspace, "user", f"[{decision}] {detail}")
        append_main_message(workspace, "user", f"[{decision}] {detail}")

        if decision == "approve":
            print(f"[planner] Spec approved!")
            spec_path = os.path.join(workspace, "planner_spec.json")
            with open(spec_path, "w") as f:
                json.dump({"spec": spec, "summary": summary}, f, indent=2)
            append_main_message(workspace, "system", json.dumps({
                "type": "planner_complete",
                "summary": summary,
            }))
            return StopAgent(
                data={"spec": spec, "summary": summary},
                reason="APPROVED"
            )

        if decision == "reject":
            print(f"[planner] Spec rejected with feedback: {detail}")
            write_planner_state(workspace, "thinking")
            return f"REJECTED — User feedback: {detail}\n\nUse this feedback to ask better questions with ask_user and then propose a revised spec."

        print(f"[planner] Edit requested: {detail}")
        write_planner_state(workspace, "thinking")
        return f"EDIT REQUEST: {detail}"

    def handle_edit_spec(edits, summary):
        nonlocal current_spec, current_summary

        if not current_spec:
            return "ERROR: No spec has been proposed yet. Call propose_spec first."

        for edit in edits:
            path = edit["path"]
            value = edit["value"]
            try:
                _set_nested(current_spec, path, value)
                print(f"[planner] Patched {path}")
            except (KeyError, IndexError, TypeError) as e:
                print(f"[planner] Failed to patch {path}: {e}")

        current_summary = summary

        write_planner_state(workspace, "waiting_edit", {
            "spec": dict(current_spec),
            "summary": current_summary,
            "edits_applied": [{"path": e["path"], "reason": e.get("reason", "")} for e in edits],
        })

        append_planner_message(workspace, "planner", json.dumps({
            "type": "spec_edit",
            "spec": dict(current_spec),
            "summary": current_summary,
            "edits": [{"path": e["path"], "reason": e.get("reason", "")} for e in edits],
        }))
        append_main_message(workspace, "assistant", json.dumps({
            "type": "spec_edit",
            "summary": current_summary,
        }))

        print(f"[planner] Edits applied, waiting for decision...")

        answer_data = wait_for_answer(workspace)
        decision = answer_data.get("decision", "").lower()
        detail = answer_data.get("detail", "")

        append_planner_message(workspace, "user", f"[{decision}] {detail}")
        append_main_message(workspace, "user", f"[{decision}] {detail}")

        if decision == "approve":
            print(f"[planner] Spec approved after edits!")
            spec_path = os.path.join(workspace, "planner_spec.json")
            with open(spec_path, "w") as f:
                json.dump({"spec": dict(current_spec), "summary": current_summary}, f, indent=2)
            append_main_message(workspace, "system", json.dumps({
                "type": "planner_complete",
                "summary": current_summary,
            }))
            return StopAgent(
                data={"spec": dict(current_spec), "summary": current_summary},
                reason="APPROVED"
            )

        if decision == "reject":
            print(f"[planner] Spec rejected after edits, feedback: {detail}")
            write_planner_state(workspace, "thinking")
            return f"REJECTED — User feedback: {detail}\n\nUse this feedback to ask better questions with ask_user and then propose a revised spec."

        print(f"[planner] More edits requested: {detail}")
        write_planner_state(workspace, "thinking")
        return f"EDIT REQUEST: {detail}"

    return {
        "ask_user":     lambda questions, context: handle_ask_user(questions, context),
        "propose_spec": lambda spec, summary: handle_propose_spec(spec, summary),
        "edit_spec":    lambda edits, summary: handle_edit_spec(edits, summary),
    }


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════════

def run_planner(workspace, message, model_override=None):
    """
    Main planner entry point. Called from the planner route's background thread.

    Credits are deducted immediately after each run via direct DB call.
    NOT written to deduct_credits.json — avoids double-deduction when builder runs.
    On quit/error, partial_deduction.json (from BaseAgent) is read and deducted.
    """
    anthropic_model = model_override
    if not anthropic_model:
        anthropic_model = resolve_model_from_meta(workspace)

    hb_model = ANTHROPIC_TO_HB.get(anthropic_model, "hb-6-pro")
    print(f"[planner] Using model: {anthropic_model} (HB: {hb_model})")

    try:
        write_planner_state(workspace, "thinking")

        # Clean up stale signal files
        for stale in ["planner_answer.json", "planner_quit.json"]:
            p = os.path.join(workspace, stale)
            if os.path.exists(p):
                os.remove(p)

        # Clean up partial_deduction from any previous run
        partial_path = os.path.join(workspace, "partial_deduction.json")
        if os.path.exists(partial_path):
            try:
                os.remove(partial_path)
            except Exception:
                pass

        tool_handlers = make_tool_handlers(workspace)

        agent = BaseAgent(
            client=client,
            model=anthropic_model,
            system_prompt=REQUIREMENTS_AGENT_SYSTEM_PROMPT,
            tools=REQUIREMENTS_AGENT_TOOLS,
            tool_map=tool_handlers,
            temperature=1,
            max_tokens=8096,
            workspace=workspace,
        )

        # ── Load existing planner conversation history for resume ─────
        messages_path = os.path.join(workspace, "planner_messages.jsonl")
        if os.path.exists(messages_path):
            with open(messages_path, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        role = entry.get("role")
                        text = entry.get("text", "")
                        if role == "user" and text:
                            agent.messages.append({"role": "user", "content": text})
                        elif role == "planner" and text:
                            agent.messages.append({"role": "assistant", "content": text})
                    except json.JSONDecodeError:
                        continue

        user_message = message.strip()
        append_planner_message(workspace, "user", user_message)
        append_main_message(workspace, "user", user_message)

        print(f"[planner] Starting with message: {user_message[:100]}...")

        # Run the agentic loop
        text, token_totals, code_changed, stop_data = agent.chat(user_message)

        print(f"[planner] Agent finished. stop_data={'present' if stop_data else 'None'}, text_len={len(text)}")

        # ── Deduct credits immediately (no deduct_credits.json) ───────
        credits_used = deduct_planner_credits(workspace, token_totals, hb_model)
        if credits_used == 0:
            # Fallback: calculate for display even if DB deduction failed
            credits_used = tokens_to_credits(
                input_tokens=token_totals["input"],
                output_tokens=token_totals["output"],
                cache_write_tokens=token_totals["cache_write"],
                cache_read_tokens=token_totals["cache_read"],
                model=hb_model,
            )
        print(f"[planner] Credits: {credits_used} | Tokens: {token_totals}")

        # Clean up partial since we deducted the full amount
        if os.path.exists(partial_path):
            try:
                os.remove(partial_path)
            except Exception:
                pass

        if stop_data is not None:
            write_planner_state(workspace, "completed", {
                "spec": stop_data.get("spec"),
                "summary": stop_data.get("summary"),
                "credits_used": credits_used,
            })
        elif text:
            append_planner_message(workspace, "planner", text)
            append_main_message(workspace, "assistant", text)
            write_planner_state(workspace, "waiting_reply", {
                "message": text,
                "credits_used": credits_used,
            })
        else:
            write_planner_state(workspace, "idle", {
                "credits_used": credits_used,
            })

    except PlannerQuit:
        print(f"[planner] User quit the planner.")
        deduct_from_partial(workspace, hb_model)
        write_planner_state(workspace, "quit")

    except TimeoutError as e:
        print(f"[planner] Timeout: {e}")
        deduct_from_partial(workspace, hb_model)
        write_planner_state(workspace, "error", {"error": str(e)})

    except Exception as e:
        print(f"[planner] Error: {e}")
        traceback.print_exc()
        deduct_from_partial(workspace, hb_model)
        write_planner_state(workspace, "error", {
            "error": str(e),
            "traceback": traceback.format_exc(),
        })


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--message",   required=True)
    parser.add_argument("--model",     default=None)
    args = parser.parse_args()
    run_planner(args.workspace, args.message, args.model)


if __name__ == "__main__":
    main()