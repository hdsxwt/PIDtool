# PIDtool Serial Protocol Specification (v1)

For firmware (STM32-side) implementers. The PIDtool host ships a built-in adapter
named `default` that implements this protocol; implement the device side as
described here and simply connect PIDtool to the serial port.

- Protocol version: 1 (matches PIDtool 0.1.0)
- Reference implementation: [`docs/firmware/pid_protocol.h`](firmware/pid_protocol.h) / [`pid_protocol.c`](firmware/pid_protocol.c) (C99, no dependencies, drop-in)
- The reference implementation is byte-for-byte cross-checked against the host
  codec (`tests/test_c_reference.py`, 5 parity tests)

---

## 1. Physical Layer and Rates

| Item | Recommendation |
| --- | --- |
| USART | 8 data bits / no parity / 1 stop bit (8N1) |
| Baud rate | ≥ 115200; use 460800+ for 1 kHz telemetry |
| Telemetry frame size | 34 bytes fixed (28-byte payload + 6-byte framing overhead) |
| Bandwidth | 200 Hz ≈ 6.8 KB/s; 1 kHz ≈ 34 KB/s |

All multi-byte values are little-endian (Cortex-M native). A single UART TX/RX
pair is sufficient; no flow control is required.

## 2. Frame Format

```
+--------+--------+-------+--------+-------------+--------+--------+
| 0xAA   | 0x55   | LEN   | TYPE   | PAYLOAD     | CRC_LO | CRC_HI |
+--------+--------+-------+--------+-------------+--------+--------+
   header (2 bytes)  1 byte  1 byte   LEN-1 bytes   CRC (2 bytes, LE)
```

- `LEN` = 1 + payload length (TYPE included), valid range `1..251`
- `CRC16` covers `TYPE + PAYLOAD` (header and LEN excluded)
- Maximum frame is 256 bytes (LEN=251)

## 3. CRC16 Parameters

| Parameter | Value |
| --- | --- |
| Algorithm | CRC-16/CCITT-FALSE |
| Polynomial | 0x1021 |
| Initial value | 0xFFFF |
| Input/output reflection | none |
| Output XOR | none |
| Check value | `"123456789"` → `0x29B1` |

Reference implementation (identical in the C reference and the host codec):

```c
uint16_t pidp_crc16_ccitt(const uint8_t *data, size_t len)
{
    uint16_t crc = 0xFFFFu;
    for (size_t i = 0; i < len; i++) {
        crc ^= (uint16_t)data[i] << 8;
        for (int bit = 0; bit < 8; bit++)
            crc = (crc & 0x8000u) ? (uint16_t)((crc << 1) ^ 0x1021u) : (uint16_t)(crc << 1);
    }
    return crc;
}
```

## 4. Message Overview

| TYPE | Name | Direction | Payload length | Description |
| --- | --- | --- | --- | --- |
| `0x01` | TELEMETRY | device → host | 28 | Telemetry samples (plot source) |
| `0x10` | SET_GAINS | host → device | 12 | Set Kp/Ki/Kd at runtime |
| `0x11` | SET_SETPOINT | host → device | 4 | Set target value |
| `0x12` | SET_MODE | host → device | 1 | Mode (0 idle / 1 run) |
| `0x13` | START_TELEMETRY | host → device | 2 | Start streaming (period ms, 0 = every control tick) |
| `0x14` | STOP_TELEMETRY | host → device | 0 | Stop streaming |
| `0x15` | GET_GAINS | host → device | 0 | Read current gains |
| `0x20` | GAINS | device → host | 12 | Gains reply (Kp/Ki/Kd) |
| `0x30` | TEXT | device → host | 0..250 | Debug text (UTF-8, shown in status bar) |
| `0x7F` | ACK | device → host | 2 | Command acknowledgement (type + status) |

## 5. Payload Layouts (little-endian)

### 5.1 TELEMETRY `0x01` (28 bytes)

