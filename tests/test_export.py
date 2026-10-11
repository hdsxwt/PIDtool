from __future__ import annotations

import json

from pidtool.core.export import export_artifacts
from pidtool.workspace import Workspace


def test_export_artifacts(tmp_path):
    workspace = Workspace(tmp_path)
    workspace.ensure()
    config_path, header_path = export_artifacts(
        workspace,
        {"kp": 1.5, "ki": 2.5, "kd": 0.25},
        metrics={"overshoot_pct": 5.0, "steady_error": 0.001},
        source="test_trial",
        setpoint=2.0,
        notes="hello",
    )
    assert config_path == workspace.exports_dir / "config.json"
    data = json.loads(config_path.read_text(encoding="utf-8"))
    assert data["gains"] == {"kp": 1.5, "ki": 2.5, "kd": 0.25}
    assert data["source"] == "test_trial"
    assert data["setpoint"] == 2.0
    assert data["metrics"]["overshoot_pct"] == 5.0
    assert data["notes"] == "hello"
    assert data["exported_at"]

    header = header_path.read_text(encoding="utf-8")
    assert "#define PID_KP 1.500000f" in header
    assert "#define PID_KI 2.500000f" in header
    assert "#define PID_KD 0.250000f" in header

    timestamped = list(workspace.exports_dir.glob("config_*.json"))
    assert len(timestamped) == 1
    stamp_data = json.loads(timestamped[0].read_text(encoding="utf-8"))
    assert stamp_data["gains"] == data["gains"]


def test_export_overwrites_latest_and_keeps_history(tmp_path):
    workspace = Workspace(tmp_path)
    workspace.ensure()
    export_artifacts(workspace, {"kp": 1.0, "ki": 1.0, "kd": 0.0})
    export_artifacts(workspace, {"kp": 2.0, "ki": 2.0, "kd": 0.1})
    latest = json.loads((workspace.exports_dir / "config.json").read_text(encoding="utf-8"))
    assert latest["gains"]["kp"] == 2.0
    assert latest["metrics"] == {}
