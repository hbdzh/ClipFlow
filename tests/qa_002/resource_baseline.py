#!/usr/bin/env python3
"""AC26 signed native CLI resource scenarios; no playback or GUI automation."""
import argparse
import hashlib
import json
import os
import plistlib
import re
import statistics
import subprocess
import time
import traceback
from pathlib import Path

KINDS = ['accurate', 'reject', 'cancel'] + ['copy', 'accurate', 'reject', 'cancel'] * 5
PROTOCOL = 2
CANCEL_US = 120_000_000


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def write(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')


def named(a, name):
    label = getattr(a, 'label', '')
    return 'resource-' + name + ('-' + label if label else '')


def ps():
    q = subprocess.run(['/bin/ps', '-axo', 'pid=,ppid=,pgid=,rss=,pcpu=,comm='],
                       capture_output=True, text=True, check=True)
    rows = {}
    for line in q.stdout.splitlines():
        values = line.strip().split(None, 5)
        if len(values) == 6:
            pid, ppid, pgid, rss = map(int, values[:4])
            rows[pid] = dict(pid=pid, ppid=ppid, pgid=pgid, rss_bytes=rss * 1024,
                             pcpu=float(values[4]), executable=values[5])
    return rows


def tree(rows, parent):
    selected = {parent}
    while True:
        more = {pid for pid, row in rows.items() if row['ppid'] in selected}
        if more <= selected:
            return [rows[pid] for pid in sorted(selected) if pid in rows]
        selected |= more


def stages(root):
    return sorted(str(path.relative_to(root)) for path in root.rglob('*')
                  if path.name.startswith(('.mediastudio-', 'mediastudio-ai-')))


def prepare(a):
    receipt = a.evidence / (named(a, 'fixtures') + '.json')
    assert not receipt.exists(), 'Never replace fixture provenance'
    root = a.work.resolve()
    root.mkdir(parents=True, mode=0o700, exist_ok=False)
    for name in ['markers', 'gates', 'outputs']:
        (root / name).mkdir(mode=0o700)
    commands = []
    ff = a.fixture_ffmpeg.resolve()

    def command(argv):
        result = subprocess.run(list(map(str, argv)), stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=180)
        commands.append(dict(argv=list(map(str, argv)), exit=result.returncode,
                             stderr=result.stderr))
        assert result.returncode == 0, commands[-1]

    short, seed, long, cancel = [root / name for name in
                                ['silent short.mp4', 'long seed.mp4',
                                 'ten minute video.mp4', 'cancel video.mp4']]
    command([ff, '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
             'testsrc2=size=320x180:rate=30:duration=2', '-f', 'lavfi', '-i',
             'anullsrc=r=48000:cl=stereo', '-t', '2', '-c:v', 'libx264', '-threads',
             '1', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', '-g', '30', '-c:a', 'aac', short])
    command([ff, '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
             'testsrc2=size=1920x1080:rate=30:duration=2', '-an', '-c:v', 'libx264',
             '-threads', '1', '-preset', 'ultrafast', '-crf', '28', '-pix_fmt',
             'yuv420p', '-g', '30', seed])
    for target, loops, duration in [(long, 299, 600), (cancel, 59, 120)]:
        command([ff, '-nostdin', '-v', 'error', '-stream_loop', str(loops), '-i',
                 seed, '-map', '0:v:0', '-c', 'copy', '-t', str(duration), target])
    (root / 'existing.mp4').write_bytes(b'QA002 EXISTING TARGET MUST REMAIN')
    write(receipt, dict(
        origin='Locally synthesized testsrc2/anullsrc; no external media', commands=commands,
        tool_sha256=sha(ff), files={str(path): sha(path) for path in
                                  [short, seed, long, cancel, root / 'existing.mp4']}))


def scenario(a):
    root = a.work.resolve()
    actions, kinds = [], []
    if a.mode == 'cycles':
        kinds = KINDS[:]
        for n, kind in enumerate(kinds):
            source = root / ('cancel video.mp4' if kind == 'cancel' else 'silent short.mp4')
            destination = root / 'existing.mp4' if kind == 'reject' else root / 'outputs' / f'cycle-{n:02d}.mp4'
            actions += [dict(action='import', paths=[str(source)]),
                        dict(action='mode', value='accurateReencode' if kind in ['accurate', 'cancel'] else 'streamCopyV1'),
                        dict(action='range', start_us=0, end_us=CANCEL_US if kind == 'cancel' else 2_000_000),
                        dict(action='export', destination=str(destination), wait=kind != 'cancel')]
            if kind == 'cancel':
                actions += [dict(action='sleep', milliseconds=200), dict(action='queue-refresh'),
                            dict(action='cancel-and-wait')]
            # The previous model has CLOSED before this new model imports and
            # renders the same short source used by every idle checkpoint.
            actions += [dict(action='restart'), dict(action='import', paths=[str(root / 'silent short.mp4')]),
                        dict(action='preview-frame', destination=str(root / 'markers' / f'{n:02d}.png'), position_us=0),
                        dict(action='wait-observation', path=str(root / 'gates' / f'{n:02d}.ready'), contains='go'),
                        dict(action='remove', index=0)]
    else:
        actions = [dict(action='import', paths=[str(root / 'ten minute video.mp4')]),
                   dict(action='preset', value='mp4_h264_standard_v1'),
                   dict(action='batch', indices=[0] * 8,
                        destinations=[str(root / 'outputs' / f'long-{n:02d}.mp4') for n in range(8)], wait=False)]
        actions += [dict(action='sleep', milliseconds=5000)] * 125
        actions += [dict(action='cancel-and-wait'), dict(action='shutdown')]
    assert all(action['action'] != 'play' for action in actions)
    return actions, kinds


def run(a):
    root, ev, app = a.work.resolve(), a.evidence.resolve(), a.app.resolve()
    info = plistlib.loads((app / 'Contents/Info.plist').read_bytes())
    container = Path.home() / 'Library/Containers' / info['CFBundleIdentifier'] / 'Data'
    assert root.is_relative_to(container / 'tmp'), 'Use isolated candidate private tmp'
    assert a.mode != 'cycles' or a.label, 'Use a fresh work root and --label v2 for revised cycles'
    before = {str(path.relative_to(app)): sha(path) for path in app.rglob('*') if path.is_file()}
    actions, kinds = scenario(a)
    case = root / (a.mode + ('-' + a.label if a.label else '') + '-scenario.json')
    assert not case.exists(), 'Never replace a measured scenario'
    write(case, dict(schema_version=1, history_path=str(root / (a.mode + '-history.json')), actions=actions))
    argv = [str(app / 'Contents/MacOS/MediaStudio'), '--queue-self-test', '--scenario', str(case)]
    env = {key: value for key, value in os.environ.items() if not key.startswith(('MEDIASTUDIO_', 'DYLD_'))}
    env.update(PATH='/usr/bin:/bin', TMPDIR=str(root) + '/',
               MEDIASTUDIO_ACCURATE_TIMEOUT_MS='900000', MEDIASTUDIO_TRANSCODE_TIMEOUT_MS='900000')
    stem = named(a, a.mode)
    log, stdout, stderr, summary = [ev / (stem + suffix) for suffix in
                                  ['-samples.jsonl', '-native.json', '-stderr.log', '-summary.json']]
    assert not any(path.exists() for path in [log, stdout, stderr, summary]), 'Never replace prior resource evidence'
    points, seen, checkpoint, timeline = [], set(), 0, []
    start, runner_error, exit_code = time.monotonic(), None, None
    with stdout.open('x') as out, stderr.open('x') as err, log.open('x') as samples:
        process = subprocess.Popen(argv, env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=err)
        idle = []
        try:
            while process.poll() is None:
                now = time.monotonic()
                assert now - start < (600 if a.mode == 'cycles' else 1500), 'Native resource deadline'
                rows = tree(ps(), process.pid)
                seen.update(row['pid'] for row in rows)
                row = dict(elapsed_seconds=now - start, native_pid=process.pid, processes=rows,
                           resident_tree_sum_bytes=sum(item['rss_bytes'] for item in rows))
                timeline.append(row)
                samples.write(json.dumps(row) + '\n'); samples.flush()
                if a.mode == 'cycles' and checkpoint < len(kinds) and (root / 'markers' / f'{checkpoint:02d}.png').is_file():
                    child = [item for item in rows if item['pid'] != process.pid]
                    pending = stages(root)
                    main = [item for item in rows if item['pid'] == process.pid]
                    idle = idle + [(now - start, main[0]['rss_bytes'])] if not child and not pending and main else []
                    if len(idle) >= 10:
                        window = idle[-10:]
                        points.append(dict(checkpoint=checkpoint, kind=kinds[checkpoint], warmup=checkpoint < 3,
                                           rss_bytes=int(statistics.median(value for _, value in window)),
                                           idle_rss_samples=[value for _, value in window],
                                           idle_elapsed_seconds=[value for value, _ in window], descendants=child,
                                           staging=pending, elapsed_seconds=now - start,
                                           marker_phase='post-restart-short-preview'))
                        (root / 'gates' / f'{checkpoint:02d}.ready').write_text('go\n')
                        checkpoint += 1; idle = []
                time.sleep(.2)
            exit_code = process.wait()
        except Exception:
            runner_error = traceback.format_exc()
        finally:
            if process.poll() is None:
                for n in range(len(kinds)):
                    (root / 'gates' / f'{n:02d}.ready').write_text('go\n')
                try:
                    process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    try:
                        process.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        process.kill(); process.wait(timeout=15)
            exit_code = process.returncode
    try:
        report = json.loads(stdout.read_text()) if stdout.stat().st_size else {}
    except json.JSONDecodeError:
        report = {}; runner_error = (runner_error or '') + traceback.format_exc()
    current = ps()
    after = {str(path.relative_to(app)): sha(path) for path in app.rglob('*') if path.is_file()}
    # Also observe app-path processes that could have reparented before a sample.
    app_processes = [row for row in current.values() if row['executable'].startswith(str(app) + '/')]
    result = dict(measurement_protocol_version=PROTOCOL, driver_sha256=sha(Path(__file__)),
                  scenario_sha256=sha(case), label=a.label,
                  scope='Actual unchanged signed native candidate CLI, production WorkspaceModel/CoreBridge/AVFoundation. No GUI or playback claim.',
                  argv=argv, environment_overrides={key: env[key] for key in
                  ['PATH', 'TMPDIR', 'MEDIASTUDIO_ACCURATE_TIMEOUT_MS', 'MEDIASTUDIO_TRANSCODE_TIMEOUT_MS']},
                  exit=exit_code, app=str(app), bundle_files=before, bundle_unchanged=before == after,
                  elapsed_seconds=time.monotonic() - start, sampled_pids=sorted(seen),
                  surviving_pids=[current[pid] for pid in seen if pid in current],
                  same_app_processes_after=app_processes, staging_after=stages(root), idle_checkpoints=points,
                  no_playback_action=True, native_passed=report.get('passed') is True,
                  finite_run_is_not_no_leak_proof=True, runner_error=runner_error,
                  rss_method='ps RSS resident bytes; shared tree pages may double count; not physical footprint; XPC/shared services excluded')
    try:
        assert runner_error is None, runner_error
        validate(a, result, report, points, timeline, root)
        result['result'] = 'PASS'
    except Exception:
        result.update(result='FAIL', error=traceback.format_exc())
    write(summary, result)
    print(json.dumps({key: value for key, value in result.items() if key in
                      ['result', 'measured_cycles', 'elapsed_seconds', 'maximum_recovered_rss_growth_bytes',
                       'sampled_activity_span_seconds', 'tree_peak_resident_sum_bytes', 'error']}), flush=True)
    assert result['result'] == 'PASS'


def exact_task(step, destination):
    matches = [task for task in step['tasks'] if task['destinationName'] == destination]
    assert len(matches) == 1, ('Expected one current task', destination)
    task = matches[0]
    assert task.get('coreJobId') and task.get('sessionId') and task.get('id')
    return task


def queue_entry(step, task):
    queue = step.get('queue') or {}
    matches = [item for item in queue.get('history', [])
               if item['destinationName'] == task['destinationName'] and item['jobId'] == task['coreJobId']]
    assert len(matches) == 1, ('Missing current Core history', task['destinationName'])
    return queue, matches[0]


def validate_cycles(result, report, points, root):
    assert result.get('measurement_protocol_version') == PROTOCOL, 'Old sampling order is not protocol2 evidence'
    assert len(points) == len(KINDS) == 23
    steps, cursor, outcomes = report['steps'], 0, []
    for n, kind in enumerate(KINDS):
        expected = ['import', 'mode', 'range', 'export']
        if kind == 'cancel':
            expected += ['sleep', 'queue-refresh', 'cancel-and-wait']
        expected += ['before-restart', 'restart', 'import', 'preview-frame', 'wait-observation', 'remove']
        segment = steps[cursor:cursor + len(expected)]; cursor += len(expected)
        assert [item['action'] for item in segment] == expected, ('Cycle action order', n)
        export = segment[3]
        destination = f'cycle-{n:02d}.mp4'
        output = root / 'outputs' / destination
        expected_mode = 'accurateReencode' if kind in ['accurate', 'cancel'] else 'streamCopyV1'
        item = dict(checkpoint=n, kind=kind)
        if kind == 'reject':
            errors = export.get('submissionErrors') or []
            assert len(errors) == 1 and errors[0].get('code') == 'OUTPUT_EXISTS' and errors[0].get('index') == 0, ('Expected real target rejection', n)
            assert {task['id'] for task in export['tasks']} == {task['id'] for task in segment[2]['tasks']}
            assert not any(task['destinationName'] == 'existing.mp4' for task in export['tasks'])
            queue = export.get('queue') or {}
            assert queue.get('activeCount') == 0 and not queue.get('queuedJobIds') and not queue.get('history')
            assert not output.exists()
            item.update(state='rejected', code='OUTPUT_EXISTS')
        else:
            assert not export.get('submissionErrors')
            observation = segment[5] if kind == 'cancel' else export
            task = exact_task(observation, destination)
            assert task['mode'] == expected_mode
            assert task['requestedRange'] == dict(startUs=0, endUs=CANCEL_US if kind == 'cancel' else 2_000_000)
            if kind == 'cancel':
                assert task['state'] in ['queued', 'running'], ('Current job was not active before cancel', n)
                queue = observation.get('queue') or {}
                assert task['coreJobId'] == queue.get('runningJobId') or task['coreJobId'] in queue.get('queuedJobIds', [])
                terminal = exact_task(segment[6], destination)
                assert all(terminal.get(key) == task.get(key) for key in ['id', 'coreJobId', 'sessionId', 'sourceAssetId'])
                assert terminal['state'] == 'cancelled', ('Wrong current cancellation terminal state', n)
                queue, history = queue_entry(segment[6], terminal)
                assert history['state'] == 'cancelled'
                assert queue.get('activeCount') == 0 and not queue.get('queuedJobIds') and not queue.get('cleanupUnconfirmed')
                assert not output.exists()
                item.update(state='cancelled', active_state=task['state'], core_job_id=task['coreJobId'], session_id=task['sessionId'])
            else:
                assert task['state'] == 'succeeded' and not task.get('error'), ('Expected export success', n)
                media = task.get('result') or {}
                assert media.get('fileCreated') is True and media.get('mockOnly') is False
                assert media.get('strategy') == ('accurate_reencode' if kind == 'accurate' else 'stream_copy')
                queue, history = queue_entry(export, task)
                assert history['state'] == 'succeeded' and queue.get('activeCount') == 0
                assert not queue.get('queuedJobIds') and not queue.get('cleanupUnconfirmed')
                assert output.is_file() and not output.is_symlink() and output.stat().st_size > 0
                item.update(state='succeeded', core_job_id=task['coreJobId'], session_id=task['sessionId'],
                            output=str(output), output_sha256=sha(output), bytes=output.stat().st_size,
                            independent_decode='PARENT_SEPARATE_EVIDENCE_REQUIRED')
        closed, restarted, ready_import, frame, wait, removed = segment[-6:]
        assert closed['closed'] is True and closed['accessDiagnostics']['heldLeases'] == 0
        assert restarted['closed'] is False and not restarted['assets']
        assert len(ready_import['assets']) == 1 and ready_import['assets'][0]['sourceName'] == 'silent short.mp4'
        assert frame['preview']['ready'] is True and frame['preview']['firstVisibleImageConfirmed'] is True
        assert len(frame['assets']) == 1 and frame['assets'][0]['id'] == ready_import['assets'][0]['id']
        assert not frame['preview']['playbackObserved'] and not removed['assets']
        point = points[n]
        assert point['checkpoint'] == n and point['kind'] == kind and point['warmup'] == (n < 3)
        assert point.get('marker_phase') == 'post-restart-short-preview'
        assert not point['descendants'] and not point['staging']
        values, times = point['idle_rss_samples'], point['idle_elapsed_seconds']
        assert len(values) == len(times) == 10 and all(isinstance(value, int) and value > 0 for value in values)
        assert times == sorted(set(times)) and times[-1] == point['elapsed_seconds']
        assert point['rss_bytes'] == int(statistics.median(values))
        assert n == 0 or times[0] > points[n - 1]['elapsed_seconds']
        outcomes.append(item)
    assert [item['action'] for item in steps[cursor:]] == ['final-shutdown']
    assert steps[-1]['closed'] is True
    baseline = points[2]['rss_bytes']
    growth = max(point['rss_bytes'] for point in points[3:]) - baseline
    limit = max(100 * 1024 * 1024, baseline / 10)
    result.update(warmup_cycles=3, measured_cycles=20, warmup_baseline_rss_bytes=baseline,
                  maximum_recovered_rss_growth_bytes=growth, allowed_growth_bytes=limit,
                  checkpoint_scope='Fresh model after preceding model shutdown/restart; same short preview ready, no playback',
                  cycle_outcomes=outcomes)
    assert growth <= limit, 'RSS growth gate'


def validate(a, result, report, points, timeline, root):
    assert result['exit'] == 0 and report.get('passed') is True and result['bundle_unchanged']
    assert not result['surviving_pids'] and not result.get('same_app_processes_after') and not result['staging_after']
    assert (root / 'existing.mp4').read_bytes() == b'QA002 EXISTING TARGET MUST REMAIN'
    assert not any(step['preview']['playbackObserved'] for step in report['steps'])
    if a.mode == 'cycles':
        validate_cycles(result, report, points, root)
    else:
        active = [row for row in timeline if any(item['pid'] != row['native_pid'] and
                  Path(item['executable']).name in ['ffmpeg', 'ffprobe'] for item in row['processes'])]
        span = active[-1]['elapsed_seconds'] - active[0]['elapsed_seconds'] if active else 0
        sleeps = [step for step in report['steps'] if step['action'] == 'sleep']
        result.update(activity_scope='8 real P01 jobs in a sampled continuously active native queue; 600-second input; not one job guaranteed600 wall seconds or a claim of continuous CPU occupancy',
                      sampled_activity_span_seconds=span,
                      maximum_gap_between_media_child_observations_seconds=max((b['elapsed_seconds'] - a['elapsed_seconds'] for a, b in zip(active, active[1:])), default=0),
                      native_peak_rss_bytes=max(item['rss_bytes'] for row in timeline for item in row['processes'] if item['pid'] == row['native_pid']),
                      tree_peak_resident_sum_bytes=max(row['resident_tree_sum_bytes'] for row in timeline),
                      child_peak_rss_bytes=max((item['rss_bytes'] for row in timeline for item in row['processes'] if item['pid'] != row['native_pid']), default=0),
                      queue_active_at_all_125_checkpoints=len(sleeps) == 125 and all(step.get('queue', {}).get('runningJobId') for step in sleeps))
        assert span >= 600 and result['queue_active_at_all_125_checkpoints'], 'Activity duration gate'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=['prepare', 'cycles', 'long'], required=True)
    for key in ['app', 'work', 'evidence']:
        parser.add_argument('--' + key, type=Path, required=True)
    parser.add_argument('--fixture-ffmpeg', type=Path)
    parser.add_argument('--label', default='')
    a = parser.parse_args()
    assert not a.label or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,31}', a.label), 'Invalid evidence label'
    a.evidence.mkdir(parents=True, exist_ok=True)
    prepare(a) if a.mode == 'prepare' else run(a)


if __name__ == '__main__':
    main()