| Offset | Type | Field | Notes |
| --- | --- | --- | --- |
| 0 | uint32 | tick_us | Device timestamp (µs, may wrap) |
| 4 | float32 | setpoint | Current target |
| 8 | float32 | feedback | Measured value |
| 12 | float32 | u | PID output |
| 16 | float32 | up | Proportional term |
| 20 | float32 | ui | Integral term |
| 24 | float32 | ud | Derivative term |

Term fields are for visualization only; firmware without term outputs may send
zeros there (`u` still carries the total output).

### 5.2 GAINS `0x20` / SET_GAINS `0x10` (12 bytes)

| Offset | Type | Field |
| --- | --- | --- |
| 0 | float32 | kp |
| 4 | float32 | ki |
| 8 | float32 | kd |

### 5.3 SET_SETPOINT `0x11` (4 bytes)

| Offset | Type | Field |
| --- | --- | --- |
| 0 | float32 | setpoint |

### 5.4 SET_MODE `0x12` (1 byte)

| Value | Meaning |
| --- | --- |
| 0 | Idle (recommended: also reset the integrator) |
| 1 | Run |

### 5.5 START_TELEMETRY `0x13` (2 bytes)

| Offset | Type | Field |
| --- | --- | --- |
| 0 | uint16 | period_ms; 0 = one frame every control tick |

### 5.6 STOP_TELEMETRY `0x14` / GET_GAINS `0x15` (0 bytes)

No payload.

### 5.7 ACK `0x7F` (2 bytes)

| Offset | Type | Field |
| --- | --- | --- |
| 0 | uint8 | acked_type: TYPE of the acknowledged command |
| 1 | uint8 | status: 0 = OK; non-zero is device-defined |

### 5.8 TEXT `0x30` (0..250 bytes)

UTF-8 text shown in the host status bar; useful for error messages.

## 6. Behavioral Contract

1. **ACK**: reply with an ACK (status=0) after any of `0x10`..`0x14`. For
   `GET_GAINS`, replying with a GAINS frame is sufficient (ACK optional).
2. **CRC errors**: drop the whole frame and do not ACK. The host decoder
   re-synchronizes on its own, so raw noise can be fed to the parser.
3. **Power-on defaults**: recommended `mode=idle`, telemetry off; the host
   explicitly sends `SET_MODE run` and `START_TELEMETRY`.
4. **Safety clamping**: the device should clamp gains to its own limits
   (the host also clamps via the workspace `[safety]` settings).
5. **Period semantics**: `period_ms>0` sends on a millisecond cadence; `0` means
   every control tick (watch bandwidth; use 460800+ baud at 1 kHz).
6. **Timestamps**: `tick_us` may wrap; the host plots by receive time and does
   not depend on device time.

## 7. Example Frames

The following byte sequences are cross-verified between the reference
implementation and the host codec (hex):

```
SET_GAINS(1.5, 2.5, 0.25)   AA 55 0D 10 00 00 C0 3F 00 00 20 40 00 00 80 3E A5 2C
SET_SETPOINT(2.0)           AA 55 05 11 00 00 00 40 C3 F7
SET_MODE(run)               AA 55 02 12 01 3F 68
START_TELEMETRY(5ms)        AA 55 03 13 05 00 5A 29
STOP_TELEMETRY              AA 55 01 14 45 B3
GET_GAINS                   AA 55 01 15 64 A3
TELEMETRY                   AA 55 1D 01 40 E2 01 00 00 00 00 40 9A 99 F9 3F 00 00 40 40
                            00 00 00 40 66 66 66 3F CD CC CC 3D 79 B4
                            (tick=123456, sp=2.0, fb=1.95, u=3.0, up=2.0, ui=0.9, ud=0.1)
GAINS(1.5, 2.5, 0.25)       AA 55 0D 20 00 00 C0 3F 00 00 20 40 00 00 80 3E 79 17
ACK(0x10, ok)               AA 55 03 7F 10 00 D6 3B
TEXT("ok")                  AA 55 03 30 6F 6B E0 CF
```

How to read one: `AA 55 | 0D | 10 | 12-byte payload | A5 2C` with LEN=0x0D=13
(TYPE included).

## 8. Firmware Integration (recommended reference)

Add `docs/firmware/pid_protocol.h/.c` to your project; a HAL integration skeleton:

