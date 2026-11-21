/* QA-only real OS permission denial at the real worker's second output open. */
#define _DARWIN_C_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
#include "ai_atomic_config.h"

static int second_open(const char *path, int flags, ...) {
    mode_t mode=0;
    if(flags&O_CREAT){va_list ap;va_start(ap,flags);mode=(mode_t)va_arg(ap,int);va_end(ap);}
    const char *leaf=strrchr(path,'/');
    if(!strcmp(FAULT_MODE,"second-create") && (flags&O_CREAT) && leaf && !strcmp(leaf,"/accompaniment.wav")) {
        char parent[4096],vox[4096];size_t n=(size_t)(leaf-path);
        if(n==0 || n+1>=sizeof(parent)){errno=EINVAL;return -1;}
        memcpy(parent,path,n);parent[n]=0;
        (void)snprintf(vox,sizeof(vox),"%s/vocals.wav",parent);
        struct stat before,first;
        if(stat(parent,&before)!=0 || stat(vox,&first)!=0 || chmod(parent,0500)!=0)return -1;
        int fd=open(path,flags,mode);int actual_errno=errno;
        int restored=chmod(parent,before.st_mode&0777)==0;
        FILE *log=fopen(AUDIT_PATH,"a");
        if(log){fprintf(log,"{\"phase\":\"real-second-open-permission-denial\",\"pid\":%ld,\"first_stem_existed\":true,\"first_stem_bytes\":%ld,\"open_result\":%d,\"errno\":%d,\"restored_directory_mode\":%s}\n",(long)getpid(),(long)first.st_size,fd,actual_errno,restored?"true":"false");fflush(log);fsync(fileno(log));fclose(log);}
        errno=actual_errno;return fd;
    }
    return open(path,flags,mode);
}
static ssize_t second_write(int descriptor,const void *data,size_t count) {
    char path[4096];struct stat current;
    if(!strcmp(FAULT_MODE,"second-write") && fcntl(descriptor,F_GETPATH,path)==0 &&
        fstat(descriptor,&current)==0 && current.st_size>=8192) {
        const char *leaf=strrchr(path,'/');
        if(leaf && !strcmp(leaf,"/accompaniment.wav")) {
            // The descriptor stays owned and valid; this OS write genuinely
            // fails EBADF, without introducing a closed-descriptor reuse race.
            int read_only=open("/dev/null",O_RDONLY);
            if(read_only<0)return -1;
            if(dup2(read_only,descriptor)<0){close(read_only);return -1;}
            close(read_only);
            ssize_t result=write(descriptor,data,count);int actual_errno=errno;
            FILE *log=fopen(AUDIT_PATH,"a");
            if(log){fprintf(log,"{\"phase\":\"real-second-write-ebadf\",\"pid\":%ld,\"second_stem_bytes_before_failure\":%ld,\"write_result\":%ld,\"errno\":%d}\n",(long)getpid(),(long)current.st_size,(long)result,actual_errno);fflush(log);fsync(fileno(log));fclose(log);}
            errno=actual_errno;return result;
        }
    }
    return write(descriptor,data,count);
}
__attribute__((used)) static struct {const void *replacement;const void *replacee;} interpose[] __attribute__((section("__DATA,__interpose")))={{(const void*)second_open,(const void*)open},{(const void*)second_write,(const void*)write}};
