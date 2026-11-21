/* Ordinary successful public x264 lifecycle only.
 * No pictures, failed allocation, abort hook, input media or audio/device access.
 * Compile this unchanged source against the paired reviewed x264.h/libx264.a.
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <x264.h>

typedef struct
{
    const char *name;
    int threads, sliced, interlaced, csp, sync_lookahead, rc_method;
} NormalCase;

typedef struct
{
    int bit_depth, threads, lookahead_threads, sliced, interlaced, csp;
    int sync_lookahead, rc_method, header_bytes, nal_count;
} Observation;

static const NormalCase cases[] = {
    { "serial420",       1, 0, 0, X264_CSP_I420,  0, X264_RC_CRF },
    { "frame420",        4, 0, 0, X264_CSP_I420, 10, X264_RC_CRF },
    { "slice420",        4, 1, 0, X264_CSP_I420,  0, X264_RC_CRF },
    { "frame444",        4, 0, 0, X264_CSP_I444, 10, X264_RC_CRF },
    { "slice444",        4, 1, 0, X264_CSP_I444,  0, X264_RC_CRF },
    { "frame420-mbaff",  4, 0, 1, X264_CSP_I420, 10, X264_RC_CRF },
    { "slice420-mbaff",  4, 1, 1, X264_CSP_I420,  0, X264_RC_CRF },
    { "frame420-abr",    4, 0, 0, X264_CSP_I420, 10, X264_RC_ABR },
    { "frame420-cqp",    4, 0, 0, X264_CSP_I420, 10, X264_RC_CQP },
};

/* No API call is hidden inside assert, so NDEBUG cannot skip lifecycles. */
#define REQUIRE(condition) do { \
    if( !(condition) ) { \
        fprintf( stderr, "normal lifecycle check failed: %s, depth=%d, %s\n", \
                 test->name, depth, #condition ); \
        result = 1; \
        goto cleanup; \
    } \
} while( 0 )

static int run_case( const NormalCase *test, int depth, Observation *observed )
{
    x264_param_t requested, actual;
    x264_t *encoder = NULL;
    x264_nal_t *nals = NULL;
    int result = 0, header_bytes = 0, nal_count = 0;
    int total_bytes = 0, has_sps = 0, has_pps = 0;
    memset( &requested, 0, sizeof(requested) );
    memset( &actual, 0, sizeof(actual) );

    int preset_result = x264_param_default_preset( &requested, "medium", NULL );
    REQUIRE( preset_result == 0 );
    requested.i_width = 320;
    requested.i_height = 256;
    requested.i_fps_num = 30;
    requested.i_fps_den = 1;
    requested.i_timebase_num = 1;
    requested.i_timebase_den = 30;
    requested.b_vfr_input = 0;
    requested.i_bitdepth = depth;
    requested.i_csp = test->csp;
    requested.i_threads = test->threads;
    requested.i_lookahead_threads = test->threads == 1 ? 1 : 2;
    requested.b_sliced_threads = test->sliced;
    requested.b_interlaced = test->interlaced;
    requested.i_sync_lookahead = test->sync_lookahead;
    requested.b_opencl = 0;
    requested.psz_dump_yuv = NULL;
    requested.i_log_level = X264_LOG_NONE;
    requested.rc.i_rc_method = test->rc_method;
    requested.rc.f_rf_constant = 23;
    requested.rc.i_qp_constant = 23;
    requested.rc.i_bitrate = 1000;
    requested.rc.b_stat_read = 0;
    requested.rc.b_stat_write = 0;
    requested.rc.psz_stat_in = NULL;
    requested.rc.psz_stat_out = NULL;
    requested.rc.i_zones = 0;
    requested.rc.zones = NULL;
    requested.rc.psz_zones = NULL;

    encoder = x264_encoder_open( &requested );
    REQUIRE( encoder != NULL );
    header_bytes = x264_encoder_headers( encoder, &nals, &nal_count );
    REQUIRE( header_bytes > 0 && nals != NULL && nal_count >= 2 );
    for( int i = 0; i < nal_count; i++ )
    {
        REQUIRE( nals[i].p_payload != NULL && nals[i].i_payload > 0 );
        total_bytes += nals[i].i_payload;
        has_sps |= nals[i].i_type == NAL_SPS;
        has_pps |= nals[i].i_type == NAL_PPS;
    }
    REQUIRE( total_bytes == header_bytes && has_sps && has_pps );

    x264_encoder_parameters( encoder, &actual );
    REQUIRE( actual.i_width == 320 && actual.i_height == 256 );
    REQUIRE( actual.i_bitdepth == depth );
    REQUIRE( (actual.i_csp & X264_CSP_MASK) == test->csp );
    REQUIRE( actual.b_interlaced == test->interlaced );
    REQUIRE( actual.b_sliced_threads == test->sliced );
    REQUIRE( actual.rc.i_rc_method == test->rc_method );
    REQUIRE( actual.i_threads >= 1 && actual.i_threads <= test->threads );
    REQUIRE( actual.i_lookahead_threads >= 1 && actual.i_lookahead_threads <= 2 );
    if( test->threads == 1 )
        REQUIRE( actual.i_threads == 1 );
    else
        REQUIRE( actual.i_threads > 1 );
    if( test->sync_lookahead )
        REQUIRE( actual.i_sync_lookahead > 0 );
    else
        REQUIRE( actual.i_sync_lookahead == 0 );
    REQUIRE( !actual.rc.b_stat_read && !actual.rc.b_stat_write );
    REQUIRE( !actual.rc.i_zones && actual.rc.psz_zones == NULL );
    REQUIRE( !actual.b_opencl && actual.psz_dump_yuv == NULL );

    observed->bit_depth = actual.i_bitdepth;
    observed->threads = actual.i_threads;
    observed->lookahead_threads = actual.i_lookahead_threads;
    observed->sliced = actual.b_sliced_threads;
    observed->interlaced = actual.b_interlaced;
    observed->csp = actual.i_csp & X264_CSP_MASK;
    observed->sync_lookahead = actual.i_sync_lookahead;
    observed->rc_method = actual.rc.i_rc_method;
    observed->header_bytes = header_bytes;
    observed->nal_count = nal_count;

cleanup:
    if( encoder )
        x264_encoder_close( encoder );
    /* actual contains borrowed encoder pointers, never cleanup that copy. */
    x264_param_cleanup( &requested );
    return result;
}

