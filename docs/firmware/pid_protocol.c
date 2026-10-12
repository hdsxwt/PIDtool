/*
 * pid_protocol.c - PIDtool recommended serial protocol, device-side reference
 *
 * See pid_protocol.h for the wire format. Assumes a little-endian target
 * (Cortex-M); float bit patterns are copied with memcpy to avoid alignment
 * issues.
 */
#include "pid_protocol.h"

#include <string.h>

#define STATE_WAIT_SOF0 0u
#define STATE_WAIT_SOF1 1u
#define STATE_WAIT_LEN 2u
#define STATE_READ_BODY 3u
#define STATE_READ_CRC_LO 4u
#define STATE_READ_CRC_HI 5u

uint16_t pidp_crc16_ccitt(const uint8_t *data, size_t len)
{
    uint16_t crc = 0xFFFFu;
    size_t i;
    int bit;
    for (i = 0; i < len; i++) {
        crc ^= (uint16_t)data[i] << 8;
        for (bit = 0; bit < 8; bit++) {
            if (crc & 0x8000u) {
                crc = (uint16_t)((crc << 1) ^ 0x1021u);
            } else {
                crc = (uint16_t)(crc << 1);
            }
        }
    }
    return crc;
}

static void put_u16_le(uint8_t *dst, uint16_t value)
{
    dst[0] = (uint8_t)(value & 0xFFu);
    dst[1] = (uint8_t)(value >> 8);
}

static void put_u32_le(uint8_t *dst, uint32_t value)
{
    dst[0] = (uint8_t)(value & 0xFFu);
    dst[1] = (uint8_t)((value >> 8) & 0xFFu);
    dst[2] = (uint8_t)((value >> 16) & 0xFFu);
    dst[3] = (uint8_t)((value >> 24) & 0xFFu);
}

static void put_f32(uint8_t *dst, float value)
{
    memcpy(dst, &value, 4);
}

static float get_f32(const uint8_t *src)
{
    float value;
    memcpy(&value, src, 4);
    return value;
}

static uint16_t get_u16_le(const uint8_t *src)
{
    return (uint16_t)((uint16_t)src[0] | ((uint16_t)src[1] << 8));
}

size_t pidp_encode_frame(uint8_t msg_type, const uint8_t *payload, size_t payload_len,
                         uint8_t *out, size_t out_size)
{
    size_t body_len = 1u + payload_len;
    size_t frame_len = body_len + 5u;
    uint16_t crc;
    if (body_len > PIDP_MAX_BODY || out_size < frame_len) {
        return 0u;
    }
    out[0] = PIDP_SOF0;
    out[1] = PIDP_SOF1;
    out[2] = (uint8_t)body_len;
    out[3] = msg_type;
    if (payload_len > 0u && payload != NULL) {
        memcpy(&out[4], payload, payload_len);
    }
    crc = pidp_crc16_ccitt(&out[3], body_len);
    put_u16_le(&out[3 + body_len], crc);
    return frame_len;
}

int pidp_send_telemetry(pidp_write_fn write, void *user, uint32_t tick_us,
                        float setpoint, float feedback, float u,
                        float up, float ui, float ud)
{
    uint8_t payload[28];
    uint8_t frame[34];
    size_t frame_len;
    if (write == NULL) {
        return -1;
    }
    put_u32_le(&payload[0], tick_us);
    put_f32(&payload[4], setpoint);
    put_f32(&payload[8], feedback);
    put_f32(&payload[12], u);
    put_f32(&payload[16], up);
    put_f32(&payload[20], ui);
    put_f32(&payload[24], ud);
    frame_len = pidp_encode_frame(PIDP_MSG_TELEMETRY, payload, sizeof(payload),
                                  frame, sizeof(frame));
    if (frame_len == 0u) {
        return -1;
    }
    write(frame, frame_len, user);
    return 0;
}

int pidp_send_gains(pidp_write_fn write, void *user, float kp, float ki, float kd)
{
    uint8_t payload[12];
    uint8_t frame[18];
    size_t frame_len;
    if (write == NULL) {
        return -1;
    }
    put_f32(&payload[0], kp);
    put_f32(&payload[4], ki);
    put_f32(&payload[8], kd);
    frame_len = pidp_encode_frame(PIDP_MSG_GAINS, payload, sizeof(payload), frame, sizeof(frame));
    if (frame_len == 0u) {
        return -1;
    }
    write(frame, frame_len, user);
    return 0;
}

int pidp_send_ack(pidp_write_fn write, void *user, uint8_t acked_type, uint8_t status)
{
    uint8_t payload[2];
    uint8_t frame[8];
    size_t frame_len;
    if (write == NULL) {
        return -1;
    }
    payload[0] = acked_type;
    payload[1] = status;
    frame_len = pidp_encode_frame(PIDP_MSG_ACK, payload, sizeof(payload), frame, sizeof(frame));
    if (frame_len == 0u) {
        return -1;
    }
    write(frame, frame_len, user);
    return 0;
}

