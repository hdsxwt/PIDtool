from __future__ import annotations

import copy
import os
import tomllib
from pathlib import Path
from typing import Any

import tomli_w

from pidtool.secure_store import DPAPI_PREFIX, protect, unprotect

APP_DIR = Path(os.environ.get("APPDATA") or Path.home()) / "PIDtool"
APP_CONFIG_PATH = APP_DIR / "app.toml"

DEFAULT_LLM_MODEL = "deepseek-flash"
LEGACY_LLM_MODELS = ("", "deepseek-chat")

DEFAULT_AUTOTUNE: dict[str, Any] = {
    "schema": 2,
    "cycles": 2,
    "rounds": [
        {
            "name": "轮次 1",
            "actions": [
                {
                    "kind": "step",
                    "baseline_sp": 0.0,
                    "target_sp": 300.0,
                    "settle_s": 2.0,
                    "countdown_s": 3.0,
                    "measure_s": 3.0,
                },
                {
                    "kind": "step",
                    "baseline_sp": 300.0,
                    "target_sp": 600.0,
                    "settle_s": 2.0,
                    "countdown_s": 3.0,
                    "measure_s": 3.0,
                },
            ],
        }
    ],
}

DEFAULT_APP_CONFIG: dict[str, Any] = {
    "recent_workspaces": [],
    "last_workspace": "",
    "llm": {
        "provider": "deepseek",
        "base_url": "https://api.deepseek.com",
        "model": DEFAULT_LLM_MODEL,
        "api_key": "",
    },
    "codeagent": {
        "executable": "opencode",
        "agent_name": "",
        "timeout_s": 900,
        "auto_approve": False,
        "approved_adapters": {},
    },
    "autotune": DEFAULT_AUTOTUNE,
    "ui": {"window_s": 10.0},
}

MAX_RECENT = 10


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def _migrate_autotune(autotune: dict[str, Any]) -> bool:
    if not isinstance(autotune, dict):
        return False
    if "plan" not in autotune and "loops" not in autotune:
        return False
    plan = [str(kind) for kind in (autotune.get("plan") or ["step"])]
    try:
        loops = max(1, int(autotune.get("loops", 1)))
    except (TypeError, ValueError):
        loops = 1
    try:
        disturb_s = float(autotune.get("disturb_s", 5.0))
    except (TypeError, ValueError):
        disturb_s = 5.0
    try:
        recover_s = float(autotune.get("recover_s", 4.0))
    except (TypeError, ValueError):
        recover_s = 4.0
    rounds: list[dict[str, Any]] = []
    for index, kind in enumerate(plan):
        if kind == "disturbance":
            action = {
                "kind": "disturbance",
                "target_sp": 2.0,
                "settle_s": 2.0,
                "countdown_s": 3.0,
                "disturb_s": disturb_s,
                "recover_s": recover_s,
            }
        else:
            action = {
                "kind": "step",
                "baseline_sp": 0.0,
                "target_sp": 2.0,
                "settle_s": 2.0,
                "countdown_s": 3.0,
                "measure_s": 4.0,
            }
        rounds.append({"name": f"轮次 {index + 1}", "actions": [action]})
    autotune.clear()
    autotune.update({"schema": 2, "cycles": loops, "rounds": rounds})
    return True


class AppConfig:
    def __init__(self, path: Path = APP_CONFIG_PATH) -> None:
        self.path = Path(path)
        self.data: dict[str, Any] = copy.deepcopy(DEFAULT_APP_CONFIG)
        self.load()

    def load(self) -> None:
        self.data = copy.deepcopy(DEFAULT_APP_CONFIG)
        if self.path.is_file():
            try:
                with self.path.open("rb") as fh:
                    raw = tomllib.load(fh)
                self.data = _merge(self.data, raw)
            except (tomllib.TOMLDecodeError, OSError):
                pass
        llm = self.data.setdefault("llm", {})
        if str(llm.get("model", "")).strip() in LEGACY_LLM_MODELS:
            llm["model"] = DEFAULT_LLM_MODEL
        raw_key = str(llm.get("api_key", ""))
        if raw_key and not raw_key.startswith(DPAPI_PREFIX):
            llm["api_key"] = protect(raw_key)
            self.save()
        autotune = self.data.setdefault("autotune", {})
        if _migrate_autotune(autotune):
            self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        llm = self.data.setdefault("llm", {})
        raw_key = str(llm.get("api_key", ""))
        if raw_key and not raw_key.startswith(DPAPI_PREFIX):
            llm["api_key"] = protect(raw_key)
        with self.path.open("wb") as fh:
            tomli_w.dump(self.data, fh)

    def get_api_key(self) -> str:
        return unprotect(str(self.data.get("llm", {}).get("api_key", "")))

    def set_api_key(self, value: str) -> None:
        llm = self.data.setdefault("llm", {})
        value = (value or "").strip()
        llm["api_key"] = protect(value) if value else ""

    def add_recent(self, path: str) -> None:
        recent = [p for p in self.data.get("recent_workspaces", []) if Path(p) != Path(path)]
        recent.insert(0, path)
        self.data["recent_workspaces"] = recent[:MAX_RECENT]
        self.data["last_workspace"] = path
        self.save()