int main( void )
{
    enum { CASE_COUNT = sizeof(cases) / sizeof(cases[0]), TOTAL = CASE_COUNT * 2 };
    Observation observations[TOTAL];
    memset( observations, 0, sizeof(observations) );
    int count = 0;
    for( int depth = 8; depth <= 10; depth += 2 )
        for( int i = 0; i < CASE_COUNT; i++ )
        {
            if( run_case( &cases[i], depth, &observations[count] ) )
                return EXIT_FAILURE;
            count++;
        }
    if( count != 18 )
        return EXIT_FAILURE;

    printf( "{\"result\":\"PASS_NORMAL_PUBLIC_LIFECYCLE_ONLY\","
            "\"completed_open_headers_parameters_close\":%d,\"frames_submitted\":0,\"cases\":[", count );
    for( int i = 0; i < count; i++ )
    {
        const NormalCase *test = &cases[i % CASE_COUNT];
        const Observation *row = &observations[i];
        printf( "%s{\"name\":\"%s\",\"bit_depth\":%d,"
                "\"requested\":{\"threads\":%d,\"sliced\":%d,\"interlaced\":%d,"
                "\"csp\":%d,\"sync_lookahead\":%d,\"rc_method\":%d},"
                "\"actual\":{\"threads\":%d,\"lookahead_threads\":%d,\"sliced\":%d,"
                "\"interlaced\":%d,\"csp\":%d,\"sync_lookahead\":%d,\"rc_method\":%d},"
                "\"header_bytes\":%d,\"nal_count\":%d}",
                i ? "," : "", test->name, row->bit_depth,
                test->threads, test->sliced, test->interlaced, test->csp,
                test->sync_lookahead, test->rc_method,
                row->threads, row->lookahead_threads, row->sliced, row->interlaced,
                row->csp, row->sync_lookahead, row->rc_method,
                row->header_bytes, row->nal_count );
    }
    puts( "]}" );
    return EXIT_SUCCESS;
}
