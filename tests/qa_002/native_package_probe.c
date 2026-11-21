/* Test-only local IP denial witness; only the main executable reports. */
#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <mach-o/dyld.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>
static pthread_t task;
static int started;
static void *probe(void *unused) {
    (void)unused;
    struct timespec pause = {0, 250000000}; (void)nanosleep(&pause, NULL);
    int fd = socket(AF_INET, SOCK_STREAM, 0), rc = -1, code = errno;
    if (fd >= 0) {
        struct sockaddr_in address = {0}; address.sin_len = sizeof(address);
        address.sin_family = AF_INET; address.sin_port = htons(9);
        (void)inet_pton(AF_INET, "127.0.0.1", &address.sin_addr);
        rc = connect(fd, (struct sockaddr *)&address, sizeof(address)); code = errno; close(fd);
    }
    const char *path = getenv("QA002_NETWORK_PROBE");
    if (path && path[0] == '/') {
        int report = open(path, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0600);
        if (report >= 0) {
            FILE *file = fdopen(report, "w");
            if (file) {
                fprintf(file, "{\"pid\":%d,\"socket_created\":%s,\"connect_return\":%d,\"errno\":%d,\"network_denied\":%s}\n",
                        getpid(), fd >= 0 ? "true" : "false", rc, code,
                        rc < 0 && (code == EPERM || code == EACCES) ? "true" : "false");
                fclose(file);
            } else close(report);
        }
    }
    return NULL;
}
__attribute__((constructor)) static void begin(void) {
    const char *expected = getenv("QA002_EXPECTED_EXECUTABLE");
    char executable[4096]; uint32_t size = sizeof(executable);
    if (!expected || _NSGetExecutablePath(executable, &size) != 0) return;
    const char *name = strrchr(executable, '/'); name = name ? name + 1 : executable;
    if (strcmp(name, expected) != 0) return;
    started = pthread_create(&task, NULL, probe, NULL) == 0;
}
__attribute__((destructor)) static void finish(void) { if (started) pthread_join(task, NULL); }
