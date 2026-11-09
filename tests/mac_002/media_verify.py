"""MAC-002 physical checks, adapted from frozen independent CORE-003 QA. No production imports."""
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import time

FFMPEG=shutil.which('ffmpeg')
FFPROBE=shutil.which('ffprobe')


def binary(commands, argv):
    argv=[str(a) for a in argv];start=time.monotonic()
    p=subprocess.run(argv,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=120)
    commands.records.append({'argv':argv,'returncode':p.returncode,'elapsed_seconds':time.monotonic()-start,
                             'stdout_bytes':len(p.stdout),'stdout_sha256':hashlib.sha256(p.stdout).hexdigest(),
                             'stderr':p.stderr.decode(errors='replace')})
    if p.returncode:raise AssertionError((argv,p.returncode,p.stderr[-4000:]))
    return p.stdout


def metadata(commands,path):
    return json.loads(commands.run([FFPROBE,'-v','error','-show_format','-show_streams','-of','json',path]).stdout)


def frames(commands,path):
    return json.loads(commands.run([FFPROBE,'-v','error','-select_streams','v:0','-show_frames',
      '-show_entries','frame=best_effort_timestamp,duration,pkt_duration,key_frame,pict_type','-of','json',path]).stdout)['frames']


def selected_indices(commands,path,start_us,end_us):
    meta=metadata(commands,path);stream=next(s for s in meta['streams'] if s['codec_type']=='video');tb=Fraction(stream['time_base'])
    fs=frames(commands,path);origin=Fraction(fs[0]['best_effort_timestamp'])*tb
    indices=[i for i,f in enumerate(fs) if Fraction(start_us,1000000)<=Fraction(f['best_effort_timestamp'])*tb-origin<Fraction(end_us,1000000)]
    if not indices:raise AssertionError('Independent source selection empty')
    assert indices==list(range(indices[0],indices[-1]+1))
    return indices,meta,fs,origin,tb


def counter_frames(commands,path):
    data=binary(commands,[FFMPEG,'-nostdin','-v','error','-i',path,'-map','0:v:0','-an','-vf',
           'crop=160:48:0:0,scale=32:12:flags=area,format=gray','-fps_mode','passthrough','-f','rawvideo','-'])
    size=32*12;assert len(data)%size==0
    return [data[i:i+size] for i in range(0,len(data),size)]


def frame_sequence(commands,source,output,expected):
    src=counter_frames(commands,source);out=counter_frames(commands,output)
    assert len(out)==len(expected),(len(out),len(expected))
    indices=[];margins=[]
    for frame,target in zip(out,expected):
        distances=[sum((a-b)**2 for a,b in zip(frame,reference)) for reference in src]
        winner=min(range(len(src)),key=distances.__getitem__)
        assert winner==target,('Wrong source content frame',winner,target)
        wrong=min(v for i,v in enumerate(distances) if i!=target)
        assert distances[target]<wrong,('Ambiguous frame identity',target)
        indices.append(winner);margins.append(wrong-distances[target])
    return {'source_frame_indices':indices,'minimum_counter_squared_error_margin':min(margins)}


