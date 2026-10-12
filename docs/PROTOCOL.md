# PIDtool 串口协议规范（v1）

面向固件（STM32 侧）实现者。上位机 PIDtool 内置适配器 `default` 实现了本协议；
快速对接步骤：按本文实现设备端 → PIDtool 选择串口连接即可。

- 协议版本：1（对应 PIDtool 0.1.0）
- 参考实现：[`docs/firmware/pid_protocol.h`](firmware/pid_protocol.h) / [`pid_protocol.c`](firmware/pid_protocol.c)（C99，无依赖，可直接加入工程）
- 参考实现已与上位机 codec 做字节级交叉验证（`tests/test_c_reference.py`，连续 5 组对拍通过）

---

## 1. 物理层与速率

| 项目 | 建议 |
| --- | --- |
| USART | 8 数据位 / 无校验 / 1 停止位（8N1） |
| 波特率 | ≥ 115200；遥测 1 kHz 时建议 460800 或更高 |
| 遥测帧大小 | 固定 34 字节（28 字节 payload + 6 字节帧开销） |
| 带宽估算 | 200 Hz ≈ 6.8 KB/s；1 kHz ≈ 34 KB/s |

数据采用小端（Cortex-M 原生）。设备仅需一个 UART TX/RX，无流控要求。

## 2. 帧格式

```
+--------+--------+-------+--------+-------------+--------+--------+
| 0xAA   | 0x55   | LEN   | TYPE   | PAYLOAD     | CRC_LO | CRC_HI |
+--------+--------+-------+--------+-------------+--------+--------+
   帧头(2字节)     1字节   1字节    LEN-1 字节     校验(2字节, 小端)
```

- `LEN` = 1 + payload 长度（包含 TYPE 字节），合法范围 `1..251`
- `CRC16` 覆盖 `TYPE + PAYLOAD`（不含帧头与 LEN）
- 单帧最大 256 字节（LEN=251 时）

## 3. CRC16 参数

| 参数 | 值 |
| --- | --- |
| 算法 | CRC-16/CCITT-FALSE |
| 多项式 | 0x1021 |
| 初值 | 0xFFFF |
| 输入/输出反转 | 无 |
| 异或输出 | 无 |
| 校验值 | `"123456789"` → `0x29B1` |

参考实现（与 C 参考实现、上位机完全一致）：

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

## 4. 消息总表

| TYPE | 名称 | 方向 | payload 长度 | 说明 |
| --- | --- | --- | --- | --- |
| `0x01` | TELEMETRY | 设备 → 上位机 | 28 | 遥测数据（波形来源） |
| `0x10` | SET_GAINS | 上位机 → 设备 | 12 | 在线设置 Kp/Ki/Kd |
| `0x11` | SET_SETPOINT | 上位机 → 设备 | 4 | 设置目标值 |
| `0x12` | SET_MODE | 上位机 → 设备 | 1 | 运行模式（0 停止 / 1 运行） |
| `0x13` | START_TELEMETRY | 上位机 → 设备 | 2 | 开始遥测（周期 ms，0=每控制周期） |
| `0x14` | STOP_TELEMETRY | 上位机 → 设备 | 0 | 停止遥测 |
| `0x15` | GET_GAINS | 上位机 → 设备 | 0 | 读取当前参数 |
| `0x20` | GAINS | 设备 → 上位机 | 12 | 参数应答（Kp/Ki/Kd） |
| `0x30` | TEXT | 设备 → 上位机 | 0..250 | 调试文本（UTF-8，可显示在状态栏） |
| `0x7F` | ACK | 设备 → 上位机 | 2 | 命令应答（应答类型 + 状态码） |

## 5. Payload 布局（小端）

### 5.1 TELEMETRY `0x01`（28 字节）

| 偏移 | 类型 | 字段 | 说明 |
| --- | --- | --- | --- |
| 0 | uint32 | tick_us | 设备时间戳（µs，可回绕） |
| 4 | float32 | setpoint | 当前目标值 |
| 8 | float32 | feedback | 反馈值（被控量） |
| 12 | float32 | u | PID 总输出 |
| 16 | float32 | up | 比例分量 |
| 20 | float32 | ui | 积分分量 |
| 24 | float32 | ud | 微分分量 |

分量字段仅用于可视化；没有分量输出的固件可全填 0（`u` 仍填总输出）。

### 5.2 GAINS `0x20` / SET_GAINS `0x10`（12 字节）

| 偏移 | 类型 | 字段 |
| --- | --- | --- |
| 0 | float32 | kp |
| 4 | float32 | ki |
| 8 | float32 | kd |

### 5.3 SET_SETPOINT `0x11`（4 字节）

| 偏移 | 类型 | 字段 |
| --- | --- | --- |
| 0 | float32 | setpoint |

### 5.4 SET_MODE `0x12`（1 字节）

| 值 | 含义 |
| --- | --- |
| 0 | 停止（建议同时清除积分器） |
| 1 | 运行 |

### 5.5 START_TELEMETRY `0x13`（2 字节）

| 偏移 | 类型 | 字段 |
| --- | --- | --- |
| 0 | uint16 | period_ms；0 表示每个控制周期发送一帧 |

### 5.6 STOP_TELEMETRY `0x14` / GET_GAINS `0x15`（0 字节）

无 payload。

### 5.7 ACK `0x7F`（2 字节）

| 偏移 | 类型 | 字段 |
| --- | --- | --- |
| 0 | uint8 | acked_type：被应答的命令 TYPE |
| 1 | uint8 | status：0 = 成功；非 0 由设备自定义错误码 |

### 5.8 TEXT `0x30`（0..250 字节）

UTF-8 文本，上位机显示在状态栏，可用于错误提示。

## 6. 行为约定

