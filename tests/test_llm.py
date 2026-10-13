from __future__ import annotations

import pytest

from pidtool.ai.llm import LLMError, Suggestion, parse_suggestion
from pidtool.ai.prompts import build_user_prompt


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


def test_build_user_prompt_contains_history():
    history = [
        {
            "iteration": 1,
            "kp": 4.0,
            "ki": 60.0,
            "kd": 0.05,
            "overshoot_pct": 5.4,
            "rise_time": 0.049,
            "settling_time": 0.75,
            "steady_error": 0.0003,
            "oscillating": False,
            "score": 12.3,
            "best": True,
        }
    ]
    limits = {
        "kp": {"min": 0.0, "max": 500.0},
        "ki": {"min": 0.0, "max": 5000.0},
        "kd": {"min": 0.0, "max": 100.0},
    }
    text = build_user_prompt(history, limits, 2.0)
    assert "第1轮" in text
    assert "Kp=4" in text
    assert "当前最优" in text
    assert "目标设定值: 2" in text
    assert "JSON" in text
    assert "降采样波形" not in text


def test_build_user_prompt_renders_disturbance_round():
    history = [
        {
            "iteration": 2,
            "kind": "disturbance",
            "kp": 5.0,
            "ki": 40.0,
            "kd": 0.1,
            "max_deviation": 0.32,
            "recovery_time": 1.35,
            "residual_error": 0.0002,
            "oscillating": False,
            "score": 23.1,
        }
    ]
    limits = {
        "kp": {"min": 0.0, "max": 500.0},
        "ki": {"min": 0.0, "max": 5000.0},
        "kd": {"min": 0.0, "max": 100.0},
    }
    text = build_user_prompt(history, limits, 2.0)
    assert "第2轮（扰动）" in text
    assert "最大偏差=0.32" in text
    assert "恢复=1.3500s" in text
    assert "残余偏差=+0.00020" in text


def test_build_user_prompt_includes_waveform():
    history = [
        {
            "iteration": 1,
            "kp": 4.0,
            "ki": 60.0,
            "kd": 0.05,
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
    ]
    limits = {
        "kp": {"min": 0.0, "max": 500.0},
        "ki": {"min": 0.0, "max": 5000.0},
        "kd": {"min": 0.0, "max": 100.0},
    }
    text = build_user_prompt(history, limits, 2.0)
    assert "降采样波形" in text
    assert "0,2,1,3,1,1,0.1" in text
    assert "0.01,2,1.2,4,2,1.5,0.1" in text