def timing(commands,path,source=None,actual_video_origin=None,requested_end_absolute=None):
    meta=metadata(commands,path)
    packets=json.loads(commands.run([FFPROBE,'-v','error','-show_packets','-show_entries',
               'packet=stream_index,pts,dts,duration','-of','json',path]).stdout)['packets']
    result={}
    for stream in meta['streams']:
        if stream['codec_type'] not in ('video','audio'):continue
        index=stream['index'];tb=Fraction(stream['time_base']);ps=[p for p in packets if p['stream_index']==index]
        assert ps
        dts=[int(p['dts']) for p in ps];assert all(a<b for a,b in zip(dts,dts[1:])),('nonmonotonic decode DTS',index)
        pts=[Fraction(p['pts'])*tb for p in ps]
        result[index]={'type':stream['codec_type'],'packet_count':len(ps),'minimum_pts_seconds':str(min(pts)),
                       'maximum_end_seconds':str(max((Fraction(p['pts'])+Fraction(p.get('duration',0)))*tb for p in ps)),
                       'decode_order_dts_monotonic':True,'packet_order_pts_monotonic':all(a<=b for a,b in zip(pts,pts[1:]))}
    fs=frames(commands,path);video=next(s for s in meta['streams'] if s['codec_type']=='video');tb=Fraction(video['time_base'])
    pts=[Fraction(f['best_effort_timestamp'])*tb for f in fs]
    assert all(a<b for a,b in zip(pts,pts[1:])), 'nonmonotonic presentation PTS'
    assert abs(pts[0])<=Fraction(1,1000),('first video not zero',pts[0])
    video_end=Fraction(result[video['index']]['maximum_end_seconds'])
    fps=Fraction(video['avg_frame_rate']);max_end=Fraction(40,1000)
    deltas=[]
    original_audio=[]
    if source is not None:original_audio=[s for s in metadata(commands,source)['streams'] if s['codec_type']=='audio']
    audio_order=0
    for stream in meta['streams']:
        if stream['codec_type']=='audio':
            tolerance=max(max_end,1/fps+Fraction(1024,int(stream['sample_rate'])))
            expected_end=video_end
            if original_audio:
                original=original_audio[audio_order]
                original_end=Fraction(original.get('start_time','0'))+Fraction(original['duration'])
                expected_end=min(original_end,requested_end_absolute)-actual_video_origin
            delta=abs(Fraction(result[stream['index']]['maximum_end_seconds'])-expected_end)
            assert delta<=tolerance,('Audio mapping tail mismatch',delta,tolerance)
            deltas.append({'stream_index':stream['index'],'tail_mapping_error_seconds':str(delta),'expected_source_mapped_end_seconds':str(expected_end),'tolerance_seconds':str(tolerance)})
            audio_order+=1
    return {'streams':result,'presentation_pts_monotonic':True,'audio_video_ends':deltas}


def psnr_and_patches(commands,source,output,expected,directory):
    stats=Path(directory)/'quality-psnr.txt'
    graph=f'[0:v]trim=start_frame={expected[0]}:end_frame={expected[-1]+1},setpts=PTS-STARTPTS[ref];[1:v]setpts=PTS-STARTPTS[out];[out][ref]psnr=stats_file={stats}:stats_version=2:shortest=1:repeatlast=0'
    commands.run([FFMPEG,'-nostdin','-hide_banner','-v','error','-i',source,'-i',output,
                  '-filter_complex_threads','1','-filter_complex',graph,'-an','-f','null','-'],timeout=120)
    rows=[dict(item.split(':',1) for item in line.split()) for line in stats.read_text().splitlines() if not line.startswith('psnr_log_version')]
    assert len(rows)==len(expected),(len(rows),len(expected))
    values={plane:[float(r['mse_'+plane]) for r in rows] for plane in ['y','u','v']}
    means={plane:sum(v)/len(v) for plane,v in values.items()}
    psnr={plane:10*math.log10(255**2/mse) if mse else 999 for plane,mse in means.items()}
    assert all(v>=30 for v in psnr.values()),('plane PSNR below frozen 30dB',psnr)
    patches=[]
    width=int(next(s for s in metadata(commands,source)['streams'] if s['codec_type']=='video')['width'])
    xs=[64,256,512,768,1024,1536] if width>=1920 else [176]
    for x in xs:
        source_filter=f'trim=start_frame={expected[0]}:end_frame={expected[-1]+1},crop=64:64:{x}:64,format=yuv420p'
        output_filter=f'crop=64:64:{x}:64,format=yuv420p'
        data=[]
        for path,vf in [(source,source_filter),(output,output_filter)]:
            raw=binary(commands,[FFMPEG,'-nostdin','-v','error','-i',path,'-map','0:v:0','-an','-vf',vf,
                   '-fps_mode','passthrough','-f','rawvideo','-'])
            assert len(raw)==len(expected)*6144
            data.append(raw)
        maximum={'u':0.0,'v':0.0}
        for n in range(len(expected)):
            for name,offset in [('u',4096),('v',5120)]:
                lo=n*6144+offset;hi=lo+1024
                delta=abs(sum(data[0][lo:hi])-sum(data[1][lo:hi]))/1024
                maximum[name]=max(maximum[name],delta)
                assert delta<=8,('patch chroma mean error',x,n,name,delta)
        patches.append({'x':x,'y':64,'size':64,'maximum_chroma_mean_delta':maximum})
    return {'plane_average_psnr_db':psnr,'fixed_64px_patches':patches}


