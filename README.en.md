# PIDtool

An STM32 PID tuning workbench: live serial visualization, scripted experiments,
AI-driven closed-loop auto-tuning, and authorized code editing.

Combined with the firmware-side [serial protocol](docs/en/PROTOCOL.md) (an
[STM32 C reference implementation](docs/firmware/pid_protocol.h) is included), it
turns the "tweak gains -> observe -> evaluate -> repeat" loop from manual serial
terminal fiddling into a semi/fully automated workflow.

## Features

- **Live plots**: setpoint/feedback, error, output and PID terms in three
  rolling panes, with freeze/clear
- **Manual experiments**: push gains and setpoints online, capture and compute
  step metrics (rise/peak/overshoot/settling/steady error/oscillation)
- **Scripted experiments**: step response and disturbance rejection flows with
  human-in-the-loop prompts (countdown banner, "apply/stop resistance" confirm)
  and event markers on the plots
- **AI closed-loop auto-tuning**: DeepSeek proposes new gains from the trial
  history; the tool runs experiments, scores them, keeps the best, stops early
  on oscillation/degradation and restores the best gains at the end
- **Param export**: `config.json` plus a paste-ready `pid_config.h`
- **Authorized code editing**: for structural changes, opencode edits code in a
  limited scope; a forced git diff review with keep/rollback
- **Workspace isolation**: per-firmware-directory config and history
  (`.pidtool/`), no cross-talk
- **No hardware required**: a built-in closed-loop motor simulator (with
  anti-windup PID) lets you exercise the whole flow offline

## Install

Python 3.11+ (Windows).

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

Dependencies: PySide6, pyqtgraph, pyserial, numpy, tomli-w, openai.

## Quick Start (no hardware)

```powershell
.\.venv\Scripts\python.exe main.py
```

1. Toolbar "workspace -> Browse" and pick any directory
2. Keep the link type "Mock" and click Connect
3. Change the setpoint and click "apply setpoint"; watch plots and metrics
4. To try auto-tuning: fill in your DeepSeek API key on the AI tab, save, start

## Connecting a Real Board

1. Implement the [protocol](docs/en/PROTOCOL.md) in firmware (the C reference is
   drop-in), or write an [adapter](docs/en/ADAPTER.md) for an existing protocol
2. Select the firmware directory as the workspace; choose the serial link, port
   and baud rate, then connect
3. Click "start telemetry" and plots should appear; if not, see the
   [debugging tips](docs/en/PROTOCOL.md#10-debugging-tips)

## UI Tour

Four tabs at the bottom:

| Tab | Purpose |
| --- | --- |
| Experiments | Online gains, manual capture/metrics, scripted step/disturbance |
| AI Tuning | Auto-tune controls, iteration log, DeepSeek settings |
| Code Edit | Request -> preview -> authorized run -> git diff review -> keep/rollback |
| History | All trials with gains/metrics; export a selected trial |

### Scripted experiments

- **Step**: wait for settle -> capture baseline -> countdown -> step -> metrics
- **Disturbance**: countdown prompt "apply manual resistance for N seconds" ->
  mark start -> prompt "stop and confirm" -> recovery window -> disturbance
  metrics (max deviation / recovery time)

### AI tuning flow and safety

Each round: apply gains -> step experiment (baseline then step, so every round
measures a clean step) -> extract metrics -> score -> DeepSeek proposes the next
gains -> clamp. Safety: workspace limits, per-round recording, **restore of the
best gains at the end**, early stop on oscillation or repeated degradation,
abort anytime. The API key is stored only in the global config
(`%APPDATA%/PIDtool/app.toml`).

Optional data: tick "send downsampled raw waveform to AI" to attach ~80
downsampled samples per round (t/sp/fb/u/up/ui/ud), helping the model judge
response shape and output saturation; off by default (increases token usage).

### Code editing (opencode)

Preconditions: the workspace is a git repo with no uncommitted tracked changes
(so rollback is always safe).

Flow: write the request -> "generate preview" (shows the exact command and
prompt, runs prechecks) -> "authorize execution" -> live log -> git diff review
-> **keep** (recorded in history) or **rollback** (restores everything and
removes files created by the run).

Notes:

- Default command: `opencode run --dir <project> --auto "<prompt>"`; the
  executable, restricted agent and timeout are configurable on the tab
- The prompt includes scope constraints (PID-related sources only, no flashing);
  the hard guarantee is still the diff review
- Building and flashing remain manual (or separately authorized); the tool
  never auto-flashes

## Export and Persist

When satisfied, select a trial on the History tab and click
"export selected as config.json / .h":

- `.pidtool/exports/config.json`: gains + source + metrics (latest)
- `.pidtool/exports/config_<timestamp>.json`: timestamped copies
- `.pidtool/exports/pid_config.h`: `#define PID_KP ...`, paste into firmware

See [DATA_FORMATS.md](docs/en/DATA_FORMATS.md) for all file formats.

## Packaging as a Windows Executable

```powershell
.\build_win.ps1
```

The result lands in `dist\PIDtool\` (~170 MB, portable):

- Copy the whole `dist\PIDtool\` folder into a program directory, e.g.
  `C:\Program Files\PIDtool` (admin required) or `%LOCALAPPDATA%\Programs\PIDtool`
  (no admin)
- Right-click `PIDtool.exe` -> Send to -> Desktop shortcut
- Runtime config still lives in `%APPDATA%\PIDtool\app.toml`, and workspace data
  follows the firmware directory, independent of the install location; replace
  the folder to upgrade

## Project Layout

```
pidtool/
├── link/          # serial link, frame protocol + adapters, mock simulator
├── core/          # ring buffer, metrics, experiment engine, export
├── ai/            # DeepSeek client, scoring, closed-loop tuner
├── codeagent/     # git tools, opencode subprocess, authorized session
└── ui/            # PySide6 + pyqtgraph interface
```

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Covers protocol codec, analytic metric references, experiment state machine,
tuner loop, offscreen UI smoke, plus byte-level cross-checks of the C reference
against the Python codec (requires gcc; skipped otherwise).

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| No serial data | "start telemetry" clicked, baud matches, TX/RX crossed, common ground |
| CRC error counter grows | Baud mismatch or line noise; see the [protocol doc](docs/en/PROTOCOL.md) |
| "missing API key" | Fill and save it on the AI tab |
| "not a git repository" | `git init` and commit once, or pick another directory |
| Code-edit window drops into the TUI | Executable points to a .bat that swallows args; use the full `opencode.cmd`/`opencode.exe` path (the tool reports this too) |
| COM port busy | Close other serial tools and click refresh |

## Documentation

- [Serial protocol spec v1](docs/en/PROTOCOL.md) · [中文](docs/PROTOCOL.md)
- [Custom adapter guide](docs/en/ADAPTER.md) · [中文](docs/ADAPTER.md)
- [Data and configuration formats](docs/en/DATA_FORMATS.md) · [中文](docs/DATA_FORMATS.md)
- [STM32 C reference implementation](docs/firmware/pid_protocol.h)
