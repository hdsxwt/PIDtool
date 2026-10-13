# Data and Configuration Formats

This document describes everything PIDtool writes to disk, for scripting and
version control.

## 1. Directory Overview

```
%APPDATA%/PIDtool/app.toml      # global config (cross-workspace; holds API key)
<firmware-dir>/.pidtool/
├── settings.toml               # workspace config (serial/gains/safety/...)
├── trials.jsonl                # experiment and code-edit log (one JSON per line)
├── exports/
│   ├── config.json             # latest exported gains (human-readable)
│   ├── config_YYYYmmdd_HHMMSS.json  # timestamped copy per export
│   └── pid_config.h            # C macro snippet, ready to paste into firmware
└── logs/                       # reserved (raw waveform recording)
```

Convention: everything under `.pidtool/` moves with the firmware directory;
secrets such as the API key live only in the global `app.toml` and never enter
the firmware repository.

## 2. Global Config `app.toml`

| Field | Type | Description |
| --- | --- | --- |
| `recent_workspaces` | string[] | Recently opened workspaces (max 10) |
| `last_workspace` | string | Last used workspace |
| `llm.base_url` | string | OpenAI-compatible endpoint (default `https://api.deepseek.com`) |
| `llm.model` | string | Model name (default `deepseek-flash`, i.e. DeepSeek-V4.1-Flash) |
| `llm.api_key` | string | API key (stored encrypted via Windows DPAPI, decryptable only on this machine; legacy plaintext auto-migrates) |
| `codeagent.executable` | string | opencode executable (default `opencode`) |
| `codeagent.agent_name` | string | Optional restricted agent name |
| `codeagent.timeout_s` | int | Code-edit task timeout, seconds (default 900) |
| `codeagent.auto_approve` | bool | Pass `--auto` (default true; required headless) |
| `codeagent.approved_adapters` | table | Trusted custom adapters: `absolute path → file sha256` (re-confirmed on change) |
| `autotune.plan` | string[] | Test scheme list per loop (`"step"` / `"disturbance"`) |
| `autotune.loops` | int | Loop count (total rounds = list length × loops) |
| `autotune.disturb_s` | float | Disturbance duration per round, seconds |
| `autotune.recover_s` | float | Recovery window per round, seconds |
| `ui.window_s` | float | Default plot time window, seconds |

## 3. Workspace Config `settings.toml`

```toml
[serial]
port = "COM5"            # serial port
baud = 115200            # baud rate
adapter = "default"      # "default" or "path.py:ClassName" (see ADAPTER.md)

[link]
type = "serial"          # "serial" or "mock"

[gains]
kp = 4.0                 # last used gains (auto-applied on the Mock link)
ki = 60.0
kd = 0.05

[setpoint]
value = 1.0              # last applied target

[telemetry]
period_ms = 5            # period requested by "start telemetry" (0 = every tick)

[safety]
kp = { min = 0.0, max = 500.0 }     # hard clamps applied before sending
ki = { min = 0.0, max = 5000.0 }
kd = { min = 0.0, max = 100.0 }
```

## 4. Trial Log `trials.jsonl`

One JSON object per line. Common fields:

| Field | Description |
| --- | --- |
| `ts` | Unix timestamp (seconds) |
| `kind` | `manual_step` / `script_step` / `script_disturbance` / `code_edit` |
| `setpoint` | Experiment target |
| `gains` | `{kp, ki, kd}` (absent for code_edit) |
| `metrics` | Metrics dictionary (below) |

### 4.1 Step kinds (`manual_step` / `script_step`)

```json
{
  "ts": 1754870400.12, "kind": "script_step", "script": "auto-tune step",
  "setpoint": 2.0, "gains": {"kp": 5.0, "ki": 40.0, "kd": 0.1},
  "metrics": {
    "rise_time": 0.049, "peak_time": 0.165, "overshoot_pct": 0.7,
    "settling_time": 0.7, "steady_error": -0.0003, "tail_ripple": 0.004,
    "oscillating": false, "samples": 260
  },
  "marks": [{"t": 1754870399.9, "label": "step start"}],
  "capture_s": 1.6
}
```

### 4.2 Disturbance kind (`script_disturbance`)

```json
{
  "ts": 1754871000.0, "kind": "script_disturbance", "script": "disturbance rejection",
  "setpoint": 1.0, "gains": {"kp": 4.0, "ki": 60.0, "kd": 0.05},
  "metrics": {
    "max_deviation": 0.32, "max_deviation_time": 1754871002.4,
    "recovery_time": 1.35, "residual_error": 0.0002, "samples": 900
  },
  "marks": [
    {"t": 1754871001.0, "label": "disturbance start"},
    {"t": 1754871006.0, "label": "disturbance end"}
  ]
}
```

### 4.3 Code edit (`code_edit`)

```json
{
  "ts": 1754872000.0, "kind": "code_edit",
  "request": "add integral anti-windup",
  "outcome": "kept",            // kept | rolled_back
  "exit_code": 0, "timed_out": false, "cancelled": false, "duration_s": 10.8,
  "changed_files": ["Core/Src/pid.c"], "new_files": [],
  "diff_stat": "...", "output_tail": ["..."]
}
```

A `null`/missing metric means it could not be measured for that trial.

## 5. Exports `exports/`

### 5.1 `config.json`

```json
{
  "exported_at": "2026-10-11 21:30:00",
  "source": "script_step",       // trial kind or "current"
  "setpoint": 2.0,
  "gains": {"kp": 5.0, "ki": 40.0, "kd": 0.1},
  "metrics": { "...": "metrics from that trial" },
  "notes": ""
}
```

### 5.2 `pid_config.h`

```c
/* PIDtool export 2026-10-11 21:30:00 source=script_step */
#define PID_KP 5.000000f
#define PID_KI 40.000000f
#define PID_KD 0.100000f
```

Paste it where your firmware defines PID parameters, or let a build script read
`config.json` and generate them.

## 6. Compatibility

- `trials.jsonl` is append-only; parsers should ignore unknown fields
- File names are stable (`config.json` is always the latest export)
- Upgrades never delete existing records; new fields stay backward compatible
