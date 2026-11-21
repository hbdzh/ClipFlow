#!/usr/bin/env python3
"""Synthetic score-sheet validation tests, never human listening evidence."""
import csv,json,tempfile,types,contextlib,io
from pathlib import Path
import ai_quality_listening as q

root=Path(tempfile.mkdtemp(prefix='qa002-listening-checker-only-'))
key={'listeners':{v:{f'S{i:02d}':{'case_id':str(i),'variants':{s:{'A':'native','B':'independent_cpu'} for s in ['vocals','accompaniment']}} for i in range(1,7)} for v in ['L1','L2']}}
q.write(root/'key.json',key)
rows={}
for listener in ['L1','L2']:
 rows[listener]=[]
 for sid in key['listeners'][listener]:
  for stem in ['vocals','accompaniment']:
   for variant in ['A','B']:
    rows[listener].append(dict(zip(q.HEADERS,[listener,sid,stem,variant,'3','3','3','no','SYNTHETIC CHECKER DATA','test device','test player','test volume','2000-01-01','yes'])))
def save():
 for listener,values in rows.items():
  with (root/(listener+'.csv')).open('w',newline='') as f:
   w=csv.DictWriter(f,fieldnames=q.HEADERS);w.writeheader();w.writerows(values)
def run():
 save()
 with contextlib.redirect_stdout(io.StringIO()):q.assess(types.SimpleNamespace(key=root/'key.json',scores=[root/'L1.csv',root/'L2.csv'],report=root/'report.json'))
 return json.loads((root/'report.json').read_text())
checks=[]
assert run()['result']=='PASS';checks.append('synthetic_complete_valid_format')
rows['L1'][0]['fidelity']=''
try:run();raise RuntimeError('blank score accepted')
except AssertionError:checks.append('blank_is_not_pass')
rows['L1'][0]['fidelity']='3';rows['L2'][0]['independent_confirmation']=''
try:run();raise RuntimeError('missing independence declaration accepted')
except AssertionError:checks.append('independence_declaration_required')
rows['L2'][0]['independent_confirmation']='yes'
for r in rows['L2']:
 if r['stem']=='accompaniment' and r['version']=='A':r['artifact_control']='2'
assert run()['result']=='FAIL';checks.append('one_listener_one_stem_dimension_cannot_be_hidden')
for r in rows['L2']:r['artifact_control']='3'
rows['L1'][0]['severe_missing_or_pop']='yes';assert run()['result']=='FAIL';checks.append('severe_event_fails_despite_medians')
rows['L1'][0]['severe_missing_or_pop']='no';rows['L1'].append(rows['L1'][0].copy())
try:run();raise RuntimeError('duplicate score accepted')
except AssertionError:checks.append('duplicate_rows_rejected')
print(json.dumps({'result':'PASS','scope':'Synthetic verifier controls only; no real human scores','checks':checks},indent=2))
