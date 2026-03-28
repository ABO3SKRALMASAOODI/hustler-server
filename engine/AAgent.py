import json
import concurrent.futures
import traceback
from typing import Any, Callable, Dict, List, Optional
import anthropic
from dotenv import load_dotenv
import time
import os


def _get(item, key, default=None):
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def _serialize_content_block(block) -> dict:
    if isinstance(block, dict):
        return block
    d = block.model_dump()
    allowed = {"type", "text", "id", "name", "input", "cache_control"}
    return {k: v for k, v in d.items() if k in allowed and v is not None}


# ══════════════════════════════════════════════════════════════════════════════
#  STOP SENTINEL
# ══════════════════════════════════════════════════════════════════════════════

class StopAgent:
    """
    Return this from any tool handler to stop the agent loop immediately.
    The tool result is still appended to messages cleanly before exit.

    BaseAgent.chat() returns (text, totals, code_changed, stop.data)
    when a StopAgent is returned, or (text, totals, code_changed, None) normally.
    """
    def __init__(self, data: Any = None, reason: str = ""):
        self.data = data
        self.reason = reason


class BaseAgent:
    def __init__(
        self,
        *,
        client: anthropic.Anthropic,
        model: str,
        system_prompt: str,
        tools: Optional[List[dict]] = None,
        tool_map: Optional[Dict[str, Callable[..., Any]]] = None,
        reviewer=None,
        temperature=1,
        max_tokens=64000,
        workspace: Optional[str] = None,
    ):
        self.pending_notices: List[str] = []
        self.client = client
        self.model = model
        self.system_prompt = system_prompt
        self.tools = tools or []
        self.tool_map = tool_map or {}
        self.messages: List[dict] = []
        self.reviewer = reviewer
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.workspace = workspace

        # ── Callback hooks ────────────────────────────────────────────
        self.on_thinking: Optional[Callable] = None
        self.on_tool_start: Optional[Callable] = None
        self.on_tool_end: Optional[Callable] = None
        self.on_text: Optional[Callable] = None
        self.on_rate_limit: Optional[Callable] = None

    # ------------------------------------------------------------------ #
    #  Partial deduction                                                   #
    # ------------------------------------------------------------------ #

    def _save_partial_deduction(self, totals):
        if not self.workspace:
            return
        try:
            anthropic_to_V = {
                "claude-haiku-4-5-20251001": "V6",
                "claude-sonnet-4-6":         "V6-pro",
                "claude-opus-4-6":           "V7",
            }
            V_model = anthropic_to_V.get(self.model, "V6-pro")
            path = os.path.join(self.workspace, "partial_deduction.json")
            data = [{
                "input_tokens":       totals.get("input", 0),
                "output_tokens":      totals.get("output", 0),
                "cache_write_tokens": totals.get("cache_write", 0),
                "cache_read_tokens":  totals.get("cache_read", 0),
                "tokens_used":        sum(totals.values()),
                "partial":            True,
                "model":              V_model,
            }]
            with open(path, "w") as f:
                json.dump(data, f)
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    #  Crash recovery — write state.json on failure                        #
    # ------------------------------------------------------------------ #

    def _write_crash_state(self, error_msg: str, tb: str = ""):
        """
        Write a failed state to state.json so the frontend knows the job died.
        Also writes crash details to crash_log.json for debugging.
        """
        if not self.workspace:
            return
        try:
            state_path = os.path.join(self.workspace, "state.json")
            with open(state_path, "w") as f:
                json.dump({
                    "state":      "failed",
                    "error":      error_msg[:500],
                    "updated_at": time.time(),
                }, f)
            print(f"[agent] Wrote failed state: {error_msg[:200]}")
        except Exception as e:
            print(f"[agent] CRITICAL: Could not write crash state: {e}")

        # Write detailed crash log for debugging
        try:
            crash_path = os.path.join(self.workspace, "crash_log.json")
            crash_data = {
                "error":     error_msg,
                "traceback": tb[:5000] if tb else "",
                "model":     self.model,
                "timestamp": time.time(),
                "turn_count": len(self.messages),
                "last_tool":  self._last_tool_name or "none",
            }
            with open(crash_path, "w") as f:
                json.dump(crash_data, f, indent=2)
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    #  Caching helpers                                                     #
    # ------------------------------------------------------------------ #

    def _build_system(self) -> List[dict]:
        return [
            {
                "type": "text",
                "text": self.system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ]

    def _build_tools(self) -> List[dict]:
        if not self.tools:
            return []
        tools = [t.copy() for t in self.tools]
        tools[-1] = {**tools[-1], "cache_control": {"type": "ephemeral"}}
        return tools

    def _apply_history_cache(self):
        if len(self.messages) < 2:
            return
        for msg in self.messages[:-1]:
            content = msg.get("content", [])
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and "cache_control" in block:
                        del block["cache_control"]
        target_msg = self.messages[-2]
        content = target_msg["content"]
        if isinstance(content, str):
            self.messages[-2]["content"] = [
                {
                    "type": "text",
                    "text": content,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        elif isinstance(content, list) and content:
            last_block = content[-1]
            if isinstance(last_block, dict) and "cache_control" not in last_block:
                content[-1] = {**last_block, "cache_control": {"type": "ephemeral"}}

    # ------------------------------------------------------------------ #
    #  Inter-agent notices                                                 #
    # ------------------------------------------------------------------ #

    def receive_sys_notice(self, content: dict):
        self.pending_notices.append(json.dumps(content, ensure_ascii=True))

    def notify_reviewer(self, message):
        if self.reviewer:
            self.reviewer.receive_sys_notice(message)

    # ------------------------------------------------------------------ #
    #  Main chat loop                                                      #
    # ------------------------------------------------------------------ #

    CODE_WRITING_TOOLS = {
        "write_file", "edit_file", "generate_image",
        "edit_image", "delete_file", "rename_file"
    }

    # Maximum turns to prevent infinite loops
    MAX_TURNS = 80

    def chat(self, user_msg):
        """
        Run the agentic loop.

        Returns (text, token_totals, code_changed, stop_data) where:
          - text:         the final assistant text response
          - token_totals: dict with input/output/cache_write/cache_read
          - code_changed: True if any code-writing tool was called
          - stop_data:    the data payload from StopAgent if a tool returned one, else None
        """
        # Track the last tool called for crash diagnostics
        self._last_tool_name = None

        if self.pending_notices:
            notice_text = str(self.pending_notices)
            if isinstance(user_msg, str):
                user_msg = f"{notice_text}\n\n{user_msg}"
            elif isinstance(user_msg, list):
                user_msg = [{"type": "text", "text": notice_text}] + user_msg
            self.pending_notices.clear()

        self.messages.append({"role": "user", "content": user_msg})

        totals = {"input": 0, "output": 0, "cache_write": 0, "cache_read": 0}
        code_changed = False
        turn_count = 0

        try:
            while True:
                turn_count += 1

                # ── Safety: prevent infinite loops ────────────────────────
                if turn_count > self.MAX_TURNS:
                    error_msg = f"Agent exceeded maximum turn limit ({self.MAX_TURNS}). Stopping to prevent runaway loop."
                    print(f"[agent] {error_msg}")
                    self._write_crash_state(error_msg)
                    self._save_partial_deduction(totals)
                    return error_msg, totals, code_changed, None

                # ── Check for cancellation ────────────────────────────────
                if self.workspace:
                    cancelled_path = os.path.join(self.workspace, "cancelled.lock")
                    if os.path.exists(cancelled_path):
                        print(f"[agent] Cancellation detected at turn {turn_count}")
                        self._save_partial_deduction(totals)
                        return "", totals, code_changed, None

                self._apply_history_cache()

                if self.on_thinking:
                    self.on_thinking(turn_count, f"Generating code (step {turn_count})...")

                self._save_partial_deduction(totals)

                kwargs = dict(
                    model=self.model,
                    system=self._build_system(),
                    messages=self.messages,
                    max_tokens=self.max_tokens,
                    temperature=self.temperature,
                )
                if self.tools:
                    kwargs["tools"] = self._build_tools()
                    kwargs["tool_choice"] = {"type": "auto"}

                # ── API call with retry ───────────────────────────────────
                max_retries = 8
                base_delay = 5
                resp = None
                for attempt in range(max_retries):
                    try:
                        with self.client.messages.stream(**kwargs) as stream:
                            resp = stream.get_final_message()
                        break
                    except anthropic.RateLimitError:
                        if attempt == max_retries - 1:
                            raise
                        delay = base_delay * (2 ** attempt)
                        print(f"[rate limit] hit on attempt {attempt + 1}, retrying in {delay}s...")
                        if self.on_rate_limit:
                            self.on_rate_limit(attempt, delay)
                        time.sleep(delay)
                    except anthropic.APIStatusError as e:
                        # Catch overloaded, bad gateway, etc.
                        if e.status_code in (500, 502, 503, 529) and attempt < max_retries - 1:
                            delay = base_delay * (2 ** attempt)
                            print(f"[api_error] status {e.status_code} on attempt {attempt + 1}, retrying in {delay}s...")
                            time.sleep(delay)
                        else:
                            raise

                if resp is None:
                    error_msg = "API returned no response after all retries"
                    print(f"[agent] {error_msg}")
                    self._write_crash_state(error_msg)
                    self._save_partial_deduction(totals)
                    return error_msg, totals, code_changed, None

                usage = resp.usage
                turn_input       = usage.input_tokens
                turn_output      = usage.output_tokens
                turn_cache_write = getattr(usage, "cache_creation_input_tokens", 0)
                turn_cache_read  = getattr(usage, "cache_read_input_tokens", 0)

                totals["input"]       += turn_input
                totals["output"]      += turn_output
                totals["cache_write"] += turn_cache_write
                totals["cache_read"]  += turn_cache_read

                print(
                    f"[tokens] input={turn_input} | output={turn_output} | "
                    f"cache_write={turn_cache_write} | cache_read={turn_cache_read} | "
                    f"running_totals={totals}"
                )

                self._save_partial_deduction(totals)

                self.messages.append({
                    "role": "assistant",
                    "content": [_serialize_content_block(block) for block in resp.content]
                })

                tool_uses = [b for b in resp.content if _get(b, "type") == "tool_use"]

                if not tool_uses:
                    text = "".join(
                        _get(b, "text", "")
                        for b in resp.content
                        if _get(b, "type") == "text"
                    )
                    return text, totals, code_changed, None

                thinking_text = "".join(
                    _get(b, "text", "") for b in resp.content if _get(b, "type") == "text"
                ).strip()

                if thinking_text and self.on_text:
                    self.on_text(thinking_text)

                # ── Split image tools from other tools ────────────────────
                image_blocks = [b for b in tool_uses if _get(b, "name") == "generate_image"]
                other_blocks  = [b for b in tool_uses if _get(b, "name") != "generate_image"]

                tool_results = []
                stop_sentinel = None

                # ── Execute non-image tools sequentially ──────────────────
                for block in other_blocks:
                    name    = _get(block, "name")
                    call_id = _get(block, "id")
                    raw_in  = _get(block, "input") or {}
                    args    = raw_in if isinstance(raw_in, dict) else json.loads(raw_in)

                    self._last_tool_name = name

                    if self.on_tool_start:
                        self.on_tool_start(name, args)

                    if name in self.CODE_WRITING_TOOLS:
                        code_changed = True
                        print(f"[code_changed] set True — tool '{name}' was called")

                    if name not in self.tool_map:
                        error_msg = f"Model called unknown tool: {name}"
                        print(f"[agent] {error_msg}")
                        # Don't crash — return an error to the model so it can recover
                        tool_results.append({
                            "type":        "tool_result",
                            "tool_use_id": call_id,
                            "content":     f"TOOL_ERROR: Unknown tool '{name}'. Available tools: {list(self.tool_map.keys())}",
                        })
                        continue

                    # ── Execute tool with error handling ──────────────────
                    try:
                        result = self.tool_map[name](**args)
                    except Exception as e:
                        tb = traceback.format_exc()
                        print(f"[agent] Tool '{name}' raised exception: {e}\n{tb}")
                        result = f"TOOL_EXECUTION_ERROR: {type(e).__name__}: {str(e)[:300]}. The tool crashed — please try a different approach or skip this step."

                    # ── StopAgent check ───────────────────────────────────
                    if isinstance(result, StopAgent):
                        stop_sentinel = result
                        result_content = result.reason if result.reason else "APPROVED"
                    else:
                        result_content = result if isinstance(result, str) else json.dumps(result)

                    if self.on_tool_end:
                        self.on_tool_end(name, args, result_content)

                    tool_results.append({
                        "type":        "tool_result",
                        "tool_use_id": call_id,
                        "content":     result_content,
                    })

                # ── Execute image generation in batched parallel threads ──
                if image_blocks:
                    code_changed = True
                    BATCH_SIZE = 4

                    def _run_image(block):
                        name    = _get(block, "name")
                        call_id = _get(block, "id")
                        raw_in  = _get(block, "input") or {}
                        args    = raw_in if isinstance(raw_in, dict) else json.loads(raw_in)

                        if self.on_tool_start:
                            self.on_tool_start(name, args)

                        if name not in self.tool_map:
                            return call_id, f"IMAGE_GENERATION_FAILED: unknown tool {name}"

                        try:
                            result = self.tool_map[name](**args)
                        except Exception as e:
                            result = f"IMAGE_GENERATION_FAILED: {str(e)[:120]} — use CSS gradient placeholder instead. Do NOT import this path."

                        if self.on_tool_end:
                            self.on_tool_end(name, args, result)

                        return call_id, result

                    for batch_idx in range(0, len(image_blocks), BATCH_SIZE):
                        batch = image_blocks[batch_idx:batch_idx + BATCH_SIZE]

                        if batch_idx > 0:
                            print(f"[images] Waiting 3s before batch {batch_idx // BATCH_SIZE + 1}...")
                            time.sleep(3)

                        with concurrent.futures.ThreadPoolExecutor(max_workers=BATCH_SIZE) as executor:
                            futures = {
                                executor.submit(_run_image, block): block
                                for block in batch
                            }
                            for future in concurrent.futures.as_completed(futures):
                                try:
                                    call_id, result = future.result()
                                except Exception as e:
                                    block   = futures[future]
                                    call_id = _get(block, "id")
                                    result  = f"IMAGE_GENERATION_FAILED: {str(e)[:120]} — use CSS gradient placeholder instead. Do NOT import this path."

                                tool_results.append({
                                    "type":        "tool_result",
                                    "tool_use_id": call_id,
                                    "content":     result if isinstance(result, str) else json.dumps(result),
                                })

                self.messages.append({"role": "user", "content": tool_results})
                self._apply_history_cache()

                # ── Exit cleanly if any tool signalled stop ────────────────
                if stop_sentinel is not None:
                    return "", totals, code_changed, stop_sentinel.data

        # ══════════════════════════════════════════════════════════════════
        #  TOP-LEVEL EXCEPTION HANDLERS
        #  These catch anything that slips through the loop so the job
        #  never silently vanishes.
        # ══════════════════════════════════════════════════════════════════

        except anthropic.APIStatusError as e:
            tb = traceback.format_exc()
            error_msg = f"Anthropic API error (HTTP {e.status_code}): {str(e)[:300]}"
            print(f"[agent] FATAL: {error_msg}\n{tb}")
            self._write_crash_state(error_msg, tb)
            self._save_partial_deduction(totals)
            return error_msg, totals, code_changed, None

        except anthropic.APIConnectionError as e:
            tb = traceback.format_exc()
            error_msg = f"Anthropic API connection error: {str(e)[:300]}"
            print(f"[agent] FATAL: {error_msg}\n{tb}")
            self._write_crash_state(error_msg, tb)
            self._save_partial_deduction(totals)
            return error_msg, totals, code_changed, None

        except KeyboardInterrupt:
            print(f"[agent] KeyboardInterrupt — saving partial deduction")
            self._write_crash_state("Process interrupted")
            self._save_partial_deduction(totals)
            raise

        except Exception as e:
            tb = traceback.format_exc()
            error_msg = f"Agent crashed: {type(e).__name__}: {str(e)[:300]}"
            print(f"[agent] FATAL UNHANDLED EXCEPTION:\n{tb}")
            self._write_crash_state(error_msg, tb)
            self._save_partial_deduction(totals)
            return error_msg, totals, code_changed, None