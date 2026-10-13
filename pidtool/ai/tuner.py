from __future__ import annotations

import threading
from typing import Callable

from pidtool.ai.llm import Suggestion
from pidtool.ai.plan import normalize_rounds
from pidtool.ai.scoring import aggregate_round_score, score_metrics
from pidtool.core.metrics import DisturbanceMetrics, StepMetrics

SuggestFn = Callable[[list[dict], dict], Suggestion]

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
        self._rounds: list[dict] = []
        self._cycles = 1
        self._total = 1
        self._round_index = 0
        self._action_index = 0
        self._round_buffer: list[dict] = []
        self._history: list[dict] = []
        self._best: dict | None = None
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
        rounds: list[dict],
        cycles: int,
        suggest: SuggestFn,
        limits: dict,
    ) -> None:
        if self._active:
            raise RuntimeError("自动调参已在运行")
        self._rounds = normalize_rounds(rounds)
        self._cycles = max(1, int(cycles))
        self._total = len(self._rounds) * self._cycles
        self._suggest = suggest
        self._limits = limits
        self._active = True
        self._history = []
        self._best = None
        self._round_index = 0
        self._action_index = 0
        self._round_buffer = []
        self._worse_streak = 0
        self._osc_streak = 0
        self._result = None
        self._current = {"kp": float(kp), "ki": float(ki), "kd": float(kd)}
        self._on_event(
            "status",
            {
                "text": (
                    f"自动调参开始：每循环 {len(self._rounds)} 轮 × {self._cycles} 循环"
                    f" = 共 {self._total} 轮（每轮一次 AI 评估）"
                )
            },
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
        action = self._current_action() or {}
        action_kind = str(action.get("kind", kind))
        kind = action_kind if action_kind in VALID_KINDS else "step"
        target = float(action.get("target_sp", setpoint))
        score = score_metrics(kind, metrics, target)
        entry: dict = {"kind": kind, "setpoint": target, "score": score}
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
        self._round_buffer.append(entry)
        if score == float("inf"):
            self._graceful_finish("实验数据无效，停止调参")
            return

        actions = self._current_actions()
        if self._action_index + 1 < len(actions):
            self._action_index += 1
            self._on_event("run_experiment", self._experiment_payload())
            return

        completed_meta = self._round_meta()
        self._round_index += 1
        scores = [float(item["score"]) for item in self._round_buffer]
        round_score = aggregate_round_score(scores)
        round_entry: dict = {
            "iteration": self._round_index,
            "cycle": completed_meta["cycle"],
            "round": completed_meta["round"],
            "round_global": completed_meta["round_global"],
            "round_total": self._total,
            "score": round_score,
            "kp": float(gains["kp"]),
            "ki": float(gains["ki"]),
            "kd": float(gains["kd"]),
            "actions": [dict(item) for item in self._round_buffer],
        }
        improved = round_score != float("inf") and (
            self._best is None or round_score < float(self._best["score"]) - 1e-9
        )
        if improved:
            self._best = {
                "score": round_score,
                "gains": dict(gains),
                "iteration": self._round_index,
            }
            round_entry["best"] = True
        self._history.append(round_entry)
        self._on_event("iteration", round_entry)

        oscillating = any(bool(item.get("oscillating")) for item in self._round_buffer)
        self._worse_streak = 0 if improved else self._worse_streak + 1
        self._osc_streak = self._osc_streak + 1 if oscillating else 0
        self._round_buffer = []
        self._action_index = 0

        if self._osc_streak >= 2:
            self._graceful_finish("系统连续振荡，提前停止")
            return
        if self._round_index >= self._total:
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

    def _round_meta(self) -> dict:
        rounds_count = max(1, len(self._rounds))
        return {
            "cycle": self._round_index // rounds_count + 1,
            "cycles": self._cycles,
            "round": self._round_index % rounds_count + 1,
            "round_global": self._round_index + 1,
            "round_total": self._total,
            "rounds_per_cycle": rounds_count,
        }

    def _current_actions(self) -> list[dict]:
        if not self._rounds:
            return []
        return self._rounds[self._round_index % len(self._rounds)]["actions"]

    def _current_action(self) -> dict | None:
        actions = self._current_actions()
        if 0 <= self._action_index < len(actions):
            return actions[self._action_index]
        return None

    def _experiment_payload(self) -> dict:
        payload = self._round_meta()
        actions = self._current_actions()
        action = self._current_action() or (actions[0] if actions else {})
        payload.update(
            {
                "action_index": self._action_index + 1,
                "action_total": len(actions),
                "action": dict(action),
            }
        )
        return payload

    def _request(self) -> None:
        if self._suggest is None:
            self._graceful_finish("AI 建议器未配置，停止调参")
            return

        def task() -> None:
            try:
                result: object = self._suggest(list(self._history), self._limits)
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
        restore = self._round_index >= self._total
        payload = dict(gains)
        payload["restore"] = bool(restore)
        if restore:
            rounds_count = max(1, len(self._rounds))
            payload.update(
                {
                    "cycle": self._cycles,
                    "cycles": self._cycles,
                    "round": rounds_count,
                    "round_global": self._total,
                    "round_total": self._total,
                    "rounds_per_cycle": rounds_count,
                    "action_kinds": [],
                    "action_total": 0,
                }
            )
        else:
            payload.update(self._round_meta())
            actions = self._current_actions()
            payload["action_kinds"] = [str(action.get("kind", "step")) for action in actions]
            payload["action_total"] = len(actions)
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
        rounds_done = self._round_index
        self._active = False
        self._worker = None
        self._round_buffer = []
        self._action_index = 0
        self._on_event("pause_motor", {})
        self._on_event(
            "finished",
            {
                "reason": reason,
                "rounds": rounds_done,
                "best_score": None if best is None else float(best["score"]),
                "best_gains": None if best is None else dict(best["gains"]),
            },
        )
