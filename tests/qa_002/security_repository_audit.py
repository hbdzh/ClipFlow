#!/usr/bin/env python3
"""Local, redacted repository checks. No network or software execution.

Checks named high-confidence credential formats and private-key headers, not
arbitrary secrets or all intellectual-property rights. Values are never logged.
"""
import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path


RULES = {
    'private-key-header': rb'-----BEGIN (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----',
    'aws-access-key-id': rb'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b',
    'github-token': rb'\b(?:gh[pousr]_[A-Za-z0-9]{36,255}|github_pat_[A-Za-z0-9_]{50,255})\b',
    'slack-token': rb'\bxox[baprs]-[A-Za-z0-9-]{20,255}\b',
    'openai-token': rb'\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{40,255}\b',
}
PROHIBITED = {'.mp4', '.mov', '.mkv', '.webm', '.wav', '.flac', '.mp3', '.m4a',
              '.onnx', '.pth', '.pt', '.safetensors', '.p12', '.pfx',
              '.mobileprovision', '.keystore', '.jks', '.pem', '.key'}


def git(*args):
    return subprocess.check_output(['git', *args])


def inspect(data, locator):
    findings = []
    for name, pattern in RULES.items():
        for match in re.finditer(pattern, data):
            findings.append({'rule': name, 'locator': locator,
                             'line': data[:match.start()].count(b'\n') + 1,
                             'fingerprint': hashlib.sha256(match[0]).hexdigest(),
                             'value_redacted': True})
    return findings


def prohibited(name):
    p = Path(name)
    return p.suffix.lower() in PROHIBITED or (
        p.name.startswith('.env') and p.name != '.env.example')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True, type=Path)
    a = parser.parse_args()
    tracked = [x.decode() for x in git('ls-files', '-z').split(b'\0') if x]
    untracked = [x.decode() for x in git('ls-files', '--others', '--exclude-standard', '-z').split(b'\0') if x]
    findings, scanned, missing = [], [], []
    for name in sorted(set(tracked + untracked)):
        p = Path(name)
        if not p.is_file() or p.is_symlink():
            missing.append(name)
            continue
        data = p.read_bytes()
        scanned.append({'file': name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
        findings += inspect(data, 'working-tree:' + name)

    objects = []
    for line in git('rev-list', '--objects', '--all').splitlines():
        oid, _, raw_name = line.partition(b' ')
        objects.append((oid.decode(), raw_name.decode(errors='replace')))
    history_prohibited = [name for _, name in objects if prohibited(name)]
    history_count = 0
    process = subprocess.Popen(['git', 'cat-file', '--batch'], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    assert process.stdin is not None and process.stdout is not None
    for oid, name in objects:
        process.stdin.write((oid + '\n').encode()); process.stdin.flush()
        header = process.stdout.readline().decode().strip().split()
        assert len(header) == 3, header
        size = int(header[2]); data = process.stdout.read(size)
        assert len(data) == size and process.stdout.read(1) == b'\n'
        if header[1] == 'blob':
            history_count += 1
            findings += inspect(data, 'git-object:' + oid + ':' + name)
    process.stdin.close()
    assert process.wait() == 0
    result = {'result': 'PASS_SCOPED_PATTERNS' if not findings and not history_prohibited
              and not any(map(prohibited, tracked)) else 'REVIEW_REQUIRED',
              'scope': 'Current tracked and nonignored untracked files plus all reachable Git blobs; no upload, deletion, or history rewrite',
              'not_a_proof_that_all_secrets_or_user_media_are_absent': True,
              'head': git('rev-parse', 'HEAD').decode().strip(),
              'rules': list(RULES), 'prohibited_suffixes': sorted(PROHIBITED),
              'tracked_count': len(tracked), 'nonignored_untracked_count': len(untracked),
              'current_files_scanned': len(scanned), 'historical_blobs_scanned': history_count,
              'current_unreadable_or_nonregular_files': missing,
              'tracked_prohibited_paths': [x for x in tracked if prohibited(x)],
              'historical_prohibited_paths': history_prohibited, 'redacted_findings': findings,
              'working_tree_files': scanned}
    a.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k not in ['working_tree_files', 'redacted_findings']}))
    return 0 if result['result'] == 'PASS_SCOPED_PATTERNS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
