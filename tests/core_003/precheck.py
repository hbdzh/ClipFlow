#!/usr/bin/env python3
"""Independently exercise synthetic fixtures and positive/negative quality checker.
This does not execute Core or provide a successful Core media result.
"""
from pathlib import Path
import json
import shutil
import sys
sys.dont_write_bytecode=True
from support import Commands,HERE,digest,report
from fixtures import make_fixtures
from verify import selected_indices,psnr_and_patches,frame_sequence


def main():
    build=HERE/'build'
    if build.is_symlink():raise ValueError('No redirected QA build root')
    build.mkdir(exist_ok=True);directory=build/'fixtures';directory.mkdir(exist_ok=True)
    commands=Commands();files=make_fixtures(directory,commands)
    manifest={n:{'path':str(p),'sha256':digest(p)} for n,p in files.items()}
    source=files['quality1080'];expected,*_=selected_indices(commands,source,1100000,2600000)
    reference=directory/'checker-reference.mp4'
    prefix=[shutil.which('ffmpeg'),'-nostdin','-hide_banner','-v','error','-y','-i',source]
    codec=['-map','0:v:0','-an','-c:v','libx264','-crf','23','-preset','medium','-threads','1','-pix_fmt','yuv420p',
           '-color_range','tv','-colorspace','bt709','-color_primaries','bt709','-color_trc','bt709']
    trim=f'trim=start_frame={expected[0]}:end_frame={expected[-1]+1},setpts=PTS-STARTPTS'
    commands.run(prefix+['-vf',trim]+codec+[reference],timeout=120)
    positive=psnr_and_patches(commands,source,reference,expected,directory)
    positive['counter']=frame_sequence(commands,source,reference,expected)
    swapped=directory/'checker-swapped-chroma.mp4'
    commands.run(prefix+['-vf',trim+',swapuv']+codec+[swapped],timeout=120)
    rejected=False;reason=''
    try:psnr_and_patches(commands,source,swapped,expected,directory)
    except AssertionError as error:rejected=True;reason=str(error)
    assert rejected,'Quality checker accepted wrong chroma'
    report(HERE/'evidence/fixture-precheck.json',{'result':'PASS','scope':'Self-authored fixture/checker preflight only; no Core media execution',
        'input_manifest':manifest,'positive_checker_metrics':positive,'wrong_chroma_rejected':rejected,
        'negative_rejection':reason,'commands':commands.records})
    report(HERE/'build/fixture-manifest.json',manifest)
    print('PASS: fixtures and independent quality/counter checker; wrong chroma rejected')


if __name__=='__main__':main()
