/* Normal actual OpenCL lifecycle observer. Pair with the exact reviewed internal
 * HAVE_OPENCL=1 source/library and BIT_DEPTH=8,HIGH_BIT_DEPTH=0. No failure hooks,
 * fake handles, active abort or extra OpenCL commands. Single synchronous host
 * instance. Arguments: new absolute cache-file and output-file paths inside a
 * private caller-owned directory; run there to retain compiler logs. */
#define _POSIX_C_SOURCE 200809L
#include "common/common.h"
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <sys/stat.h>
#include <unistd.h>
#if !HAVE_OPENCL || BIT_DEPTH != 8 || HIGH_BIT_DEPTH
#error Requires paired OpenCL-enabled 8-bit internal build
#endif
/* api.c declares these real inner entrypoints. Do not cast its public wrapper. */
x264_t *x264_8_encoder_open( x264_param_t *, void * );
int x264_8_encoder_headers( x264_t *, x264_nal_t **, int * );
int x264_8_encoder_encode( x264_t *, x264_nal_t **, int *, x264_picture_t *, x264_picture_t * );
int x264_8_encoder_delayed_frames( x264_t * );
void x264_8_encoder_close( x264_t * );
static struct
{
    clEnqueueUnmapMemObject_func unmap;
    clFinish_func finish;
    clReleaseMemObject_func release_mem;
    clReleaseCommandQueue_func release_queue;
    cl_command_queue queue;
    cl_mem buffer;
    void *mapped;
    unsigned events, unmap_event, finish_event, buffer_event, queue_event;
    unsigned unmap_calls, unmap_success, buffer_calls, buffer_success;
    unsigned queue_calls, queue_success, finish_after_unmap, invalid_order, finish_errors;
    cl_int unmap_status;
} obs;
static cl_int CL_API_CALL observe_unmap( cl_command_queue q, cl_mem m, void *p,
    cl_uint count, const cl_event *wait, cl_event *event )
{
    if( q != obs.queue || m != obs.buffer || p != obs.mapped || obs.buffer_calls ||
        obs.queue_calls || obs.unmap_calls )
        obs.invalid_order++;
    obs.unmap_calls++;
    obs.unmap_event = ++obs.events;
    cl_int status = obs.unmap( q, m, p, count, wait, event );
    obs.unmap_status = status;
    obs.unmap_success += status == CL_SUCCESS;
    return status;
}
static cl_int CL_API_CALL observe_finish( cl_command_queue q )
{
    if( q == obs.queue && obs.queue_calls ) obs.invalid_order++;
    cl_int status = obs.finish( q );
    if( q == obs.queue )
    {
        obs.finish_errors += status != CL_SUCCESS;
        if( obs.unmap_success && !obs.buffer_calls && status == CL_SUCCESS )
        {
            obs.finish_after_unmap++;
            obs.finish_event = ++obs.events;
        }
    }
    return status;
}
static cl_int CL_API_CALL observe_release_mem( cl_mem m )
{
    if( m == obs.buffer )
    {
        if( obs.unmap_success != 1 || !obs.finish_after_unmap || obs.buffer_calls || obs.queue_calls )
            obs.invalid_order++;
        obs.buffer_calls++;
        obs.buffer_event = ++obs.events;
    }
    cl_int status = obs.release_mem( m );
    if( m == obs.buffer ) obs.buffer_success += status == CL_SUCCESS;
    return status;
}
static cl_int CL_API_CALL observe_release_queue( cl_command_queue q )
{
    if( q == obs.queue )
    {
        if( obs.unmap_success != 1 || !obs.finish_after_unmap || obs.buffer_success != 1 || obs.queue_calls )
            obs.invalid_order++;
        obs.queue_calls++;
        obs.queue_event = ++obs.events;
    }
    cl_int status = obs.release_queue( q );
    if( q == obs.queue ) obs.queue_success += status == CL_SUCCESS;
    return status;
}
static int install_observer( x264_t *h )
{
    x264_opencl_function_t *ocl = h->opencl.ocl;
    if( !ocl || !h->opencl.context || !h->opencl.queue || !h->opencl.page_locked_buffer ||
        !h->opencl.page_locked_ptr || !ocl->clEnqueueUnmapMemObject || !ocl->clFinish ||
        !ocl->clReleaseMemObject || !ocl->clReleaseCommandQueue ) return -1;
    obs.queue = h->opencl.queue; obs.buffer = h->opencl.page_locked_buffer;
    obs.mapped = h->opencl.page_locked_ptr;
    obs.unmap = ocl->clEnqueueUnmapMemObject; obs.finish = ocl->clFinish;
    obs.release_mem = ocl->clReleaseMemObject; obs.release_queue = ocl->clReleaseCommandQueue;
    ocl->clEnqueueUnmapMemObject = observe_unmap; ocl->clFinish = observe_finish;
    ocl->clReleaseMemObject = observe_release_mem; ocl->clReleaseCommandQueue = observe_release_queue;
    return 0;
}
static void json_string( const char *s )
{
    putchar( '"' );
    for( const unsigned char *p = (const unsigned char *)s; *p; p++ )
        if( *p == '"' || *p == '\\' ) printf( "\\%c", *p );
        else if( *p < 32 ) printf( "\\u%04x", *p );
        else putchar( *p );
    putchar( '"' );
}
static int write_nals( FILE *f, x264_nal_t *nals, int count, int bytes )
{
    if( count < 0 || bytes < 0 || (count && !nals) ) return -1;
    int total = 0;
    for( int i = 0; i < count; i++ )
    {
        if( nals[i].i_payload <= 0 || !nals[i].p_payload ||
            fwrite( nals[i].p_payload, 1, nals[i].i_payload, f ) != (size_t)nals[i].i_payload ) return -1;
        total += nals[i].i_payload;
    }
    return total == bytes ? 0 : -1;
}
int main( int argc, char **argv )
{
    enum { WIDTH = 640, HEIGHT = 360, FRAMES = 30 };
    x264_param_t param; x264_picture_t input, decoded;
    x264_nal_t *nals = NULL; x264_t *h = NULL; FILE *out = NULL;
    int result = 1, picture_owned = 0, param_ready = 0, closed = 0;
    int active = 0, hooks = 0, mapped = 0, queue = 0, buffer = 0, fatal = 0;
    int frames_in = 0, frames_out = 0, nal_count = 0, flush_calls = 0;
    memset( &param, 0, sizeof(param) ); memset( &input, 0, sizeof(input) );
    memset( &decoded, 0, sizeof(decoded) );
    if( argc != 3 || argv[1][0] != '/' || argv[2][0] != '/' )
    {
        fprintf( stderr, "Usage: %s /private/new-cache.clbin /private/new-output.h264\n", argv[0] );
        return 2;
    }
    struct stat st;
    if( lstat( argv[1], &st ) == 0 || errno != ENOENT )
    {
        fprintf( stderr, "Cache must be new in caller-owned private test directory\n" ); return 2;
    }
    int fd = open( argv[2], O_WRONLY | O_CREAT | O_EXCL, 0600 );
    if( fd < 0 ) { perror( "new output" ); return 2; }
    out = fdopen( fd, "wb" );
    if( !out ) { close( fd ); perror( "fdopen" ); return 2; }
    if( x264_param_default_preset( &param, "medium", NULL ) < 0 ) goto cleanup;
    param_ready = 1;
    param.i_width = WIDTH; param.i_height = HEIGHT;
    param.i_csp = X264_CSP_I420; param.i_bitdepth = 8;
    param.i_fps_num = 30; param.i_fps_den = 1;
    param.i_timebase_num = 1; param.i_timebase_den = 30; param.b_vfr_input = 0;
    param.i_threads = param.i_lookahead_threads = 1;
    param.b_sliced_threads = 0; param.i_sync_lookahead = 0;
    param.rc.i_lookahead = 10; param.rc.i_rc_method = X264_RC_CRF; param.rc.f_rf_constant = 23;
    param.rc.b_stat_read = param.rc.b_stat_write = 0;
    param.rc.i_zones = 0; param.rc.zones = NULL; param.rc.psz_zones = NULL;
    param.psz_dump_yuv = NULL; param.b_opencl = 1; param.psz_clbin_file = argv[1];
    param.b_annexb = 1; param.b_repeat_headers = 0; param.i_log_level = X264_LOG_INFO;
    if( x264_picture_alloc( &input, X264_CSP_I420, WIDTH, HEIGHT ) < 0 ) goto cleanup;
    picture_owned = 1;
    h = x264_8_encoder_open( &param, NULL );
    if( !h ) goto cleanup;
    active = !!h->param.b_opencl; mapped = h->opencl.page_locked_ptr != NULL;
    buffer = h->opencl.page_locked_buffer != NULL; queue = h->opencl.queue != NULL;
    if( active ) { if( install_observer( h ) < 0 ) goto cleanup; hooks = 1; }
    int bytes = x264_8_encoder_headers( h, &nals, &nal_count );
    if( write_nals( out, nals, nal_count, bytes ) < 0 ) goto cleanup;
    for( int n = 0; n < FRAMES; n++ )
    {
        for( int p = 0; p < 3; p++ )
            for( int y = 0; y < (p ? HEIGHT/2 : HEIGHT); y++ )
                for( int x = 0; x < (p ? WIDTH/2 : WIDTH); x++ )
                    input.img.plane[p][y*input.img.i_stride[p]+x] = p ?
                        (uint8_t)(96 + ((x+y+3*n+17*p) % 64)) : (uint8_t)(16 + ((x+2*y+7*n) % 220));
        input.i_pts = n; frames_in++;
        bytes = x264_8_encoder_encode( h, &nals, &nal_count, &input, &decoded );
        if( write_nals( out, nals, nal_count, bytes ) < 0 ) goto cleanup;
        frames_out += bytes > 0;
    }
    while( x264_8_encoder_delayed_frames( h ) )
    {
        if( ++flush_calls > 256 ) goto cleanup;
        bytes = x264_8_encoder_encode( h, &nals, &nal_count, NULL, &decoded );
        if( write_nals( out, nals, nal_count, bytes ) < 0 ) goto cleanup;
        frames_out += bytes > 0;
    }
    if( frames_out != FRAMES || fflush( out ) != 0 || h->opencl.b_fatal_error ) goto cleanup;
    result = 0;
cleanup:
    if( h )
    {
        fatal = h->opencl.b_fatal_error;
        x264_8_encoder_close( h ); h = NULL; closed = 1;
    }
    /* Table/library may be gone: never call saved pointers or inspect h/objects. */
    obs.unmap = NULL; obs.finish = NULL; obs.release_mem = NULL; obs.release_queue = NULL;
    if( picture_owned ) x264_picture_clean( &input );
    if( param_ready ) x264_param_cleanup( &param );
    if( out && fclose( out ) != 0 ) result = 1;
    if( active && (!hooks || obs.unmap_calls != 1 || obs.unmap_success != 1 ||
        obs.buffer_calls != 1 || obs.buffer_success != 1 || obs.queue_calls != 1 || obs.queue_success != 1 ||
        !obs.finish_after_unmap || obs.finish_errors || obs.invalid_order ||
        !(obs.unmap_event < obs.finish_event && obs.finish_event < obs.buffer_event &&
          obs.buffer_event < obs.queue_event)) ) result = 1;
    printf( "{\"result\":\"%s\",\"actual_opencl_active\":%d,\"closed\":%d,"
            "\"mapped_pointer_present_after_open\":%d,\"page_buffer_present_after_open\":%d,"
            "\"queue_present_after_open\":%d,\"frames_submitted\":%d,\"frames_output\":%d,"
            "\"fatal_before_close\":%d,\"unmap_calls\":%u,\"unmap_success\":%u,\"unmap_status\":%d,"
            "\"finish_after_unmap\":%u,\"finish_errors\":%u,\"page_release_calls\":%u,"
            "\"page_release_success\":%u,\"queue_release_calls\":%u,\"queue_release_success\":%u,"
            "\"observed_invalid_order\":%u,\"events\":[%u,%u,%u,%u],\"cache_path\":",
            result ? "FAIL" : active ? "PASS_REAL_OPENCL_NORMAL_CLOSE" : "NOT_ACTIVE_CPU_FALLBACK_ONLY",
            active, closed, mapped, buffer, queue, frames_in, frames_out, fatal,
            obs.unmap_calls, obs.unmap_success, obs.unmap_status, obs.finish_after_unmap,
            obs.finish_errors, obs.buffer_calls, obs.buffer_success, obs.queue_calls, obs.queue_success,
            obs.invalid_order, obs.unmap_event, obs.finish_event, obs.buffer_event, obs.queue_event );
    json_string( argv[1] ); printf( ",\"output_path\":" ); json_string( argv[2] );
    printf( ",\"scope\":\"Normal real OpenCL; delegated unmap/finish/target-page-release/queue-release observation only, not full-driver UAF detection; no active abort or failure injection\"}\n" );
    return result;
}
