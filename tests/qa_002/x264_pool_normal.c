/* Ordinary valid threadpool lifecycle only; no failure triggers or media. */
#include <assert.h>
#include <stdint.h>
#include <stdio.h>

typedef struct x264_threadpool_t x264_threadpool_t;
int x264_threadpool_init(x264_threadpool_t **pool, int threads);
void x264_threadpool_run(x264_threadpool_t *pool, void *(*function)(void *), void *arg);
void *x264_threadpool_wait(x264_threadpool_t *pool, void *arg);
void x264_threadpool_delete(x264_threadpool_t *pool);

typedef struct { uint32_t seed, result; } Job;
static void *compute(void *arg)
{
    Job *job = arg;
    job->result = job->seed * 3 + 1;
    return job;
}

int main(void)
{
    unsigned jobs_completed = 0, pools_completed = 0;
    const int sizes[] = { 2, 4, 8 };
    for (unsigned cycle = 0; cycle < 4; cycle++)
        for (unsigned s = 0; s < sizeof(sizes) / sizeof(sizes[0]); s++)
        {
            const int count = sizes[s];
            x264_threadpool_t *pool = NULL;
            assert(x264_threadpool_init(&pool, count) == 0 && pool != NULL);
            for (unsigned round = 0; round < 8; round++)
            {
                Job jobs[8] = {{0}};
                for (int i = 0; i < count; i++)
                {
                    jobs[i].seed = cycle * 1000 + round * 10 + (unsigned)i;
                    x264_threadpool_run(pool, compute, &jobs[i]);
                }
                for (int i = count - 1; i >= 0; i--)
                {
                    assert(x264_threadpool_wait(pool, &jobs[i]) == &jobs[i]);
                    assert(jobs[i].result == jobs[i].seed * 3 + 1);
                    jobs_completed++;
                }
            }
            x264_threadpool_delete(pool);
            pools_completed++;
        }
    assert(pools_completed == 12 && jobs_completed == 448);
    puts("{\"result\":\"PASS_NORMAL_LIFECYCLE_ONLY\",\"pools\":12,\"valid_jobs\":448}");
    return 0;
}
