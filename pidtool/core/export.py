from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from pidtool.workspace import Workspace


def export_artifacts(
    workspace: Workspace,
    gains: dict,
    metrics: dict | None = None,
    source: str = "manual",
    setpoint: float | None = None,
    notes: str = "",
) -> tuple[Path, Path]:
    exports = workspace.exports_dir
    exports.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": source,
        "setpoint": setpoint,
        "gains": {
            "kp": float(gains["kp"]),
            "ki": float(gains["ki"]),
            "kd": float(gains["kd"]),
        },
        "metrics": metrics or {},
        "notes": notes,
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    config_path = exports / "config.json"
    config_path.write_text(text, encoding="utf-8")
    stamp = time.strftime("%Y%m%d_%H%M%S")
    (exports / f"config_{stamp}.json").write_text(text, encoding="utf-8")
    header_path = exports / "pid_config.h"
    header_path.write_text(_c_header(payload), encoding="utf-8")
    return config_path, header_path


def _c_header(payload: dict[str, Any]) -> str:
    gains = payload["gains"]
    lines = [
        f"/* PIDtool export {payload['exported_at']} source={payload['source']} */",
        f"#define PID_KP {gains['kp']:.6f}f",
        f"#define PID_KI {gains['ki']:.6f}f",
        f"#define PID_KD {gains['kd']:.6f}f",
        "",
    ]
    return "\n".join(lines)
