#!/usr/bin/env python3
"""Recover only previously approved quality inputs, requiring frozen full hashes.

Downloads are local QA data, never played or added to the repository. Archive
members are read by exact allowlist; tar paths are never extracted to disk.
"""
import argparse
import hashlib
import json
import tarfile
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--work', type=Path, required=True)
    p.add_argument('--report', type=Path, required=True)
    a = p.parse_args()
    a.work.mkdir(mode=0o700, parents=True, exist_ok=False)
    raw = a.work / 'raw'; raw.mkdir(mode=0o700)
    expected = {x['key']: x['sha256'] for x in json.loads((ROOT /
        'docs/qa-002/evidence/ai-20261114/quality-predeclared-plan.json').read_text())['sources']}
    expected['weights'] = 'b62c91cedbc7a066f1778ead5b5cecb377aa3a46a31af1cce7c5c8769339d083'
    rows = []

    def save_report():
        a.report.write_text(json.dumps({'result': 'PASS' if len(rows) == 9 and
            all(r['result'] == 'PASS' for r in rows) else 'INCOMPLETE',
            'scope': 'Previously approved public QA fixtures and original MIT weights; '
            'frozen full SHA256 required. No playback, extraction, Git admission or upload.',
            'work': str(a.work), 'files': rows}, indent=2) + '\n')

    def read_url(url, bound=8 * 1024 * 1024):
        request = urllib.request.Request(url, headers={'User-Agent': 'MediaStudio-local-QA/1'})
        for attempt in range(3):
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    data = response.read(bound + 1)
                    assert len(data) <= bound, 'Public fixture exceeded expected read bound'
                    return data
            except Exception:
                if attempt == 2: raise
                time.sleep(1)

    def accept(key, data, path, source):
        digest = hashlib.sha256(data).hexdigest()
        assert digest == expected[key], (key, 'Frozen source hash mismatch', digest)
        path.write_bytes(data); path.chmod(0o600)
        rows.append({'key': key, 'path': str(path), 'sha256': digest, 'bytes': len(data),
                     'source': source, 'result': 'PASS'})
        save_report(); print(key, 'PASS', flush=True)

    def failure(key, source, error):
        rows.append({'key': key, 'source': source, 'result': 'BLOCKED',
                     'error': str(error)[:500]})
        save_report(); print(key, 'BLOCKED', str(error)[:150], flush=True)

    for key, dataset, revision, index, field, filename in [
        ('v1', 'DynamicSuperb/VocalTechniqueClassification_VocalSet', 'bb84b8ecadeb80ca27e70e09e053fa465805ee17', 1, 'audio', 'vocalset-1.wav'),
        ('v3', 'DynamicSuperb/VocalTechniqueClassification_VocalSet', 'bb84b8ecadeb80ca27e70e09e053fa465805ee17', 3, 'audio', 'vocalset-3.wav'),
        ('v5', 'DynamicSuperb/VocalTechniqueClassification_VocalSet', 'bb84b8ecadeb80ca27e70e09e053fa465805ee17', 5, 'audio', 'vocalset-5.wav'),
        ('p0', 'DynamicSuperb/SongSeparation_SingSet', 'a1ff673313e550a457112129985390979c4f10a5', 0, 'label', 'singset-0-label.wav'),
        ('p10', 'DynamicSuperb/SongSeparation_SingSet', 'a1ff673313e550a457112129985390979c4f10a5', 10, 'label', 'singset-10-label.wav')]:
        url = 'https://datasets-server.huggingface.co/rows?' + urllib.parse.urlencode(
            {'dataset': dataset, 'config': 'default', 'split': 'test', 'offset': index,
             'length': 1, 'revision': revision})
        try:
            value = json.loads(read_url(url, 1024 * 1024))['rows'][0]['row'][field]
            if isinstance(value, list): assert len(value) == 1; value = value[0]
            source_url = value['src']
            assert urllib.parse.urlsplit(source_url).scheme == 'https'
            data = read_url(source_url)
            source = {'metadata_url': url, 'revision': revision, 'field': field, 'row': index,
                      'authority': 'HF publisher mirror; original historical full hash required',
                      'no_temporary_signed_CDN_query_recorded': True}
            if key in ('p0', 'p10') and hashlib.sha256(data).hexdigest() != expected[key]:
                # The old approved piano fixtures were mono WAVs. Preserve the
                # exact historical bytes; never accept a different hash/cohort.
                import soundfile as sf
                folder = a.work / ('restore-' + key); folder.mkdir(mode=0o700)
                published, intermediate, restored = (folder / name for name in
                    ('publisher.wav', 'mono24.wav', 'original.wav'))
                published.write_bytes(data)
                argv = ['/opt/homebrew/bin/ffmpeg', '-nostdin', '-v', 'error',
                        '-i', str(published), '-ar', '48000', '-ac', '1',
                        '-c:a', 'pcm_s24le', str(intermediate)]
                result = subprocess.run(argv, capture_output=True, text=True)
                assert result.returncode == 0, 'Fixed piano restoration failed'
                samples, rate = sf.read(intermediate, dtype='float32', always_2d=True)
                sf.write(restored, samples, rate, subtype='PCM_16')
                source.update(publisher_payload_sha256=hashlib.sha256(data).hexdigest(),
                              restoration_argv=argv, soundfile_version=sf.__version__,
                              historical_whole_WAV_hash_required_after_restoration=True)
                data = restored.read_bytes()
            accept(key, data, raw / filename, source)
        except Exception as error:
            failure(key, url, error)

    url = 'https://www.openslr.org/resources/12/test-clean.tar.gz'
    wanted = {'LibriSpeech/test-clean/6930/75918/6930-75918-0000.flac': ('male', a.work/'librispeech-male.flac'),
              'LibriSpeech/test-clean/121/121726/121-121726-0004.flac': ('female', a.work/'librispeech-female-121.flac')}
    found = set()
    try:
        request = urllib.request.Request(url, headers={'User-Agent': 'MediaStudio-local-QA/1'})
        with urllib.request.urlopen(request, timeout=60) as response, tarfile.open(fileobj=response, mode='r|gz') as archive:
            for member in archive:
                if member.name in wanted:
                    key, target = wanted[member.name]
                    assert member.isfile() and 0 < member.size < 8 * 1024 * 1024
                    stream = archive.extractfile(member); data = stream.read(member.size + 1)
                    assert len(data) == member.size
                    accept(key, data, target, {'archive_url': url, 'member': member.name,
                        'license_reference': 'https://www.openslr.org/12'})
                    found.add(key)
                    if len(found) == 2: break
        assert len(found) == 2, 'Requested exact LibriSpeech members not found'
    except Exception as error:
        for key in {'male', 'female'} - found: failure(key, url, error)

    source = json.loads((ROOT/'docs/qa-002/evidence/ai-20261114/quality-fleurs-source.json').read_text())
    try:
        request = urllib.request.Request(source['source_url'], headers={'User-Agent': 'MediaStudio-local-QA/1'})
        with urllib.request.urlopen(request, timeout=60) as response, tarfile.open(fileobj=response, mode='r|gz') as archive:
            for member in archive:
                if member.name == source['archive_member']:
                    assert member.isfile() and 0 < member.size < 8 * 1024 * 1024
                    stream = archive.extractfile(member); data = stream.read(member.size + 1)
                    assert len(data) == member.size
                    accept('mandarin', data, a.work/'fleurs-mandarin.wav', source)
                    break
            else: raise AssertionError('Requested exact FLEURS member not found')
    except Exception as error: failure('mandarin', source['source_url'], error)

    url = 'https://zenodo.org/records/3370489/files/vocals-b62c91ce.pth?download=1'
    try: accept('weights', read_url(url, 64 * 1024 * 1024), a.work/'umx-original.pth',
                {'url': url, 'record': 'https://zenodo.org/records/3370489', 'license': 'MIT record, separately reviewed'})
    except Exception as error: failure('weights', url, error)
    save_report()
    return 0 if len(rows) == 9 and all(r['result'] == 'PASS' for r in rows) else 1


if __name__ == '__main__':
    raise SystemExit(main())
