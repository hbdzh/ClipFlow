"""Independent new JSON failure cases plus legacy request compatibility."""
import argparse
import copy
import json
from pathlib import Path
import sys
import traceback
sys.dont_write_bytecode=True
from support import API,HERE,captured_at,configuration,control,report,sha256,transcode

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--library',required=True);parser.add_argument('--label',required=True);options=parser.parse_args()
    api=API(options.library);checks=[];engine=None
    def check(name,fn):
        try:value=fn();checks.append({'name':name,'result':'PASS','detail':value})
        except Exception as failure:checks.append({'name':name,'result':'FAIL','error':str(failure),'traceback':traceback.format_exc()})
    def malformed_config():
        records=[]
        for field,values in [('core004_enabled',[None,1,'true',[]]),('transcode_timeout_ms',[None,0,-1,7200001,1.5,'600000',True,2**64])]:
            for value in values:
                cfg=configuration();cfg['media_backend'][field]=value;rc,e=api.create_raw(cfg);assert rc==-1 and e is None,(field,value,rc);records.append({'field':field,'value':value,'status':rc})
        cfg=configuration();cfg['media_backend']['core003_enabled']=False;rc,e=api.create_raw(cfg);assert rc==-1 and e is None;return records
    check('new-config-types-and-boundaries',malformed_config)
    def create():
        nonlocal engine
        engine=api.create(configuration());return engine.query('ms_get_version')
    check('explicit-extension-engine',create)
    if engine is not None:
        def failures():
            request=transcode(HERE/'build/fixtures/small.mp4',HERE/'build/contract-output.mp4')
            mutations=[]
            for field,value in [('profile','unknown'),('profile',None),('audio_selection',{'mode':'one'}),('audio_selection',{'mode':'one','stream_index':-1}),('audio_selection',{'mode':'all_supported','stream_index':1}),('audio_selection',{'mode':'none','stream_index':1}),('audio_selection',{'mode':'one','stream_index':True}),('audio_selection',{'mode':'one','stream_index':1,'arbitrary':True}),('audio_selection',{'mode':'unknown'}),('range',{'start_us':0,'end_us':0}),('arbitrary_filter','scale=1:1')]:
                r=copy.deepcopy(request);r[field]=value;mutations.append(r)
            for operation,fields in [('batch_submit',{'submission_id':'','items':[request]}),('batch_submit',{'submission_id':'x','items':[]}),('batch_submit',{'submission_id':'含空格','items':[request]}),('batch_submit',{'submission_id':'x'*65,'items':[request]}),('queue_snapshot',{'arbitrary':1}),('queue_reorder',{'job_ids':[],'arbitrary':1}),('job_retry',{'job_id':'x','submission_id':'x'}),('history_clear',{'arbitrary':1})]:mutations.append(control(operation,**fields))
            before=engine.submit(control('queue_snapshot'));assert before[0]==0,before
            records=[]
            for r in mutations:
                rc,value=engine.submit(r);assert rc==-1 and value is None,(rc,r,value);records.append({'request':r,'status':rc})
            rc,value=engine.submit(control('batch_submit',submission_id='too_many',items=[request]*33));assert rc==-9 and value is None,(rc,value)
            records.append({'case':'33-items-length-boundary','status':rc})
            after=engine.submit(control('queue_snapshot'));assert after==before,('Malformed request changed queue',before,after)
            return records
        check('strict-new-request-no-ghost',failures)
        def close():
            rc=engine.destroy();assert rc==0,rc;return {'status':rc}
        check('close-empty-engine',close)
    result='PASS' if all(x['result']=='PASS' for x in checks) else 'FAIL'
    report(HERE/'evidence'/('contract-'+options.label+'.json'),{'captured_at':captured_at(),'result':result,'scope':'Independent ABI contract cases, no production imports','library':{'path':str(api.path),'sha256':sha256(api.path)},'checks':checks,'buffer_outputs':api.outputs,'buffer_frees':api.frees})
    print(json.dumps({'result':result,'checks':[(x['name'],x['result']) for x in checks]}));raise SystemExit(0 if result=='PASS' else 1)
if __name__=='__main__':main()
