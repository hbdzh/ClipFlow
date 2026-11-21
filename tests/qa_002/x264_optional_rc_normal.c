/* Ordinary, fully constructed internal x264 owners; no original failure path.
 * macOS-only source test, compiled against one matched source tree/static lib.
 * No frames/jobs, fault injection, malformed input, unlink, playback or devices.
 * Unexpected assertion exits without successful-close (which could publish stats).
 */
#ifndef __APPLE__
#error This FD observation harness is macOS-only.
#endif
#if !defined(BIT_DEPTH) || !defined(HIGH_BIT_DEPTH) || (BIT_DEPTH != 8 && BIT_DEPTH != 10)
#error Compile with a matched BIT_DEPTH and HIGH_BIT_DEPTH.
#endif
#if HIGH_BIT_DEPTH != (BIT_DEPTH > 8)
#error HIGH_BIT_DEPTH must match BIT_DEPTH.
#endif
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <sys/stat.h>
#include <unistd.h>
#include "common/common.h"
#include "encoder/ratecontrol.h"

/* Real bit-depth-specific constructors, not the public opaque API wrapper. */
x264_t *x264_encoder_open( x264_param_t *, void * );
void x264_encoder_close( x264_t * );

static const char *case_name = "setup";
#define REQUIRE(c) do { if( !(c) ) { \
    fprintf( stderr, "normal optional RC test failed: %s depth=%d line=%d: %s\n", \
             case_name, BIT_DEPTH, __LINE__, #c ); \
    exit( EXIT_FAILURE ); \
} } while( 0 )

typedef struct { unsigned char *data; size_t bytes; } Snapshot;
typedef struct { int fd; char path[PATH_MAX]; } OwnedFD;
typedef struct { int width, height; unsigned fps_num, fps_den, time_num, time_den; } Source;
typedef struct
{
    const char *name;
    int read_stats, write_stats, mbtree, generated_zones, caller_zones, rescale;
} NormalCase;
typedef struct
{
    int fds_closed, temp_files_retained, caller_objects_reclaimed;
    int width, height, threads, zones_before_abort;
} Observation;

static const NormalCase cases[] = {
    { "stats-write",         0, 1, 0, 0, 0, 0 },
    { "stats-write-mbtree",  0, 1, 1, 0, 0, 0 },
    { "stats-read-pass2",    1, 0, 1, 0, 0, 0 },
    { "stats-read-write",    1, 1, 1, 0, 0, 0 },
    { "generated-zones",     0, 0, 1, 1, 0, 0 },
    { "caller-distinct",     0, 0, 1, 0, 1, 0 },
    { "caller-shared-alias", 0, 0, 1, 0, 2, 0 },
    { "stats-read-rescale",  1, 0, 1, 0, 0, 1 },
};
static int caller_callbacks;

static void caller_release( void *p )
{
    caller_callbacks++;
    x264_param_cleanup( p );
    free( p );
}

static void append_path( char out[PATH_MAX], const char *a, const char *b )
{
    int n = snprintf( out, PATH_MAX, "%s%s", a, b );
    REQUIRE( n > 0 && n < PATH_MAX );
}

static Snapshot read_file( const char *path )
{
    Snapshot s = { NULL, 0 };
    FILE *f = fopen( path, "rb" );
    REQUIRE( f != NULL );
    struct stat st;
    REQUIRE( fstat( fileno(f), &st ) == 0 && S_ISREG(st.st_mode) );
    REQUIRE( st.st_size >= 0 && st.st_size <= 64 * 1024 * 1024 );
    s.bytes = (size_t)st.st_size;
    s.data = malloc( s.bytes ? s.bytes : 1 );
    REQUIRE( s.data != NULL );
    REQUIRE( fread( s.data, 1, s.bytes, f ) == s.bytes );
    REQUIRE( fclose(f) == 0 );
    return s;
}

static void unchanged_file( const char *path, const Snapshot *before )
{
    Snapshot after = read_file( path );
    REQUIRE( after.bytes == before->bytes );
    REQUIRE( memcmp( after.data, before->data, before->bytes ) == 0 );
    free( after.data );
}

static void create_sentinel( const char *path )
{
    static const char data[] = "QA002 OPTIONAL RC FINAL TARGET MUST REMAIN\n";
    FILE *f = fopen( path, "wx" );
    REQUIRE( f != NULL );
    REQUIRE( fwrite( data, 1, sizeof(data)-1, f ) == sizeof(data)-1 );
    REQUIRE( fclose(f) == 0 );
}

static int exists_regular( const char *path )
{
    struct stat st;
    if( lstat( path, &st ) )
    {
        REQUIRE( errno == ENOENT );
        return 0;
    }
    REQUIRE( S_ISREG(st.st_mode) );
    return 1;
}

