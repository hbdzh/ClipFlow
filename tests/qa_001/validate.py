#!/usr/bin/env python3
"""Independent fixture/reference checks using actual packets and decoded frames.

Does not import generation code and never changes rules or acceptance thresholds.
"""
from collections import defaultdict
from fractions import Fraction
from decimal import Decimal
import json
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import threading
import tempfile
import time

sys.dont_write_bytecode = True
from common import HERE, ROOT, BUILD, EVIDENCE, Commands, sha256, write_json, timestamp, relative, require_owned_build, require_build_root, temporary_output, owned_input_paths


def numeric(value):
    return Fraction(str(value))


def seconds_arg(value):
    return format(Decimal(value.numerator) / Decimal(value.denominator), 'f')


def require_continuous_content(indices):
    assert indices and indices == list(range(indices[0], indices[-1] + 1)), 'Output must preserve a continuous source-content sequence'


def frames_from_hash(text):
    time_bases = {}
    frames = []
    for line in text.splitlines():
        if line.startswith('#tb '):
            stream, value = line[4:].split(':', 1)
            time_bases[int(stream)] = Fraction(value.strip())
        elif line and not line.startswith('#'):
            stream, dts, pts, duration, size, digest = [part.strip() for part in line.split(',')]
            frames.append({'stream': int(stream), 'pts': int(pts), 'dts': int(dts),
                           'time_seconds': str(int(pts) * time_bases[int(stream)]),
                           'time_base': str(time_bases[int(stream)]),
                           'duration_seconds': str(int(duration) * time_bases[int(stream)]),
                           'duration': int(duration), 'size': int(size), 'sha256': digest})
    return frames


