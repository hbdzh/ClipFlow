#!/usr/bin/env python3
"""Independent CLI paths and JSONL session; all scheduling remains in Core."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import time
import traceback
sys.dont_write_bytecode=True
from support import HERE,ROOT,Commands,captured_at,configuration,control,report,sha256,source_hashes,transcode
from media_verify import verify

class Session:
    def __init__(self,binary,config):
        self.argv=[str(binary),'core004-session',str(config)];self.p=subprocess.Popen(self.argv,cwd=ROOT,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,bufsize=1);self.rows=[]
    def send(self,value):
        self.p.stdin.write(json.dumps(value,ensure_ascii=False)+'\n');self.p.stdin.flush()
        ready,_,_=select.select([self.p.stdout],[],[],180);assert ready,'CLI did not flush response'
        line=self.p.stdout.readline();assert line,('CLI EOF',self.p.poll());result=json.loads(line);self.rows.append({'request':value,'response':result});return result
    def finish(self):
        self.p.stdin.close();code=self.p.wait(timeout=30);stderr=self.p.stderr.read();return {'argv':self.argv,'returncode':code,'stderr':stderr,'messages':self.rows}

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--binary',required=True);parser.add_argument('--label',required=True);opts=parser.parse_args();binary=Path(opts.binary).resolve(strict=True)
    directory=HERE/'build'/opts.label;directory.mkdir(exist_ok=False);c=Commands();source=HERE/'build/fixtures/small.mp4';fixture=json.loads((HERE/'build/fixture-manifest.json').read_text())['small'];config=directory/'config.json';report(config,configuration())
    before=source_hashes();tests={p.name:sha256(p) for p in HERE.glob('*.py')};artifact=sha256(binary);result='FAIL';error=None;data={};session=None
    try:
        cap=json.loads(c.run([binary,'capabilities','--core004']).stdout);assert 'transcode' in json.dumps(cap) and 'mp4_h264_720p_v1' in json.dumps(cap);data['capabilities']=cap
        output=directory/'direct-720-none.mp4';value=json.loads(c.run([binary,'transcode',source,output,'mp4_h264_720p_v1','none']).stdout);assert value['final']['state']=='succeeded',value
        data['direct']={'result':value,'physical':verify(c,source,output,fixture,'mp4_h264_720p_v1',[],directory)}
        failure=c.run([binary,'transcode',source,output,'mp4_h264_720p_v1','none'],expected=None);refused=json.loads(failure.stdout);assert failure.returncode!=0 and not refused['preflight']['accepted'] and 'final' not in refused,refused;data['existing_target_rejected']=refused
        unicode_fixture=json.loads((HERE/'build/fixture-manifest.json').read_text())['unicode'];unicode_source=Path(unicode_fixture['path']);unicode_output=directory/"CLI 输出 ; $() '引号'.mp4"
        unicode_result=json.loads(c.run([binary,'transcode',unicode_source,unicode_output,'mp4_h264_720p_v1','2']).stdout);assert unicode_result['final']['state']=='succeeded',unicode_result
        data['unicode_one_global_index']={'result':unicode_result,'physical':verify(c,unicode_source,unicode_output,unicode_fixture,'mp4_h264_720p_v1',[2],directory)}
        session=Session(binary,config)
        invalid=session.send({'command':'invalid'});assert not invalid['ok'] and invalid['error']['code']==-8
        empty=session.send({'command':'submit','request':control('queue_snapshot')});assert empty['ok'] and empty['response']['active_count']==0
        request=control('batch_submit',submission_id='cli_real_batch',items=[transcode(source,directory/'session-real.mp4')])
        accepted=session.send({'command':'submit','request':request});assert accepted['ok'] and accepted['response']['accepted'];ids=accepted['response']['job_ids'];assert len(ids)==1
        replay=session.send({'command':'submit','request':request});assert replay['ok'] and replay['response']['replayed'] and replay['response']['job_ids']==ids
        terminal=session.send({'command':'wait','job_id':ids[0],'timeout_ms':120000});assert terminal['ok'] and terminal['response']['state']=='succeeded'
        snapshot=session.send({'command':'snapshot','job_id':ids[0]});assert snapshot['ok'] and snapshot['response']['state']=='succeeded'
        cleared=session.send({'command':'submit','request':control('history_clear')});assert cleared['ok'] and cleared['response']['cleared_count']==1
        missing=session.send({'command':'snapshot','job_id':ids[0]});assert not missing['ok'] and missing['error']['code']==-6
        close=session.send({'command':'close'});assert close['ok'] and close['response']['closed']
        data['session']=session.finish();session=None;assert data['session']['returncode']==0
        data['session_physical']=verify(c,source,directory/'session-real.mp4',fixture,'mp4_h264_standard_v1',[1],directory)
        huge=b'{' + b' '*66000+b'}\n'+b'{"command":"close"}\n'
        start=time.monotonic();p=subprocess.run([binary,'core004-session',config],input=huge,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
        rows=[json.loads(line) for line in p.stdout.splitlines()];assert p.returncode!=0 and len(rows)==1 and rows[0]['error']['code']==-9,rows
        data['oversized_line_no_tail_execution']={'argv':[str(binary),'core004-session',str(config)],'input_bytes':len(huge),'input_sha256':hashlib.sha256(huge).hexdigest(),'returncode':p.returncode,'stdout':rows,'stderr':p.stderr.decode(),'elapsed_seconds':time.monotonic()-start}
        assert before==source_hashes() and artifact==sha256(binary) and tests=={p.name:sha256(p) for p in HERE.glob('*.py')}
        result='PASS'
    except Exception as e:error={'message':str(e),'traceback':traceback.format_exc()}
    finally:
        if session is not None:
            try:data['session_on_failure']=session.finish()
            except Exception as e:data['session_cleanup_error']=str(e)
    report(HERE/'evidence'/('cli-'+opts.label+'.json'),{'captured_at':captured_at(),'result':result,'scope':'Independent real CLI transcode/session, not native GUI','binary':{'path':str(binary),'sha256':artifact},'source_hashes':before,'test_source_hashes':tests,'detail':data,'error':error,'commands':c.records})
    print(json.dumps({'result':result,'error':error['message'] if error else None}));raise SystemExit(0 if result=='PASS' else 1)
if __name__=='__main__':main()
