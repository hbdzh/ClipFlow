#include "media_core_v0.h"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

_Static_assert(offsetof(ms_buffer, ptr) == 0, "buffer pointer is first");
_Static_assert(offsetof(ms_buffer, len) >= sizeof(uint8_t *), "buffer length follows pointer");
_Static_assert(sizeof(((ms_buffer *)0)->len) == sizeof(size_t), "length uses platform size_t");
_Static_assert(MS_E_INVALID_JSON == -5, "stable JSON error code");
_Static_assert(MS_E_BUSY == -7, "stable busy error code");

int main(void) {
    const uint8_t config[] = "{\"schema_version\":0}";
    ms_engine *engine = NULL;
    ms_buffer output = {NULL, 0};
    int32_t (*create_fn)(const uint8_t *, size_t, ms_engine **) = ms_engine_create;
    int32_t (*destroy_fn)(ms_engine *) = ms_engine_destroy;
    void (*free_fn)(ms_buffer) = ms_buffer_free;
    assert(create_fn(config, sizeof(config) - 1, &engine) == MS_OK);
    assert(engine != NULL);
    assert(ms_get_version(engine, &output) == MS_OK);
    assert(output.ptr != NULL && output.len > 0);
    assert(destroy_fn(engine) == MS_OK);
    /* The output owns its allocation independently of the destroyed engine. */
    char *terminated = malloc(output.len + 1);
    assert(terminated != NULL);
    memcpy(terminated, output.ptr, output.len);
    terminated[output.len] = '\0';
    assert(strstr(terminated, "schema_version") != NULL);
    free(terminated);
    free_fn(output);
    ms_buffer empty = {NULL, 0};
    free_fn(empty);
    assert(ms_engine_destroy(NULL) == MS_E_INVALID_HANDLE);
    printf("C ABI linked/runtime PASS: pointer=%zu size_t=%zu buffer=%zu align=%zu len_offset=%zu\n",
           sizeof(void *), sizeof(size_t), sizeof(ms_buffer), _Alignof(ms_buffer), offsetof(ms_buffer, len));
    return 0;
}
