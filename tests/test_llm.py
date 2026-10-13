from __future__ import annotations

import sys
import types

import pytest

from pidtool.ai.llm import DeepSeekTuner, LLMError, Suggestion, parse_suggestion
from pidtool.ai.prompts import _fmt, build_user_prompt


def _install_fake_openai(monkeypatch, content: str | None):
    fake = types.ModuleType("openai")
    state: dict = {}

    class _Completions:
        def create(self, **kwargs):
            state.setdefault("create", []).append(kwargs)
            message = types.SimpleNamespace(content=content)
            return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])

    class _Client:
        def __init__(self, api_key=None, base_url=None, timeout=None):
            state["init"] = {"api_key": api_key, "base_url": base_url, "timeout": timeout}
            self.chat = types.SimpleNamespace(completions=_Completions())

    fake.OpenAI = _Client
    monkeypatch.setitem(sys.modules, "openai", fake)
    return state


def test_parse_suggestion_ok():
    suggestion = parse_suggestion('{"kp": 1.5, "ki": 2.5, "kd": 0.25, "reasoning": "reason"}')
    assert isinstance(suggestion, Suggestion)
    assert (suggestion.kp, suggestion.ki, suggestion.kd) == (1.5, 2.5, 0.25)
    assert suggestion.reasoning == "reason"


def test_parse_suggestion_missing_reasoning_defaults_empty():
    suggestion = parse_suggestion('{"kp": 1, "ki": 2, "kd": 3}')
    assert suggestion.reasoning == ""


def test_parse_suggestion_invalid():
    with pytest.raises(LLMError):
        parse_suggestion("not json")
    with pytest.raises(LLMError):
        parse_suggestion('{"kp": 1}')
    with pytest.raises(LLMError):
        parse_suggestion("[1, 2, 3]")
    with pytest.raises(LLMError):
        parse_suggestion('{"kp": "abc", "ki": 1, "kd": 1}')


def _limits():
    return {
        "kp": {"min": 0.0, "max": 500.0},
        "ki": {"min": 0.0, "max": 5000.0},
        "kd": {"min": 0.0, "max": 100.0},
    }


def test_build_user_prompt_renders_rounds_with_actions():
    history = [
        {
            "iteration": 1,
            "cycle": 1,
            "round": 1,
            "round_global": 1,
            "kp": 4.0,
            "ki": 60.0,
            "kd": 0.05,
            "score": 12.3,
            "best": True,
            "actions": [
                {
                    "kind": "step",
                    "setpoint": 2.0,
                    "overshoot_pct": 5.4,
                    "rise_time": 0.049,
                    "settling_time": 0.75,
                    "steady_error": 0.0003,
                    "oscillating": False,
                    "score": 12.3,
                },
                {
                    "kind": "disturbance",
                    "setpoint": 2.0,
                    "max_deviation": 0.32,
                    "recovery_time": 1.35,
                    "residual_error": 0.0002,
                    "oscillating": False,
                    "score": 23.1,
                },
            ],
        }
    ]
    text = build_user_prompt(history, _limits())
    assert "第1轮（循环1 第1轮）" in text
    assert "Kp=4" in text
    assert "轮评分=12.30（当前最优）" in text
    assert "动作1（阶跃 目标=2）" in text
    assert "超调=5.40%" in text
    assert "动作2（扰动 目标=2）" in text
    assert "最大偏差=0.32" in text
    assert "恢复=1.3500s" in text
    assert "降采样波形" not in text
    assert "JSON" in text


def test_build_user_prompt_includes_per_action_waveform():
    action = {
        "kind": "step",
        "setpoint": 2.0,
        "overshoot_pct": 5.0,
        "rise_time": 0.05,
        "settling_time": 0.5,
        "steady_error": 0.001,
        "oscillating": False,
        "score": 12.0,
        "waveform": {
            "t": [0.0, 0.01],
            "setpoint": [2.0, 2.0],
            "feedback": [1.0, 1.2],
            "u": [3.0, 4.0],
            "up": [1.0, 2.0],
            "ui": [1.0, 1.5],
            "ud": [0.1, 0.1],
        },
    }
    history = [
        {
            "iteration": 1,
            "cycle": 1,
            "round_global": 3,
            "kp": 4.0,
            "ki": 60.0,
            "kd": 0.05,
            "score": 12.0,
            "actions": [action],
        }
    ]
    text = build_user_prompt(history, _limits())
    assert "降采样波形" in text
    assert "0,2,1,3,1,1,0.1" in text
    assert "0.01,2,1.2,4,2,1.5,0.1" in text


def test_fmt_handles_invalid_values():
    assert _fmt(None, ".2f") == "—"
    assert _fmt("abc", ".2f") == "—"
    assert _fmt(float("nan"), ".2f") == "—"
    assert _fmt(float("inf"), ".2f") == "—"
    assert _fmt(float("-inf"), ".2f") == "—"
    assert _fmt(1.234, ".2f") == "1.23"


def test_build_user_prompt_tolerates_missing_and_invalid_fields():
    history = [
        {
            "iteration": 1,
            "cycle": 1,
            "round_global": 1,
            "kp": "bad",
            "ki": None,
            "kd": float("nan"),
            "score": float("nan"),
            "actions": [
                {
                    "kind": "step",
                    "setpoint": None,
                    "overshoot_pct": None,
                    "rise_time": "x",
                    "settling_time": float("inf"),
                    "steady_error": 0.001,
                    "oscillating": True,
                    "score": float("nan"),
                    "waveform": {"t": [0.0, 0.01]},
                },
                {
                    "kind": "disturbance",
                    "setpoint": 1.0,
                    "max_deviation": None,
                    "recovery_time": None,
                    "residual_error": None,
                    "oscillating": False,
                    "score": 5.0,
                },
            ],
        },
        {"iteration": 2, "kp": 1.0, "ki": 1.0, "kd": 0.0},
    ]
    text = build_user_prompt(history, _limits())
    assert "Kp=—" in text
    assert "动作1（阶跃 目标=—）" in text
    assert "振荡=是" in text
    assert "评分=—" in text
    assert "动作2（扰动 目标=1）" in text
    assert "最大偏差=—" in text
    assert "降采样波形" in text


def test_build_user_prompt_empty_limits():
    text = build_user_prompt([], {})
    assert "Kp∈[None, None]" in text
    assert "历史轮次" in text


def test_deepseek_tuner_suggest(monkeypatch):
    state = _install_fake_openai(
        monkeypatch, '{"kp": 9.0, "ki": 8.0, "kd": 0.7, "reasoning": "because"}'
    )
    tuner = DeepSeekTuner("key-1", "https://example.test", "model-x", timeout=12.0)
    assert state["init"] == {
        "api_key": "key-1",
        "base_url": "https://example.test",
        "timeout": 12.0,
    }
    assert tuner.model == "model-x"
    suggestion = tuner.suggest(
        [{"iteration": 1, "kp": 1.0, "ki": 2.0, "kd": 0.1, "score": 3.0}],
        {"kp": {"min": 0.0, "max": 100.0}},
    )
    assert suggestion == Suggestion(9.0, 8.0, 0.7, "because")
    call = state["create"][0]
    assert call["model"] == "model-x"
    assert call["response_format"] == {"type": "json_object"}
    assert call["temperature"] == 0.2
    assert "历史轮次" in call["messages"][1]["content"]
    assert call["messages"][0]["role"] == "system"


def test_deepseek_tuner_invalid_response(monkeypatch):
    _install_fake_openai(monkeypatch, None)
    tuner = DeepSeekTuner("key", "url", "model")
    with pytest.raises(LLMError):
        tuner.suggest([], {})
