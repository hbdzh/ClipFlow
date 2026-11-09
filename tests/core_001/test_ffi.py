#!/usr/bin/env python3
"""Independent ABI contract tests. Requires an explicitly supplied real library.

Only live handles and original Rust buffers are dereferenced. Invalid non-null
addresses, use-after-free and double-free are caller UB and are never exercised.
"""
import concurrent.futures
import ctypes as C
import json
from pathlib import Path
import sys
import unittest

OK, ARG, HANDLE, UTF8, SCHEMA = 0, -1, -2, -3, -4
JSON_ERROR, NOT_FOUND, BUSY, UNSUPPORTED, LIMIT = -5, -6, -7, -8, -9


class Buffer(C.Structure):
    _fields_ = [('ptr', C.POINTER(C.c_uint8)), ('len', C.c_size_t)]


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode()


def raw(data):
    if data is None:
        return None
    return (C.c_uint8 * max(1, len(data))).from_buffer_copy(data or b'\0')


def dirty_buffer():
    # A sentinel output is not an allocation; failure paths must zero it, and
    # tests never attempt to read/free it unless the call reports success.
    return Buffer(C.cast(C.c_void_p(1), C.POINTER(C.c_uint8)), 999)


class API:
    def __init__(self, library):
        self.lib = C.CDLL(str(library))
        for name in ('ms_get_version', 'ms_get_capabilities'):
            fn = getattr(self.lib, name)
            fn.argtypes = [C.c_void_p, C.POINTER(Buffer)]
            fn.restype = C.c_int32
        self.lib.ms_engine_create.argtypes = [C.c_void_p, C.c_size_t, C.POINTER(C.c_void_p)]
        self.lib.ms_engine_create.restype = C.c_int32
        self.lib.ms_engine_destroy.argtypes = [C.c_void_p]
        self.lib.ms_engine_destroy.restype = C.c_int32
        for name in ('ms_submit_job', 'ms_get_job_snapshot'):
            fn = getattr(self.lib, name)
            fn.argtypes = [C.c_void_p, C.c_void_p, C.c_size_t, C.POINTER(Buffer)]
            fn.restype = C.c_int32
        self.lib.ms_cancel_job.argtypes = [C.c_void_p, C.c_void_p, C.c_size_t]
        self.lib.ms_cancel_job.restype = C.c_int32
        self.lib.ms_buffer_free.argtypes = [Buffer]
        self.lib.ms_buffer_free.restype = None

    def take(self, out):
        try:
            if not out.ptr or not out.len:
                raise AssertionError('Successful JSON output must own nonempty bytes')
            return json.loads(C.string_at(out.ptr, out.len).decode('utf-8'))
        finally:
            self.lib.ms_buffer_free(out)

    def create(self, config=None):
        payload = encoded(config if config is not None else {'schema_version': 0})
        handle = C.c_void_p()
        rc = self.lib.ms_engine_create(raw(payload), len(payload), C.byref(handle))
        if rc != OK or not handle.value:
            raise AssertionError(f'Create failed: {rc}')
        return Engine(self, handle)


class Engine:
    def __init__(self, api, handle):
        self.api, self.handle, self.jobs, self.closed = api, handle, [], False

    def query(self, name):
        out = Buffer()
        rc = getattr(self.api.lib, name)(self.handle, C.byref(out))
        if rc != OK:
            raise AssertionError(f'{name} failed: {rc}')
        return self.api.take(out)

    def submit(self, outcome='succeed'):
        payload = encoded({'schema_version': 0, 'operation': 'mock', 'outcome': outcome})
        out = Buffer()
        rc = self.api.lib.ms_submit_job(self.handle, raw(payload), len(payload), C.byref(out))
        if rc != OK:
            raise AssertionError(f'Submit failed: {rc}')
        result = self.api.take(out)
        if result.get('schema_version') != 0 or not isinstance(result.get('job_id'), str) or not result['job_id']:
            raise AssertionError('Submit response lacks schema 0 and a nonempty job identifier')
        self.jobs.append(result['job_id'])
        return result

    def snapshot(self, job_id):
        payload = job_id.encode()
        out = Buffer()
        rc = self.api.lib.ms_get_job_snapshot(self.handle, raw(payload), len(payload), C.byref(out))
        if rc != OK:
            raise AssertionError(f'Snapshot failed: {rc}')
        result = self.api.take(out)
        if result.get('schema_version') != 0:
            raise AssertionError('Every snapshot must contain schema_version 0')
        return result

    def cancel(self, job_id):
        payload = job_id.encode()
        return self.api.lib.ms_cancel_job(self.handle, raw(payload), len(payload))

    def destroy(self):
        rc = self.api.lib.ms_engine_destroy(self.handle)
        if rc == OK:
            self.closed = True
        return rc

    def cleanup(self):
        if self.closed:
            return
        for job_id in self.jobs:
            if self.cancel(job_id) == OK:
                for _ in range(4):
                    if self.snapshot(job_id)['state'] in ('succeeded', 'failed', 'cancelled'):
                        break
        if self.destroy() != OK:
            raise AssertionError('Cleanup could not destroy live test engine')