1. **ACK**：收到 `0x10`~`0x14` 任一命令后，回 `ACK`（status=0 表示已接受）。收到
   `GET_GAINS` 后回 `GAINS` 帧即可（`ACK` 可选）。
2. **CRC 错误**：整帧丢弃并且不回 ACK。上位机侧解码器内置乱码重扫，允许将噪声
   直接交给解析器。
3. **上电默认**：建议 `mode=idle`、遥测关闭；由上位机显式下发
   `SET_MODE run` 与 `START_TELEMETRY`。
4. **安全限幅**：设备端应对增益做自己的上下限钳制（PIDtool 侧还有一层工作区
   限幅，见 `settings.toml` 的 `[safety]`）。
5. **周期语义**：`period_ms>0` 按毫秒节拍发送；`0` 表示控制周期发送（注意带宽，
   1 kHz 控制周期时建议波特率 ≥ 460800）。
6. **时间戳**：`tick_us` 允许回绕，上位机以接收时刻绘图，不依赖设备时间。

## 7. 示例帧

以下字节序列由参考实现与上位机 codec 双向验证（十六进制）：

```
SET_GAINS(1.5, 2.5, 0.25)   AA 55 0D 10 00 00 C0 3F 00 00 20 40 00 00 80 3E A5 2C
SET_SETPOINT(2.0)           AA 55 05 11 00 00 00 40 C3 F7
SET_MODE(run)               AA 55 02 12 01 3F 68
START_TELEMETRY(5ms)        AA 55 03 13 05 00 5A 29
STOP_TELEMETRY              AA 55 01 14 45 B3
GET_GAINS                   AA 55 01 15 64 A3
TELEMETRY                   AA 55 1D 01 40 E2 01 00 00 00 00 40 9A 99 F9 3F 00 00 40 40
                            00 00 00 40 66 66 66 3F CD CC CC 3D 79 B4
                            （tick=123456, sp=2.0, fb=1.95, u=3.0, up=2.0, ui=0.9, ud=0.1）
GAINS(1.5, 2.5, 0.25)       AA 55 0D 20 00 00 C0 3F 00 00 20 40 00 00 80 3E 79 17
ACK(0x10, ok)               AA 55 03 7F 10 00 D6 3B
TEXT("ok")                  AA 55 03 30 6F 6B E0 CF
```

解读示例：`AA 55 | 0D | 10 | 12 字节 payload | A5 2C`，LEN=0x0D=13（含 TYPE）。

## 8. 固件集成（推荐参考实现）

把 `docs/firmware/pid_protocol.h/.c` 加入工程，参考如下 HAL 集成骨架：

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
    /* 中断上下文可用环形缓冲 + DMA；主循环上下文可直接发送 */
    HAL_UART_Transmit(&huart1, (uint8_t *)data, (uint16_t)len, 10);
}

static void on_set_gains(float kp, float ki, float kd, void *user)
{
    (void)user;
    pid_set_gains(&g_pid, kp, ki, kd);
    pidp_send_ack(uart_write, NULL, PIDP_CMD_SET_GAINS, PIDP_ACK_OK);
}

static void on_set_setpoint(float setpoint, void *user) { /* 同上 */ }
static void on_set_mode(uint8_t mode, void *user)       { /* idle 时清积分 */ }
static void on_start_telemetry(uint16_t period_ms, void *user) { /* 记录周期 */ }
static void on_stop_telemetry(void *user)               { /* 关闭遥测 */ }
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

/* RX 中断：逐字节喂解析器。回调应只做标量赋值/置标志，避免长耗时操作；
   若 ACK 从 ISR 直接发送影响实时性，可改为置标志由主循环发送。 */
void HAL_UART_RxCpltCallback(UART_HandleTypeDef *huart)
{
    if (huart == &huart1) {
        pidp_parser_feed(&g_parser, &g_rx_byte, 1, &g_cb);
        HAL_UART_Receive_IT(&huart1, &g_rx_byte, 1);
    }
}

/* 控制环（例：1 kHz） */
void control_step(void)
{
    pid_update(&g_pid);
    if (g_telemetry_on) {
        static uint32_t divider;
        uint32_t ticks = g_telemetry_period_ms == 0 ? 1u
                       : (uint32_t)g_telemetry_period_ms; /* 控制周期为 1ms 时 */
        if (++divider >= ticks) {
            divider = 0;
            pidp_send_telemetry(uart_write, NULL, tick_us(), g_pid.setpoint,
                                g_pid.feedback, g_pid.u, g_pid.up, g_pid.ui, g_pid.ud);
        }
    }
}
```

要点：

- `pidp_parser_feed()` 是纯状态机，可在中断中逐字节调用
- 发送侧建议用环形缓冲避免阻塞控制环（示例为简化直接发送）
- 回调里不要直接跑 PID 重初始化等长耗时逻辑，置标志回主循环处理更稳妥

## 9. 上位机侧配置

- 适配器：使用内置 `default`（工作区 `settings.toml` 中 `[serial] adapter = "default"`）
- 波特率：在工具栏波特率下拉框选择，与固件一致
- 若你的固件协议已固定、不能改：见 [ADAPTER.md](ADAPTER.md) 编写适配器

## 10. 调试建议

- **无数据**：确认 `START_TELEMETRY` 已发送（界面“开始遥测”）、波特率一致、
  TX/RX 交叉连接
- **CRC 错计数增长**：波特率不匹配、共地问题或噪声；上位机状态栏显示
  `CRC错 / 杂散字节` 计数
- **逻辑分析仪**：抓 `AA 55` 帧头即可快速定位；TELEMETRY 帧首 4 字节为
  tick_us（小端）
- **自检**：设备回环测试（TX 接 RX）可用上位机“循环发送 SET_SETPOINT”验证链路
