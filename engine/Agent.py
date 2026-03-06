import json
from typing import Any, Callable, Dict, List, Optional
from openai import OpenAI
from colorama import Fore,Back,Style
from dotenv import load_dotenv
def _get(item, key, default=None):
    
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)

class BaseAgent:
    def __init__(
        self,
        *,
        client: OpenAI,
        model: str,
        system_prompt: str,
        tools: Optional[List[dict]] = None,
        tool_map: Optional[Dict[str, Callable[..., Any]]] = None,
        reviewer=None,
        temperature=1
        
        
     
    ):

        self.pending_notices: List[str] = []
        self.client = client
        self.model = model
        self.tools = tools or []
        self.tool_map = tool_map or {}
        
        self.items: List[dict] = [{"role":"system","content":"You should obey the user"},
                                  {"role": "user", "content": system_prompt},
                                  ]
        self.reviewer = reviewer
        self.temperature = temperature
        
    def receive_sys_notice(self,content:dict):
        self.pending_notices.append(json.dumps(content,ensure_ascii=True))
    
    def notify_reviewer(self,message):
        if self.reviewer:
            self.reviewer.receive_sys_notice(message)
    def chat(self, user_msg: str) -> str:
       
        if self.pending_notices:
            user_msg = f"{self.pending_notices}\n\n{user_msg}"
        self.pending_notices.clear()
        self.items.append({"role": "user", "content": user_msg})
        while True:
            for i, it in enumerate(self.items):
                if not isinstance(it, dict) or "content" not in it:
                    continue
                c = it["content"]
                if not isinstance(c, (str, list)):
                    print("BAD ITEM INDEX:", i)
                    print("ROLE/TYPE:", it.get("role"), it.get("type"))
                    print("CONTENT TYPE:", type(c))
                    print("CONTENT:", c)
                    break

            resp = self.client.responses.create(
                model=self.model,
                input=self.items,
                tools=self.tools,
                tool_choice="auto",
                temperature=self.temperature
            )

            
            self.items += list(resp.output)

            # Find tool calls
            calls = [it for it in resp.output if _get(it, "type") == "function_call"]
            if not calls:
                
                return resp.output_text or ""

            
            for call in calls:
                name = _get(call, "name")
                call_id = _get(call, "call_id")
                raw_args = _get(call, "arguments") or "{}"

                if name not in self.tool_map:
                    raise RuntimeError(f"Model called unknown tool: {name}")

                args = json.loads(raw_args)
                result = self.tool_map[name](**args)

                
                self.items.append({
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": result if isinstance(result, str) else json.dumps(result),
                })
