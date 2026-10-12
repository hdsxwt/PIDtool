# Custom Protocol Adapter Guide

If your firmware protocol is already fixed and cannot follow
[PROTOCOL.md](PROTOCOL.md), write an adapter that translates it to the PIDtool
internal model. Nothing else in the host needs to change.

## 1. Adapter Interface

Subclass `pidtool.link.protocol.ProtocolAdapter` and implement 8 methods:

| Method | Purpose | Returns |
| --- | --- | --- |
| `encode_set_gains(kp, ki, kd)` | Build a set-gains frame | `bytes` |
| `encode_set_setpoint(setpoint)` | Build a set-target frame | `bytes` |
| `encode_set_mode(mode)` | Build a mode frame (0 idle / 1 run) | `bytes` |
| `encode_start_telemetry(period_ms=0)` | Build a start-streaming frame | `bytes` |
| `encode_stop_telemetry()` | Build a stop-streaming frame | `bytes` |
| `encode_get_gains()` | Build a read-gains frame | `bytes` |
| `feed(data)` | Parse received bytes | `(list[Sample], list[LinkEvent])` |

`Sample` and `LinkEvent` (`pidtool.link.protocol`):

```python
Sample(t, tick_us, setpoint, feedback, u, up, ui, ud)
# t: receive time (time.perf_counter() seconds); the rest are device fields
LinkEvent(t, kind, data)
# kind="gains" -> data={"kp","ki","kd"}    (gains read-back)
# kind="ack"   -> data={"acked","status"}  (command acknowledgement)
# kind="text"  -> data={"text": "..."}     (debug text)
```

Notes:

- `feed()` runs on the serial reader thread; **never touch Qt widgets there**.
  Returned samples are written to the ring buffer by the link layer.
- You may return zero or more samples per call; set `t` to the current
  `time.perf_counter()`.
- Keep an internal buffer so arbitrary chunk boundaries are fine.

## 2. Hooking It to a Workspace

Edit `<firmware-dir>/.pidtool/settings.toml`:

```toml
[serial]
port = "COM5"
baud = 115200
adapter = "D:/firmware/pid_adapter.py:Adapter"
```

- Format: `adapter_file.py:ClassName`; path-only defaults to class `Adapter`
- Windows drive-letter colons are handled (whole-path match takes priority)
- Reconnect the link to apply

## 3. Full Example (ASCII line protocol)

Suppose the firmware speaks ASCII lines (`\n` terminated):

```
host -> device:  GAIN <kp> <ki> <kd>\n   SP <value>\n   MODE <0|1>\n   STREAM <ms>\n   STOP\n   GET\n
device -> host:  T <tick> <sp> <fb> <u> <up> <ui> <ud>\n
                 GAIN <kp> <ki> <kd>\n
```

Adapter implementation:

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

## 4. Verification Tips

- Validate the device protocol with a serial terminal first, then switch to PIDtool
- After connecting, watch the status bar: `frames` growing means parsing works.
  Send a gain update from the Experiments tab and see whether the device ACKs.
- Closed-loop experiments (step/disturbance) require a `SET_SETPOINT` equivalent
  in the firmware; runtime gain updates likewise
- Note: the built-in "Mock" link speaks the default frame format and does
  **not** work with custom adapters; use a real port (or a TX-RX loopback)
  while integrating
