#!/usr/bin/env python3
"""Fixture/checker preflight only; no native app execution or acceptance claim."""
import json
from pathlib import Path
import shutil
import sys
sys.dont_write_bytecode=True
from support import Commands,HERE,sha256,report,captured_at
from fixtures import make_fixtures
from media_verify import metadata,selected_indices,frame_sequence,psnr_and_patches


def main():
    build=HERE/'build';assert not build.is_symlink();build.mkdir(exist_ok=True)
    directory=build/'fixtures';directory.mkdir(exist_ok=True)
    if list(directory.glob('*.mp4')):raise ValueError('Use a fresh precheck fixture directory; do not silently replace evidence inputs')
    commands=Commands();files=make_fixtures(directory,commands)
    manifests={k:{'path':str(p),'sha256':sha256(p),'metadata':metadata(commands,p) if k!='bad' else 'truncated'} for k,p in files.items()}
    for k in ['a','b','noaudio','quality1080']:
        v=next(s for s in manifests[k]['metadata']['streams'] if s['codec_type']=='video')
        assert [v.get(x) for x in ['color_range','color_space','color_primaries','color_transfer']]==['tv','bt709','bt709','bt709'],(k,v)
    rot=next(s for s in manifests['rotation']['metadata']['streams'] if s['codec_type']=='video')
    assert any(abs(x.get('rotation',0))==90 for x in rot.get('side_data_list',[])),rot
    source=files['quality1080'];expected,*_=selected_indices(commands,source,1100000,2600000)
    prefix=[shutil.which('ffmpeg'),'-nostdin','-hide_banner','-v','error','-n','-i',source]
    trim=f'trim=start_frame={expected[0]}:end_frame={expected[-1]+1},setpts=PTS-STARTPTS'
    codec=['-map','0:v:0','-an','-c:v','libx264','-threads','1','-crf','23','-preset','medium','-pix_fmt','yuv420p','-color_range','tv','-colorspace','bt709','-color_primaries','bt709','-color_trc','bt709']
    good=directory/'checker-reference.mp4';commands.run(prefix+['-vf',trim]+codec+[good])
    scores=psnr_and_patches(commands,source,good,expected,directory);scores['frame_identity']=frame_sequence(commands,source,good,expected)
    bad=directory/'checker-wrong-chroma.mp4';commands.run(prefix+['-vf',trim+',swapuv']+codec+[bad])
    rejected=False;reason=''
    try:psnr_and_patches(commands,source,bad,expected,directory)
    except AssertionError as e:rejected=True;reason=str(e)
    assert rejected,'Independent verifier accepted wrong chroma'
    report(HERE/'build/fixture-manifest.json',manifests)
    report(HERE/'evidence/fixture-precheck.json',{'captured_at':captured_at(),'result':'PASS','scope':'Self-authored fixture/checker preflight only; not Native/Core execution','fixtures':manifests,'positive_checker':scores,'wrong_chroma_rejected':rejected,'rejection_reason':reason,'commands':commands.records})
    print('PASS: MAC002 self-authored fixture metadata, precise physical checker and wrong-chroma negative')


if __name__=='__main__':main()
