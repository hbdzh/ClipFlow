/* QA-only: execute the real FFmpeg at input rate for private PCM preview jobs.
 * No Core state/result is supplied and no media bytes are synthesized here. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#ifndef REAL_FFMPEG
#define REAL_FFMPEG "/opt/homebrew/bin/ffmpeg"
#endif
static void quoted(FILE *file,const char *s) {
 fputc('"',file);
 for(;*s;s++){if(*s=='"'||*s=='\\')fputc('\\',file);if((unsigned char)*s<32)fprintf(file,"\\u%04x",(unsigned char)*s);else fputc(*s,file);}
 fputc('"',file);
}
int main(int argc,char **argv) {
 int preview=0,input=-1;
 for(int i=1;i<argc;i++){if(strcmp(argv[i],"pcm_f32le")==0)preview=1;if(input<0&&strcmp(argv[i],"-i")==0)input=i;}
 preview=preview&&argc>1&&argv[argc-1][0]=='/'; /* exclude validation stdout */
 const char *marker=getenv("QA002_AUDIO_PREVIEW_READY");
 if(preview&&input>0&&marker) {
  FILE *file=fopen(marker,"wx");if(!file)return 91;
  fprintf(file,"{\"pid\":%d,\"output\":",getpid());quoted(file,argv[argc-1]);fputs("}\n",file);fflush(file);fsync(fileno(file));fclose(file);
 }
 char **forward=calloc((size_t)argc+2,sizeof(char*));if(!forward)return 92;
 int n=0;forward[n++]=REAL_FFMPEG;
 for(int i=1;i<argc;i++){if(preview&&i==input)forward[n++]="-re";forward[n++]=argv[i];}
 execv(forward[0],forward);return 93;
}
