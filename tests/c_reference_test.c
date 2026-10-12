/*
 * Host-side cross-check program for the STM32 reference implementation.
 * Built and driven by tests/test_c_reference.py (gcc required).
 *
 * Usage:
 *   c_reference_test encode          -> prints CRC/FRAME/SEND hex lines
 *   c_reference_test parse <hex>     -> feeds hex stream byte by byte,
 *                                       prints CMD lines and PARSED:frames:crc_errors
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "pid_protocol.h"

static void put_f32(uint8_t *dst, float value)
{
    memcpy(dst, &value, 4);
}

static void put_u32_le(uint8_t *dst, uint32_t value)
{
    dst[0] = (uint8_t)(value & 0xFFu);
    dst[1] = (uint8_t)((value >> 8) & 0xFFu);
    dst[2] = (uint8_t)((value >> 16) & 0xFFu);
    dst[3] = (uint8_t)((value >> 24) & 0xFFu);
}

static void put_u16_le(uint8_t *dst, uint16_t value)
{
    dst[0] = (uint8_t)(value & 0xFFu);
    dst[1] = (uint8_t)(value >> 8);
}

static void emit_hex_line(const char *prefix, const uint8_t *data, size_t len)
{
    size_t i;
    printf("%s:", prefix);
    for (i = 0; i < len; i++) {
        printf("%02X", data[i]);
    }
    printf("\n");
}

static void emit_frame(const uint8_t *frame, size_t len)
{
    emit_hex_line("FRAME", frame, len);
}

static uint8_t capture[512];
static size_t capture_len = 0u;

static void capture_write(const uint8_t *data, size_t len, void *user)
{
    (void)user;
    if (capture_len + len <= sizeof(capture)) {
        memcpy(&capture[capture_len], data, len);
        capture_len += len;
    }
}

static void on_set_gains(float kp, float ki, float kd, void *user)
{
    (void)user;
    printf("CMD:10:%f:%f:%f\n", (double)kp, (double)ki, (double)kd);
}

static void on_set_setpoint(float setpoint, void *user)
{
    (void)user;
    printf("CMD:11:%f\n", (double)setpoint);
}

static void on_set_mode(uint8_t mode, void *user)
{
    (void)user;
    printf("CMD:12:%u\n", (unsigned)mode);
}

static void on_start_telemetry(uint16_t period_ms, void *user)
{
    (void)user;
    printf("CMD:13:%u\n", (unsigned)period_ms);
}

static void on_stop_telemetry(void *user)
{
    (void)user;
    printf("CMD:14\n");
}

static void on_get_gains(void *user)
{
    (void)user;
    printf("CMD:15\n");
}

static int run_encode(void)
{
    uint8_t frame[300];
    uint8_t payload[64];
    size_t n;

    printf("CRC:%04X\n", pidp_crc16_ccitt((const uint8_t *)"123456789", 9));

    put_u32_le(&payload[0], 0x01020304u);
    put_f32(&payload[4], 1.0f);
    put_f32(&payload[8], 0.5f);
    put_f32(&payload[12], 2.0f);
    put_f32(&payload[16], 1.0f);
    put_f32(&payload[20], 0.5f);
    put_f32(&payload[24], 0.25f);
    n = pidp_encode_frame(PIDP_MSG_TELEMETRY, payload, 28u, frame, sizeof(frame));
    emit_frame(frame, n);

    put_f32(&payload[0], 1.5f);
    put_f32(&payload[4], 2.5f);
    put_f32(&payload[8], 0.25f);
    n = pidp_encode_frame(PIDP_MSG_GAINS, payload, 12u, frame, sizeof(frame));
    emit_frame(frame, n);

    put_f32(&payload[0], 1.5f);
    put_f32(&payload[4], 2.5f);
    put_f32(&payload[8], 0.25f);
    n = pidp_encode_frame(PIDP_CMD_SET_GAINS, payload, 12u, frame, sizeof(frame));
    emit_frame(frame, n);

    put_f32(&payload[0], 3.25f);
    n = pidp_encode_frame(PIDP_CMD_SET_SETPOINT, payload, 4u, frame, sizeof(frame));
    emit_frame(frame, n);

    payload[0] = PIDP_MODE_RUN;
    n = pidp_encode_frame(PIDP_CMD_SET_MODE, payload, 1u, frame, sizeof(frame));
    emit_frame(frame, n);

    put_u16_le(&payload[0], 5u);
    n = pidp_encode_frame(PIDP_CMD_START_TELEMETRY, payload, 2u, frame, sizeof(frame));
    emit_frame(frame, n);

    n = pidp_encode_frame(PIDP_CMD_STOP_TELEMETRY, NULL, 0u, frame, sizeof(frame));
    emit_frame(frame, n);

    n = pidp_encode_frame(PIDP_CMD_GET_GAINS, NULL, 0u, frame, sizeof(frame));
    emit_frame(frame, n);

    payload[0] = PIDP_CMD_SET_GAINS;
    payload[1] = PIDP_ACK_OK;
    n = pidp_encode_frame(PIDP_MSG_ACK, payload, 2u, frame, sizeof(frame));
    emit_frame(frame, n);

    n = pidp_encode_frame(PIDP_MSG_TEXT, (const uint8_t *)"hi", 2u, frame, sizeof(frame));
    emit_frame(frame, n);

    capture_len = 0u;
    (void)pidp_send_telemetry(capture_write, NULL, 123456u, 2.0f, 1.9f, 3.0f, 2.0f, 0.9f, 0.1f);
    emit_hex_line("SEND", capture, capture_len);
    return 0;
}

static int hex_value(int c)
{
    if (c >= '0' && c <= '9') {
        return c - '0';
    }
    if (c >= 'a' && c <= 'f') {
        return c - 'a' + 10;
    }
    if (c >= 'A' && c <= 'F') {
        return c - 'A' + 10;
    }
    return -1;
}

static int run_parse(void)
{
    static uint8_t buffer[65536];
    size_t len = 0u;
    int hi = -1;
    int c;
    size_t i;
    pidp_parser_t parser;
    pidp_callbacks_t callbacks;

    while ((c = getchar()) != EOF) {
        int value = hex_value(c);
        if (value < 0) {
            continue;
        }
        if (hi < 0) {
            hi = value;
        } else {
            if (len < sizeof(buffer)) {
                buffer[len++] = (uint8_t)((hi << 4) | value);
            }
            hi = -1;
        }
    }

    callbacks.on_set_gains = on_set_gains;
    callbacks.on_set_setpoint = on_set_setpoint;
    callbacks.on_set_mode = on_set_mode;
    callbacks.on_start_telemetry = on_start_telemetry;
    callbacks.on_stop_telemetry = on_stop_telemetry;
    callbacks.on_get_gains = on_get_gains;
    callbacks.user = NULL;

    pidp_parser_init(&parser);
    for (i = 0; i < len; i++) {
        pidp_parser_feed(&parser, &buffer[i], 1u, &callbacks);
    }
    printf("PARSED:%u:%u\n", (unsigned)parser.frames, (unsigned)parser.crc_errors);
    return 0;
}

int main(int argc, char **argv)
{
    if (argc < 2) {
        fprintf(stderr, "usage: %s encode|parse\n", argv[0]);
        return 2;
    }
    if (strcmp(argv[1], "encode") == 0) {
        return run_encode();
    }
    if (strcmp(argv[1], "parse") == 0) {
        return run_parse();
    }
    fprintf(stderr, "unknown mode: %s\n", argv[1]);
    return 2;
}