def main():
    rules = json.loads((HERE / 'rules.json').read_text())
    require_build_root()
    generation = json.loads((EVIDENCE / 'generation.json').read_text())
    reference_plan = json.loads((HERE / 'reference-plan.json').read_text())
    assert sha256(HERE / 'rules.json') == generation['rules_sha256_before_generation'], 'Do not change rules after generation'
    ffmpeg, ffprobe = shutil.which('ffmpeg'), shutil.which('ffprobe')
    assert ffmpeg and ffprobe, 'BLOCKED: missing ffmpeg/ffprobe'
    assert reference_plan['rules_sha256'] == generation['rules_sha256_before_generation']
    assert reference_plan['ffmpeg_executable_sha256'] == sha256(ffmpeg)
    assert reference_plan['ffmpeg_version_first_line'] == generation['tools']['ffmpeg'].splitlines()[0]
    for identifier, digest in reference_plan['source_sha256'].items():
        assert generation['files'][identifier]['sha256'] == digest, 'Reference plan must be re-reviewed for changed input'
    commands = Commands()
    results = []
    measurements = {}
    outputs = BUILD / 'outputs'
    require_owned_build(outputs)
    outputs.mkdir(exist_ok=True)
    inputs = owned_input_paths(rules, generation)
    before_hashes = {key: sha256(path) for key, path in inputs.items()}

    def passed(identifier, summary):
        results.append({'id': identifier, 'result': 'PASS', 'summary': summary})
        print('PASS:', identifier)

    def probe(path):
        result = commands.run([ffprobe, '-v', 'error', '-show_format', '-show_streams', '-of', 'json', path])
        return json.loads(result.stdout)

    def frames(path):
        result = commands.run([ffprobe, '-v', 'error', '-select_streams', 'v:0', '-show_frames',
            '-show_entries', 'frame=best_effort_timestamp,best_effort_timestamp_time,pict_type,key_frame,pkt_duration', '-of', 'json', path])
        return json.loads(result.stdout)['frames']

    def packets(path):
        result = commands.run([ffprobe, '-v', 'error', '-show_packets', '-show_data_hash', 'sha256',
            '-show_entries', 'packet=stream_index,pts,dts,pts_time,dts_time,duration_time,flags,data_hash', '-of', 'json', path])
        return json.loads(result.stdout)['packets']

    def decoded_frames(path):
        result = commands.run([ffmpeg, '-hide_banner', '-nostdin', '-v', 'error', '-xerror', '-threads', '1',
            '-i', path, '-map', '0:v:0', '-fps_mode', 'passthrough', '-f', 'framehash', '-hash', 'sha256', '-'])
        return frames_from_hash(result.stdout)

    def assert_decodes(path, duration):
        commands.run([ffmpeg, '-hide_banner', '-nostdin', '-v', 'error', '-xerror', '-threads', '1', '-i', path,
                      '-map', '0:v?', '-map', '0:a?', '-f', 'null', '-'])
        for start in ['0', seconds_arg(max(Fraction(0), numeric(duration) - Fraction(1, 2)))]:
            commands.run([ffmpeg, '-hide_banner', '-nostdin', '-v', 'error', '-xerror', '-threads', '1',
                          '-ss', start, '-i', path, '-t', '0.25', '-map', '0:v:0', '-f', 'null', '-'])

    def normalize(metadata):
        keys = ['index', 'codec_type', 'codec_name', 'width', 'height', 'pix_fmt', 'sample_rate', 'channels',
                'time_base', 'start_pts', 'start_time', 'duration_ts', 'duration', 'nb_frames', 'avg_frame_rate', 'r_frame_rate', 'has_b_frames']
        return {'format': {key: metadata['format'].get(key) for key in ['format_name', 'start_time', 'duration', 'nb_streams']},
                'streams': [{**{key: stream[key] for key in keys if key in stream},
                             'language': stream.get('tags', {}).get('language')} for stream in metadata['streams']]}

    for identifier, rule in rules['fixtures'].items():
        path = inputs[identifier]
        assert sha256(path) == generation['files'][identifier]['sha256'], identifier
        if rule.get('expected_probe') == 'nonzero':
            commands.run([ffprobe, '-v', 'error', '-show_format', '-show_streams', '-of', 'json', path], expected='nonzero')
            commands.run([ffmpeg, '-hide_banner', '-nostdin', '-v', 'error', '-xerror', '-i', path, '-f', 'null', '-'], expected='nonzero')
            passed(identifier + '/damaged_input', 'Both independent probe and decode reject the owned 32-byte truncation without output.')
            continue
        metadata = probe(path)
        video = [s for s in metadata['streams'] if s['codec_type'] == 'video']
        audio = [s for s in metadata['streams'] if s['codec_type'] == 'audio']
        assert [s['codec_name'] for s in video] == rule['video_codecs'], identifier
        assert [s['codec_name'] for s in audio] == rule['audio_codecs'], identifier
        assert all(s['width'] == rules['generation']['width'] and s['height'] == rules['generation']['height'] for s in video)
        duration = numeric(metadata['format']['duration'])
        assert numeric(rule['duration_min']) <= duration <= numeric(rule['duration_max']), identifier
        display = frames(path)
        timestamps = [int(f['best_effort_timestamp']) for f in display]
        deltas = [b - a for a, b in zip(timestamps, timestamps[1:])]
        assert deltas and all(delta > 0 for delta in deltas), identifier
        if rule['video_pts'] == 'constant':
            assert len(set(deltas)) == 1, (identifier, sorted(set(deltas)))
            assert numeric(video[0]['time_base']) * deltas[0] == Fraction(1, rules['generation']['rate']), identifier
            assert len(display) == rules['generation']['duration_seconds'] * rules['generation']['rate'], identifier
        else:
            assert len(set(deltas)) >= 2, (identifier, sorted(set(deltas)))
        if rule.get('require_b_frames'):
            assert any(f['pict_type'] == 'B' for f in display), identifier
        if 'keyframe_period_seconds' in rule:
            keys = [int(f['best_effort_timestamp']) * numeric(video[0]['time_base']) for f in display if f['key_frame']]
            assert len(keys) >= 2, identifier
            expected_period = numeric(rule['keyframe_period_seconds'])
            assert all(abs((b - a) - expected_period) <= numeric(video[0]['time_base']) for a, b in zip(keys, keys[1:])), identifier
        source_packets = packets(path)
        by_stream = defaultdict(list)
        for packet in source_packets:
            if 'dts' in packet:
                by_stream[packet['stream_index']].append(int(packet['dts']))
        assert all(all(a < b for a, b in zip(values, values[1:])) for values in by_stream.values()), identifier
        # PTS are intentionally NOT required to increase in decode/packet order:
        # B-frame reordering makes that an incorrect assertion.
        if 'audio_languages' in rule:
            assert [s.get('tags', {}).get('language') for s in audio] == rule['audio_languages']
        audio_hashes = []
        if rule.get('require_distinct_decoded_audio'):
            for index in range(len(audio)):
                result = commands.run([ffmpeg, '-hide_banner', '-nostdin', '-v', 'error', '-xerror', '-i', path,
                    '-map', f'0:a:{index}', '-c:a', 'pcm_s16le', '-f', 'hash', '-hash', 'sha256', '-'])
                audio_hashes.append(result.stdout.strip())
            assert len(set(audio_hashes)) == len(audio_hashes), identifier
        if 'same_bytes_as' in rule:
            assert sha256(path) == sha256(inputs[rule['same_bytes_as']]), identifier
        assert_decodes(path, metadata['format']['duration'])
        observations = {'path': relative(path), 'sha256': sha256(path), 'normalized_ffprobe': normalize(metadata),
            'decoded_frame_count': len(display), 'display_pts_deltas_ticks': sorted(set(deltas)),
            'video_keyframe_times_seconds': [f['best_effort_timestamp_time'] for f in display if f['key_frame']],
            'picture_types': sorted(set(f['pict_type'] for f in display)), 'decoded_audio_hashes': audio_hashes,
            'packet_dts_strictly_monotonic_per_stream': True,
            'packet_pts_monotonicity_required': False}
        measurements[identifier] = observations
        passed(identifier + '/metadata_pts_decode', 'Golden semantic rules, actual display PTS/DTS, streams and complete/first/last decode passed.')

    copy_rule = rules['stream_copy']

    # Preserve the original request as a real negative regression. B-frame packet
    # cut-off can keep a trailing reference frame while dropping displayed B frames.
    source = inputs['cfr_bframes']
    rejected = require_owned_build(outputs / 'naive-boundary-rejected.mp4')
    with temporary_output(rejected, 'negative-reference') as temporary:
        commands.run([ffmpeg, '-hide_banner', '-nostdin', '-y', '-v', 'error', '-ss', copy_rule['requested_start_seconds'],
            '-i', source, '-t', copy_rule['requested_duration_seconds'], '-map', '0', '-c', 'copy',
            '-avoid_negative_ts', 'make_zero', '-map_metadata', '-1', temporary])
        input_frames = decoded_frames(source)
        rejected_frames = decoded_frames(temporary)
        indices = []
        for frame in rejected_frames:
            found = [index for index, original in enumerate(input_frames) if original['sha256'] == frame['sha256']]
            assert len(found) == 1
            indices.append(found[0])
        try:
            require_continuous_content(indices)
        except AssertionError:
            missing = sorted(set(range(indices[0], indices[-1] + 1)) - set(indices))
            assert missing, 'The negative reference must expose the actual continuity failure'
        else:
            raise AssertionError('Known boundary-failure reference changed; review tool/input changes, do not silently accept')
        measurements['naive_boundary_negative'] = {'requested_start_seconds': copy_rule['requested_start_seconds'],
            'requested_duration_seconds': copy_rule['requested_duration_seconds'], 'executed_duration_seconds': copy_rule['requested_duration_seconds'],
            'decoded_source_indices': indices, 'missing_source_indices_between_boundaries': missing,
            'boundary_validation': 'FAIL_EXPECTED', 'result_exposed_as_success': False,
            'source_first_frame_pts_seconds': input_frames[indices[0]]['time_seconds'],
            'source_last_frame_pts_seconds': input_frames[indices[-1]]['time_seconds']}
    assert not temporary.exists() and not rejected.exists()
    passed('naive_boundary_negative_rejected', 'Original -ss1.1/-t1.5 remains rejected by the SAME continuity assertion, and its owned partial output is cleaned.')

    for identifier in copy_rule['sources']:
        source = inputs[identifier]
        path = require_owned_build(outputs / (identifier + '_stream_copy.mp4'))
        with temporary_output(path, 'stream-copy') as temporary:
            assert source.resolve() not in [path.resolve(), temporary.resolve()]
            commands.run([ffmpeg, '-hide_banner', '-nostdin', '-y', '-v', 'error', '-ss', copy_rule['requested_start_seconds'],
                '-i', source, '-t', reference_plan['execution_duration_seconds'], '-map', '0', '-c', 'copy',
                '-avoid_negative_ts', 'make_zero', '-map_metadata', '-1', temporary])
            metadata = probe(temporary)
            assert [(s['codec_type'], s['codec_name']) for s in metadata['streams']] == [
                (s['codec_type'], s['codec_name']) for s in probe(source)['streams']]
            output_duration = numeric(metadata['format']['duration'])
            assert numeric(copy_rule['output_duration_min_seconds']) <= output_duration <= numeric(copy_rule['output_duration_max_seconds'])
            assert_decodes(temporary, metadata['format']['duration'])
            input_hash_frames = decoded_frames(source)
            output_hash_frames = decoded_frames(temporary)
            assert output_hash_frames
            matches = []
            for frame in output_hash_frames:
                candidates = [src for src in input_hash_frames if src['sha256'] == frame['sha256']]
                assert len(candidates) == 1, (identifier, 'Frame content is ambiguous or changed', len(candidates))
                matches.append(candidates[0])
            indices = [input_hash_frames.index(frame) for frame in matches]
            require_continuous_content(indices)
            start = numeric(matches[0]['time_seconds'])
            expected_start = numeric(copy_rule['expected_preceding_keyframe_seconds'])
            assert abs(start - expected_start) <= numeric(copy_rule['first_source_packet_tolerance_seconds']), (identifier, str(start))
            assert start != numeric(copy_rule['requested_start_seconds']), 'Reference must expose non-exact lossless start'
            source_packet_hashes = defaultdict(set)
            for packet in packets(source):
                source_packet_hashes[packet['stream_index']].add(packet['data_hash'])
            output_packets = packets(temporary)
            by_stream = defaultdict(list)
            for packet in output_packets:
                assert packet['data_hash'] in source_packet_hashes[packet['stream_index']], identifier
                if 'dts' in packet:
                    by_stream[packet['stream_index']].append(int(packet['dts']))
            assert all(all(a < b for a, b in zip(values, values[1:])) for values in by_stream.values())
            ends = [numeric(s['start_time']) + numeric(s['duration']) for s in metadata['streams'] if 'start_time' in s and 'duration' in s]
            av_end_delta = max(ends) - min(ends)
            assert av_end_delta <= numeric(copy_rule['max_av_end_difference_seconds']), (identifier, str(av_end_delta))
            temporary.replace(path)
            measurements[identifier + '/stream_copy'] = {'source_sha256': sha256(source), 'output_path': relative(path), 'output_sha256': sha256(path),
                'request': {'start_seconds': copy_rule['requested_start_seconds'], 'duration_seconds': copy_rule['requested_duration_seconds']},
                'reference_execution_duration_seconds': reference_plan['execution_duration_seconds'],
                'reference_plan_scope': 'Fixture-only reviewed reference; not an automatic Core/UI policy and not the exact requested interval.',
                'source_content_boundary_by_unique_decoded_frame_sha256': {'first_seconds': str(start), 'last_frame_seconds': matches[-1]['time_seconds'],
                    'last_frame_end_exclusive_seconds': str(numeric(matches[-1]['time_seconds']) + numeric(matches[-1]['duration_seconds'])),
                    'end_exclusive_basis': 'Actual decoded framehash header timebase and last frame duration; not inferred from output-reset timestamps.',
                    'decoded_source_indices': indices, 'matched_frame_count': len(matches),
                    'first_hash': output_hash_frames[0]['sha256'], 'last_hash': output_hash_frames[-1]['sha256']},
                'source_start_deviation_seconds': str(start - numeric(copy_rule['requested_start_seconds'])),
                'output_pts_metadata_separate_from_source_content': normalize(metadata),
                'all_compressed_output_packet_hashes_found_in_corresponding_source_stream': True,
                'av_stream_end_difference_seconds': str(av_end_delta),
                'audio_timing_note': 'AAC priming/last-packet and MP4 start offsets are retained in measured metadata. End-time proximity is not a perceptual AV-sync proof.'}
            passed(identifier + '/stream_copy', 'No re-encode payload changes; unique decoded frame content establishes source start/end separately from output PTS.')
    # A missing parent is a safe, deterministic write-error case. Never attempt
    # source-as-output or chmod user files to manufacture an error.
    missing_parent = require_owned_build(outputs / 'not-created-parent' / 'output.mp4')
    assert not missing_parent.parent.exists(), 'Error-case parent must not exist'
    commands.run([ffmpeg, '-hide_banner', '-nostdin', '-v', 'error', '-i', inputs['cfr_bframes'], '-c', 'copy', missing_parent], expected='nonzero')
    assert not missing_parent.exists()
    passed('missing_output_parent', 'Direct FFmpeg reference rejects missing output parent; no source modified/output created.')

    # Confirm our own worker is actually progressing before terminating it.
    temporary = require_owned_build(outputs / 'cancel-owned.partial.mkv')
    stderr_path = require_owned_build(outputs / 'cancel-owned.stderr.txt')
    argv = [ffmpeg, '-hide_banner', '-nostdin', '-y', '-v', 'error', '-re', '-f', 'lavfi', '-i',
            'testsrc2=size=160x96:rate=30:duration=120', '-c:v', 'libx264', '-threads:v', '1', '-preset', 'ultrafast',
            '-progress', 'pipe:1', '-nostats', '-f', 'matroska', str(temporary)]
    progress = queue.Queue()
    lines = []
    start = time.monotonic()
    with stderr_path.open('w') as errors:
        process = subprocess.Popen(argv, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=errors, text=True)
        def read_progress():
            for line in process.stdout:
                progress.put(line.rstrip())
        reader = threading.Thread(target=read_progress, daemon=True)
        reader.start()
        observed = False
        killed = False
        try:
            while time.monotonic() - start < rules['cancellation']['maximum_wait_seconds']:
                try:
                    line = progress.get(timeout=0.1)
                    lines.append(line)
                    if line.startswith('frame=') and int(line.split('=', 1)[1].strip()) > 0:
                        observed = True
                        break
                except queue.Empty:
                    if process.poll() is not None:
                        break
            assert observed, 'Worker did not report a processed frame before bounded cancellation'
            process.terminate()
            try:
                returncode = process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                killed = True
                process.kill()
                returncode = process.wait(timeout=5)
            reader.join(timeout=1)
            assert returncode != 0
            existed = temporary.exists()
            assert existed, 'Expected owned temporary output after confirmed worker progress'
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            reader.join(timeout=1)
            if process.stdout:
                process.stdout.close()
            if temporary.exists():
                temporary.unlink()
        assert not temporary.exists()
    measurements['cancellation'] = {'argv': argv, 'worker_pid': process.pid, 'observed_frame_before_cancel': observed,
        'progress_lines_before_cancel': lines, 'returncode': returncode, 'kill_escalation': killed,
        'temporary_output_existed_before_cleanup': existed, 'temporary_output_after_cleanup': temporary.exists(),
        'stderr': stderr_path.read_text(), 'elapsed_seconds': round(time.monotonic() - start, 6),
        'scope': 'Only this Popen child and owned partial output; not Core cancellation acceptance.'}
    passed('owned_worker_cancel_cleanup', 'Own progressing FFmpeg child terminated and own temporary output removed; no Core claim.')

    # Current Core must explicitly refuse media operations. Its refusal is a
    # successful capability check, while media acceptance remains BLOCKED.
    with tempfile.TemporaryDirectory(prefix='core-target-', dir=BUILD) as target:
        core_target = require_owned_build(Path(target))
        commands.run(['cargo', 'build', '--manifest-path', 'core/Cargo.toml', '--offline', '--locked', '--package', 'media-cli',
                      '--target-dir', str(core_target)])
        cli = core_target / 'debug/media-cli'
        capabilities = json.loads(commands.run([cli, 'capabilities']).stdout)
        assert not capabilities['features']['probe']['available'] and not capabilities['features']['trim_stream_copy']['available']
        for operation in ['probe', 'trim']:
            response = commands.run([cli, operation], expected='nonzero')
            assert json.loads(response.stdout)['error']['code'] == -8
    results.append({'id': 'core_media_pipeline', 'result': 'BLOCKED',
                    'summary': 'CORE-001 intentionally reports media probe/trim unavailable and returns unsupported. Direct FFmpeg references do not satisfy Core media acceptance.'})
    for identifier, path in inputs.items():
        assert sha256(path) == before_hashes[identifier], (identifier, 'Source modified')
    assert sha256(HERE / 'rules.json') == generation['rules_sha256_before_generation']
    passed('source_and_rules_unchanged', 'All fixture source bytes and predeclared semantic rules unchanged by validation/copy/errors/cancel.')
    write_json(EVIDENCE / 'normalized-golden-info.json', {'scope': 'Measured normalized ffprobe/content evidence under fixed local tools; normative expectations remain rules.json.',
                'rules_sha256': generation['rules_sha256_before_generation'], 'measurements': measurements})
    write_json(EVIDENCE / 'validation.json', {'captured_at': timestamp(), 'scope': 'Synthetic input/reference acceptance only; no platform or Core media pipeline pass',
        'rules_sha256': generation['rules_sha256_before_generation'], 'results': results, 'commands': commands.records})
    print('QA-001 reference checks:', sum(r['result'] == 'PASS' for r in results), 'PASS;', sum(r['result'] == 'BLOCKED' for r in results), 'BLOCKED')


if __name__ == '__main__':
    main()
