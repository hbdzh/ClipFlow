/* Test-only FFmpeg gate. It never fabricates a media result or encoder list. */
#define _DARWIN_C_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>
#include "gate_config.h"

#define GATE_NAME ".qa002-full-disk-gate"
#define GATE_BYTES 4096

static double monotonic_seconds(void) {
    struct timespec value;
    if (clock_gettime(CLOCK_MONOTONIC, &value) != 0) return -1.0;
    return (double)value.tv_sec + (double)value.tv_nsec / 1000000000.0;
}

static bool is_selected_encode(int argc, char **argv) {
    bool encoder = false;
    for (int index = 1; index + 1 < argc; ++index) {
        if ((strcmp(argv[index], "-c:v") == 0 || strcmp(argv[index], "-c:v:0") == 0)
                && strcmp(argv[index + 1], "libx264") == 0) encoder = true;
    }
    if (!encoder || argc < 2) return false;
    const char *output = argv[argc - 1];
    const size_t prefix_length = strlen(OUTPUT_PREFIX);
    if (strncmp(output, OUTPUT_PREFIX, prefix_length) != 0 || output[prefix_length] != '/') return false;
    const char *stage = output + prefix_length + 1;
    if (strncmp(stage, ".mediastudio-", 13) != 0) return false;
    const char *slash = strchr(stage, '/');
    return slash != NULL && strcmp(slash, "/output.mp4") == 0;
}

static void unlink_owned(const char *path, const struct stat *owned) {
    struct stat current;
    if (lstat(path, &current) == 0 && current.st_dev == owned->st_dev && current.st_ino == owned->st_ino) {
        (void)unlink(path);
    }
}

static int gate(const char *output) {
    char path[PATH_MAX];
    const char *slash = strrchr(output, '/');
    if (slash == NULL) return 125;
    const size_t parent_length = (size_t)(slash - output);
    if (parent_length + sizeof("/" GATE_NAME) > sizeof(path)) return 125;
    memcpy(path, output, parent_length);
    memcpy(path + parent_length, "/" GATE_NAME, sizeof("/" GATE_NAME));
    const int descriptor = open(path, O_RDWR | O_CREAT | O_EXCL | O_NOFOLLOW, 0600);
    if (descriptor < 0) return 125;
    struct stat owned;
    if (fstat(descriptor, &owned) != 0) { close(descriptor); return 125; }
    char block[GATE_BYTES] = {0};
    (void)snprintf(block, sizeof(block), "0 QA002_FULL_DISK_GATE pid=%ld\n", (long)getpid());
    size_t written = 0;
    while (written < sizeof(block)) {
        const ssize_t count = write(descriptor, block + written, sizeof(block) - written);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) { unlink_owned(path, &owned); close(descriptor); return 125; }
        written += (size_t)count;
    }
    if (fsync(descriptor) != 0) { unlink_owned(path, &owned); close(descriptor); return 125; }
    const double start = monotonic_seconds();
    if (start < 0.0) { unlink_owned(path, &owned); close(descriptor); return 125; }
    const struct timespec delay = {.tv_sec = 0, .tv_nsec = 20000000};
    int result = 125;
    while (true) {
        char state = 0;
        const ssize_t count = pread(descriptor, &state, 1, 0);
        if (count == 1 && state == '1') { result = 0; break; }
        const double now = monotonic_seconds();
        if (now < 0.0 || now - start > 120.0) break;
        if (count != 1 && errno != EINTR) break;
        (void)nanosleep(&delay, NULL);
    }
    unlink_owned(path, &owned);
    close(descriptor);
    return result;
}

int main(int argc, char **argv) {
    if (is_selected_encode(argc, argv) && gate(argv[argc - 1]) != 0) {
        fputs("Controlled QA encoder gate did not complete.\n", stderr);
        return 125;
    }
    argv[0] = (char *)REAL_FFMPEG;
    execv(REAL_FFMPEG, argv);
    fputs("Controlled QA media tool could not start.\n", stderr);
    return 126;
}
