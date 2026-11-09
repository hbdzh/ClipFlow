#!/usr/bin/env python3
"""Checker/fixture validation only; not Core implementation acceptance."""
import json
from pathlib import Path
import shutil
import sys
import traceback
sys.dont_write_bytecode=True
from support import Commands,HERE,captured_at,report,sha256
from fixtures import make_fixtures
from media_verify import verify,quality,source_selection,scaling_filter

def main():
    c=Commands();directory=HERE/'build';directory.mkdir(exist_ok=True);result='FAIL';detail={};error=None
    try:
        files=make_fixtures(directory/'fixtures',c);report(directory/'fixture-manifest.json',files)
        ffmpeg=shutil.which('ffmpeg')
        for name in ['small','round-landscape','round-portrait']:
            fixture=files[name];source=Path(fixture['path']);output=directory/(name+'-reference.mp4');preset='mp4_h264_720p_v1'
            c.run([ffmpeg,'-nostdin','-hide_banner','-v','error','-y','-i',source,'-map','0:v:0','-map','0:a?',
                   '-vf',scaling_filter(fixture['width'],fixture['height'],preset),'-c:v','libx264','-threads:v','1',
                   '-pix_fmt','yuv420p','-crf','25','-preset','medium','-c:a','aac','-b:a','64k',output])
            detail[name]=verify(c,source,output,fixture,preset,[1],directory)
        source=Path(files['small']['path']);bad=directory/'checker-wrong-chroma.mp4'
        c.run([ffmpeg,'-nostdin','-hide_banner','-v','error','-y','-i',source,'-map','0:v:0','-an','-vf','swapuv','-c:v','libx264','-threads:v','1','-crf','18',bad])
        _,_,indices,_,_=source_selection(c,source)
        rejected=False
        try:quality(c,source,bad,files['small'],'mp4_h264_standard_v1',indices,directory)
        except AssertionError as failure:rejected=True;detail['negative_chroma_error']=str(failure)
        assert rejected,'Independent checker wrongly accepted swapped U/V'
        result='PASS'
    except Exception as failure:error={'message':str(failure),'traceback':traceback.format_exc()}
    report(HERE/'evidence/fixture-precheck.json',{'captured_at':captured_at(),'result':result,'scope':'Self-authored fixtures and independent checker only; not Core success','detail':detail,'error':error,'commands':c.records})
    print(json.dumps({'result':result,'error':error['message'] if error else None}))
    raise SystemExit(0 if result=='PASS' else 1)
if __name__=='__main__':main()
