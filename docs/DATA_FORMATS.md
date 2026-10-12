# 数据与配置格式

本文件描述 PIDtool 落盘的各类文件，便于脚本化消费与版本管理。

## 1. 目录总览

```
%APPDATA%/PIDtool/app.toml      # 全局配置（跨工作区；含 API Key）
<固件工程>/.pidtool/
├── settings.toml               # 工作区配置（串口/增益/安全限幅等）
├── trials.jsonl                # 实验与代码修改记录（追加写，一行一条 JSON）
├── exports/
│   ├── config.json             # 最近一次导出的参数（直接可读）
│   ├── config_YYYYmmdd_HHMMSS.json  # 每次导出的时间戳副本
│   └── pid_config.h            # 可直接粘贴进固件的 C 宏片段
└── logs/                       # 预留（原始波形录制）
```

约定：`.pidtool/` 内所有文件随固件目录移动；API Key 等敏感信息只在全局
`app.toml`，不会进入固件仓库。

## 2. 全局配置 `app.toml`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `recent_workspaces` | string[] | 最近打开的工作区（最多 10 个） |
| `last_workspace` | string | 上次使用的工作区 |
| `llm.base_url` | string | OpenAI 兼容端点（默认 `https://api.deepseek.com`） |
| `llm.model` | string | 模型名（默认 `deepseek-chat`） |
| `llm.api_key` | string | API Key（仅存本机） |
| `codeagent.executable` | string | opencode 可执行文件（默认 `opencode`） |
| `codeagent.agent_name` | string | 可选的受限 agent 名称 |
| `codeagent.timeout_s` | int | 代码修改任务超时秒数（默认 900） |
| `codeagent.auto_approve` | bool | 是否传 `--auto`（默认 true，无头模式必需） |
| `ui.window_s` | float | 波形时间窗口默认值（秒） |

## 3. 工作区配置 `settings.toml`

```toml
[serial]
port = "COM5"            # 串口号
baud = 115200            # 波特率
adapter = "default"      # "default" 或 "路径.py:类名"（见 ADAPTER.md）

[link]
type = "serial"          # "serial" 或 "mock"（模拟器）

[gains]
kp = 4.0                 # 最近使用的参数（连接模拟器时自动下发）
ki = 60.0
kd = 0.05

[setpoint]
value = 1.0              # 最近下发的目标值

[telemetry]
period_ms = 5            # 开始遥测时请求的周期（0 = 每控制周期）

[safety]
kp = { min = 0.0, max = 500.0 }     # 下发前的硬性钳制
ki = { min = 0.0, max = 5000.0 }
kd = { min = 0.0, max = 100.0 }
```

## 4. 试验记录 `trials.jsonl`

每行一个 JSON 对象，公共字段：

| 字段 | 说明 |
| --- | --- |
| `ts` | Unix 时间戳（秒） |
| `kind` | `manual_step` / `script_step` / `script_disturbance` / `code_edit` |
| `setpoint` | 本次实验目标值 |
| `gains` | `{kp, ki, kd}`（code_edit 无此字段） |
| `metrics` | 指标字典（见下） |

### 4.1 阶跃类（`manual_step` / `script_step`）

```json
{
  "ts": 1754870400.12, "kind": "script_step", "script": "自动调参阶跃",
  "setpoint": 2.0, "gains": {"kp": 5.0, "ki": 40.0, "kd": 0.1},
  "metrics": {
    "rise_time": 0.049, "peak_time": 0.165, "overshoot_pct": 0.7,
    "settling_time": 0.7, "steady_error": -0.0003, "tail_ripple": 0.004,
    "oscillating": false, "samples": 260
  },
  "marks": [{"t": 1754870399.9, "label": "阶跃起点"}],
  "capture_s": 1.6
}
```

### 4.2 扰动类（`script_disturbance`）

```json
{
  "ts": 1754871000.0, "kind": "script_disturbance", "script": "扰动抑制",
  "setpoint": 1.0, "gains": {"kp": 4.0, "ki": 60.0, "kd": 0.05},
  "metrics": {
    "max_deviation": 0.32, "max_deviation_time": 1754871002.4,
    "recovery_time": 1.35, "residual_error": 0.0002, "samples": 900
  },
  "marks": [
    {"t": 1754871001.0, "label": "扰动开始"},
    {"t": 1754871006.0, "label": "扰动结束"}
  ]
}
```

### 4.3 代码修改（`code_edit`）

```json
{
  "ts": 1754872000.0, "kind": "code_edit",
  "request": "给 PID 增加积分抗饱和",
  "outcome": "kept",            // kept | rolled_back
  "exit_code": 0, "timed_out": false, "cancelled": false, "duration_s": 10.8,
  "changed_files": ["Core/Src/pid.c"], "new_files": [],
  "diff_stat": "...", "output_tail": ["..."]
}
```

指标为 `null`/缺省表示该轮未测到（例如窗口无效）。

## 5. 导出物 `exports/`

### 5.1 `config.json`

```json
{
  "exported_at": "2026-10-11 21:30:00",
  "source": "script_step",       // 来源：试验 kind 或 "current"
  "setpoint": 2.0,
  "gains": {"kp": 5.0, "ki": 40.0, "kd": 0.1},
  "metrics": { "...": "对应试验的指标" },
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

可直接粘贴进固件的参数定义处，或由构建脚本读取 `config.json` 生成。

## 6. 兼容性

- `trials.jsonl` 只追加写入，解析方应忽略未知字段
- 文件名保持稳定（`config.json` 始终为最近一次导出）
- 版本升级不会删除既有记录；新增字段时保持向后兼容
