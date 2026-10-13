from __future__ import annotations

import json
from dataclasses import dataclass

from pidtool.ai.prompts import SYSTEM_PROMPT, build_user_prompt


class LLMError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Suggestion:
    kp: float
    ki: float
    kd: float
    reasoning: str = ""


def parse_suggestion(text: str) -> Suggestion:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMError(f"AI 返回内容不是有效 JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise LLMError("AI 返回的 JSON 不是对象")
    try:
        kp = float(data["kp"])
        ki = float(data["ki"])
        kd = float(data["kd"])
    except (KeyError, TypeError, ValueError) as exc:
        raise LLMError(f"AI 返回的 JSON 缺少增益字段: {data!r}") from exc
    return Suggestion(kp, ki, kd, str(data.get("reasoning", "")))


class DeepSeekTuner:
    def __init__(self, api_key: str, base_url: str, model: str, timeout: float = 60.0) -> None:
        from openai import OpenAI

        self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
        self.model = model

    def suggest(self, history: list[dict], limits: dict) -> Suggestion:
        response = self._client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": build_user_prompt(history, limits)},
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
        )
        text = response.choices[0].message.content or ""
        return parse_suggestion(text)
