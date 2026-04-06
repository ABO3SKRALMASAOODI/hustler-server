import json
import concurrent.futures
from typing import Any, Callable, Dict, List, Optional
import anthropic
from dotenv import load_dotenv
import time


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
            import os as _os, json as _json
            anthropic_to_V = {
                "claude-haiku-4-5-20251001": "V6",
                "claude-sonnet-4-6":         "V6-pro",
                "claude-opus-4-6":           "V7",
            }
            V_model = anthropic_to_V.get(self.model, "V6-pro")
            path = _os.path.join(self.workspace, "partial_deduction.json")
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
                _json.dump(data, f)
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

    def chat(self, user_msg):
        """
        Run the agentic loop.

        Returns (text, token_totals, code_changed, stop_data) where:
          - text:         the final assistant text response
          - token_totals: dict with input/output/cache_write/cache_read
          - code_changed: True if any code-writing tool was called
          - stop_data:    the data payload from StopAgent if a tool returned one, else None
        """
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
        max_tokens_retries = 0
        accumulated_thinking = []  # Track thinking text from tool-use turns

        while True:
            turn_count += 1
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

            max_retries = 8
            base_delay = 5
            for attempt in range(max_retries):
                try:
                    with self.client.messages.stream(**kwargs) as stream:
                        resp = stream.get_final_message()
                    break
                except (anthropic.RateLimitError, anthropic.APIStatusError) as e:
                    err_msg = str(e).lower()
                    is_retryable = isinstance(e, anthropic.RateLimitError) or "overloaded" in err_msg or "529" in err_msg or "503" in err_msg
                    if not is_retryable or attempt == max_retries - 1:
                        raise
                    delay = base_delay * (2 ** attempt)
                    print(f"[api error] {type(e).__name__} on attempt {attempt + 1}, retrying in {delay}s...")
                    if self.on_rate_limit:
                        self.on_rate_limit(attempt, delay)
                    time.sleep(delay)

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

            # ── Handle max_tokens truncation (tool call cut off) ─────
            stop_reason = getattr(resp, "stop_reason", None)
            if stop_reason == "max_tokens" and not tool_uses and max_tokens_retries < 3:
                max_tokens_retries += 1
                new_limit = min(self.max_tokens * 2, 64000)
                print(f"[max_tokens] Truncated at {self.max_tokens} tokens (no tool calls completed), "
                      f"retrying with {new_limit} (attempt {max_tokens_retries}/3)")
                self.messages.pop()  # Remove truncated assistant message
                self.max_tokens = new_limit
                continue

            if not tool_uses:
                # Final response — this is the actual answer to the user
                text = "".join(
                    _get(b, "text", "")
                    for b in resp.content
                    if _get(b, "type") == "text"
                )
                return text, totals, code_changed, None

            # This is a tool-use turn — any text here is "thinking", not the final answer
            thinking_text = "".join(
                _get(b, "text", "") for b in resp.content if _get(b, "type") == "text"
            ).strip()

            if thinking_text:
                accumulated_thinking.append(thinking_text)
                if self.on_text:
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
 
                if self.on_tool_start:
                    self.on_tool_start(name, args)
 
                if name in self.CODE_WRITING_TOOLS:
                    code_changed = True
                    print(f"[code_changed] set True — tool '{name}' was called")
 
                if name not in self.tool_map:
                    result = f"TOOL_ERROR: Unknown tool '{name}'. Available tools: {', '.join(self.tool_map.keys())}"
                else:
                    try:
                        result = self.tool_map[name](**args)
                    except TypeError as e:
                        # Wrong arguments passed to tool
                        result = f"TOOL_ERROR: Bad arguments for '{name}': {str(e)[:200]}. Check the tool schema and try again."
                        print(f"[tool_error] {name} TypeError: {e}")
                    except FileNotFoundError as e:
                        result = f"TOOL_ERROR: File not found: {str(e)[:200]}"
                        print(f"[tool_error] {name} FileNotFoundError: {e}")
                    except PermissionError as e:
                        result = f"TOOL_ERROR: Permission denied: {str(e)[:200]}"
                        print(f"[tool_error] {name} PermissionError: {e}")
                    except Exception as e:
                        # Catch-all: return error to model so it can self-correct
                        result = f"TOOL_ERROR: {type(e).__name__}: {str(e)[:300]}. Fix the issue and try again."
                        print(f"[tool_error] {name} {type(e).__name__}: {e}")
 
                # ── StopAgent check ───────────────────────────────────
                if isinstance(result, StopAgent):
                    stop_sentinel = result
                    result_content = result.reason if result.reason else "APPROVED"
                else:
                    result_content = result if isinstance(result, str) else json.dumps(result)
                # Log tool results for debugging
                if name in ("create_storage_bucket", "upload_to_storage", "migration"):
                    print(f"[tool_result] {name}: {result_content[:300]}")
                
            
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
                        error_msg = f"IMAGE_GENERATION_FAILED: {type(e).__name__}: {str(e)[:120]} — use CSS gradient placeholder instead. Do NOT import this path."
                        print(f"[image_error] {name}: {e}")
                        result = error_msg
 
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