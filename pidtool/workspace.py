from __future__ import annotations

import json
import time
import tomllib
from pathlib import Path
from typing import Any

import tomli_w

WORKSPACE_DIRNAME = ".pidtool"
GITIGNORE_ENTRY = ".pidtool/"

DEFAULT_SETTINGS: dict[str, Any] = {
    "serial": {"port": "", "baud": 115200, "adapter": "default"},
    "link": {"type": "mock"},
    "gains": {"kp": 4.0, "ki": 60.0, "kd": 0.05},
    "setpoint": {"value": 1.0},
    "telemetry": {"period_ms": 5},
    "safety": {
        "kp": {"min": 0.0, "max": 500.0},
        "ki": {"min": 0.0, "max": 5000.0},
        "kd": {"min": 0.0, "max": 100.0},
    },
}


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


class Workspace:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        if not self.root.is_dir():
            raise NotADirectoryError(str(self.root))

    @property
    def base_dir(self) -> Path:
        return self.root / WORKSPACE_DIRNAME

    @property
    def settings_path(self) -> Path:
        return self.base_dir / "settings.toml"

    @property
    def trials_path(self) -> Path:
        return self.base_dir / "trials.jsonl"

    @property
    def exports_dir(self) -> Path:
        return self.base_dir / "exports"

    @property
    def logs_dir(self) -> Path:
        return self.base_dir / "logs"

    @classmethod
    def is_workspace(cls, root: Path) -> bool:
        return (Path(root) / WORKSPACE_DIRNAME).is_dir()

    def ensure(self) -> bool:
        created = not self.base_dir.exists()
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.exports_dir.mkdir(exist_ok=True)
        self.logs_dir.mkdir(exist_ok=True)
        if not self.settings_path.exists():
            self.save_settings(DEFAULT_SETTINGS)
        return created

    def load_settings(self) -> dict[str, Any]:
        if not self.settings_path.is_file():
            return deep_merge(DEFAULT_SETTINGS, {})
        try:
            with self.settings_path.open("rb") as fh:
                raw = tomllib.load(fh)
        except (tomllib.TOMLDecodeError, OSError):
            return deep_merge(DEFAULT_SETTINGS, {})
        return deep_merge(DEFAULT_SETTINGS, raw)

    def save_settings(self, settings: dict[str, Any]) -> None:
        self.base_dir.mkdir(parents=True, exist_ok=True)
        with self.settings_path.open("wb") as fh:
            tomli_w.dump(settings, fh)

    def append_trial(self, trial: dict[str, Any]) -> None:
        record = {"ts": time.time(), **trial}
        self.base_dir.mkdir(parents=True, exist_ok=True)
        with self.trials_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    def read_trials(self) -> list[dict[str, Any]]:
        if not self.trials_path.is_file():
            return []
        out: list[dict[str, Any]] = []
        with self.trials_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return out


def ensure_gitignore(root: Path) -> bool:
    path = Path(root) / ".gitignore"
    if path.is_file():
        text = path.read_text(encoding="utf-8", errors="replace")
        if any(line.strip().rstrip("/") == WORKSPACE_DIRNAME for line in text.splitlines()):
            return True
        separator = "" if text.endswith("\n") else "\n"
        path.write_text(text + separator + GITIGNORE_ENTRY + "\n", encoding="utf-8")
        return True
    path.write_text(GITIGNORE_ENTRY + "\n", encoding="utf-8")
    return True