def audio_mapping(commands,source,output,actual_video_origin):
    """Compare decoded noise waveform timing at two windows; no arbitrary output shift."""
    from array import array
    src=metadata(commands,source);dst=metadata(commands,output);records=[];rate=12000
    videos=next(s for s in dst['streams'] if s['codec_type']=='video');fps=Fraction(videos['avg_frame_rate'])
    for order,(ss,ds) in enumerate(zip([s for s in src['streams'] if s['codec_type']=='audio'],
                                      [s for s in dst['streams'] if s['codec_type']=='audio'])):
        decoded=[];firsts=[]
        for path,stream in [(source,ss),(output,ds)]:
            raw=binary(commands,[FFMPEG,'-nostdin','-v','error','-i',path,'-map',f'0:a:{order}',
                  '-vn','-ac','1','-ar',str(rate),'-f','f32le','-'])
            values=array('f');values.frombytes(raw);decoded.append(values)
            fs=json.loads(commands.run([FFPROBE,'-v','error','-select_streams',f'a:{order}','-show_frames',
                '-show_entries','frame=best_effort_timestamp,nb_samples','-of','json',path]).stdout)['frames']
            firsts.append(Fraction(fs[0]['best_effort_timestamp'])*Fraction(stream['time_base']))
        original,exported=decoded
        tolerance=max(Fraction(40,1000),1/fps+Fraction(1024,int(ds['sample_rate'])))
        max_lag=math.ceil(float(tolerance)*rate)+120
        count=min(960,max(120,len(exported)//5));windows=[]
        for begin in sorted({max_lag+120,max(max_lag+120,len(exported)-max_lag-count-120)}):
            if begin+count>=len(exported):continue
            target=round(float(actual_video_origin+firsts[1]-firsts[0])*rate)+begin
            if target-max_lag<0 or target+max_lag+count>=len(original):continue
            wave=exported[begin:begin+count];wave_energy=sum(v*v for v in wave)
            correlations=[]
            for lag in range(-max_lag,max_lag+1):
                ref=original[target+lag:target+lag+count]
                energy=sum(v*v for v in ref)
                score=sum(a*b for a,b in zip(wave,ref))/math.sqrt(max(energy*wave_energy,1e-30))
                correlations.append(score)
            at=max(range(len(correlations)),key=correlations.__getitem__);lag=at-max_lag;score=correlations[at]
            assert score>=.7,('Decoded audio cannot map to source noise',order,begin,score)
            assert abs(Fraction(lag,rate))<=tolerance,('Decoded audio mapping drift',order,lag,tolerance)
            windows.append({'output_sample_at_12k':begin,'predicted_source_sample_at_12k':target,
                'measured_lag_samples':lag,'correlation':score,'tolerance_seconds':str(tolerance)})
        assert windows,('No audio mapping window',len(original),len(exported))
        records.append({'audio_order':order,'source_first_decoded_pts_seconds':str(firsts[0]),
                        'output_first_decoded_pts_seconds':str(firsts[1]),'windows':windows})
    return records