static int capture_fds( const char *work, const char *stats, const char *mbtree, OwnedFD out[16] )
{
    int count = 0;
    size_t work_len = strlen(work);
    /* Fresh test process: a missed high FD cannot pass the expected-count gate. */
    for( int fd = 0; fd < 4096; fd++ )
    {
        char path[PATH_MAX];
        if( fcntl( fd, F_GETPATH, path ) == -1 )
            continue;
        if( (!strncmp( path, work, work_len ) && path[work_len] == '/') ||
            !strcmp( path, stats ) || !strcmp( path, mbtree ) )
        {
            REQUIRE( count < 16 );
            out[count].fd = fd;
            memcpy( out[count].path, path, strlen(path)+1 );
            count++;
        }
    }
    return count;
}

static Source read_source_options( const char *path )
{
    FILE *f = fopen( path, "rb" );
    REQUIRE( f != NULL );
    char line[16384];
    REQUIRE( fgets( line, sizeof(line), f ) != NULL );
    REQUIRE( fclose(f) == 0 );
    Source s;
    int depth, mbtree;
    REQUIRE( sscanf( line, "#options: %dx%d", &s.width, &s.height ) == 2 );
    const char *p = strstr( line, " fps=" );
    REQUIRE( p && sscanf( p, " fps=%u/%u", &s.fps_num, &s.fps_den ) == 2 );
    p = strstr( line, " timebase=" );
    REQUIRE( p && sscanf( p, " timebase=%u/%u", &s.time_num, &s.time_den ) == 2 );
    p = strstr( line, " bitdepth=" );
    REQUIRE( p && sscanf( p, " bitdepth=%d", &depth ) == 1 && depth == BIT_DEPTH );
    p = strstr( line, " mbtree=" );
    REQUIRE( p && sscanf( p, " mbtree=%d", &mbtree ) == 1 && mbtree == 1 );
    REQUIRE( strstr( line, " interlaced=0 " ) != NULL );
    REQUIRE( s.width >= 64 && s.height >= 64 && !(s.width % 32) && !(s.height % 32) );
    REQUIRE( s.fps_num && s.fps_den && s.time_num && s.time_den );
    return s;
}

