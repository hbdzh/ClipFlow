/* Research-only macOS interposer: deterministic same-UID race in a disposable
 * media-ai test fixture. Never load this into an application or user job. */
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>

extern int renameatx_np(int, const char *, int, const char *, unsigned int);
static int injected_rename(int from_fd, const char *from, int to_fd,
                           const char *to, unsigned int flags) {
    if (strcmp(from, "ready") == 0 && strcmp(to, "result") == 0) {
        pid_t pid = fork();
        if (pid == 0) {
            if (renameat(from_fd, "ready", from_fd, "original-ready") != 0 ||
                mkdirat(from_fd, "ready", 0700) != 0) _exit(21);
            int dir = openat(from_fd, "ready", O_RDONLY | O_DIRECTORY);
            if (dir < 0) _exit(22);
            const char *names[] = {"vocals.wav", "accompaniment.wav"};
            for (int i = 0; i < 2; ++i) {
                int fd = openat(dir, names[i], O_WRONLY | O_CREAT | O_EXCL, 0600);
                if (fd < 0 || write(fd, "substituted", 11) != 11 || close(fd) != 0) _exit(23);
            }
            close(dir);
            _exit(0);
        }
        int status = 0;
        if (pid < 0 || waitpid(pid, &status, 0) != pid || !WIFEXITED(status) || WEXITSTATUS(status) != 0) {
            _exit(24);
        }
        fprintf(stderr, "AI001_TEST_INTERPOSITION_COMPLETED\n");
    }
    return renameatx_np(from_fd, from, to_fd, to, flags);
}
__attribute__((used)) static struct { const void *replacement; const void *original; }
interpose __attribute__((section("__DATA,__interpose"))) = {
    (const void *)injected_rename, (const void *)renameatx_np
};
