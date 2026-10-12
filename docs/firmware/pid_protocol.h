/*
 * pid_protocol.h - PIDtool recommended serial protocol, device-side reference
 *
 * Frame format (host <-> MCU):
 *   AA 55 | LEN | TYPE | PAYLOAD | CRC16_LO CRC16_HI
 *   LEN    = 1 + payload length (TYPE included), range 1..251
 *   CRC16  = CRC16-CCITT-FALSE (poly 0x1021, init 0xFFFF, no reflection)
 *            computed over TYPE + PAYLOAD, little-endian on the wire
 *
 * Multi-byte integers and floats are little-endian (Cortex-M native).
 * No dependencies, C99, safe to call the parser from an UART RX interrupt.
 */
#ifndef PID_PROTOCOL_H
#define PID_PROTOCOL_H

#include <stddef.h>
#include <stdint.h>

#define PIDP_SOF0 0xAAu
#define PIDP_SOF1 0x55u
#define PIDP_MAX_BODY 251u

/* device -> host */
#define PIDP_MSG_TELEMETRY 0x01u
#define PIDP_MSG_GAINS 0x20u
#define PIDP_MSG_TEXT 0x30u
#define PIDP_MSG_ACK 0x7Fu

/* host -> device */
#define PIDP_CMD_SET_GAINS 0x10u
#define PIDP_CMD_SET_SETPOINT 0x11u
#define PIDP_CMD_SET_MODE 0x12u
#define PIDP_CMD_START_TELEMETRY 0x13u
#define PIDP_CMD_STOP_TELEMETRY 0x14u
#define PIDP_CMD_GET_GAINS 0x15u

#define PIDP_MODE_IDLE 0u
#define PIDP_MODE_RUN 1u

#define PIDP_ACK_OK 0u

/* CRC16-CCITT-FALSE, check value: "123456789" -> 0x29B1 */
uint16_t pidp_crc16_ccitt(const uint8_t *data, size_t len);

/* Write callback, e.g. wraps HAL_UART_Transmit / DMA ring buffer. */
typedef void (*pidp_write_fn)(const uint8_t *data, size_t len, void *user);

/*
 * Build a full frame into out. Returns total frame length on success,
 * 0 if out_size is too small or payload is longer than 250 bytes.
 */
size_t pidp_encode_frame(uint8_t msg_type, const uint8_t *payload, size_t payload_len,
                         uint8_t *out, size_t out_size);

/* Convenience senders. Return 0 on success, -1 on encoding error. */
int pidp_send_telemetry(pidp_write_fn write, void *user, uint32_t tick_us,
                        float setpoint, float feedback, float u,
                        float up, float ui, float ud);
int pidp_send_gains(pidp_write_fn write, void *user, float kp, float ki, float kd);
int pidp_send_ack(pidp_write_fn write, void *user, uint8_t acked_type, uint8_t status);
int pidp_send_text(pidp_write_fn write, void *user, const char *text);

/*
 * Command callbacks. Unused callbacks may be NULL; unknown messages and
 * messages with unexpected payload lengths are ignored.
 */
typedef struct {
    void (*on_set_gains)(float kp, float ki, float kd, void *user);
    void (*on_set_setpoint)(float setpoint, void *user);
    void (*on_set_mode)(uint8_t mode, void *user);
    void (*on_start_telemetry)(uint16_t period_ms, void *user);
    void (*on_stop_telemetry)(void *user);
    void (*on_get_gains)(void *user);
    void *user;
} pidp_callbacks_t;

/* Streaming parser state. Kept in static storage by the caller. */
typedef struct {
    uint8_t state;
    uint8_t body[PIDP_MAX_BODY];
    uint8_t body_len;
    uint8_t pos;
    uint8_t crc_lo;
    uint32_t frames;
    uint32_t crc_errors;
    uint32_t garbage_bytes;
} pidp_parser_t;

void pidp_parser_init(pidp_parser_t *p);

/*
 * Feed received bytes. Returns the number of command callbacks invoked.
 * Safe to call from an ISR if the callbacks only set flags / scalar values.
 */
int pidp_parser_feed(pidp_parser_t *p, const uint8_t *data, size_t len,
                     const pidp_callbacks_t *cb);

#endif /* PID_PROTOCOL_H */
