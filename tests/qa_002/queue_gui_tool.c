/* Local QA forwarding tool. No media result or Core state is faked. */
#define _DARWIN_C_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include "queue_gui_config.h"

int main(int argc, char **argv) {
    if (argc < 1 || argc > 1024) return 125;
    int input = -1, encode = 0, slow_probe = 0;
    for (int i = 1; i < argc; ++i) {
        if (strcmp(argv[i], "-i") == 0) input = i;
        if (strcmp(argv[i], "libx264") == 0) encode = 1;
        if (strstr(argv[i], "/slow-probe.mp4") != NULL) slow_probe = 1;
    }
    if (IS_PROBE && slow_probe) {
        struct timespec delay = {.tv_sec = 22, .tv_nsec = 0};
        while (nanosleep(&delay, &delay) != 0 && errno == EINTR) {}
    }
    const char *output = argv[argc - 1];
    size_t prefix = strlen(OUTPUT_PREFIX);
    if (!IS_PROBE && encode && input > 0 &&
        strncmp(output, OUTPUT_PREFIX, prefix) == 0 && output[prefix] == '/') {
        char marker[PATH_MAX];
        const char *slash = strrchr(output, '/');
        size_t length = slash == NULL ? 0 : (size_t)(slash - output);
        while (length > 0 && output[length - 1] != '/') --length;
        if (length == 0 || length + 64 > sizeof(marker)) return 125;
        memcpy(marker, output, length);
        (void)snprintf(marker + length, sizeof(marker) - length, ".qa002-encode-%ld", (long)getpid());
        int fd = open(marker, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0600);
        if (fd < 0) return 125;
        char text[PATH_MAX + 64]; int bytes = snprintf(text, sizeof(text), "%ld\n%s\n", (long)getpid(), output);
        if (write(fd, text, (size_t)bytes) != bytes) { close(fd); return 125; }
        close(fd);
        char **args = calloc((size_t)argc + 3, sizeof(char *));
        if (args == NULL) return 125;
        int n = 0;
        for (int i = 0; i < argc; ++i) {
            if (i == input) { args[n++] = "-readrate"; args[n++] = "0.25"; }
            args[n++] = i == 0 ? (char *)REAL_TOOL : argv[i];
        }
        execv(REAL_TOOL, args);
        free(args); return 126;
    }
    argv[0] = (char *)REAL_TOOL;
    execv(REAL_TOOL, argv);
    return 126;
}