API_INSTANCE = None


class FFIContract(unittest.TestCase):
    def setUp(self):
        self.api = API_INSTANCE

    def engine(self, config=None):
        e = self.api.create(config)
        self.addCleanup(e.cleanup)
        return e

    def assert_empty(self, out):
        self.assertFalse(out.ptr)
        self.assertEqual(out.len, 0)

    def submit_failure(self, e, payload, expected):
        out = dirty_buffer()
        self.assertEqual(self.api.lib.ms_submit_job(e.handle, raw(payload), len(payload), C.byref(out)), expected)
        self.assert_empty(out)

    def test_create_version_capabilities_and_destroy(self):
        e = self.engine()
        version = e.query('ms_get_version')
        self.assertEqual(version['schema_version'], 0)
        self.assertIsInstance(version['core_version'], str)
        self.assertTrue(version['core_version'])
        capabilities = e.query('ms_get_capabilities')
        self.assertEqual(capabilities['schema_version'], 0)
        for name in ('probe', 'trim_stream_copy', 'accurate_reencode', 'ai_separation'):
            self.assertIs(capabilities['features'][name]['available'], False, name)
        self.assertIs(capabilities['features']['mock_job']['available'], True)
        self.assertEqual(capabilities['media_backend']['name'], 'mock')
        self.assertEqual(e.destroy(), OK)

    def test_config_input_failures_zero_handle(self):
        cases = [(b'', ARG), (b'\xff', UTF8), (b'{', JSON_ERROR),
                 (b'[]', JSON_ERROR), (b'null', JSON_ERROR), (b'{}', ARG),
                 (encoded({'schema_version': 999}), SCHEMA),
                 (encoded({'schema_version': -1}), SCHEMA),
                 (encoded({'schema_version': False}), ARG),
                 (encoded({'schema_version': 0.0}), ARG),
                 (encoded({'schema_version': '0'}), ARG),
                 (encoded({'schema_version': 0, 'extra': True}), ARG),
                 (encoded({'schema_version': 0, 'max_jobs': 0}), ARG),
                 (encoded({'schema_version': 0, 'max_jobs': 257}), ARG),
                 (encoded({'schema_version': 0, 'max_jobs': 1.5}), ARG),
                 (encoded({'schema_version': 0, 'max_jobs': True}), ARG),
                 (b' ' * 65537, LIMIT)]
        for payload, expected in cases:
            with self.subTest(payload=payload[:80]):
                h = C.c_void_p(1)
                self.assertEqual(self.api.lib.ms_engine_create(raw(payload), len(payload), C.byref(h)), expected)
                self.assertFalse(h.value)

    def test_create_null_parameters(self):
        payload = encoded({'schema_version': 0})
        self.assertEqual(self.api.lib.ms_engine_create(raw(payload), len(payload), None), ARG)
        for length in (0, 1):
            h = C.c_void_p(1)
            self.assertEqual(self.api.lib.ms_engine_create(None, length, C.byref(h)), ARG)
            self.assertFalse(h.value)

    def test_null_engine_is_invalid_and_output_is_zero(self):
        for name in ('ms_get_version', 'ms_get_capabilities'):
            out = dirty_buffer()
            self.assertEqual(getattr(self.api.lib, name)(None, C.byref(out)), HANDLE)
            self.assert_empty(out)
        self.assertEqual(self.api.lib.ms_engine_destroy(None), HANDLE)
        payload = encoded({'schema_version': 0, 'operation': 'mock'})
        out = dirty_buffer()
        self.assertEqual(self.api.lib.ms_submit_job(None, raw(payload), len(payload), C.byref(out)), HANDLE)
        self.assert_empty(out)
        out = dirty_buffer()
        self.assertEqual(self.api.lib.ms_get_job_snapshot(None, raw(b'x'), 1, C.byref(out)), HANDLE)
        self.assert_empty(out)
        self.assertEqual(self.api.lib.ms_cancel_job(None, raw(b'x'), 1), HANDLE)

    def test_null_output_parameters_are_rejected(self):
        e = self.engine()
        for name in ('ms_get_version', 'ms_get_capabilities'):
            self.assertEqual(getattr(self.api.lib, name)(e.handle, None), ARG)
        payload = encoded({'schema_version': 0, 'operation': 'mock'})
        self.assertEqual(self.api.lib.ms_submit_job(e.handle, raw(payload), len(payload), None), ARG)
        self.assertEqual(self.api.lib.ms_get_job_snapshot(e.handle, raw(b'x'), 1, None), ARG)
        self.assertEqual(e.destroy(), OK, 'Rejected submit must not create a task')

    def test_request_failure_cases_and_no_ghost_task(self):
        e = self.engine({'schema_version': 0, 'max_jobs': 1})
        cases = [(b'', ARG), (b'\xff', UTF8), (b'{', JSON_ERROR), (b'[]', JSON_ERROR),
                 (b'{}', ARG), (encoded({'schema_version': 1, 'operation': 'mock'}), SCHEMA),
                 (encoded({'schema_version': 0}), ARG),
                 (encoded({'schema_version': 0, 'operation': 3}), ARG),
                 (encoded({'schema_version': 0, 'operation': 'mock', 'extra': 'x'}), ARG),
                 (encoded({'schema_version': 0, 'operation': 'mock', 'outcome': 'other'}), ARG),
                 (encoded({'schema_version': 0, 'operation': 'mock', 'outcome': None}), ARG),
                 (b' ' * 65537, LIMIT)]
        for payload, expected in cases:
            with self.subTest(payload=payload[:80]):
                self.submit_failure(e, payload, expected)
        # All rejected submissions must leave capacity intact.
        self.assertEqual(e.submit()['state'], 'queued')

    def test_media_operations_and_command_like_values_are_never_executed(self):
        e = self.engine()
        for operation in ('probe', 'trim', 'accurate_reencode', 'sh -c echo BAD'):
            with self.subTest(operation=operation):
                self.submit_failure(e, encoded({'schema_version': 0, 'operation': operation}), UNSUPPORTED)
        self.assertEqual(e.destroy(), OK)

    def test_explicit_input_length_does_not_require_nul_termination(self):
        config = encoded({'schema_version': 0})
        data = raw(config + b'INVALID-TRAILING-DATA')
        h = C.c_void_p()
        self.assertEqual(self.api.lib.ms_engine_create(data, len(config), C.byref(h)), OK)
        e = Engine(self.api, h)
        self.addCleanup(e.cleanup)
        payload = encoded({'schema_version': 0, 'operation': 'mock'})
        out = Buffer()
        self.assertEqual(self.api.lib.ms_submit_job(e.handle, raw(payload + b'junk'), len(payload), C.byref(out)), OK)
        response = self.api.take(out)
        e.jobs.append(response['job_id'])
        self.assertEqual(response['state'], 'queued')

    def test_embedded_nul_is_json_error_not_silent_truncation(self):
        e = self.engine()
        self.submit_failure(e, encoded({'schema_version': 0, 'operation': 'mock'}) + b'\0', JSON_ERROR)

    def test_exact_json_limit_is_accepted_and_oversize_rejected(self):
        payload = encoded({'schema_version': 0})
        padded = payload + b' ' * (65536 - len(payload))
        handle = C.c_void_p()
        self.assertEqual(self.api.lib.ms_engine_create(raw(padded), len(padded), C.byref(handle)), OK)
        e = Engine(self.api, handle)
        self.addCleanup(e.cleanup)
        request = encoded({'schema_version': 0, 'operation': 'mock'})
        request += b' ' * (65536 - len(request))
        out = Buffer()
        self.assertEqual(self.api.lib.ms_submit_job(e.handle, raw(request), len(request), C.byref(out)), OK)
        response = self.api.take(out)
        e.jobs.append(response['job_id'])
        self.assertEqual(response['state'], 'queued')
        self.submit_failure(e, request + b' ', LIMIT)

    def test_duplicate_json_keys_use_final_value_as_documented(self):
        config = b'{"schema_version":999,"schema_version":0}'
        handle = C.c_void_p()
        self.assertEqual(self.api.lib.ms_engine_create(raw(config), len(config), C.byref(handle)), OK)
        e = Engine(self.api, handle)
        self.addCleanup(e.cleanup)
        payload = b'{"schema_version":0,"operation":"trim","operation":"mock"}'
        out = Buffer()
        self.assertEqual(self.api.lib.ms_submit_job(e.handle, raw(payload), len(payload), C.byref(out)), OK)
        response = self.api.take(out)
        e.jobs.append(response['job_id'])
        self.assertEqual(response['state'], 'queued')
        self.submit_failure(e, b'{"schema_version":0,"operation":"mock","operation":"trim"}', UNSUPPORTED)

    def test_null_request_input_and_zero_length(self):
        e = self.engine()
        for length in (0, 1):
            out = dirty_buffer()
            self.assertEqual(self.api.lib.ms_submit_job(e.handle, None, length, C.byref(out)), ARG)
            self.assert_empty(out)
        self.submit_failure(e, b'', ARG)

    def test_length_limits_are_checked_before_reading_input(self):
        e = self.engine()
        out = dirty_buffer()
        self.assertEqual(self.api.lib.ms_submit_job(e.handle, None, 65537, C.byref(out)), LIMIT)
        self.assert_empty(out)
        out = dirty_buffer()
        self.assertEqual(self.api.lib.ms_get_job_snapshot(e.handle, None, 129, C.byref(out)), LIMIT)
        self.assert_empty(out)
        self.assertEqual(self.api.lib.ms_cancel_job(e.handle, None, 129), LIMIT)

    def test_mock_success_trace_and_stable_terminal(self):
        e = self.engine()
        submitted = e.submit()
        self.assertEqual(submitted['schema_version'], 0)
        self.assertEqual(submitted['state'], 'queued')
        job_id = submitted['job_id']
        self.assertEqual(e.snapshot(job_id)['state'], 'running')
        done = e.snapshot(job_id)
        self.assertEqual(done['state'], 'succeeded')
        self.assertIs(done['result']['mock_only'], True)
        self.assertIs(done['result']['file_created'], False)
        self.assertIsNone(done['error'])
        self.assertEqual(e.snapshot(job_id), done)
        self.assertEqual(e.cancel(job_id), OK)
        self.assertEqual(e.snapshot(job_id), done)

    def test_mock_failure_is_not_media_success(self):
        e = self.engine()
        job_id = e.submit('fail')['job_id']
        self.assertEqual(e.snapshot(job_id)['state'], 'running')
        failed = e.snapshot(job_id)
        self.assertEqual(failed['state'], 'failed')
        self.assertIsNone(failed['result'])
        self.assertIsNotNone(failed['error'])
        self.assertEqual(e.cancel(job_id), OK)
        self.assertEqual(e.snapshot(job_id)['state'], 'failed')

    def test_cancel_queued_and_running_are_confirmed_before_destroy(self):
        for start_running in (False, True):
            with self.subTest(start_running=start_running):
                e = self.engine()
                job_id = e.submit()['job_id']
                if start_running:
                    self.assertEqual(e.snapshot(job_id)['state'], 'running')
                self.assertEqual(e.destroy(), BUSY)
                self.assertEqual(e.cancel(job_id), OK)
                self.assertEqual(e.cancel(job_id), OK)
                self.assertEqual(e.destroy(), BUSY)
                self.assertEqual(e.snapshot(job_id)['state'], 'cancelling')
                self.assertEqual(e.destroy(), BUSY)
                done = e.snapshot(job_id)
                self.assertEqual(done['state'], 'cancelled')
                self.assertIsNone(done['result'])
                self.assertEqual(e.cancel(job_id), OK)
                self.assertEqual(e.destroy(), OK)

    def test_busy_destroy_preserves_handle_and_multiple_jobs(self):
        e = self.engine()
        first, second = e.submit()['job_id'], e.submit()['job_id']
        self.assertNotEqual(first, second)
        self.assertEqual(e.destroy(), BUSY)
        self.assertEqual(e.query('ms_get_version')['schema_version'], 0)
        self.assertEqual(e.cancel(first), OK)
        self.assertEqual(e.snapshot(first)['state'], 'cancelling')
        self.assertEqual(e.snapshot(first)['state'], 'cancelled')
        self.assertEqual(e.destroy(), BUSY, 'Other active task still owns the engine lifetime')
        self.assertEqual(e.snapshot(second)['state'], 'running')
        self.assertEqual(e.snapshot(second)['state'], 'succeeded')
        self.assertEqual(e.destroy(), OK)

    def test_capacity_counts_retained_terminal_tasks(self):
        e = self.engine({'schema_version': 0, 'max_jobs': 1})
        job_id = e.submit()['job_id']
        e.snapshot(job_id)
        self.assertEqual(e.snapshot(job_id)['state'], 'succeeded')
        self.submit_failure(e, encoded({'schema_version': 0, 'operation': 'mock'}), LIMIT)
        self.assertEqual(e.snapshot(job_id)['state'], 'succeeded')

    def test_unknown_and_invalid_job_ids(self):
        e = self.engine()
        job_id = e.submit()['job_id']
        for payload, expected in [(b'', ARG), (b'\xff', UTF8), (b'x' * 129, LIMIT),
                                  (b'not-a-job', NOT_FOUND), (job_id.encode() + b'\0tail', NOT_FOUND)]:
            with self.subTest(payload=payload[:30]):
                out = dirty_buffer()
                self.assertEqual(self.api.lib.ms_get_job_snapshot(e.handle, raw(payload), len(payload), C.byref(out)), expected)
                self.assert_empty(out)
                self.assertEqual(self.api.lib.ms_cancel_job(e.handle, raw(payload), len(payload)), expected)

    def test_null_job_input(self):
        e = self.engine()
        for length in (0, 1):
            out = dirty_buffer()
            self.assertEqual(self.api.lib.ms_get_job_snapshot(e.handle, None, length, C.byref(out)), ARG)
            self.assert_empty(out)
            self.assertEqual(self.api.lib.ms_cancel_job(e.handle, None, length), ARG)

    def test_buffer_can_outlive_engine(self):
        e = self.engine()
        out = Buffer()
        self.assertEqual(self.api.lib.ms_get_version(e.handle, C.byref(out)), OK)
        self.assertEqual(e.destroy(), OK)
        self.assertEqual(self.api.take(out)['schema_version'], 0)
        self.api.lib.ms_buffer_free(Buffer())

    def test_separate_engines_have_separate_task_state(self):
        left, right = self.engine(), self.engine()
        left_id, right_id = left.submit()['job_id'], right.submit('fail')['job_id']
        self.assertEqual(left.cancel(left_id), OK)
        self.assertEqual(right.snapshot(right_id)['state'], 'running')
        self.assertEqual(left.snapshot(left_id)['state'], 'cancelling')
        self.assertEqual(right.snapshot(right_id)['state'], 'failed')
        self.assertEqual(left.snapshot(left_id)['state'], 'cancelled')

    def test_many_create_query_free_destroy_cycles(self):
        for _ in range(1000):
            e = self.api.create()
            try:
                self.assertEqual(e.query('ms_get_version')['schema_version'], 0)
            finally:
                e.cleanup()

    def test_parallel_calls_use_different_engines(self):
        def worker(_):
            for _ in range(64):
                e = self.api.create()
                try:
                    job_id = e.submit()['job_id']
                    if e.snapshot(job_id)['state'] != 'running':
                        raise AssertionError('Unexpected state')
                    if e.snapshot(job_id)['state'] != 'succeeded':
                        raise AssertionError('Unexpected terminal')
                finally:
                    e.cleanup()
            return True
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(list(pool.map(worker, range(4))), [True] * 4)


def main():
    global API_INSTANCE
    if len(sys.argv) < 3 or sys.argv[1] != '--library':
        raise SystemExit('Usage: test_ffi.py --library /absolute/path/to/library [-v]')
    library = Path(sys.argv[2]).resolve(strict=True)
    sys.argv = [sys.argv[0], *sys.argv[3:]]
    API_INSTANCE = API(library)
    unittest.main()


if __name__ == '__main__':
    main()
