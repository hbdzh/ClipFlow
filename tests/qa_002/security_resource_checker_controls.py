#!/usr/bin/env python3
"""Pure resource-checker controls, never a candidate or media workload.

Synthetic mode uses dummy files and synthetic evidence only. Real mode mutates
in-memory copies of archived logs. Neither mode launches native apps or players.
"""
import argparse
import copy
import hashlib
import importlib.util
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace


def synthetic(checker, root):
    (root / 'outputs').mkdir()
    (root / 'existing.mp4').write_bytes(b'QA002 EXISTING TARGET MUST REMAIN')
    steps, points, history = [], [], []

    def step(action, tasks=None, assets=None, **extras):
        value = dict(action=action, tasks=copy.deepcopy(history if tasks is None else tasks),
                     assets=copy.deepcopy([] if assets is None else assets), closed=False,
                     preview=dict(playbackObserved=False, ready=True, firstVisibleImageConfirmed=True),
                     accessDiagnostics=dict(heldLeases=0), submissionErrors=[],
                     queue=dict(activeCount=0, queuedJobIds=[], history=[], cleanupUnconfirmed=False))
        value.update(extras)
        return value

    def queue(task, active=False):
        return dict(activeCount=1 if active else 0, queuedJobIds=[], cleanupUnconfirmed=False,
                    runningJobId=task['coreJobId'] if active else None,
                    history=[] if active else [dict(destinationName=task['destinationName'],
                                                     jobId=task['coreJobId'], state=task['state'])])

    for n, kind in enumerate(checker.KINDS):
        source = dict(id=f'input-{n}', sourceName='cancel video.mp4' if kind == 'cancel' else 'silent short.mp4')
        steps += [step(action, assets=[source]) for action in ['import', 'mode', 'range']]
        destination = f'cycle-{n:02d}.mp4'
        task = dict(id=f'task-{n}', sessionId=f'session-{n}', sourceAssetId=source['id'],
                    coreJobId='media-1', destinationName=destination,
                    mode='accurateReencode' if kind in ['accurate', 'cancel'] else 'streamCopyV1',
                    requestedRange=dict(startUs=0, endUs=checker.CANCEL_US if kind == 'cancel' else 2_000_000),
                    state='running' if kind == 'cancel' else 'succeeded', error=None,
                    result=dict(fileCreated=True, mockOnly=False,
                                strategy='accurate_reencode' if kind == 'accurate' else 'stream_copy'))
        if kind == 'reject':
            steps.append(step('export', assets=[source], submissionErrors=[dict(code='OUTPUT_EXISTS', index=0)]))
        elif kind == 'cancel':
            active = [task] + history
            steps += [step(action, active, [source], queue=queue(task, active=True))
                      for action in ['export', 'sleep', 'queue-refresh']]
            task = copy.deepcopy(task); task.update(state='cancelled', result=None)
            history = [task] + history
            steps.append(step('cancel-and-wait', assets=[source], queue=queue(task)))
        else:
            history = [task] + history
            (root / 'outputs' / destination).write_bytes(b'SYNTHETIC CHECKER DATA; NOT MEDIA')
            steps.append(step('export', assets=[source], queue=queue(task)))
        fresh = dict(id=f'restored-preview-{n}', sourceName='silent short.mp4')
        steps += [step('before-restart', assets=[source], closed=True), step('restart'),
                  step('import', assets=[fresh]), step('preview-frame', assets=[fresh]),
                  step('wait-observation', assets=[fresh]), step('remove')]
        rss = (150 + n) * 1024 * 1024
        times = [n * 10 + i / 5 for i in range(10)]
        points.append(dict(checkpoint=n, kind=kind, warmup=n < 3, rss_bytes=rss,
                           idle_rss_samples=[rss] * 10, idle_elapsed_seconds=times,
                           descendants=[], staging=[], elapsed_seconds=times[-1],
                           marker_phase='post-restart-short-preview'))
    steps.append(step('final-shutdown', closed=True))
    result = dict(exit=0, bundle_unchanged=True, surviving_pids=[], same_app_processes_after=[],
                  staging_after=[], measurement_protocol_version=checker.PROTOCOL, idle_checkpoints=points)
    return result, dict(passed=True, steps=steps), []


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--evidence', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--checker', type=Path, default=Path(__file__).with_name('resource_baseline.py'))
    parser.add_argument('--synthetic', action='store_true')
    parser.add_argument('--label', default='')
    a = parser.parse_args()
    assert not a.output.exists(), 'Do not overwrite prior checker evidence'
    spec = importlib.util.spec_from_file_location('resource_baseline', a.checker)
    checker = importlib.util.module_from_spec(spec); spec.loader.exec_module(checker)
    with tempfile.TemporaryDirectory(prefix='mediastudio-resource-checker-', dir='/private/tmp') as temp:
        if a.synthetic:
            root = Path(temp)
            result, native, timeline = synthetic(checker, root)
            base = 'SYNTHETIC_CONTROL_ONLY; dummy files are not media or actual job evidence'
        else:
            assert a.evidence is not None
            stem = 'resource-cycles' + ('-' + a.label if a.label else '')
            result = json.loads((a.evidence / (stem + '-summary.json')).read_text())
            native = json.loads((a.evidence / (stem + '-native.json')).read_text())
            timeline = [json.loads(line) for line in (a.evidence / (stem + '-samples.jsonl')).read_text().splitlines()]
            root = Path(result['argv'][-1]).parent
            base = 'In-memory copies of completed actual native-cycle logs; media files read only'
        cases = []

        def run(name, expected, mutate=lambda r, n, p: None):
            r, n = copy.deepcopy(result), copy.deepcopy(native)
            points = r['idle_checkpoints']; mutate(r, n, points)
            try:
                checker.validate(SimpleNamespace(mode='cycles'), r, n, points, timeline, root)
                accepted, reason = True, None
            except AssertionError as error:
                accepted, reason = False, str(error)
            cases.append(dict(name=name, expected_acceptance=expected, actual_acceptance=accepted,
                              result='PASS' if accepted == expected else 'FAIL', rejection_reason=reason))

        def failed_positive(r, n, points):
            for item in n['steps']:
                if item['action'] == 'export':
                    for task in item['tasks']:
                        if task['destinationName'].startswith('cycle-') and task['state'] == 'succeeded':
                            task.update(state='failed', result=None)

        def stale_cancel(r, n, points):
            for item in n['steps']:
                if item['action'] == 'cancel-and-wait':
                    for task in item['tasks']:
                        if task['state'] == 'cancelled':
                            task['state'] = 'succeeded'
                    item['tasks'].append(dict(id='old-history', state='cancelled', destinationName='unrelated-prior.mp4'))

        def excessive_rss(r, n, points):
            baseline = points[2]['rss_bytes']
            value = baseline + max(100 * 1024 * 1024, baseline // 10) + 1024
            points[3].update(rss_bytes=value, idle_rss_samples=[value] * 10)

        run('unmodified-control-positive', True)
        run('failed-success-exports-must-not-pass', False, failed_positive)
        run('unrelated-old-cancel-must-not-mask-current-success', False, stale_cancel)
        run('over-limit-rss-must-fail', False, excessive_rss)
        run('changed-package-must-fail', False, lambda r, n, p: r.update(bundle_unchanged=False))
        run('surviving-sampled-child-must-fail', False, lambda r, n, p: r.update(surviving_pids=[dict(pid=999999)]))
        if a.synthetic or a.label:
            def wrong_rejection(r, n, points):
                next(item for item in n['steps'] if item['action'] == 'export' and item['submissionErrors'])['submissionErrors'][0]['code'] = 'INVALID_INPUT'

            def wrong_cancel_id(r, n, points):
                next(item for item in n['steps'] if item['action'] == 'cancel-and-wait')['tasks'][0]['coreJobId'] = 'unrelated-job'

            def bad_order(r, n, points):
                first = next(i for i, item in enumerate(n['steps']) if item['action'] == 'before-restart')
                frame = next(i for i, item in enumerate(n['steps']) if item['action'] == 'preview-frame')
                n['steps'][first], n['steps'][frame] = n['steps'][frame], n['steps'][first]

            run('wrong-rejection-code-must-fail', False, wrong_rejection)
            run('wrong-current-cancel-job-id-must-fail', False, wrong_cancel_id)
            run('preview-before-restart-must-fail', False, bad_order)
            run('old-sampling-protocol-must-fail', False, lambda r, n, p: r.update(measurement_protocol_version=1))
            run('idle-child-residue-must-fail', False, lambda r, n, p: p[3].update(descendants=[dict(pid=999999)]))
            run('forged-rss-median-must-fail', False, lambda r, n, p: p[3].update(rss_bytes=1))
            # Inspect the producer's planned order independently of log fixtures.
            actions, _ = checker.scenario(SimpleNamespace(mode='cycles', work=root))
            frames = [i for i, item in enumerate(actions) if item['action'] == 'preview-frame']
            ordered = len(frames) == 23 and all(actions[i - 2]['action'] == 'restart' and
                      actions[i - 1]['action'] == 'import' and actions[i + 1]['action'] == 'wait-observation' and
                      actions[i + 2]['action'] == 'remove' for i in frames)
            cases.append(dict(name='producer-post-restart-checkpoint-order', result='PASS' if ordered else 'FAIL'))
        report = dict(result='PASS' if all(case['result'] == 'PASS' for case in cases) else 'FAIL',
                      scope=base, checker=str(a.checker), checker_sha256=hashlib.sha256(a.checker.read_bytes()).hexdigest(),
                      no_native_app_or_media_workload_executed=True, checks=cases)
        a.output.write_text(json.dumps(report, indent=2) + '\n'); print(json.dumps(report))
        return 0 if report['result'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
