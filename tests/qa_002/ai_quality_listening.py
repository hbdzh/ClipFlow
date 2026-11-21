#!/usr/bin/env python3
"""Prepare randomized offline listening sets, or assess two returned human CSVs.

The reveal key stays outside the user package. No scores are filled automatically.
"""
import argparse,csv,hashlib,json,secrets,shutil,statistics,zipfile
from pathlib import Path
import numpy as np
import soundfile as sf
HEADERS=['listener_id','sample_id','stem','version','fidelity','crosstalk','artifact_control','severe_missing_or_pop','notes','device','player','volume','date','independent_confirmation']
DIMS=['fidelity','crosstalk','artifact_control']
GAIN=.5
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def write(p,d):Path(p).write_text(json.dumps(d,ensure_ascii=False,indent=2,allow_nan=False)+'\n')

def prepare(a):
    result=json.loads(a.results.read_text());assert result['result']=='PASS'
    cohort=json.loads(a.results.with_name('quality-cohort.json').read_text());root=Path(result['audio_root'])
    a.output.mkdir(parents=True,exist_ok=False);a.key.parent.mkdir(parents=True,exist_ok=True)
    assert not a.key.exists() and not a.key.is_relative_to(a.output)
    cases=[v for v in cohort['cases'] if v['blind_listening']];assert len(cases)>=6
    key={'scope':'Reveal only after both independent listeners submit original scores','presentation_gain':GAIN,'no_per_file_normalization':True,'listeners':{},'production_results_sha256':sha(a.results),'production_core_sha256':result['core_sha256'],'worker_sha256':result['worker_sha256']}
    files=[]
    for listener in ['L1','L2']:
        folder=a.output/listener;folder.mkdir();order=list(cases);rng=secrets.SystemRandom();rng.shuffle(order);mapping={};rows=[];sections=[]
        for index,case in enumerate(order,1):
            sid=f'S{index:02d}';sample=folder/sid;sample.mkdir();source=Path(case['folder'])
            def presentation(original,name):
                x,rate=sf.read(original,dtype='float32',always_2d=True);assert rate==44100 and x.shape[1]==2 and np.isfinite(x).all()
                target=sample/name;sf.write(target,x*GAIN,rate,subtype='FLOAT');files.append({'path':str(target.relative_to(a.output)),'sha256':sha(target),'source_sha256':sha(original),'presentation_gain':GAIN})
            presentation(source/'mix.wav','mix.wav');blocks=[];casekey={'case_id':case['id'],'variants':{}}
            for stem in ['vocals','accompaniment']:
                presentation(source/('clean-'+stem+'.wav'),'clean-'+stem+'.wav')
                variants=['native','independent_cpu'];rng.shuffle(variants);casekey['variants'][stem]={}
                for version,origin in zip(['A','B'],variants):
                    original=root/('result-'+case['id'])/(stem+'.wav') if origin=='native' else source/'onnx_cpu'/(stem+'.wav')
                    presentation(original,stem+'-'+version+'.wav');casekey['variants'][stem][version]=origin
                    rows.append(dict(zip(HEADERS,[listener,sid,stem,version]+['']*(len(HEADERS)-4))))
                title='人声' if stem=='vocals' else '伴奏'
                audios=''.join(f'<p>匿名版本 {v}<audio controls preload="none" src="{sid}/{stem}-{v}.wav"></audio></p>' for v in ['A','B'])
                scoring=''
                for v in ['A','B']:
                    scoring+=f'<tr data-sample="{sid}" data-stem="{stem}" data-version="{v}"><td>{sid}/{title}/{v}</td>'
                    for d in DIMS:scoring+=f'<td><select data-field="{d}"><option value="">未评分</option>'+''.join(f'<option>{n}</option>' for n in range(1,6))+'</select></td>'
                    scoring+='<td><select data-field="severe_missing_or_pop"><option value="">未判断</option><option value="no">未发现</option><option value="yes">发现</option></select></td><td><input data-field="notes" placeholder="问题与时间；无则填无"></td></tr>'
                blocks.append(f'<h3>{title}</h3><p>干净目标参考<audio controls preload="none" src="{sid}/clean-{stem}.wav"></audio></p>{audios}<table><tr><th>条目</th><th>保真</th><th>串音控制</th><th>伪影控制</th><th>严重缺字/新增爆音</th><th>备注/时间</th></tr>{scoring}</table>')
            mapping[sid]=casekey
            sections.append(f'<section><h2>{sid}</h2><p>原始混合<audio controls preload="none" src="{sid}/mix.wav"></audio></p>'+''.join(blocks)+'</section>')
        key['listeners'][listener]=mapping
        with (folder/'scores-empty.csv').open('w',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=HEADERS);writer.writeheader();writer.writerows(rows)
        script=r'''const listener=LISTENER;const headers=HEADERS;
function save(){let rows=[headers];document.querySelectorAll('tr[data-sample]').forEach(tr=>{let r={listener_id:listener,sample_id:tr.dataset.sample,stem:tr.dataset.stem,version:tr.dataset.version};tr.querySelectorAll('[data-field]').forEach(v=>r[v.dataset.field]=v.value);['device','player','volume','date'].forEach(k=>r[k]=document.getElementById(k).value);r.independent_confirmation=document.getElementById('independent').checked?'yes':'';rows.push(headers.map(h=>r[h]??''));});let csv=rows.map(r=>r.map(v=>'"'+String(v).replaceAll('"','""')+'"').join(',')).join('\r\n');let url=URL.createObjectURL(new Blob(['\ufeff'+csv],{type:'text/csv;charset=utf-8'}));let a=document.createElement('a');a.href=url;a.download=listener+'-scores.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
document.getElementById('save').addEventListener('click',save);'''.replace('LISTENER',json.dumps(listener)).replace('HEADERS',json.dumps(HEADERS))
        page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>独立盲听 LISTENER</title><style>body{max-width:1080px;margin:32px auto;font:17px system-ui;line-height:1.6;padding:0 18px;background:#fafaf8;color:#202421}section{background:white;border:1px solid #ddd;border-radius:12px;margin:24px 0;padding:20px}audio{display:block;width:min(100%,560px)}table{width:100%;border-collapse:collapse;font-size:14px}td,th{text-align:left;border:1px solid #ddd;padding:7px}input{max-width:180px}select,button,input{font:inherit;padding:6px}button{background:#174c3c;color:white;border:0;border-radius:8px;padding:12px 20px}fieldset{margin:20px 0}</style><h1>独立盲听 LISTENER</h1><p>请独立评分，不与另一位听者讨论、交换顺序或分数，请勿提前揭盲。六组素材，每组人声/伴奏各两个匿名版本。对照混合和干净目标参考，评价每个版本。</p><p>1=很差，3=可接受，5=很好。串音控制和伪影控制越高越好。干净目标为静音时评价输出是否保持安静。严重缺字或新增爆音即使分数合格也须记录“发现”并标时间。</p><p>全部音频统一使用公开的 <b>0.5 线性呈现增益</b>，没有逐条响度归一化、限幅、裁切或相关峰移位；原始验收输出未改动。选定播放音量后保持不变，关闭播放器音效/响度均衡。此处不显示算法、指标或版本身份。</p><fieldset><legend>听评条件（各自填写）</legend>设备 <input id="device"> 播放器 <input id="player"> 音量 <input id="volume"> 日期 <input id="date" type="date"><p><label><input id="independent" type="checkbox">我独立听评，评分前未与另一位交换分数或揭盲</label></p></fieldset>SECTIONS<button id="save">导出本人的 CSV 评分</button><p>评分不会联网或自动提交，CSV保存到本机下载目录；请回传内容，无需姓名等个人资料。也可手动填写 scores-empty.csv。刷新会丢失尚未导出的评分。</p><p>来源署名见包根目录 ATTRIBUTION.md；仅供本轮听评。</p><script>SCRIPT</script></html>'''.replace('LISTENER',listener).replace('SECTIONS',''.join(sections)).replace('SCRIPT',script)
        (folder/'index.html').write_text(page)
    write(a.key,key);write(a.output/'audio-manifest.json',{'presentation_gain':GAIN,'no_per_file_normalization':True,'files':files,'human_scores_generated':False})
    (a.output/'README.md').write_text('两名听者分别打开 L1/index.html 与 L2/index.html，独立评分后导出 CSV。每人24条，三维各1–5。留空不算通过。收到两份原始评分后揭盲，逐生产stem/逐听者评估三维中位>=3且无严重缺字/新增爆音。机器指标不替代评分。\n')
    if a.attribution:shutil.copy2(a.attribution,a.output/'ATTRIBUTION.md')
    archive=a.output.with_suffix('.zip');assert not archive.exists()
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for f in sorted(a.output.rglob('*')):
            if f.is_file():z.write(f,str(f.relative_to(a.output)))
    summary={'result':'READY_FOR_HUMAN_LISTENING','score_status':'NOT_RUN','package':str(a.output),'zip':str(archive),'zip_sha256':sha(archive),'audio_files':len(files),'listeners':['L1','L2'],'rows_per_listener':len(rows),'presentation_gain':GAIN,'reveal_key_outside_package':True,'no_human_scores_generated':True}
    write(a.results.with_name('quality-listening-package.json'),summary);print(json.dumps(summary,ensure_ascii=False))

def assess(a):
    key=json.loads(a.key.read_text());allrows={};summary=[]
    for path in a.scores:
        rows=list(csv.DictReader(path.open(encoding='utf-8-sig')));ids={r['listener_id'] for r in rows};assert len(ids)==1
        listener=next(iter(ids));assert listener in key['listeners'] and listener not in allrows
        assert len(key['listeners'][listener])>=6
        expected={(sid,stem,v) for sid,c in key['listeners'][listener].items() for stem in ['vocals','accompaniment'] for v in ['A','B']};seen=set()
        for r in rows:
            identity=(r['sample_id'],r['stem'],r['version']);assert identity in expected and identity not in seen;seen.add(identity)
            for d in DIMS:assert r[d] in ['1','2','3','4','5'],(identity,d,'missing_or_invalid')
            assert r['severe_missing_or_pop'] in ['yes','no']
            assert r['independent_confirmation']=='yes'
            assert all(r[k].strip() for k in ['device','player','volume','date'])
        assert seen==expected;allrows[listener]=rows
    assert set(allrows)=={'L1','L2'}
    for listener,rows in allrows.items():
        for stem in ['vocals','accompaniment']:
            prod=[r for r in rows if r['stem']==stem and key['listeners'][listener][r['sample_id']]['variants'][stem][r['version']]=='native']
            medians={d:statistics.median(int(r[d]) for r in prod) for d in DIMS};severe=[{'sample':r['sample_id'],'notes':r['notes']} for r in prod if r['severe_missing_or_pop']=='yes']
            summary.append({'listener':listener,'stem':stem,'medians':medians,'severe_events':severe,'passed':all(v>=3 for v in medians.values()) and not severe})
    value={'result':'PASS' if all(v['passed'] for v in summary) else 'FAIL','scope':'Two returned human sheets, production variants only after reveal','score_files':{str(p):sha(p) for p in a.scores},'summary':summary}
    write(a.report,value);print(json.dumps(value,ensure_ascii=False))

def main():
    p=argparse.ArgumentParser(description=__doc__);s=p.add_subparsers(dest='action',required=True)
    b=s.add_parser('prepare');b.add_argument('--results',required=True,type=Path);b.add_argument('--output',required=True,type=Path);b.add_argument('--key',required=True,type=Path);b.add_argument('--attribution',type=Path)
    c=s.add_parser('assess');c.add_argument('--key',required=True,type=Path);c.add_argument('--scores',required=True,type=Path,nargs=2);c.add_argument('--report',required=True,type=Path)
    a=p.parse_args();prepare(a) if a.action=='prepare' else assess(a)
if __name__=='__main__':main()