int pidp_send_text(pidp_write_fn write, void *user, const char *text)
{
    uint8_t frame[256];
    size_t text_len;
    size_t frame_len;
    if (write == NULL || text == NULL) {
        return -1;
    }
    text_len = strlen(text);
    if (text_len > 250u) {
        text_len = 250u;
    }
    frame_len = pidp_encode_frame(PIDP_MSG_TEXT, (const uint8_t *)text, text_len,
                                  frame, sizeof(frame));
    if (frame_len == 0u) {
        return -1;
    }
    write(frame, frame_len, user);
    return 0;
}

void pidp_parser_init(pidp_parser_t *p)
{
    memset(p, 0, sizeof(*p));
    p->state = STATE_WAIT_SOF0;
}

static void dispatch(const pidp_parser_t *p, const pidp_callbacks_t *cb, int *dispatched)
{
    uint8_t type = p->body[0];
    const uint8_t *pl = &p->body[1];
    uint8_t n = (uint8_t)(p->body_len - 1u);

    switch (type) {
    case PIDP_CMD_SET_GAINS:
        if (n == 12u && cb->on_set_gains != NULL) {
            cb->on_set_gains(get_f32(&pl[0]), get_f32(&pl[4]), get_f32(&pl[8]), cb->user);
            (*dispatched)++;
        }
        break;
    case PIDP_CMD_SET_SETPOINT:
        if (n == 4u && cb->on_set_setpoint != NULL) {
            cb->on_set_setpoint(get_f32(&pl[0]), cb->user);
            (*dispatched)++;
        }
        break;
    case PIDP_CMD_SET_MODE:
        if (n == 1u && cb->on_set_mode != NULL) {
            cb->on_set_mode(pl[0], cb->user);
            (*dispatched)++;
        }
        break;
    case PIDP_CMD_START_TELEMETRY:
        if (n == 2u && cb->on_start_telemetry != NULL) {
            cb->on_start_telemetry(get_u16_le(&pl[0]), cb->user);
            (*dispatched)++;
        }
        break;
    case PIDP_CMD_STOP_TELEMETRY:
        if (n == 0u && cb->on_stop_telemetry != NULL) {
            cb->on_stop_telemetry(cb->user);
            (*dispatched)++;
        }
        break;
    case PIDP_CMD_GET_GAINS:
        if (n == 0u && cb->on_get_gains != NULL) {
            cb->on_get_gains(cb->user);
            (*dispatched)++;
        }
        break;
    default:
        break;
    }
}

static void handle_byte(pidp_parser_t *p, uint8_t b, const pidp_callbacks_t *cb,
                        int *dispatched)
{
    switch (p->state) {
    case STATE_WAIT_SOF0:
        if (b == PIDP_SOF0) {
            p->state = STATE_WAIT_SOF1;
        } else {
            p->garbage_bytes++;
        }
        break;
    case STATE_WAIT_SOF1:
        if (b == PIDP_SOF1) {
            p->state = STATE_WAIT_LEN;
        } else {
            p->garbage_bytes++;
            p->state = (b == PIDP_SOF0) ? STATE_WAIT_SOF1 : STATE_WAIT_SOF0;
        }
        break;
    case STATE_WAIT_LEN:
        if (b == 0u || b > PIDP_MAX_BODY) {
            p->garbage_bytes++;
            p->state = STATE_WAIT_SOF0;
        } else {
            p->body_len = b;
            p->pos = 0u;
            p->state = STATE_READ_BODY;
        }
        break;
    case STATE_READ_BODY:
        p->body[p->pos++] = b;
        if (p->pos >= p->body_len) {
            p->state = STATE_READ_CRC_LO;
        }
        break;
    case STATE_READ_CRC_LO:
        p->crc_lo = b;
        p->state = STATE_READ_CRC_HI;
        break;
    case STATE_READ_CRC_HI: {
        uint16_t crc_rx = (uint16_t)((uint16_t)p->crc_lo | ((uint16_t)b << 8));
        if (pidp_crc16_ccitt(p->body, p->body_len) == crc_rx) {
            p->frames++;
            if (cb != NULL) {
                dispatch(p, cb, dispatched);
            }
        } else {
            p->crc_errors++;
        }
        p->state = STATE_WAIT_SOF0;
        break;
    }
    default:
        p->state = STATE_WAIT_SOF0;
        break;
    }
}

int pidp_parser_feed(pidp_parser_t *p, const uint8_t *data, size_t len,
                     const pidp_callbacks_t *cb)
{
    int dispatched = 0;
    size_t i;
    for (i = 0; i < len; i++) {
        handle_byte(p, data[i], cb, &dispatched);
    }
    return dispatched;
}
