import json
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

        # ── Callback hooks (set by caller for progress tracking) ─────────
        # on_thinking(turn: int, detail: str)
        self.on_thinking: Optional[Callable] = None
        # on_tool_start(name: str, input: dict)
        self.on_tool_start: Optional[Callable] = None
        # on_tool_end(name: str, input: dict, result: str)
        self.on_tool_end: Optional[Callable] = None
        # on_text(text: str) — called when agent emits text alongside tools
        self.on_text: Optional[Callable] = None
        # on_rate_limit(attempt: int, delay: int)
        self.on_rate_limit: Optional[Callable] = None

    # ------------------------------------------------------------------ #
    #  Caching helpers                                                     #
    # ------------------------------------------------------------------ #

    def _build_system(self) -> List[dict]:
        """Wrap system prompt with cache_control so it's cached after first call."""
        return [
            {
                "type": "text",
                "text": self.system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ]

    def _build_tools(self) -> List[dict]:
        """Mark the last tool with cache_control — tools list is stable across turns."""
        if not self.tools:
            return []
        tools = [t.copy() for t in self.tools]
        tools[-1] = {**tools[-1], "cache_control": {"type": "ephemeral"}}
        return tools

    def _apply_history_cache(self):
        if len(self.messages) < 2:
            return

        # Strip ALL existing cache_control from history
        for msg in self.messages[:-1]:
            content = msg.get("content", [])
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and "cache_control" in block:
                        del block["cache_control"]

        # Mark only messages[-2] as the single cache checkpoint
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
    #  Main chat loop (with hooks)                                         #
    # ------------------------------------------------------------------ #

    # Tools that indicate code/assets were actually modified
    CODE_WRITING_TOOLS = {"write_file", "edit_file", "generate_image", "edit_image", "delete_file", "rename_file"}

    def chat(self, user_msg: str):
        """
        Run the agentic loop.
        Returns (text, token_totals, code_changed) where:
          - text: the final assistant text response
          - token_totals: dict with input/output/cache_write/cache_read
          - code_changed: True if write_file, edit_file, or generate_image was called
        """
        if self.pending_notices:
            user_msg = f"{self.pending_notices}\n\n{user_msg}"
            self.pending_notices.clear()

        self.messages.append({"role": "user", "content": user_msg})

        totals = {"input": 0, "output": 0, "cache_write": 0, "cache_read": 0}
        code_changed = False
        turn_count = 0

        while True:
            turn_count += 1
            self._apply_history_cache()

            # ── Hook: thinking ────────────────────────────────────────
            if self.on_thinking:
                self.on_thinking(turn_count, f"Generating code (step {turn_count})...")

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
                except anthropic.RateLimitError:
                    if attempt == max_retries - 1:
                        raise
                    delay = base_delay * (2 ** attempt)
                    print(f"[rate limit] hit on attempt {attempt + 1}, retrying in {delay}s...")
                    # ── Hook: rate limit ──────────────────────────────
                    if self.on_rate_limit:
                        self.on_rate_limit(attempt, delay)
                    time.sleep(delay)

            usage = resp.usage
            turn_input = usage.input_tokens
            turn_output = usage.output_tokens
            turn_cache_write = getattr(usage, "cache_creation_input_tokens", 0)
            turn_cache_read = getattr(usage, "cache_read_input_tokens", 0)

            totals["input"] += turn_input
            totals["output"] += turn_output
            totals["cache_write"] += turn_cache_write
            totals["cache_read"] += turn_cache_read

            print(
                f"[tokens] input={turn_input} | output={turn_output} | "
                f"cache_write={turn_cache_write} | cache_read={turn_cache_read} | "
                f"running_totals={totals}"
            )

            self.messages.append({
                "role": "assistant",
                "content": [_serialize_content_block(block) for block in resp.content]
            })

            tool_uses = [b for b in resp.content if _get(b, "type") == "tool_use"]

            if not tool_uses:
                # Pure text reply — no tools called
                text = "".join(
                    _get(b, "text", "")
                    for b in resp.content
                    if _get(b, "type") == "text"
                )
                return text, totals, code_changed

            # Extract any thinking text from this turn
            thinking_text = "".join(
                _get(b, "text", "") for b in resp.content if _get(b, "type") == "text"
            ).strip()

            # ── Hook: text emitted alongside tools ────────────────────
            if thinking_text and self.on_text:
                self.on_text(thinking_text)

            tool_results = []
            for block in tool_uses:
                name = _get(block, "name")
                call_id = _get(block, "id")
                raw_in = _get(block, "input") or {}

                args = raw_in if isinstance(raw_in, dict) else json.loads(raw_in)

                # ── Hook: before tool execution ───────────────────────
                if self.on_tool_start:
                    self.on_tool_start(name, args)

                # Track code changes
                if name in self.CODE_WRITING_TOOLS:
                    code_changed = True
                    print(f"[code_changed] set True — tool '{name}' was called")

                if name not in self.tool_map:
                    raise RuntimeError(f"Model called unknown tool: {name}")

                result = self.tool_map[name](**args)

                # ── Hook: after tool execution ────────────────────────
                if self.on_tool_end:
                    self.on_tool_end(name, args, result)

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": call_id,
                    "content": result if isinstance(result, str) else json.dumps(result),
                })

            self.messages.append({"role": "user", "content": tool_results})
            self._apply_history_cache()