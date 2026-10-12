# 自定义协议适配器开发指南

当固件协议已经固定、无法按 [PROTOCOL.md](PROTOCOL.md) 调整时，写一个适配器把
你的协议翻译成 PIDtool 的内部模型即可，无需改动上位机其他代码。

## 1. 适配器接口

继承 `pidtool.link.protocol.ProtocolAdapter`，实现 8 个方法：

| 方法 | 作用 | 返回 |
| --- | --- | --- |
| `encode_set_gains(kp, ki, kd)` | 组帧：设置增益 | `bytes` |
| `encode_set_setpoint(setpoint)` | 组帧：设置目标值 | `bytes` |
| `encode_set_mode(mode)` | 组帧：运行模式（0 停止/1 运行） | `bytes` |
| `encode_start_telemetry(period_ms=0)` | 组帧：开始遥测 | `bytes` |
| `encode_stop_telemetry()` | 组帧：停止遥测 | `bytes` |
| `encode_get_gains()` | 组帧：读取参数 | `bytes` |
| `feed(data)` | 解析接收字节流 | `(list[Sample], list[LinkEvent])` |

`Sample` 与 `LinkEvent` 定义（`pidtool.link.protocol`）：

```python
Sample(t, tick_us, setpoint, feedback, u, up, ui, ud)
# t: 接收时刻（time.perf_counter() 秒），其余为设备字段
LinkEvent(t, kind, data)
# kind="gains" -> data={"kp","ki","kd"}    （参数回读）
# kind="ack"   -> data={"acked","status"}  （命令应答）
# kind="text"  -> data={"text": "..."}     （调试文本）
```

要点：

- `feed()` 在串口读取线程中被调用，**不要在其中操作 Qt 控件**；返回的 Sample
  会由链路层写入环形缓冲
- 可以一次返回 0 个或多个 Sample；`t` 用 `time.perf_counter()` 当前值即可
- 解析器可以内置缓冲，允许任意分片输入（串口按块到达）

## 2. 挂载到工作区

编辑 `<固件目录>/.pidtool/settings.toml`：

```toml
[serial]
port = "COM5"
baud = 115200
adapter = "D:/firmware/pid_adapter.py:Adapter"
```

- 格式：`适配器文件路径:类名`；只写路径时默认取类名 `Adapter`
- Windows 盘符冒号不影响解析（路径按“整路径是否存在”优先匹配）
- 保存后重新连接链路生效

## 3. 完整示例（ASCII 行协议）

假设固件协议为 ASCII 行（`\n` 结尾）：

```
上位机 → 设备:  GAIN <kp> <ki> <kd>\n   SP <值>\n   MODE <0|1>\n   STREAM <ms>\n   STOP\n   GET\n
设备 → 上位机:  T <tick> <sp> <fb> <u> <up> <ui> <ud>\n
                GAIN <kp> <ki> <kd>\n
```

适配器实现：

```python
import time

from pidtool.link.protocol import LinkEvent, ProtocolAdapter, Sample


class Adapter(ProtocolAdapter):
    name = "ascii-lines"

    def __init__(self) -> None:
        self._buf = b""

    def encode_set_gains(self, kp, ki, kd):
        return f"GAIN {kp:.6g} {ki:.6g} {kd:.6g}\n".encode()

    def encode_set_setpoint(self, setpoint):
        return f"SP {setpoint:.6g}\n".encode()

    def encode_set_mode(self, mode):
        return f"MODE {int(mode)}\n".encode()

    def encode_start_telemetry(self, period_ms=0):
        return f"STREAM {int(period_ms)}\n".encode()

    def encode_stop_telemetry(self):
        return b"STOP\n"

    def encode_get_gains(self):
        return b"GET\n"

    def feed(self, data):
        samples = []
        events = []
        now = time.perf_counter()
        self._buf += data
        while b"\n" in self._buf:
            line, _, self._buf = self._buf.partition(b"\n")
            parts = line.decode("ascii", "replace").strip().split()
            if not parts:
                continue
            if parts[0] == "T" and len(parts) == 8:
                tick = int(parts[1])
                sp, fb, u, up, ui, ud = (float(v) for v in parts[2:])
                samples.append(Sample(now, tick, sp, fb, u, up, ui, ud))
            elif parts[0] == "GAIN" and len(parts) == 4:
                kp, ki, kd = (float(v) for v in parts[1:])
                events.append(LinkEvent(now, "gains", {"kp": kp, "ki": ki, "kd": kd}))
        return samples, events
```

## 4. 验证建议

- 先用串口助手确认设备协议收发正常，再切到 PIDtool
- 连接后观察状态栏：`帧数` 增长表示解析成功；可在“实验”页下发一次增益并
  观察设备是否 ACK
- 若需要控制器闭环（阶跃/扰动实验），设备必须支持 `SET_SETPOINT` 等价命令；
  参数在线设置同理
- 注意：内置“模拟器”链路使用默认帧格式，**不支持**自定义适配器；联调时请
  使用真实串口（或 TX-RX 回环）
