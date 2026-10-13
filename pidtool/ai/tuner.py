from __future__ import annotations

import threading
from typing import Callable

from pidtool.ai.llm import Suggestion
from pidtool.ai.scoring import score_metrics
from pidtool.core.metrics import DisturbanceMetrics, StepMetrics

SuggestFn = Callable[[list[dict], dict, float], Suggestion]

VALID_KINDS = ("step", "disturbance")


class AutoTuner:
    def __init__(
        self,
        on_event: Callable[[str, dict], None],
        async_mode: bool = True,
    ) -> None:
        self._on_event = on_event
        self._async = async_mode
        self._active = False
        self._worker: threading.Thread | None = None
        self._result: object | None = None
        self._suggest: SuggestFn | None = None
        self._limits: dict = {}
        self._setpoint = 1.0
        self._sequence: list[str] = ["step"]
        self._total = 1
        self._disturb_s = 5.0
        self._recover_s = 4.0
        self._history: list[dict] = []
        self._best: dict | None = None
        self._iteration = 0
        self._worse_streak = 0
        self._osc_streak = 0
        self._current = {"kp": 0.0, "ki": 0.0, "kd": 0.0}

    @property
    def active(self) -> bool:
        return self._active

    @property
    def best_score(self) -> float | None:
        return None if self._best is None else float(self._best["score"])

    def start(
        self,
        kp: float,
        ki: float,
        kd: float,
        plan: list[str],
        loops: int,
        suggest: SuggestFn,
        limits: dict,
        setpoint: float,
        disturb_s: float = 5.0,
        recover_s: float = 4.0,
    ) -> None:
        if self._active:
            raise RuntimeError("自动调参已在运行")
        kinds = [str(kind).lower() for kind in plan]
        kinds = [kind for kind in kinds if kind in VALID_KINDS]
        if not kinds:
            kinds = ["step"]
        loops = max(1, int(loops))
        self._active = True
        self._suggest = suggest
        self._limits = limits
        self._setpoint = float(setpoint)
        self._sequence = kinds * loops
        self._total = len(self._sequence)
        self._disturb_s = float(disturb_s)
        self._recover_s = float(recover_s)
        self._history = []
        self._best = None
        self._iteration = 0
        self._worse_streak = 0
        self._osc_streak = 0
        self._result = None
        self._current = {"kp": float(kp), "ki": float(ki), "kd": float(kd)}
        self._on_event(
            "status",
            {"text": f"自动调参开始：目标设定值 {setpoint:g}，共 {self._total} 轮"},
        )
        self._emit_apply(self._current)
        self._on_event("run_experiment", self._experiment_payload())

    def abort(self) -> None:
        if self._active:
            self._graceful_finish("用户中止")

    def on_trial(
        self,
        metrics: StepMetrics | DisturbanceMetrics | None,
        gains: dict,
        setpoint: float,
        kind: str = "step",
        waveform: dict | None = None,
    ) -> None:
        if not self._active:
            return
        kind = kind if kind in VALID_KINDS else "step"
        score = score_metrics(kind, metrics, setpoint)
        self._iteration += 1
        entry: dict = {
            "iteration": self._iteration,
            "kind": kind,
            "setpoint": float(setpoint),
            "kp": float(gains["kp"]),
            "ki": float(gains["ki"]),
            "kd": float(gains["kd"]),
            "score": score,
        }
        if metrics is not None:
            if kind == "disturbance":
                entry.update(
                    {
                        "max_deviation": metrics.max_deviation,
                        "max_deviation_time": metrics.max_deviation_time,
                        "recovery_time": metrics.recovery_time,
                        "residual_error": metrics.residual_error,
                        "tail_ripple": metrics.tail_ripple,
                        "oscillating": metrics.oscillating,
                    }
                )
            else:
                entry.update(
                    {
                        "overshoot_pct": metrics.overshoot_pct,
                        "rise_time": metrics.rise_time,
                        "settling_time": metrics.settling_time,
                        "steady_error": metrics.steady_error,
                        "tail_ripple": metrics.tail_ripple,
                        "oscillating": metrics.oscillating,
                    }
                )
        if waveform:
            entry["waveform"] = waveform
        improved = self._best is None or score < float(self._best["score"]) - 1e-9
        if improved:
            self._best = {"score": score, "gains": dict(gains), "iteration": self._iteration}
            entry["best"] = True
        self._history.append(entry)
        self._on_event("iteration", dict(entry))

        self._worse_streak = 0 if improved else self._worse_streak + 1
        if metrics is not None and getattr(metrics, "oscillating", False):
            self._osc_streak += 1
        else:
            self._osc_streak = 0

        if metrics is None or score == float("inf"):
            self._graceful_finish("实验数据无效，停止调参")
            return
        if self._osc_streak >= 2:
            self._graceful_finish("系统连续振荡，提前停止")
            return
        if self._iteration >= self._total:
            self._graceful_finish("完成全部轮次")
            return
        if self._worse_streak >= 3:
            self._graceful_finish("连续 3 轮退化，提前停止")
            return
        self._on_event("pause_motor", {})
        self._on_event("status", {"text": "正在请求 AI 建议…（电机已停止）"})
        self._request()

    def tick(self) -> None:
        if not self._active:
            return
        if self._worker is not None or self._result is None:
            return
        result = self._result
        self._result = None
        if isinstance(result, Exception):
            self._graceful_finish(f"AI 调用失败: {result}")
            return
        suggestion: Suggestion = result
        gains = {
            "kp": self._clamp("kp", suggestion.kp),
            "ki": self._clamp("ki", suggestion.ki),
            "kd": self._clamp("kd", suggestion.kd),
        }
        if suggestion.reasoning:
            self._on_event("status", {"text": f"AI 建议: {suggestion.reasoning}"})
        self._current = gains
        self._emit_apply(gains)
        self._on_event("run_experiment", self._experiment_payload())

    def _experiment_payload(self) -> dict:
        index = min(self._iteration, self._total - 1)
        kind = self._sequence[index] if 0 <= index < len(self._sequence) else "step"
        return {
            "kind": kind,
            "disturb_s": self._disturb_s,
            "recover_s": self._recover_s,
        }

    def _request(self) -> None:
        if self._suggest is None:
            self._graceful_finish("AI 建议器未配置，停止调参")
            return

        def task() -> None:
            try:
                result: object = self._suggest(list(self._history), self._limits, self._setpoint)
            except Exception as exc:
                result = exc
            self._result = result
            self._worker = None

        if self._async:
            self._worker = threading.Thread(target=task, name="autotuner-llm", daemon=True)
            self._worker.start()
        else:
            task()

    def _clamp(self, key: str, value: float) -> float:
        limits = self._limits.get(key, {})
        low = float(limits.get("min", 0.0))
        high = float(limits.get("max", value))
        if value != value:
            return low
        return max(low, min(high, value))

    def _emit_apply(self, gains: dict) -> None:
        index = min(self._iteration, self._total - 1) if self._sequence else 0
        kind = self._sequence[index] if self._sequence else "step"
        payload = dict(gains)
        payload["round"] = self._iteration + 1
        payload["total"] = self._total
        payload["kind"] = kind
        self._on_event("apply_gains", payload)

    def _graceful_finish(self, reason: str) -> None:
        best = self._best
        if best is not None:
            best_gains = best["gains"]
            if (
                float(best_gains["kp"]),
                float(best_gains["ki"]),
                float(best_gains["kd"]),
            ) != (self._current["kp"], self._current["ki"], self._current["kd"]):
                self._on_event("status", {"text": "恢复最优参数"})
                self._current = dict(best_gains)
                self._emit_apply(best_gains)
        self._active = False
        self._worker = None
        self._on_event("pause_motor", {})
        self._on_event(
            "finished",
            {
                "reason": reason,
                "iterations": self._iteration,
                "best_score": None if best is None else float(best["score"]),
                "best_gains": None if best is None else dict(best["gains"]),
            },
        )
