#!/usr/bin/env python3
"""Bounded read-only HTTP range probe of official ORT wheel metadata.

Does not download a complete wheel or execute/import any code from it. This is
metadata attribution, not full-wheel hash verification or proof of a build.
"""
import argparse
import binascii
import hashlib
import json
import struct
import urllib.request
import zlib
from pathlib import Path


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--release-metadata', required=True, type=Path)
    p.add_argument('--evidence', required=True, type=Path)
    a = p.parse_args()
    info = json.loads(a.release_metadata.read_text())['wheel'][0]
    url, size = info['url'], info['size']
    assert url.startswith('https://files.pythonhosted.org/') and size < 100_000_000
    requests = []
    total = 0

    def read_range(start, length):
        nonlocal total
        assert 0 <= start < size and 0 < length <= 1_000_000 and start + length <= size
        assert total + length <= 2_000_000
        request = urllib.request.Request(url, headers={
            'Range': f'bytes={start}-{start + length - 1}',
            'Accept-Encoding': 'identity', 'User-Agent': 'MediaStudio-AC25-metadata-audit'})
        with urllib.request.urlopen(request, timeout=25) as response:
            assert response.status == 206, 'Server did not honor bounded range; no full body read'
            assert response.headers['Content-Range'] == f'bytes {start}-{start + length - 1}/{size}'
            value = response.read(length + 1)
        assert len(value) == length
        total += len(value)
        requests.append({'range': request.headers['Range'], 'status': 206,
                         'bytes': length, 'sha256': hashlib.sha256(value).hexdigest()})
        return value

    tail = read_range(size - 65557, 65557)
    end = tail.rfind(b'PK\x05\x06')
    assert end >= 0
    fields = struct.unpack_from('<4s4H2IH', tail, end)
    assert fields[1] == fields[2] == 0 and fields[3] == fields[4] < 10000
    directory_size, directory_offset = fields[5:7]
    directory = read_range(directory_offset, directory_size)
    members = []
    offset = 0
    while offset < len(directory):
        d = struct.unpack_from('<4s6H3I5H2I', directory, offset)
        assert d[0] == b'PK\x01\x02'
        name = directory[offset + 46:offset + 46 + d[10]].decode('utf-8')
        members.append({'name': name, 'method': d[4], 'crc32': d[7],
                        'compressed_size': d[8], 'size': d[9], 'local_offset': d[16]})
        offset += 46 + d[10] + d[11] + d[12]
    assert len(members) == fields[4]
    selected = []
    for member in members:
        name = member['name']
        if any(token in name.lower() for token in ['sbom', 'spdx', 'cyclonedx', 'git_commit', 'build_and_package_info', 'version_number']) or name.endswith(('.dist-info/METADATA', '.dist-info/RECORD', '.dist-info/WHEEL')):
            if member['size'] > 1_000_000:
                selected.append({**member, 'result': 'NOT_FETCHED_SIZE_BOUND'})
                continue
            header = read_range(member['local_offset'], 30)
            h = struct.unpack('<4s5H3I2H', header)
            assert h[0] == b'PK\x03\x04' and h[3] == member['method']
            data_offset = member['local_offset'] + 30 + h[9] + h[10]
            packed = read_range(data_offset, member['compressed_size'])
            if member['method'] == 8:
                inflater = zlib.decompressobj(-15)
                value = inflater.decompress(packed, 1_000_001)
                assert inflater.eof and len(value) <= 1_000_000
            else:
                assert member['method'] == 0
                value = packed
            assert len(value) == member['size'] and binascii.crc32(value) == member['crc32']
            output = 'license-ort-wheel-' + name.replace('/', '__') + '.txt'
            (a.evidence / output).write_bytes(value)
            selected.append({**member, 'result': 'FETCHED_METADATA_ONLY',
                             'sha256': hashlib.sha256(value).hexdigest(), 'evidence': output})
    sbom_names = [x['name'] for x in members if any(token in x['name'].lower()
                  for token in ['sbom', 'spdx', 'cyclonedx'])]
    result = {'result': 'METADATA_PROBED_FULL_CLOSURE_NOT_PROVEN', 'wheel': info,
              'scope': 'Only HTTP ranges and ZIP metadata; no wheel import, extraction of executable code, full-wheel download or local full-wheel SHA verification',
              'total_fetched_bytes': total, 'central_directory_member_count': len(members),
              'sbom_named_members': sbom_names, 'members': members,
              'fetched_metadata': selected, 'http_ranges': requests}
    (a.evidence / 'license-ort-wheel-metadata-probe.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'total_fetched_bytes': total, 'member_count': len(members),
                      'sbom_named_members': sbom_names, 'metadata_files': [x['name'] for x in selected]}))


if __name__ == '__main__':
    main()
