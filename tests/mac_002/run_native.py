#!/usr/bin/env python3
"""Independent Native MAC-002 acceptance against the production scenario entry."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import threading
import time
import traceback
import uuid
sys.dont_write_bytecode=True
from support import HERE,ROOT,Commands,captured_at,report,sha256,source_hashes
from output_checks import accurate,fast
from media_verify import frame_sequence
from fault_tools import missing_encoders,observed_export_hang


class Suite:
    def __init__(self,binary,label):
        self.binary=Path(binary).resolve(strict=True);self.commands=Commands();self.checks=[]
        self.directory=HERE/'build'/label;self.directory.mkdir(exist_ok=False)
        self.manifest=json.loads((HERE/'build/fixture-manifest.json').read_text())
        self.files={key:Path(value['path']) for key,value in self.manifest.items()}
        self.before=source_hashes();self.binary_hash=sha256(self.binary);self.tests_before={str(p.relative_to(ROOT)):sha256(p) for p in sorted(HERE.glob('*.py'))}
        self.outputs={};self.scenarios=[]
    def run(self,name,actions,history=None,timeout=240):
        scenario=self.directory/(name+'.json')
        if history is None:history=self.directory/(name+'-history.json')
        report(scenario,{'schema_version':1,'history_path':str(history),'actions':actions})
        result=self.commands.run([self.binary,'--workspace-self-test','--scenario',scenario],timeout=timeout)
        value=json.loads(result.stdout)
        raw_report=self.directory/(name+'-stdout.json');raw_report.write_text(result.stdout)
        self.commands.records[-1]['stdout_bytes']=len(result.stdout.encode());self.commands.records[-1]['stdout_sha256']=hashlib.sha256(result.stdout.encode()).hexdigest();self.commands.records[-1]['stdout_artifact']=str(raw_report);self.commands.records[-1].pop('stdout')
        self.scenarios.append({'name':name,'scenario_sha256':sha256(scenario),'report':value})
        assert value['passed'] and value['closed'],value
        d=value['bridgeDiagnostics'];a=value['accessDiagnostics']
        assert d['allCallsOffMainThread'] and d['outputCalls']==d['bufferReleaseCalls']
        assert a['activeGranted']==0 and a['granted']==a['stops']
        if d['outputCalls']>0:assert value['heartbeatCount']>2 and value['heartbeatMaxIntervalMs']<1000,value
        return value['steps']
    def check(self,name,fn):
        try:detail=fn();self.checks.append({'name':name,'result':'PASS','detail':detail})
        except Exception as e:self.checks.append({'name':name,'result':'FAIL','error':str(e),'traceback':traceback.format_exc()})
    def path(self,name):return str(self.directory/name)
    def imp(self,*keys):return {'action':'import','paths':[str(self.files[k]) for k in keys]}
    def rng(self,a,b):return {'action':'range','start_us':a,'end_us':b}
    def mode(self,m):return {'action':'mode','value':m}
    def export(self,name,wait=True):return {'action':'export','destination':self.path(name),'wait':wait}
    def task(self,step,name):return next(t for t in step['tasks'] if t['destinationName']==name)
    def successful(self,task,mode):
        assert task['state']=='succeeded' and task['coreJobId'] and task['mode']==mode,task
        r=task['result'];assert r['fileCreated'] and not r['mockOnly']
        assert r['reencoded']==(mode=='accurateReencode') and r['lossless']==(mode!='accurateReencode')
        assert r['profile']==('mp4_h264_standard_v1' if mode=='accurateReencode' else 'mp4_h264_copy_v1')
        return r
    def workspace(self):
        steps=self.run('workspace',[self.imp('a','b','a'),self.rng(1100000,2600000),self.mode('streamCopyV1'),
            {'action':'select','index':1},self.rng(400000,1700000),self.mode('accurateReencode'),
            {'action':'seek','position_us':800000},{'action':'preview-frame','destination':self.path('b-preview.png'),'position_us':800000},
            {'action':'play','milliseconds':1500},{'action':'select','index':0},self.export('a-fast.mp4',False),
            {'action':'select','index':1},self.rng(500000,1500000),{'action':'wait'},
            self.rng(400000,1700000),self.export('b-accurate.mp4'),{'action':'restart'},{'action':'clear-history'}])
        assert len(steps[0]['assets'])==2
        a,b=steps[0]['assets'];assert a['sourceName']=='a.mp4' and b['sourceName']=='b.mp4'
        selected=next(s for s in steps if s['action']=='preview-frame');p=selected['preview']
        assert p['ready'] and p['timelineOffsetUs']==5000000 and abs(p['imageActualItemUs']-5800000)<=40000,p
        preview_identity=frame_sequence(self.commands,self.files['b'],self.directory/'b-preview.png',[20])
        play=next(s for s in steps if s['action']=='play');assert play['preview']['playbackObserved']
        exports=[s for s in steps if s['action']=='export'];active=exports[0]['activeExport']
        assert active['draft']['assetId']==a['id'] and active['draft']['range']=={'startUs':1100000,'endUs':2600000}
        assert active['draft']['mode']=='streamCopyV1'
        switching=[s for s in steps if s['action']=='select'][-1];assert switching['selectedAssetId']==b['id']
        wait=next(s for s in steps if s['action']=='wait');at=self.task(wait,'a-fast.mp4');self.successful(at,'streamCopyV1')
        assert at['sourceAssetId']==a['id'] and at['requestedRange']=={'startUs':1100000,'endUs':2600000}
        assert len(wait['tasks'])==1
        bt=self.task(exports[1],'b-accurate.mp4');self.successful(bt,'accurateReencode');assert bt['sourceAssetId']==b['id']
        restart=next(s for s in steps if s['action']=='restart');assert not restart['assets'] and not restart.get('activeExport')
        assert len(restart['tasks'])==2 and all(not t.get('result') for t in restart['tasks'])
        assert not next(s for s in steps if s['action']=='clear-history')['tasks']
        self.outputs['a-fast.mp4']=fast(self.commands,self.files['a'],self.directory/'a-fast.mp4',30,90)
        self.outputs['b-accurate.mp4']=accurate(self.commands,self.files['b'],self.directory/'b-accurate.mp4',400000,1700000,self.directory)
        return {'steps':len(steps),'physical_outputs':['a-fast.mp4','b-accurate.mp4'],'normalized_preview_at_us':p['imageActualItemUs'],'physical_preview_frame_identity':preview_identity}
    def accurate_matrix(self):
        result=[]
        for key in ['a','quality1080','noaudio','unicode']:
            name=("输出 空格 ; $() '引号'.mp4" if key=='unicode' else key+'-accurate.mp4');steps=self.run('accurate-'+key,[self.imp(key),self.rng(1100000,2600000),self.mode('accurateReencode'),self.export(name)])
            self.successful(self.task(steps[-1],name),'accurateReencode')
            self.outputs[name]=accurate(self.commands,self.files[key],self.directory/name,1100000,2600000,self.directory)
            result.append(name)
        return result
    def recovery(self):
        conflict=self.directory/'existing.mp4';conflict.write_bytes(b'KEEP EXISTING TARGET EXACTLY');before=sha256(conflict)
        steps=self.run('recovery',[self.imp('bad','rotation','a'),{'action':'select','index':0},self.export('bad-must-not-exist.mp4'),
            {'action':'select','index':1},self.export('rotation-must-not-exist.mp4'),{'action':'select','index':2},
            self.rng(2600000,1100000),self.export('range-must-not-exist.mp4'),self.rng(1100000,2600000),self.export('existing.mp4'),
            {'action':'export','destination':str(self.files['a'])},self.export('recovered.mp4')])
        assert steps[0]['assets'][0]['state']=='failed'
        for name in ['bad-must-not-exist.mp4','rotation-must-not-exist.mp4','range-must-not-exist.mp4']:assert not (self.directory/name).exists()
        assert sha256(conflict)==before
        failures=[t for t in steps[-1]['tasks'] if t['state']=='failed'];assert any(t.get('error',{}).get('code')=='OUTPUT_EXISTS' for t in failures),failures
        self.successful(self.task(steps[-1],'recovered.mp4'),'streamCopyV1');self.outputs['recovered.mp4']=fast(self.commands,self.files['a'],self.directory/'recovered.mp4',30,90)
        return {'bad_state':'failed','existing_sha256':before,'failed_jobs':failures}
    def limits(self):
        directory=self.directory/'capacity';directory.mkdir();paths=[]
        for i in range(101):
            p=directory/(str(i)+'.mp4');shutil.copyfile(self.files['a'],p);paths.append(str(p))
        alias=directory/'same-a.mp4';alias.symlink_to(paths[0]);hard=directory/'hard-a.mp4';os.link(paths[0],hard)
        steps=self.run('limits',[{'action':'import','paths':paths},{'action':'import','paths':paths[:100]},
            {'action':'import','paths':[paths[0],str(alias),str(hard)]},self.imp('b')],timeout=600)
        assert not steps[0]['assets'] and steps[0].get('error')
        assert len(steps[1]['assets'])==100 and all(a['state']=='succeeded' for a in steps[1]['assets'])
        assert len(steps[2]['assets'])==100 and len(steps[3]['assets'])==100 and steps[3].get('error')
        return {'real_unique_files':101,'successfully_probed':100,'canonical_symlink_hardlink_dedup':True,'batch_overflow_atomic':True}
    def history(self):
        template={'id':str(uuid.uuid4()),'sourceAssetId':str(uuid.uuid4()),'sourceName':'metadata-only-source.mp4','destinationName':'metadata-only-output.mp4','mode':'accurateReencode','requestedRange':{'startUs':0,'endUs':1000000},'createdAt':800000000,'state':'succeeded','interrupted':False}
        records=[]
        for i in range(101):
            v=dict(template);v['id']=str(uuid.uuid4());v['destinationName']=f'metadata-only-{i}.mp4';v['state']='running' if i==0 else 'succeeded';records.append(v)
        history=self.directory/'boundary-history.json';report(history,records)
        steps=self.run('history-bound',[{'action':'connect'},{'action':'restart'},{'action':'clear-history'}],history)
        assert len(steps[0]['tasks'])==100 and steps[0]['tasks'][0]['state']=='interrupted' and steps[0]['tasks'][0]['interrupted']
        assert all(not t.get('result') for t in steps[0]['tasks']) and not steps[0].get('activeExport')
        assert not steps[-1]['tasks'] and json.loads(history.read_text())==[]
        return {'scope':'Injected metadata boundary, not 101 real exports','loaded':100,'running_converted_to_interrupted':True,'no_active_export':True,'clear':True}
    def stale_callbacks(self):
        actions=[self.imp('a','b')]
        for i in range(12):actions.extend([{'action':'select','index':i%2,'wait':False},{'action':'sleep','milliseconds':1}])
        actions.extend([{'action':'select','index':1},{'action':'wait'},{'action':'seek','position_us':800000},{'action':'preview-frame','destination':self.path('after-stale.png'),'position_us':800000}])
        steps=self.run('stale-callbacks',actions)
        before=steps[-2];bid=steps[0]['assets'][1]['id'];assert before['selectedAssetId']==bid
        assert before['preview']['ready'] and before['preview']['timelineOffsetUs']==5000000,before
        identity=frame_sequence(self.commands,self.files['b'],self.directory/'after-stale.png',[20])
        assert all(s['selectedAssetId']==bid for s in steps[-4:])
        return {'concurrent_selections':12,'final_selected':'b.mp4','final_preview_source_frame_identity':identity}
    def missing_encoder(self):
        shim=missing_encoders(self.directory/'missing-encoders')
        missing=self.run('missing-tool',[self.imp('a'),{'action':'configure','ffmpeg':self.path('does-not-exist'),'ffprobe':shutil.which('ffprobe')},self.rng(1100000,2600000),self.export('missing-tool-absent.mp4'),{'action':'configure','ffmpeg':shutil.which('ffmpeg'),'ffprobe':shutil.which('ffprobe')},self.export('tool-recovered.mp4')])
        assert not (self.directory/'missing-tool-absent.mp4').exists()
        self.successful(self.task(missing[-1],'tool-recovered.mp4'),'streamCopyV1')
        self.outputs['tool-recovered.mp4']=fast(self.commands,self.files['a'],self.directory/'tool-recovered.mp4',30,90)
        steps=self.run('missing-encoders',[{'action':'configure','ffmpeg':str(shim),'ffprobe':shutil.which('ffprobe')},self.imp('a'),self.mode('accurateReencode'),self.rng(1100000,2600000),self.export('missing-must-not-exist.mp4'),{'action':'configure','ffmpeg':shutil.which('ffmpeg'),'ffprobe':shutil.which('ffprobe')},self.export('encoder-recovered.mp4')])
        assert not (self.directory/'missing-must-not-exist.mp4').exists()
        failed=next(s for s in steps if s['action']=='export');assert failed.get('error') or any(t['state']=='failed' for t in failed['tasks']),failed
        self.successful(self.task(steps[-1],'encoder-recovered.mp4'),'accurateReencode')
        self.outputs['encoder-recovered.mp4']=accurate(self.commands,self.files['a'],self.directory/'encoder-recovered.mp4',1100000,2600000,self.directory)
        return {'missing_fixed_encoders_no_output':True,'same_session_real_recovery':True}
    def cancellation(self):
        evidence=[]
        for operation in ['cancel','shutdown']:
            shim,marker=observed_export_hang(self.directory/('hang-'+operation));observed=[];stop=threading.Event()
            def witness():
                while not stop.is_set():
                    try:
                        data=json.loads(marker.read_text());p=Path(data['staging'])
                        if p.is_file() and p.stat().st_size>0:
                            data['physically_observed_bytes']=p.stat().st_size;data['sha256']=sha256(p);observed.append(data);return
                    except (OSError,ValueError):pass
                    stop.wait(.01)
            watcher=threading.Thread(target=witness);watcher.start()
            try:
                steps=self.run('cleanup-'+operation,[{'action':'configure','ffmpeg':str(shim),'ffprobe':shutil.which('ffprobe')},self.imp('a'),self.rng(1100000,2600000),self.export(operation+'-absent.mp4',False),{'action':'wait-observation','path':str(marker),'contains':'staging'},{'action':'sleep','milliseconds':100},{'action':operation},{'action':'wait'}])
            finally:stop.set();watcher.join()
            assert observed,'No independent real staging witness before terminal'
            value=observed[0];assert value['real_ffmpeg_exit']==0
            try:os.kill(value['pid'],0)
            except ProcessLookupError:pass
            else:raise AssertionError(('Owned export wrapper survived terminal',value['pid']))
            assert not Path(value['staging']).exists() and not (self.directory/(operation+'-absent.mp4')).exists()
            task=self.task(steps[-1],operation+'-absent.mp4');assert task['state']=='cancelled' and not (task.get('result') or {}).get('fileCreated'),task
            assert not list(self.directory.glob('*.partial*'))
            evidence.append({'operation':operation,'owned_process_and_real_output_witness':value,'final_and_staging_absent':True,'task_state':task['state']})
        return evidence
    def finish(self):
        after=source_hashes();tests_after={str(p.relative_to(ROOT)):sha256(p) for p in sorted(HERE.glob('*.py'))};stable=after==self.before and sha256(self.binary)==self.binary_hash and self.tests_before==tests_after
        self.checks.append({'name':'source-and-binary-stability','result':'PASS' if stable else 'FAIL','changed_sources':[p for p in set(self.before)|set(after) if self.before.get(p)!=after.get(p)]})
        value={'captured_at':captured_at(),'result':'PASS' if all(c['result']=='PASS' for c in self.checks) else 'FAIL',
            'scope':'Native same-production-model automated E2E; GUI/VoiceOver not verified by this report',
            'binary':{'path':str(self.binary),'sha256':self.binary_hash},'test_source_hashes_before':self.tests_before,'test_source_hashes_after':tests_after,'source_hashes_before':self.before,'source_hashes_after':after,
            'input_hashes':{k:v['sha256'] for k,v in self.manifest.items()},'input_hashes_unchanged':all(sha256(p)==self.manifest[k]['sha256'] for k,p in self.files.items()),
            'checks':self.checks,'physical_outputs':{name:{'sha256':sha256(self.directory/name),'evidence':v} for name,v in self.outputs.items()},
            'scenarios':self.scenarios,'commands':self.commands.records}
        target=HERE/'evidence'/('native-'+self.directory.name+'.json');report(target,value)
        print(json.dumps({'result':value['result'],'evidence':str(target),'checks':[{k:c[k] for k in ['name','result']} for c in self.checks]},ensure_ascii=False))
        return 0 if value['result']=='PASS' else 1


def main():
    p=argparse.ArgumentParser();p.add_argument('--binary',required=True);p.add_argument('--label',required=True);p.add_argument('--only',choices=['workspace','accurate_matrix','recovery','limits','history','missing_encoder','cancellation','stale_callbacks','final','shutdown-regression']);args=p.parse_args()
    s=Suite(args.binary,args.label)
    for name in (['workspace','cancellation','stale_callbacks','history'] if args.only=='shutdown-regression' else ['workspace','accurate_matrix','recovery','history','missing_encoder','cancellation','stale_callbacks'] if args.only=='final' else [args.only] if args.only else ['workspace','accurate_matrix','recovery','limits','history','missing_encoder','cancellation','stale_callbacks']):s.check(name,getattr(s,name))
    raise SystemExit(s.finish())

if __name__=='__main__':main()
