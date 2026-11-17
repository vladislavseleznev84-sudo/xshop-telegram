"""Claude integration. Only ever called for chats with granted consent."""
import json
import logging
from dataclasses import dataclass
from datetime import datetime

import anthropic
from pydantic import BaseModel, ValidationError

from app.services.memory import MemoryUpdate, ProfileData
from app.services.prompts import RESPOND_TOOL, SYSTEM_PROMPT, USER_TEMPLATE

log = logging.getLogger("ghost.ai")


class Decision(BaseModel):
    should_reply: bool = False
    reply: str = ""
    tone: str = "neutral"
    relationship: str = "unknown"
    needs_owner: bool = False
    owner_note: str = ""
    memory: MemoryUpdate = MemoryUpdate()


@dataclass
class AIInput:
    owner_name: str
    style: str
    profile: ProfileData
    history: list[tuple[str, str, datetime]]  # (role in|out, text, ts)
    now: datetime
    avoid: str = ""  # a previous reply we must not repeat


@dataclass
class AIResult:
    decision: Decision
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""


def render_conversation(history: list[tuple[str, str, datetime]], owner: str) -> str:
    lines = []
    for role, text, ts in history:
        who = f"{owner} (ассистент)" if role == "out" else "Собеседник"
        lines.append(f"[{ts:%Y-%m-%d %H:%M}] {who}: {text}")
    return "\n".join(lines)


class ClaudeClient:
    def __init__(self, api_key: str, model: str, max_tokens: int = 1024):
        self.model = model
        self.max_tokens = max_tokens
        self._client = anthropic.AsyncAnthropic(api_key=api_key or "missing", max_retries=2, timeout=45.0)

    @property
    def configured(self) -> bool:
        return self._client.api_key not in (None, "", "missing")

    async def decide(self, inp: AIInput) -> AIResult:
        system = SYSTEM_PROMPT.format(owner=inp.owner_name or "владелец", style=inp.style)
        user = USER_TEMPLATE.format(
            profile=json.dumps(inp.profile.model_dump(), ensure_ascii=False, indent=1),
            conversation=render_conversation(inp.history, inp.owner_name or "владелец"),
            now=f"{inp.now:%Y-%m-%d %H:%M} UTC",
        )
        if inp.avoid:
            user += f"\n\nВажно: не повторяй дословно или близко по смыслу этот свой прошлый ответ: «{inp.avoid}». Скажи иначе или не отвечай."
        resp = await self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            tools=[RESPOND_TOOL],
            tool_choice={"type": "tool", "name": "respond"},
        )
        usage = getattr(resp, "usage", None)
        result = AIResult(
            decision=Decision(should_reply=False, needs_owner=True, owner_note="Не удалось разобрать ответ модели."),
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            model=self.model,
        )
        for block in resp.content:
            if getattr(block, "type", "") == "tool_use" and block.name == "respond":
                try:
                    result.decision = Decision.model_validate(block.input)
                except ValidationError:
                    log.warning("model returned invalid tool input")
                break
        return result