static Observation run_case( const NormalCase *test, const Source *source,
                             const char *work, const char *stats, const char *mbtree,
                             const Snapshot *stats_before, const Snapshot *mbtree_before )
{
    case_name = test->name;
    Observation observed = {0};
    char dir[PATH_MAX], final[PATH_MAX], final_mbtree[PATH_MAX];
    char tmp[PATH_MAX], tmp_mbtree[PATH_MAX];
    int dir_len = snprintf( dir, sizeof(dir), "%s/%s", work, test->name );
    REQUIRE( dir_len > 0 && dir_len < (int)sizeof(dir) );
    REQUIRE( mkdir( dir, 0700 ) == 0 );
    append_path( final, dir, "/final.stats" );
    append_path( final_mbtree, final, ".mbtree" );
    append_path( tmp, final, ".temp" );
    append_path( tmp_mbtree, final, ".mbtree.temp" );
    create_sentinel( final );
    create_sentinel( final_mbtree );
    Snapshot final_before = read_file(final), final_mbtree_before = read_file(final_mbtree);
    REQUIRE( !exists_regular(tmp) && !exists_regular(tmp_mbtree) );

    x264_param_t requested;
    int rc = x264_param_default_preset( &requested, "medium", NULL );
    REQUIRE( rc == 0 );
    requested.i_width = test->rescale ? source->width / 2 : source->width;
    requested.i_height = test->rescale ? source->height / 2 : source->height;
    requested.i_fps_num = source->fps_num;
    requested.i_fps_den = source->fps_den;
    requested.i_timebase_num = source->time_num;
    requested.i_timebase_den = source->time_den;
    requested.b_vfr_input = 0;
    requested.i_bitdepth = BIT_DEPTH;
    requested.i_csp = X264_CSP_I420;
    requested.i_threads = 4;
    requested.i_lookahead_threads = 2;
    requested.i_sync_lookahead = 0;
    requested.b_opencl = 0;
    requested.b_interlaced = 0;
    requested.i_log_level = X264_LOG_NONE;
    requested.rc.i_rc_method = test->read_stats || test->write_stats ? X264_RC_ABR : X264_RC_CRF;
    requested.rc.i_bitrate = 1000;
    requested.rc.f_rf_constant = 23;
    requested.rc.b_stat_read = test->read_stats;
    requested.rc.b_stat_write = test->write_stats;
    requested.rc.b_mb_tree = test->mbtree;
    requested.rc.psz_stat_in = (char*)stats;
    requested.rc.psz_stat_out = final;
    requested.rc.i_zones = 0;
    requested.rc.zones = NULL;
    requested.rc.psz_zones = test->generated_zones ? "0,10,ref=2/11,20,b=0.85" : NULL;

    x264_zone_t caller_zones[2] = {{0}}, zones_copy[2];
    x264_param_t *caller[2] = { NULL, NULL }, caller_copy[2];
    int unique_callers = 0;
    caller_callbacks = 0;
    if( test->caller_zones )
    {
        unique_callers = test->caller_zones == 2 ? 1 : 2;
        for( int i = 0; i < unique_callers; i++ )
        {
            caller[i] = malloc(sizeof(*caller[i]));
            REQUIRE( caller[i] != NULL );
            memcpy( caller[i], &requested, sizeof(requested) );
            caller[i]->opaque = NULL;
            caller[i]->param_free = caller_release;
            caller[i]->i_frame_reference = 2;
            /* A normal inactive filename creates a real caller-owned arena.
             * Both usage flags remain0, so this never opens another file. */
            int parsed = x264_param_parse( caller[i], "stats", "caller-owned-unused.stats" );
            REQUIRE( parsed == 0 && caller[i]->opaque != NULL );
            REQUIRE( !caller[i]->rc.b_stat_read && !caller[i]->rc.b_stat_write );
            memcpy( &caller_copy[i], caller[i], sizeof(*caller[i]) );
        }
        caller_zones[0].i_start = 0;
        caller_zones[0].i_end = 10;
        caller_zones[0].f_bitrate_factor = 1;
        caller_zones[0].param = caller[0];
        caller_zones[1].i_start = 11;
        caller_zones[1].i_end = 20;
        caller_zones[1].f_bitrate_factor = 1;
        caller_zones[1].param = caller[unique_callers-1];
        memcpy( zones_copy, caller_zones, sizeof(caller_zones) );
        requested.rc.i_zones = 2;
        requested.rc.zones = caller_zones;
    }

    OwnedFD before[16], acquired[16];
    REQUIRE( capture_fds( work, stats, mbtree, before ) == 0 );
    x264_t *h = x264_encoder_open( &requested, NULL );
    REQUIRE( h != NULL && h->rc != NULL );
    REQUIRE( h->param.i_sync_lookahead == 0 && !h->param.b_opencl );
    REQUIRE( h->param.i_threads > 1 && h->param.i_threads <= 4 );
    observed.width = h->param.i_width;
    observed.height = h->param.i_height;
    observed.threads = h->param.i_threads;
    observed.zones_before_abort = h->param.rc.i_zones;
    REQUIRE( h->param.i_width == requested.i_width && h->param.i_height == requested.i_height );
    if( test->generated_zones )
        REQUIRE( h->param.rc.i_zones == 2 && h->param.rc.zones != NULL );
    int count = capture_fds( work, stats, mbtree, acquired );
    int expected = test->write_stats + (test->write_stats && test->mbtree && !test->read_stats)
                 + (test->read_stats && test->mbtree);
    REQUIRE( count == expected );
    REQUIRE( caller_callbacks == 0 );

    /* Complete real owner, idle pools, sync0 and no submitted frame: new disposal only. */
    rc = x264_ratecontrol_abort_open(h);
    REQUIRE( rc == 0 && h->rc == NULL );
    for( int i = 0; i < h->param.i_threads; i++ )
        REQUIRE( h->thread[i]->rc == NULL );
    REQUIRE( caller_callbacks == 0 );
    /* No fopen or other FD acquisition between disposal and these checks. */
    for( int i = 0; i < count; i++ )
    {
        errno = 0;
        REQUIRE( fcntl( acquired[i].fd, F_GETFD ) == -1 && errno == EBADF );
    }
    observed.fds_closed = count;
    if( test->generated_zones )
        REQUIRE( h->param.rc.i_zones == 0 && h->param.rc.zones == NULL );
    if( test->caller_zones )
    {
        REQUIRE( h->param.rc.i_zones == 2 && h->param.rc.zones == caller_zones );
        REQUIRE( memcmp(caller_zones, zones_copy, sizeof(caller_zones)) == 0 );
        for( int i = 0; i < unique_callers; i++ )
        {
            REQUIRE( memcmp(caller[i], &caller_copy[i], sizeof(*caller[i])) == 0 );
            REQUIRE( caller[i]->opaque != NULL );
            REQUIRE( !strcmp(caller[i]->rc.psz_stat_in, "caller-owned-unused.stats") );
            REQUIRE( !strcmp(caller[i]->rc.psz_stat_out, "caller-owned-unused.stats") );
        }
    }
    REQUIRE( exists_regular(tmp) == test->write_stats );
    REQUIRE( exists_regular(tmp_mbtree) == (test->write_stats && test->mbtree && !test->read_stats) );
    observed.temp_files_retained = test->write_stats + (test->write_stats && test->mbtree && !test->read_stats);
    unchanged_file( final, &final_before );
    unchanged_file( final_mbtree, &final_mbtree_before );
    unchanged_file( stats, stats_before );
    unchanged_file( mbtree, mbtree_before );

    /* The parent still owns a complete encoder apart from the disposed RC.
     * No stats/zone flags survive into this fresh no-publish RC or normal close.
     * Keep the old opaque arena so the original internal strings free once. */
    h->param.rc.b_stat_read = h->param.rc.b_stat_write = 0;
    h->param.rc.psz_stat_in = h->param.rc.psz_stat_out = NULL;
    h->param.rc.i_zones = 0;
    h->param.rc.zones = NULL;
    h->param.rc.psz_zones = NULL;
    h->param.rc.i_rc_method = X264_RC_CRF;
    h->param.rc.f_rf_constant = 23;
    rc = x264_ratecontrol_new(h);
    REQUIRE( rc == 0 && h->rc != NULL );
    x264_encoder_close(h);
    h = NULL;
    REQUIRE( caller_callbacks == 0 );
    for( int i = 0; i < unique_callers; i++ )
    {
        REQUIRE( caller[i]->opaque != NULL );
        REQUIRE( !strcmp(caller[i]->rc.psz_stat_in, "caller-owned-unused.stats") );
        REQUIRE( !strcmp(caller[i]->rc.psz_stat_out, "caller-owned-unused.stats") );
        caller_release( caller[i] );
    }
    REQUIRE( caller_callbacks == unique_callers );
    observed.caller_objects_reclaimed = caller_callbacks;
    unchanged_file( final, &final_before );
    unchanged_file( final_mbtree, &final_mbtree_before );
    unchanged_file( stats, stats_before );
    unchanged_file( mbtree, mbtree_before );
    REQUIRE( capture_fds( work, stats, mbtree, before ) == 0 );
    x264_param_cleanup( &requested );
    free(final_before.data);
    free(final_mbtree_before.data);
    return observed;
}

