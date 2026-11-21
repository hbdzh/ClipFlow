/* QA only: forward to the real CPU worker, inject filesystem faults around it. */
#define _DARWIN_C_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>
#include "ai_atomic_config.h"

static double now(void) {
    struct timespec t; if (clock_gettime(CLOCK_MONOTONIC, &t) != 0) return -1;
    return (double)t.tv_sec + (double)t.tv_nsec / 1e9;
}
static long bytes(const char *path) { struct stat s; return lstat(path, &s) == 0 ? (long)s.st_size : -1; }
static void audit(const char *phase, const char *stage, pid_t child) {
    FILE *f = fopen(AUDIT_PATH, "a"); if (!f) return;
    char vox[4096], acc[4096];
    (void)snprintf(vox, sizeof(vox), "%s/vocals.wav", stage);
    (void)snprintf(acc, sizeof(acc), "%s/accompaniment.wav", stage);
    fprintf(f, "{\"phase\":\"%s\",\"mode\":\"%s\",\"pid\":%ld,\"child_pid\":%ld,\"stage\":\"%s\",\"vocals_bytes\":%ld,\"accompaniment_bytes\":%ld,\"clock_monotonic\":%.9f}\n",
        phase, FAULT_MODE, (long)getpid(), (long)child, stage, bytes(vox), bytes(acc), now());
    (void)fflush(f); (void)fsync(fileno(f)); (void)fclose(f);
}
static int wait_gate(const char *stage) {
    audit("gate-ready", stage, 0);
    double end = now() + 120;
    while (access(RELEASE_PATH, F_OK) != 0) {
        if (now() < 0 || now() > end) return 124;
        struct timespec t = {0, 10000000}; (void)nanosleep(&t, NULL);
    }
    return 0;
}
int main(int argc, char **argv) {
    const char *stage = NULL;
    for (int i=1; i+1<argc; ++i) if (!strcmp(argv[i], "--staging")) stage=argv[i+1];
    if (!stage || stage[0] != '/' || argc != 11) return 125;
    int inject = access(AUDIT_PATH, F_OK) != 0;
    char path[4096];
    if (inject && !strcmp(FAULT_MODE, "disk-full") && wait_gate(stage)) return 124;
    pid_t child=fork(); if(child<0)return 125;
    if(child==0) {
        if(inject && (!strcmp(FAULT_MODE,"second-create") || !strcmp(FAULT_MODE,"second-write")) && setenv("DYLD_INSERT_LIBRARIES",SECOND_OPEN_LIBRARY,1)!=0)_exit(125);
        argv[0]=(char *)REAL_WORKER; execv(REAL_WORKER,argv); _exit(126);
    }
    audit("real-worker-started",stage,child);
    int status;
    while(waitpid(child,&status,0)<0) if(errno!=EINTR)return 125;
    audit("real-worker-reaped",stage,child);
    if(!WIFEXITED(status)) return 1;
    if(WEXITSTATUS(status)!=0) return WEXITSTATUS(status);
    if(inject && !strcmp(FAULT_MODE,"second-invalid")) {
        (void)snprintf(path,sizeof(path),"%s/accompaniment.wav",stage);
        int fd=open(path,O_RDWR|O_NOFOLLOW); if(fd<0)return 125;
        uint32_t nan=0x7fc00000;
        if(pwrite(fd,&nan,sizeof(nan),44)!=(ssize_t)sizeof(nan)){close(fd);return 125;}
        (void)fsync(fd);close(fd);audit("second-output-corrupted-nan",stage,child);
    }
    if(inject && (!strcmp(FAULT_MODE,"target-race") || !strcmp(FAULT_MODE,"cancel-ready")) && wait_gate(stage))return 124;
    return 0;
}