```c
#include "pid_protocol.h"

static pidp_parser_t g_parser;
static pidp_callbacks_t g_cb;
static uint8_t g_rx_byte;
static volatile uint8_t g_telemetry_on;
static volatile uint16_t g_telemetry_period_ms;

static void uart_write(const uint8_t *data, size_t len, void *user)
{
    (void)user;
    /* Use a ring buffer + DMA in ISR context; direct send is fine in the main loop */
    HAL_UART_Transmit(&huart1, (uint8_t *)data, (uint16_t)len, 10);
}

static void on_set_gains(float kp, float ki, float kd, void *user)
{
    (void)user;
    pid_set_gains(&g_pid, kp, ki, kd);
    pidp_send_ack(uart_write, NULL, PIDP_CMD_SET_GAINS, PIDP_ACK_OK);
}

static void on_set_setpoint(float setpoint, void *user) { /* as above */ }
static void on_set_mode(uint8_t mode, void *user)       { /* reset integrator on idle */ }
static void on_start_telemetry(uint16_t period_ms, void *user) { /* store period */ }
static void on_stop_telemetry(void *user)               { /* disable streaming */ }
static void on_get_gains(void *user)
{
    pidp_send_gains(uart_write, NULL, g_pid.kp, g_pid.ki, g_pid.kd);
}

void protocol_setup(void)
{
    pidp_parser_init(&g_parser);
    g_cb.on_set_gains = on_set_gains;
    g_cb.on_set_setpoint = on_set_setpoint;
    g_cb.on_set_mode = on_set_mode;
    g_cb.on_start_telemetry = on_start_telemetry;
    g_cb.on_stop_telemetry = on_stop_telemetry;
    g_cb.on_get_gains = on_get_gains;
    g_cb.user = NULL;
    HAL_UART_Receive_IT(&huart1, &g_rx_byte, 1);
}

/* RX interrupt: feed the parser byte by byte. Callbacks should only set
   scalars/flags; if sending ACKs from the ISR hurts timing, set a flag and
   send from the main loop instead. */
void HAL_UART_RxCpltCallback(UART_HandleTypeDef *huart)
{
    if (huart == &huart1) {
        pidp_parser_feed(&g_parser, &g_rx_byte, 1, &g_cb);
        HAL_UART_Receive_IT(&huart1, &g_rx_byte, 1);
    }
}

/* Control loop (e.g. 1 kHz) */
void control_step(void)
{
    pid_update(&g_pid);
    if (g_telemetry_on) {
        static uint32_t divider;
        uint32_t ticks = g_telemetry_period_ms == 0 ? 1u
                       : (uint32_t)g_telemetry_period_ms; /* with a 1 ms control period */
        if (++divider >= ticks) {
            divider = 0;
            pidp_send_telemetry(uart_write, NULL, tick_us(), g_pid.setpoint,
                                g_pid.feedback, g_pid.u, g_pid.up, g_pid.ui, g_pid.ud);
        }
    }
}
```

Key points:

- `pidp_parser_feed()` is a pure state machine; safe to call byte-by-byte from an ISR
- Prefer a ring buffer for TX to avoid blocking the control loop (the example sends directly for brevity)
- Keep callbacks short; defer heavy re-initialization to the main loop via flags

## 9. Host-Side Configuration

- Adapter: use the built-in `default` (`[serial] adapter = "default"` in the workspace `settings.toml`)
- Baud rate: pick the matching value in the toolbar combo box
- If your firmware protocol is already fixed and cannot change, see [ADAPTER.md](ADAPTER.md)

## 10. Debugging Tips

- **No data**: make sure `START_TELEMETRY` was sent (the "start telemetry"
  button), baud rates match, and TX/RX are crossed
- **CRC error counter grows**: baud mismatch, missing common ground, or noise;
  the host status bar shows `CRC errors / garbage bytes`
- **Logic analyzer**: trigger on the `AA 55` header; the first 4 bytes of a
  TELEMETRY payload are `tick_us` (little-endian)
- **Loopback self-test**: short TX to RX and use the host to send `SET_SETPOINT`
  repeatedly to verify the link