int main( int argc, char **argv )
{
    if( argc != 3 )
    {
        fprintf( stderr, "usage: %s NEW-WORK-DIRECTORY VALID-FIRSTPASS-STATS\n", argv[0] );
        return EXIT_FAILURE;
    }
    char work[PATH_MAX], stats[PATH_MAX], mbtree[PATH_MAX], mt_name[PATH_MAX];
    REQUIRE( mkdir( argv[1], 0700 ) == 0 );
    REQUIRE( realpath( argv[1], work ) != NULL );
    REQUIRE( realpath( argv[2], stats ) != NULL );
    append_path( mt_name, stats, ".mbtree" );
    REQUIRE( realpath( mt_name, mbtree ) != NULL );
    Snapshot stats_before = read_file(stats), mbtree_before = read_file(mbtree);
    Source source = read_source_options(stats);
    Observation observations[ARRAY_ELEMS(cases)];
    for( int i = 0; i < (int)ARRAY_ELEMS(cases); i++ )
        observations[i] = run_case( &cases[i], &source, work, stats, mbtree, &stats_before, &mbtree_before );
    case_name = "final";
    unchanged_file( stats, &stats_before );
    unchanged_file( mbtree, &mbtree_before );
    free(stats_before.data);
    free(mbtree_before.data);
    printf( "{\"result\":\"PASS_NORMAL_FULLY_CONSTRUCTED_RC_ABORT_ONLY\",\"bit_depth\":%d,"
            "\"cases\":[", BIT_DEPTH );
    for( int i = 0; i < (int)ARRAY_ELEMS(cases); i++ )
    {
        const Observation *o = &observations[i];
        printf( "%s{\"name\":\"%s\",\"source_size\":[%d,%d],\"requested_size\":[%d,%d],"
                "\"threads\":%d,\"zones_before_abort\":%d,\"closed_fds\":%d,"
                "\"temp_files_retained\":%d,\"caller_callbacks_during_abort_or_close\":0,"
                "\"caller_objects_reclaimed_by_test\":%d,\"input_and_final_bytes_unchanged\":true}",
                i ? "," : "", cases[i].name, source.width, source.height,
                o->width, o->height, o->threads, o->zones_before_abort,
                o->fds_closed, o->temp_files_retained, o->caller_objects_reclaimed );
    }
    puts( "]}" );
    return EXIT_SUCCESS;
}
