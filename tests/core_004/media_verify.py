"""Independent media facts and reference comparisons; no Core validator imports."""
from array import array
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import shutil

FFMPEG=shutil.which('ffmpeg');FFPROBE=shutil.which('ffprobe')

def metadata(c,path):return json.loads(c.run([FFPROBE,'-v','error','-show_format','-show_streams','-of','json',path]).stdout)
def decoded_frames(c,path,selector='v:0'):
    return json.loads(c.run([FFPROBE,'-v','error','-select_streams',selector,'-show_frames','-show_entries','frame=best_effort_timestamp,duration,pkt_duration,nb_samples','-of','json',path]).stdout)['frames']
def dimensions(width,height,preset):
    if preset=='mp4_h264_standard_v1':return width,height,width,height
    assert preset=='mp4_h264_720p_v1'
    bw,bh=(1280,720) if width>=height else (720,1280)
    s=min(Fraction(1),Fraction(bw,width),Fraction(bh,height))
    cw=max(2,(width*s+Fraction(1,2)).numerator//(width*s+Fraction(1,2)).denominator)
    ch=max(2,(height*s+Fraction(1,2)).numerator//(height*s+Fraction(1,2)).denominator)
    return cw,ch,cw+(cw%2),ch+(ch%2)
def scaling_filter(width,height,preset):
    cw,ch,ow,oh=dimensions(width,height,preset)
    return f'scale={cw}:{ch}:flags=bicubic+accurate_rnd,pad={ow}:{oh}:0:0:color=black,setsar=1'
def source_selection(c,path,start_us=None,end_us=None):
    meta=metadata(c,path);video=next(s for s in meta['streams'] if s['codec_type']=='video');frames=decoded_frames(c,path)
    tb=Fraction(video['time_base']);pts=[Fraction(f['best_effort_timestamp'])*tb for f in frames];origin=pts[0]
    start=Fraction(0 if start_us is None else start_us,1000000)
    end=Fraction(end_us,1000000) if end_us is not None else pts[-1]-origin+1/Fraction(video['avg_frame_rate'])
    indices=[i for i,p in enumerate(pts) if start<=p-origin<end]
    assert indices and indices==list(range(indices[0],indices[-1]+1))
    return meta,frames,indices,pts[indices[0]],origin+end

def expected_reference(meta,indices,preset):
    video=next(s for s in meta['streams'] if s['codec_type']=='video')
    return f'trim=start_frame={indices[0]}:end_frame={indices[-1]+1},setpts=PTS-STARTPTS,'+scaling_filter(int(video['width']),int(video['height']),preset)
def raw(c,path,vf,pixfmt='gray'):
    return c.run([FFMPEG,'-nostdin','-v','error','-i',path,'-map','0:v:0','-an','-vf',vf,
                  '-pix_fmt',pixfmt,'-fps_mode','passthrough','-f','rawvideo','-'],binary=True)
def frame_identity(c,source,output,fixture,preset,indices):
    meta=metadata(c,source);ref=expected_reference(meta,indices,preset)
    cw,ch,_,_=dimensions(fixture['width'],fixture['height'],preset)
    counterw=max(2,round(fixture['counter_width']*cw/fixture['width']))
    counterh=max(2,round(fixture['counter_height']*ch/fixture['height']))
    crop=f'format=gray,crop={counterw}:{counterh}:0:0,scale=48:16:flags=area'
    reference=raw(c,source,ref+','+crop);export=raw(c,output,crop)
    size=48*16;refs=[reference[i:i+size] for i in range(0,len(reference),size)];outs=[export[i:i+size] for i in range(0,len(export),size)]
    assert len(refs)==len(indices)==len(outs),(len(refs),len(indices),len(outs))
    margin=[];winners=[]
    for number,out in enumerate(outs):
        distances=[sum((a-b)**2 for a,b in zip(out,r)) for r in refs]
        winner=min(range(len(refs)),key=distances.__getitem__)
        assert winner==number,('Wrong source frame',number,winner,indices[number])
        if len(refs)>1:
            other=min(d for n,d in enumerate(distances) if n!=number);assert distances[number]<other
            margin.append(other-distances[number])
        winners.append(indices[winner])
    return {'source_frame_indices':winners,'counter_minimum_squared_distance_margin':min(margin) if margin else None}

def quality(c,source,output,fixture,preset,indices,directory):
    meta=metadata(c,source);ref=expected_reference(meta,indices,preset)
    stats=Path(directory)/('psnr-'+hashlib.sha256(str(output).encode()).hexdigest()[:12]+'.txt')
    graph=f'[0:v]{ref}[reference];[1:v]setpts=PTS-STARTPTS[export];[export][reference]psnr=stats_file={stats}:stats_version=2:shortest=1:repeatlast=0'
    c.run([FFMPEG,'-nostdin','-hide_banner','-v','error','-i',source,'-i',output,'-filter_complex_threads','1','-filter_complex',graph,'-an','-f','null','-'])
    rows=[dict(item.split(':',1) for item in line.split()) for line in stats.read_text().splitlines() if not line.startswith('psnr_log_version')]
    assert len(rows)==len(indices)
    means={plane:sum(float(r['mse_'+plane]) for r in rows)/len(rows) for plane in ['y','u','v']}
    psnr={p:10*math.log10(255**2/v) if v else 999.0 for p,v in means.items()}
    assert all(v>=30 for v in psnr.values()),('Below frozen PSNR',psnr)
    _,_,width,height=dimensions(fixture['width'],fixture['height'],preset)
    # Fixed 64x64 patches under frame counter; reference is generated at target resolution.
    xs=[max(0,min(width-64,x)) for x in [width//8,width//3,width*2//3]]
    y=max(0,min(height-64,height//2));patches=[]
    for x in sorted(set(xs)):
        vf=f'crop=64:64:{x}:{y},format=yuv420p'
        reference=raw(c,source,ref+','+vf,'yuv420p');export=raw(c,output,vf,'yuv420p')
        assert len(reference)==len(export)==len(indices)*6144
        maximum={'u':0.0,'v':0.0}
        for n in range(len(indices)):
            for plane,offset in [('u',4096),('v',5120)]:
                start=n*6144+offset;delta=abs(sum(reference[start:start+1024])-sum(export[start:start+1024]))/1024
                maximum[plane]=max(maximum[plane],delta);assert delta<=8,('Chroma patch mismatch',n,x,y,plane,delta)
        patches.append({'x':x,'y':y,'size':64,'maximum_chroma_mean_delta':maximum})
    return {'reference_filter':ref,'plane_average_psnr_db':psnr,'fixed_64px_patches':patches}

def time_order(c,path):
    meta=metadata(c,path);packets=json.loads(c.run([FFPROBE,'-v','error','-show_packets','-show_entries','packet=stream_index,pts,dts,duration','-of','json',path]).stdout)['packets'];result=[]
    for stream in meta['streams']:
        ps=[p for p in packets if p['stream_index']==stream['index']];assert ps
        dts=[int(p['dts']) for p in ps];assert all(a<b for a,b in zip(dts,dts[1:])),stream
        fs=decoded_frames(c,path,str(stream['index']));pts=[int(f['best_effort_timestamp']) for f in fs];assert all(a<b for a,b in zip(pts,pts[1:])),stream
        result.append({'stream_index':stream['index'],'decode_dts_monotonic':True,'presentation_pts_monotonic':True,'packet_count':len(ps),'decoded_frames':len(fs)})
    return result

def audio(c,source,output,selected_indices,absolute_video_origin,requested_end):
    src=metadata(c,source);dst=metadata(c,output)
    originals={s['index']:s for s in src['streams']};exports=[s for s in dst['streams'] if s['codec_type']=='audio'];assert len(exports)==len(selected_indices)
    video=next(s for s in dst['streams'] if s['codec_type']=='video');fps=Fraction(video['avg_frame_rate']);records=[]
    for source_index,d in zip(selected_indices,exports):
        s=originals[source_index];assert s['codec_type']=='audio'
        for field in ['sample_rate','channels','channel_layout']:assert s.get(field)==d.get(field),(field,s,d)
        assert s.get('tags',{}).get('language','und')==d.get('tags',{}).get('language','und')
        converted=[];first=[];last=[];rate=8000
        for path,stream in [(source,s),(output,d)]:
            vf=f"atrim=start={float(absolute_video_origin):.9f}:end={float(requested_end):.9f},asetpts=PTS-({float(absolute_video_origin):.9f})/TB," if path==source else ''
            # Decode each source stream by its global stream index, preserving channels.
            data=c.run([FFMPEG,'-nostdin','-v','error','-copyts','-i',path,'-map',f"0:{stream['index']}",'-vn','-af',vf+'aresample=8000','-f','f32le','-'],binary=True)
            values=array('f');values.frombytes(data);converted.append(values)
            frames=decoded_frames(c,path,str(stream['index']));first.append(Fraction(frames[0]['best_effort_timestamp'])*Fraction(stream['time_base']))
            last.append(Fraction(frames[-1]['best_effort_timestamp'])*Fraction(stream['time_base'])+Fraction(frames[-1]['nb_samples'],int(stream['sample_rate'])))
        ref,out=converted;channels=int(d['channels']);tolerance=max(Fraction(40,1000),1/fps+Fraction(1024,int(d['sample_rate'])))
        assert abs(first[1]-max(Fraction(0),first[0]-absolute_video_origin))<=tolerance,(first,absolute_video_origin,tolerance)
        source_end=Fraction(s.get('start_time','0'))+Fraction(s['duration_ts'])*Fraction(s['time_base'])
        expected_end=min(source_end,requested_end)-absolute_video_origin
        tail_error=abs(last[1]-expected_end);assert tail_error<=tolerance,('Audio tail mismatch',source_index,last[1],expected_end,tolerance)
        length_error=Fraction(abs(len(ref)-len(out)),channels*rate);assert length_error<=tolerance+Fraction(1,rate),('Audio samples truncated or extended',source_index,length_error,tolerance)
        n=min(len(ref),len(out));assert n>0
        # Fixed common origin; no correlation-peak time shift or arbitrary alignment.
        skip=min(round(rate*.08)*channels,n//8);end=n-skip
        details=[]
        for channel in range(channels):
            r=ref[skip+channel:end:channels];o=out[skip+channel:end:channels]
            energy=sum(v*v for v in r);err=sum((a-b)**2 for a,b in zip(r,o));oe=sum(v*v for v in o)
            rms=(energy/len(r))**.5
            if rms<1e-6:assert (oe/len(o))**.5<=1e-5;relative=None;cosine=None
            else:
                relative=(err/energy)**.5;cosine=sum(a*b for a,b in zip(r,o))/(energy*oe)**.5
                assert relative<=.35 and cosine>=.90,('Audio waveform mapping mismatch',source_index,relative,cosine)
            details.append({'channel':channel,'relative_rms_error':relative,'cosine':cosine})
        records.append({'source_stream_index':source_index,'output_stream_index':d['index'],'tolerance_seconds':str(tolerance),'expected_end_seconds':str(expected_end),'decoded_end_seconds':str(last[1]),'tail_mapping_error_seconds':str(tail_error),'decoded_length_error_seconds':str(length_error),'channels':details})
    return records

def verify(c,source,output,fixture,preset,selected_audio,directory,start_us=None,end_us=None):
    src,_,indices,origin,end=source_selection(c,source,start_us,end_us);dst=metadata(c,output)
    sv=next(s for s in src['streams'] if s['codec_type']=='video');dv=next(s for s in dst['streams'] if s['codec_type']=='video');cw,ch,ow,oh=dimensions(int(sv['width']),int(sv['height']),preset)
    assert len([s for s in dst['streams'] if s['codec_type']=='video'])==1
    assert (dv['width'],dv['height'])==(ow,oh);assert dv['codec_name']=='h264' and dv['pix_fmt']=='yuv420p'
    assert Fraction(dv['avg_frame_rate'])==Fraction(sv['avg_frame_rate']);assert dv.get('sample_aspect_ratio','1:1')=='1:1'
    if fixture['tagged_709']:
        for field in ['color_space','color_transfer','color_primaries']:assert dv.get(field)=='bt709',(field,dv.get(field))
        assert dv.get('color_range')=='tv'
    else:
        for field in ['color_space','color_transfer','color_primaries']:assert dv.get(field) in (None,'unknown','unspecified')
    for stream in dst['streams']:
        if stream['codec_type']=='audio':assert stream['codec_name']=='aac' and stream.get('profile')=='LC'
    c.run([FFMPEG,'-nostdin','-v','error','-i',output,'-map','0','-f','null','-'])
    return {'source_sha256':hashlib.sha256(Path(source).read_bytes()).hexdigest(),'output_sha256':hashlib.sha256(Path(output).read_bytes()).hexdigest(),
            'target':{'content_width':cw,'content_height':ch,'canvas_width':ow,'canvas_height':oh,'padding_right':ow-cw,'padding_bottom':oh-ch},
            'frame_identity':frame_identity(c,source,output,fixture,preset,indices),'quality':quality(c,source,output,fixture,preset,indices,directory),
            'time_order':time_order(c,output),'audio_mapping':audio(c,source,output,selected_audio,origin,end),'all_streams_decoded':True}
