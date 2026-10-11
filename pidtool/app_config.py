from __future__ import annotations

import copy
import os
import tomllib
from pathlib import Path
from typing import Any

import tomli_w

APP_DIR = Path(os.environ.get("APPDATA") or Path.home()) / "PIDtool"
APP_CONFIG_PATH = APP_DIR / "app.toml"

DEFAULT_APP_CONFIG: dict[str, Any] = {
    "recent_workspaces": [],
    "last_workspace": "",
    "llm": {
        "provider": "deepseek",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
        "api_key": "",
    },
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

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("wb") as fh:
            tomli_w.dump(self.data, fh)

    def add_recent(self, path: str) -> None:
        recent = [p for p in self.data.get("recent_workspaces", []) if Path(p) != Path(path)]
        recent.insert(0, path)
        self.data["recent_workspaces"] = recent[:MAX_RECENT]
        self.data["last_workspace"] = path
        self.save()
