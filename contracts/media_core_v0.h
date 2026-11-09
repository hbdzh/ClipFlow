/* MediaStudio Core C ABI V0 — CORE-001 mock baseline, 2026-11-09.
 * Actual native-client/SDK integration is not verified by this declaration.
 * Public functions return status >= 0 for success, < 0 for errors.
 * JSON messages use explicit-length UTF-8 bytes and schema_version: 0.
 * job_id input is plain UTF-8, not JSON. No input requires NUL termination.
 * Same-engine calls must be serialized by the caller; destroy is exclusive.
 * Different engines and independently owned output buffers may use different
 * threads. All pointer storage must be valid, aligned and non-overlapping.
 * Non-null dangling pointers, double destroy/free, aliases and changed buffer
 * lengths are caller violations; errors cannot make invalid pointers safe.
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
    MS_E_INVALID_JSON = -5,
    MS_E_NOT_FOUND = -6,
    MS_E_BUSY = -7,
    MS_E_UNSUPPORTED_OPERATION = -8,
    MS_E_LIMIT_EXCEEDED = -9,
    MS_E_INTERNAL = -100
};

/* Inputs are borrowed for the call only, with non-NULL ptr and positive len.
 * JSON limit: 65536 bytes; job_id limit: 128 bytes, checked before dereference.
 * Over-limit lengths take precedence and return LIMIT_EXCEEDED; otherwise all
 * three empty/null combinations are INVALID_ARGUMENT.
 * config: {"schema_version":0,"max_jobs":64}; max_jobs optional, 1..256.
 * On error out_engine is NULL. Caller owns one live handle until destroy OK.
 * destroy returns BUSY without consuming an engine with nonterminal mock jobs.
 * Cancel and poll to terminal before retrying. Never block a UI on shutdown.
 */
int32_t ms_engine_create(const uint8_t *config_json, size_t config_len, ms_engine **out_engine);
int32_t ms_engine_destroy(ms_engine *engine);

/* out_json must be fresh output storage or {NULL,0}, with no unfreed old buffer.
 * On any error it is {NULL,0}; no diagnostic JSON buffer is allocated on error.
 * On success Rust allocates bytes; do not edit ptr/len or free with C free().
 * Each buffer is independent of engine lifetime and freed exactly once below.
 * Only operation "mock" is accepted; real probe/trim are unavailable.
 * See README.md for strict fields, deterministic polling and panic policy.
 */
int32_t ms_get_version(ms_engine *engine, ms_buffer *out_json);
int32_t ms_get_capabilities(ms_engine *engine, ms_buffer *out_json);
int32_t ms_submit_job(ms_engine *engine, const uint8_t *request_json, size_t request_len, ms_buffer *out_json);
int32_t ms_get_job_snapshot(ms_engine *engine, const uint8_t *job_id, size_t job_id_len, ms_buffer *out_json);
int32_t ms_cancel_job(ms_engine *engine, const uint8_t *job_id, size_t job_id_len);
void ms_buffer_free(ms_buffer buffer);

/* ms_buffer_free accepts {NULL,0}, or an unchanged, live Rust output buffer.
 * After MS_E_INTERNAL caused by an unwinding panic the engine is quarantined;
 * only destroy is permitted. Abort/OOM/invalid caller pointers are not caught.
 */

#ifdef __cplusplus
}
#endif
#endif
