/* MediaStudio Core C ABI DRAFT — declaration only; no implementation supplied.
 * Version 0, 2026-11-09. Validate in actual platform SDK before shipping.
 * Public functions return status >= 0 for success, < 0 for errors.
 * All JSON messages use explicit-length UTF-8 bytes and schema_version: 0.
 */
#ifndef MEDIA_CORE_V0_H
#define MEDIA_CORE_V0_H
#include <stdint.h>
#include <stddef.h>
#ifdef __cplusplus
extern "C" {
#endif

typedef struct ms_engine ms_engine;
typedef struct ms_buffer {
    uint8_t *ptr;
    size_t len;
} ms_buffer;

enum {
    MS_OK = 0,
    MS_E_INVALID_ARGUMENT = -1,
    MS_E_INVALID_HANDLE = -2,
    MS_E_INVALID_UTF8 = -3,
    MS_E_UNSUPPORTED_SCHEMA = -4,
    MS_E_INTERNAL = -100
};

/* The engine owns the handle. Call on a background thread if shutdown may block. */
int32_t ms_engine_create(const uint8_t *config_json, size_t config_len, ms_engine **out_engine);
int32_t ms_engine_destroy(ms_engine *engine);

/* All out JSON buffers belong to the Rust allocator; free with ms_buffer_free. */
int32_t ms_get_version(ms_engine *engine, ms_buffer *out_json);
int32_t ms_get_capabilities(ms_engine *engine, ms_buffer *out_json);
int32_t ms_submit_job(ms_engine *engine, const uint8_t *request_json, size_t request_len, ms_buffer *out_json);
int32_t ms_get_job_snapshot(ms_engine *engine, const uint8_t *job_id, size_t job_id_len, ms_buffer *out_json);
int32_t ms_cancel_job(ms_engine *engine, const uint8_t *job_id, size_t job_id_len);
void ms_buffer_free(ms_buffer buffer);

#ifdef __cplusplus
}
#endif
#endif
