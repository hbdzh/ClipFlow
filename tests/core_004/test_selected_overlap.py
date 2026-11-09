#!/usr/bin/env python3
"""Regression: unselected, nonoverlapping audio cannot reject valid video-only export."""
import argparse
import json
from pathlib import Path
import sys
import traceback
sys.dont_write_bytecode=True
from support import API,HERE,Commands,accepted,captured_at,configuration,control,report,sha256,source_hashes,transcode
from media_verify import metadata,verify

def main():
    p=argparse.ArgumentParser();p.add_argument('--library',required=True);p.add_argument('--label',required=True);opts=p.parse_args()
    directory=HERE/'build'/opts.label;directory.mkdir(exist_ok=False);api=API(opts.library);engine=api.create(configuration());c=Commands();files=json.loads((HERE/'build/fixture-manifest.json').read_text());before=source_hashes();artifact=sha256(opts.library);tests={p.name:sha256(p) for p in HERE.glob('*.py')};checks=[]
    cases=[('late-only',{'mode':'none'},'mp4_h264_standard_v1',True,None),('late-only',{'mode':'none'},'mp4_h264_720p_v1',True,None),
           ('dual-gaps',{'mode':'one','stream_index':1},'mp4_h264_standard_v1',True,None),('dual-gaps',{'mode':'one','stream_index':1},'mp4_h264_720p_v1',True,None),
           ('late-only',{'mode':'all_supported'},'mp4_h264_standard_v1',False,'UNSUPPORTED_FORMAT'),('late-only',{'mode':'one','stream_index':1},'mp4_h264_standard_v1',False,'UNSUPPORTED_FORMAT'),
           ('dual-gaps',{'mode':'all_supported'},'mp4_h264_standard_v1',False,'UNSUPPORTED_FORMAT'),('dual-gaps',{'mode':'one','stream_index':2},'mp4_h264_standard_v1',False,'UNSUPPORTED_FORMAT'),
           ('late-surround',{'mode':'none'},'mp4_h264_standard_v1',False,'UNSUPPORTED_FORMAT'),('rotated',{'mode':'none'},'mp4_h264_standard_v1',False,'UNSUPPORTED_FORMAT')]
    source_facts={key:metadata(c,files[key]['path']) for key in ['late-only','dual-gaps','late-surround']}
    for index,(name,audio,profile,success,expected_code) in enumerate(cases):
        try:
            source=Path(files[name]['path']);out=directory/(str(index)+'.mp4');request=transcode(source,out,profile,audio,(0,500000))
            rc,response=engine.submit(control('batch_submit',submission_id='gap_'+str(index),items=[request]));assert rc==0,(rc,response)
            if success:
                assert response['accepted'],response;value=engine.wait(response['job_ids'][0]);assert value['state']=='succeeded',value
                selected=[] if audio['mode']=='none' else [audio['stream_index']]
                assert value['result']['transcode_plan']['selected_source_audio_stream_indices']==selected
                physical=verify(c,source,out,files[name],profile,selected,directory,0,500000)
                detail={'response':response,'terminal':value,'physical':physical}
            else:
                assert not response['accepted'] and any(x['code']==expected_code for x in response['errors']),response
                assert not out.exists();detail={'response':response,'no_output':True}
            checks.append({'source':name,'audio_selection':audio,'profile':profile,'expected_success':success,'result':'PASS','detail':detail})
        except Exception as e:checks.append({'source':name,'audio_selection':audio,'profile':profile,'expected_success':success,'result':'FAIL','error':str(e),'traceback':traceback.format_exc()})
    for job in engine.jobs:
        rc,value=engine.snapshot_raw(job)
        if rc==0 and value['state'] not in ['succeeded','failed','cancelled']:engine.cancel(job);engine.wait(job)
    assert engine.destroy()==0
    stable=before==source_hashes() and artifact==sha256(opts.library) and tests=={p.name:sha256(p) for p in HERE.glob('*.py')}
    result='PASS' if all(x['result']=='PASS' for x in checks) and stable else 'FAIL'
    report(HERE/'evidence'/('selected-overlap-'+opts.label+'.json'),{'captured_at':captured_at(),'result':result,'scope':'Independent delayed/early/unsupported audio selection against real ABI and physical output','library':{'path':opts.library,'sha256':artifact},'source_hashes_before':before,'source_hashes_after':source_hashes(),'test_source_hashes':tests,'stable':stable,'source_facts':source_facts,'checks':checks,'ffi_outputs':api.outputs,'ffi_frees':api.frees,'commands':c.records})
    print(json.dumps({'result':result,'checks':[(x['source'],x['audio_selection'],x['result']) for x in checks]}));raise SystemExit(0 if result=='PASS' else 1)
if __name__=='__main__':main()
