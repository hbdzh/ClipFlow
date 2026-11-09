#!/usr/bin/env python3
"""Independent MAC-003 acceptance through the real native production model and Rust queue."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import threading
import time
import traceback
sys.dont_write_bytecode = True
from support import HERE, ROOT, FFMPEG, Commands, environment, sha256, verify_output, write_json
from fixtures import make_fixtures, observed_tools, pending_preflight_tools


class Suite:
    def __init__(self, binary, label):
        self.binary = Path(binary).resolve(strict=True)
        self.directory = HERE/'build'/label
        self.directory.mkdir(parents=True, exist_ok=False)
        self.commands = Commands()
        self.binary_before = sha256(self.binary)
        self.library = self.binary.parent.parent/'Frameworks/libmedia_ffi.dylib'
        self.library_before = sha256(self.library)
        self.tests_before = {str(path.relative_to(ROOT)): sha256(path) for path in HERE.glob('*.py')}
        self.toolchain = {}
        for name, arguments in [('git_head', ['git', 'rev-parse', 'HEAD']), ('os', ['sw_vers']),
                                ('swift', ['xcrun', 'swift', '--version']), ('ffmpeg', [FFMPEG, '-version'])]:
            self.toolchain[name] = self.commands.run(arguments).stdout
        self.checks = []
        self.scenarios = []
        self.outputs = {}
        self.sources = make_fixtures(self.directory/'fixtures', self.commands)
        self.before = self.source_hashes()

    def source_hashes(self):
        paths = sorted((ROOT/'apps/macos/Sources').glob('*.swift'))
        paths += sorted((ROOT/'core').glob('*/src/**/*.rs'))
        return {str(p.relative_to(ROOT)): sha256(p) for p in paths}

    def path(self, name):
        return str(self.directory/name)

    def imp(self, *keys):
        return {'action': 'import', 'paths': [self.sources[k]['path'] for k in keys]}

    def preset(self, value='mp4_h264_standard_v1'):
        return {'action': 'preset', 'value': value}

    def select(self, index):
        return {'action': 'select', 'index': index}

    def batch(self, indices, names, wait=True):
        return {'action': 'batch', 'indices': indices, 'destinations': [self.path(n) for n in names], 'wait': wait}

    def run(self, name, actions, timeout=240):
        scenario = self.directory/(name+'.json')
        write_json(scenario, {'schema_version': 1, 'history_path': self.path(name+'-history.json'), 'actions': actions})
        result = self.commands.run([self.binary, '--queue-self-test', '--scenario', scenario], timeout=timeout)
        stdout = self.directory/(name+'-stdout.json')
        stdout.write_text(result.stdout)
        report = json.loads(result.stdout)
        self.scenarios.append({'name': name, 'scenario_sha256': sha256(scenario), 'report_sha256': sha256(stdout), 'report': report})
        assert report['passed'] and report['closed'], report
        diagnostics = report['bridgeDiagnostics']
        assert diagnostics['allCallsOffMainThread']
        assert diagnostics['outputCalls'] == diagnostics['bufferReleaseCalls']
        access = report['accessDiagnostics']
        assert access['activeGranted'] == 0 and access['heldLeases'] == 0
        assert access['granted'] == access['stops']
        assert report['heartbeatCount'] > 2 and report['heartbeatMaxIntervalMs'] < 1000
        return report['steps']

    def task(self, step, name):
        return next(t for t in step['tasks'] if t['destinationName'] == name)

    def output(self, key, name, dims, audio, selected_range=None):
        self.outputs[name] = verify_output(self.commands, self.sources[key]['path'], self.directory/name, dims, audio, selected_range)
        return self.outputs[name]

    def checker_negative_controls(self):
        source = self.sources['audio-first']['path']
        positive = verify_output(self.commands, source, source, (480, 270), [0, 2])
        rejected = []
        for name, dimensions, indices in [('wrong_dimensions', (480, 272), [0, 2]),
                                           ('wrong_audio_source_mapping', (480, 270), [2, 0])]:
            try:
                verify_output(self.commands, source, source, dimensions, indices)
            except AssertionError:
                rejected.append(name)
        reverse = self.directory/'checker-wrong-reversed-frames.mp4'
        source = self.sources['multi']['path']
        self.commands.run([FFMPEG, '-nostdin', '-hide_banner', '-v', 'error', '-y', '-i', source,
                           '-map', '0:v:0', '-map', '0:a', '-vf', 'reverse', '-c:v', 'libx264',
                           '-threads:v', '1', '-crf', '14', '-pix_fmt', 'yuv420p', '-c:a', 'copy', reverse])
        try:
            verify_output(self.commands, source, reverse, (480, 270), [1, 2])
        except AssertionError:
            rejected.append('wrong_video_frame_sequence')
        assert len(rejected) == 3, rejected
        return {'identical_positive_control': positive, 'correctly_rejected': rejected}

    def profiles_audio(self):
        names = ['multi-all.mp4', 'audio-global-2.mp4', 'landscape-720.mp4', 'portrait-720.mp4', "输出 空格 ; $() '引号'.mp4"]
        steps = self.run('profiles-audio', [self.imp('multi', 'audio-first', 'landscape', 'portrait', 'unicode'),
            self.select(0), self.preset(), {'action': 'audio', 'value': 'all_supported'},
            self.select(1), self.preset(), {'action': 'audio', 'value': 'one', 'stream_index': 2},
            {'action': 'range', 'start_us': 200000, 'end_us': 1700000},
            self.select(2), self.preset('mp4_h264_720p_v1'), {'action': 'audio', 'value': 'none'},
            self.select(3), self.preset('mp4_h264_720p_v1'),
            self.select(4), self.preset(), self.batch(list(range(5)), names, wait=False),
            self.select(1), self.preset('mp4_h264_720p_v1'), {'action': 'audio', 'value': 'none'},
            {'action': 'range', 'start_us': 0, 'end_us': 400000}, {'action': 'await-queue'}, {'action': 'restart'}, {'action': 'clear-history'}])
        batch = next(s for s in steps if s['action'] == 'batch')
        completed = next(s for s in steps if s['action'] == 'await-queue')
        assert len(batch['tasks']) == len(completed['tasks']) == 5
        assert len({t['coreJobId'] for t in completed['tasks']}) == 5
        assert len({t['sessionId'] for t in completed['tasks']}) == 1
        for name in names:
            task = self.task(completed, name)
            assert task['state'] == 'succeeded', task
            result = task['result']
            assert result['fileCreated'] and not result['mockOnly'] and result['strategy'] == 'transcode'
            assert result['reencoded'] and not result['lossless']
        frozen = self.task(completed, names[1])
        assert frozen['requestedRange'] == {'startUs': 200000, 'endUs': 1700000}
        assert frozen['audioSelection'] == {'mode': 'one', 'streamIndex': 2}
        assert frozen['result']['profile'] == 'mp4_h264_standard_v1'
        self.output('multi', names[0], (480, 270), [1, 2])
        self.output('audio-first', names[1], (480, 270), [2], (200000, 1700000))
        self.output('landscape', names[2], (1280, 720), [])
        self.output('portrait', names[3], (720, 1280), [])
        self.output('unicode', names[4], (480, 270), [1, 2])
        restart = next(s for s in steps if s['action'] == 'restart')
        assert not restart['assets'] and len(restart['tasks']) == 5
        assert all(not t.get('result') for t in restart['tasks'])
        assert not next(s for s in steps if s['action'] == 'clear-history')['tasks']
        return {'outputs': names, 'global_source_stream_index': 2, 'frozen_draft_after_UI_changes': True,
                'same_engine_session': True, 'history_restart_metadata_only': True}

    def atomic_rejection(self):
        existing = self.directory/'keep-existing.mp4'
        existing.write_bytes(b'MAC003 existing user destination must remain byte-identical')
        before = sha256(existing)
        steps = self.run('atomic-rejection', [self.imp('multi', 'landscape'), self.select(0), self.preset(),
            self.select(1), self.preset('mp4_h264_720p_v1'), self.batch([0, 1], ['must-not-exist.mp4', 'keep-existing.mp4']),
            {'action': 'queue-refresh'}])
        batch = next(s for s in steps if s['action'] == 'batch')
        assert not batch['tasks'] and batch['submissionErrors'], batch
        assert any(e['index'] == 1 and e['code'] == 'OUTPUT_EXISTS' for e in batch['submissionErrors']), batch['submissionErrors']
        assert not (self.directory/'must-not-exist.mp4').exists() and sha256(existing) == before
        assert not steps[-1].get('queue', {}).get('queuedJobIds')
        return {'existing_target_sha256': before, 'whole_batch_rejected_without_jobs_or_outputs': True,
                'rejected_item_index': batch['submissionErrors']}

    def queue_control(self):
        shim, marker = observed_tools(self.directory/'observed-tools', 4)
        existing = self.directory/'retry-existing.mp4'
        existing.write_bytes(b'MAC003 retry cannot replace an existing destination')
        existing_hash = sha256(existing)
        names = ['delay-a-out.mp4', 'delay-b-out.mp4', 'delay-c-out.mp4', 'fail-once-out.mp4']
        actions = [{'action': 'configure', 'ffmpeg': str(shim)}, self.imp('delay-a', 'delay-b', 'delay-c', 'fail-once')]
        for index in range(4):
            actions += [self.select(index), self.preset()]
        actions += [self.batch(list(range(4)), names, wait=False),
            {'action': 'wait-observation', 'path': str(marker), 'contains': 'delay-a.mp4'},
            {'action': 'queue-refresh'}, {'action': 'reorder', 'value': 'reverse'},
            {'action': 'sleep', 'milliseconds': 200}, {'action': 'queue-refresh'},
            {'action': 'cancel-task', 'value': names[2]}, {'action': 'cancel-task', 'value': names[0]},
            {'action': 'await-queue'}, {'action': 'retry', 'value': names[3], 'destination': self.path(names[3])},
            {'action': 'retry', 'value': names[3], 'destination': str(existing)},
            {'action': 'retry', 'value': names[3], 'destination': self.path('failed-retried.mp4')},
            {'action': 'retry', 'value': names[2], 'destination': self.path('cancelled-retried.mp4')},
            {'action': 'queue-refresh'}]
        steps = self.run('queue-control', actions)
        before = next(s for s in steps if s['action'] == 'queue-refresh')
        # Production refreshes are single-flight: the control receipt can precede
        # the observer's next complete snapshot. Read Core after one poll interval.
        reordered = [s for s in steps if s['action'] == 'queue-refresh'][1]
        assert len(before['queue']['queuedJobIds']) >= 2, before['queue']
        assert reordered['queue']['queuedJobIds'] == list(reversed(before['queue']['queuedJobIds']))
        completed = next(s for s in steps if s['action'] == 'await-queue')
        assert self.task(completed, names[0])['state'] == 'cancelled'
        assert self.task(completed, names[2])['state'] == 'cancelled'
        assert self.task(completed, names[1])['state'] == 'succeeded'
        failed = self.task(completed, names[3])
        assert failed['state'] == 'failed' and failed['error']['code'] == 'PROCESS_FAILED', failed
        retries = [s for s in steps if s['action'] == 'retry']
        assert len(retries[0]['tasks']) == len(retries[1]['tasks']) == 4
        assert retries[0]['submissionErrors'] and retries[1]['submissionErrors']
        final = steps[-1]
        retry = self.task(final, 'failed-retried.mp4')
        assert retry['state'] == 'succeeded' and retry['coreJobId'] != failed['coreJobId']
        assert retry['id'] != failed['id'] and self.task(final, names[3])['state'] == 'failed'
        assert self.task(final, 'cancelled-retried.mp4')['state'] == 'succeeded'
        assert not (self.directory/names[0]).exists() and not (self.directory/names[2]).exists()
        assert not (self.directory/names[3]).exists() and sha256(existing) == existing_hash
        self.output('delay-b', names[1], (480, 270), [1, 2])
        self.output('fail-once', 'failed-retried.mp4', (480, 270), [1, 2])
        self.output('delay-c', 'cancelled-retried.mp4', (480, 270), [1, 2])
        assert not list(self.directory.rglob('.mediastudio-*')), 'Owned staging files remain'
        return {'Core_order_before': before['queue']['queuedJobIds'],
                'Core_order_after': reordered['queue']['queuedJobIds'], 'mixed_terminal_results': True,
                'failed_and_cancelled_retry_new_identity': True, 'existing_target_sha256': existing_hash,
                'fault_scope': 'explicit actual encoder child failure once; other invocations exec real ffmpeg'}

    def target_race(self):
        shim, marker = observed_tools(self.directory/'race-tools', 4)
        target = self.directory/'race-target.mp4'
        controller = {'observed_actual_first_encoder': False}
        def create_after_acceptance():
            deadline = time.monotonic()+90
            while time.monotonic() < deadline:
                if marker.exists() and 'delay-a.mp4' in marker.read_text():
                    # The first encoder can only start after the entire atomic batch preflight accepted.
                    with target.open('xb') as stream:
                        stream.write(b'MAC003 independently created destination after batch acceptance')
                    controller.update({'observed_actual_first_encoder': True, 'created_sha256': sha256(target)})
                    return
                time.sleep(.01)
            controller['error'] = 'Did not observe first actual encoder in 90 seconds'
        worker = threading.Thread(target=create_after_acceptance, daemon=True)
        worker.start()
        steps = self.run('target-race', [{'action': 'configure', 'ffmpeg': str(shim)}, self.imp('delay-a', 'multi'),
            self.select(0), self.preset(), self.select(1), self.preset(),
            self.batch([0, 1], ['race-first.mp4', 'race-target.mp4'], wait=False),
            {'action': 'await-queue'}, {'action': 'retry', 'value': 'race-target.mp4', 'destination': self.path('race-retried.mp4')}])
        worker.join(timeout=1)
        assert controller['observed_actual_first_encoder'] and sha256(target) == controller['created_sha256'], controller
        completed = next(s for s in steps if s['action'] == 'await-queue')
        assert self.task(completed, 'race-first.mp4')['state'] == 'succeeded'
        failed = self.task(completed, 'race-target.mp4')
        assert failed['state'] == 'failed' and failed['error']['code'] == 'OUTPUT_EXISTS', failed
        assert self.task(steps[-1], 'race-retried.mp4')['state'] == 'succeeded'
        self.output('delay-a', 'race-first.mp4', (480, 270), [1, 2])
        self.output('multi', 'race-retried.mp4', (480, 270), [1, 2])
        return {'race': 'actual filesystem destination creation after atomic preflight, before second job',
                'created_target_unchanged': True, 'controller': controller,
                'other_item_success_and_failed_item_retry_new_destination': True}

    def native_capacity(self):
        shim, marker = observed_tools(self.directory/'capacity-tools', 60)
        names = [f'capacity-{index:02d}.mp4' for index in range(32)]
        steps = self.run('native-capacity', [{'action': 'configure', 'ffmpeg': str(shim)}, self.imp('delay-tiny'),
            self.preset(), self.batch([0]*32, names, wait=False),
            {'action': 'wait-observation', 'path': str(marker), 'contains': 'delay-tiny.mp4'},
            {'action': 'queue-refresh'}, self.batch([0], ['capacity-33-must-not-exist.mp4'], wait=False),
            {'action': 'queue-refresh'}, {'action': 'cancel'}, {'action': 'await-queue'}], timeout=300)
        batches = [s for s in steps if s['action'] == 'batch']
        assert len(batches[0]['tasks']) == 32 and len({t['coreJobId'] for t in batches[0]['tasks']}) == 32
        snapshots = [s for s in steps if s['action'] == 'queue-refresh']
        assert snapshots[0]['queue']['activeCount'] == 32 and snapshots[0]['queue']['queueCapacity'] == 32
        assert len(batches[1]['tasks']) == 32 and batches[1]['submissionErrors'], batches[1]
        assert snapshots[1]['queue']['activeCount'] == 32
        final = next(s for s in steps if s['action'] == 'await-queue')
        assert len(final['tasks']) == 32 and all(t['state'] == 'cancelled' for t in final['tasks'])
        assert not any((self.directory/name).exists() for name in names)
        assert not (self.directory/'capacity-33-must-not-exist.mp4').exists()
        return {'one_native_engine_real_accepted_active_count': 32, '33rd_new_job_rejected': True,
                'all_32_cancelled_and_outputs_absent': True,
                'scope_limit': '2-second silent fixture, not maximum duration/size or long-term resource pressure'}

    def pending_submission(self, operation):
        ffmpeg, ffprobe, armed, marker, release = pending_preflight_tools(self.directory/('pending-'+operation+'-tools'))
        observed = {'real_preflight_observed': False}
        def release_real_child():
            deadline = time.monotonic()+90
            while time.monotonic() < deadline:
                if marker.exists():
                    observed['real_preflight_observed'] = True
                    time.sleep(1)
                    release.write_text('Allow the actual ffprobe subprocess to continue\n')
                    return
                time.sleep(.01)
        worker = threading.Thread(target=release_real_child, daemon=True)
        worker.start()
        actions = [{'action': 'configure', 'ffmpeg': str(ffmpeg), 'ffprobe': str(ffprobe)},
            self.imp('delay-a', 'delay-tiny'), self.select(0), self.preset(), self.select(1), self.preset(),
            self.batch([0], [operation+'-old.mp4'], wait=False),
            {'action': 'wait-observation', 'path': str(armed), 'contains': 'real encoder armed'},
            {**self.batch([1], [operation+'-new.mp4'], wait=False), 'submission_wait': False},
            {'action': 'wait-observation', 'path': str(marker), 'contains': 'real preflight child reached'}]
        if operation == 'cancel-old':
            actions += [{'action': 'cancel-task', 'value': operation+'-old.mp4'}, {'action': 'await-submission'}, {'action': 'await-queue'}]
        elif operation == 'cancel-pending':
            actions += [{'action': 'cancel-pending'}, {'action': 'await-submission'}, {'action': 'await-queue'}]
        else:
            actions += [{'action': 'shutdown'}]
        steps = self.run('pending-'+operation, actions)
        worker.join(timeout=1)
        assert observed['real_preflight_observed'], observed
        gated = [s for s in steps if s['action'] == 'wait-observation'][-1]
        assert gated['submitting'] and gated['pendingSubmissionToken'], gated
        final = steps[-1]
        if operation == 'cancel-old':
            assert self.task(final, operation+'-old.mp4')['state'] == 'cancelled'
            assert self.task(final, operation+'-new.mp4')['state'] == 'succeeded'
            self.output('delay-tiny', operation+'-new.mp4', (320, 180), [])
        else:
            assert self.task(final, operation+'-new.mp4')['state'] == 'cancelled'
            assert not (self.directory/(operation+'-new.mp4')).exists()
        return {'operation': operation, 'real_preflight_gate_observed': True, 'new_submission_identity_retained': True,
                'final_closed_and_balanced_scope': True}

    def first_connect_shutdown(self):
        details = []
        for index in range(4):
            name = f'first-connect-close-{index}.mp4'
            steps = self.run(f'first-connect-close-{index}', [self.imp('delay-tiny'), self.preset(),
                {**self.batch([0], [name], wait=False), 'submission_wait': False}, {'action': 'shutdown'}])
            final = steps[-1]
            assert final['closed']
            assert all(t['state'] in ['cancelled', 'succeeded', 'failed'] for t in final['tasks'])
            details.append({'iteration': index, 'terminal_tasks': len(final['tasks']), 'closed': True})
        boundaries = []
        for boundary in ['before-connect', 'before-submit']:
            name = 'boundary-'+boundary+'.mp4'
            marker = self.directory/(boundary+'-observation')
            steps = self.run(boundary+'-shutdown', [self.imp('delay-tiny'), self.preset(),
                {'action': 'submission-boundary-observation', 'path': str(marker), 'value': boundary, 'milliseconds': 800},
                {**self.batch([0], [name], wait=False), 'submission_wait': False},
                {'action': 'wait-observation', 'path': str(marker), 'contains': boundary}, {'action': 'shutdown'}])
            assert marker.exists() and boundary in marker.read_text()
            assert not steps[-1]['tasks'] and not (self.directory/name).exists()
            boundaries.append({'boundary': boundary, 'no_job_or_output': True, 'closed': steps[-1]['closed']})
        return {'scope': 'Real async first-queue submissions immediately racing shutdown; plus deterministic observer pauses after actual close/connect (no fake Core)',
                'iterations': details, 'deterministic_boundaries': boundaries}

    def check(self, name, function):
        try:
            self.checks.append({'name': name, 'result': 'PASS', 'detail': function()})
        except Exception as error:
            self.checks.append({'name': name, 'result': 'FAIL', 'error': str(error), 'traceback': traceback.format_exc()})

    def finish(self, label):
        self.check('source_and_fixture_identity', lambda: self.identities())
        result = {'schema_version': 1, 'label': label, 'scope': 'MAC-003 production native model + real Rust C ABI + actual FFmpeg, not GUI automation',
                  'captured_at': datetime.now(timezone.utc).isoformat(), 'environment': environment(), 'toolchain': self.toolchain,
                  'binary': str(self.binary), 'binary_sha256': self.binary_before, 'library_sha256': self.library_before,
                  'source_sha256': self.before, 'test_source_sha256': self.tests_before, 'fixtures': self.sources, 'checks': self.checks,
                  'physical_outputs': self.outputs, 'scenarios': self.scenarios, 'commands': self.commands.records,
                  'passed': all(check['result'] == 'PASS' for check in self.checks),
                  'not_run': ['GUI mouse/keyboard flows (root owns)', 'App Sandbox security-scoped authorization',
                              '32 simultaneous maximum-length native jobs', '100 real native exports/history eviction',
                              'ambiguous IPC receipt fault', 'full subjective audiovisual fidelity',
                              'Intel macOS/Windows/HarmonyOS', 'formal signing/notarization/distribution']}
        target = ROOT/'docs/mac-003/evidence'/('independent-'+label+'.json')
        write_json(target, result)
        print(json.dumps({'passed': result['passed'], 'checks': self.checks, 'evidence': str(target)}, ensure_ascii=False, indent=2))
        return 0 if result['passed'] else 1

    def identities(self):
        assert self.before == self.source_hashes(), 'Production sources changed during suite; rerun stable build'
        assert self.binary_before == sha256(self.binary) and self.library_before == sha256(self.library), 'Build artifacts changed during suite'
        assert self.tests_before == {str(path.relative_to(ROOT)): sha256(path) for path in HERE.glob('*.py')}, 'QA sources changed during suite'
        assert all(sha256(value['path']) == value['sha256'] for value in self.sources.values()), 'A source fixture changed'
        return {'source_and_fixture_hashes_unchanged': True}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--binary', required=True)
    parser.add_argument('--label', required=True)
    args = parser.parse_args()
    suite = Suite(args.binary, args.label)
    suite.check('independent_checker_negative_controls', suite.checker_negative_controls)
    suite.check('profiles_audio_draft_freeze_history', suite.profiles_audio)
    suite.check('atomic_batch_preflight_rejection', suite.atomic_rejection)
    suite.check('Core_reorder_cancel_runtime_failure_retry', suite.queue_control)
    suite.check('accepted_batch_real_destination_race', suite.target_race)
    suite.check('native_shared_engine_total_capacity_32', suite.native_capacity)
    suite.check('cancel_old_task_during_new_preflight', lambda: suite.pending_submission('cancel-old'))
    suite.check('cancel_new_batch_during_preflight', lambda: suite.pending_submission('cancel-pending'))
    suite.check('shutdown_during_real_preflight', lambda: suite.pending_submission('shutdown'))
    suite.check('first_connection_shutdown_interleave', suite.first_connect_shutdown)
    return suite.finish(args.label)


if __name__ == '__main__':
    raise SystemExit(main())
