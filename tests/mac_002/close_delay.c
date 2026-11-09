/* QA-only DYLD interposer: delay one real cancellation, then forward unchanged.
 * Never linked into the app or Core; no fake snapshots, buffers or results. */
#include <errno.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#include <unistd.h>
#include "media_core_v0.h"

static atomic_bool delayed_once = 0;
static double monotonic_seconds(void) {
    struct timespec value;
    clock_gettime(CLOCK_MONOTONIC, &value);
    return (double)value.tv_sec + (double)value.tv_nsec / 1000000000.0;
}
static void witness(const char *phase) {
    const char *path = getenv("MAC002_CANCEL_DELAY_MARKER");
    if (!path) return;
    FILE *file = fopen(path, "a");
    if (!file) return;
    fprintf(file, "{\"phase\":\"%s\",\"pid\":%d,\"monotonic_seconds\":%.9f}\n", phase, getpid(), monotonic_seconds());
    fflush(file); fsync(fileno(file)); fclose(file);
}
static int32_t delayed_cancel(ms_engine *engine, const uint8_t *job, size_t length) {
    const char *ready = getenv("MAC002_EXPORT_READY_MARKER");
    if (getenv("MAC002_CANCEL_DELAY_MARKER") && ready && access(ready, F_OK) == 0 && !atomic_exchange(&delayed_once, 1)) {
        witness("real-cancel-delayed");
        struct timespec remaining = {17, 0};
        while (nanosleep(&remaining, &remaining) != 0 && errno == EINTR) {}
        witness("forwarding-real-cancel");
    }
    /* dyld excludes calls originating in this interposing image from its own
     * replacement. Calling the imported symbol directly forwards to Core. */
    return ms_cancel_job(engine, job, length);
}
__attribute__((used)) static struct {
    const void *replacement;
    const void *replacee;
} interposers[] __attribute__((section("__DATA,__interpose"))) = {
    {(const void *)delayed_cancel, (const void *)ms_cancel_job}
};
