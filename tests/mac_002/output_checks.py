"""Physical output checks independent of production Core validation JSON."""
from fractions import Fraction
import hashlib
import json
from pathlib import Path
from media_verify import FFMPEG,FFPROBE,metadata,frames,selected_indices,frame_sequence,timing,psnr_and_patches,audio_mapping,binary


def full_decode(commands,path):
    return commands.run([FFMPEG,'-nostdin','-v','error','-xerror','-i',path,'-map','0:v?','-map','0:a?','-f','null','-']).returncode==0


def layout(commands,source,output):
    src=metadata(commands,source);out=metadata(commands,output)
    ss=[s for s in src['streams'] if s['codec_type'] in ('video','audio')]
    ds=[s for s in out['streams'] if s['codec_type'] in ('video','audio')]
    assert len(ss)==len(ds),(len(ss),len(ds))
    for a,b in zip(ss,ds):
        assert a['codec_type']==b['codec_type']
        for key in (['width','height','pix_fmt','avg_frame_rate'] if a['codec_type']=='video' else ['sample_rate','channels','channel_layout']):
            assert a.get(key)==b.get(key),(key,a.get(key),b.get(key))
        assert a.get('tags',{}).get('language','und')==b.get('tags',{}).get('language','und')
    return out


def accurate(commands,source,output,start_us,end_us,directory):
    expected,src,fs,origin,tb=selected_indices(commands,source,start_us,end_us)
    out=layout(commands,source,output)
    assert next(s for s in out['streams'] if s['codec_type']=='video')['codec_name']=='h264'
    for s in out['streams']:
        if s['codec_type']=='audio':assert s['codec_name']=='aac'
    absolute_origin=Fraction(fs[expected[0]]['best_effort_timestamp'])*tb
    physical={'layout':out,'full_decode':full_decode(commands,output),
       'frame_identity':frame_sequence(commands,source,output,expected),
       'timing':timing(commands,output,source,absolute_origin,origin+Fraction(end_us,1000000)),
       'quality':psnr_and_patches(commands,source,output,expected,directory),
       'audio_source_mapping':audio_mapping(commands,source,output,absolute_origin)}
    return physical


def frame_hashes(commands,path):
    raw=binary(commands,[FFMPEG,'-nostdin','-v','error','-i',path,'-map','0:v:0','-an','-f','framemd5','-'])
    return [line.split(',')[-1].strip() for line in raw.decode().splitlines() if line and not line.startswith('#')]


def fast(commands,source,output,start_frame,end_frame):
    out=layout(commands,source,output);assert full_decode(commands,output)
    src=frame_hashes(commands,source);dst=frame_hashes(commands,output)
    assert dst==src[start_frame:end_frame],('Stream copy decoded frames mismatch',len(dst),start_frame,end_frame)
    expected=list(range(start_frame,end_frame));identity=frame_sequence(commands,source,output,expected)
    def packets(path):
        result=json.loads(commands.run([FFPROBE,'-v','error','-show_packets','-show_data_hash','sha256','-show_entries','packet=stream_index,data_hash,pts,dts,duration','-of','json',path]).stdout)['packets']
        return result
    sp=packets(source);op=packets(output);packet_evidence=[];translations=[]
    srcmeta=metadata(commands,source)
    for stream in out['streams']:
        ix=stream['index'];source_packets=[p['data_hash'] for p in sp if p['stream_index']==ix];output_packets=[p['data_hash'] for p in op if p['stream_index']==ix]
        assert output_packets
        starts=[i for i in range(len(source_packets)-len(output_packets)+1) if source_packets[i:i+len(output_packets)]==output_packets]
        assert len(starts)==1,('Packet payloads are not unique contiguous source slice',ix,starts)
        dts=[int(p['dts']) for p in op if p['stream_index']==ix];assert all(a<b for a,b in zip(dts,dts[1:]))
        before=[p for p in sp if p['stream_index']==ix][starts[0]:starts[0]+len(output_packets)]
        after=[p for p in op if p['stream_index']==ix]
        stb=Fraction(next(s for s in srcmeta['streams'] if s['index']==ix)['time_base']);dtb=Fraction(stream['time_base'])
        shifts=[Fraction(a[field])*stb-Fraction(b[field])*dtb for a,b in zip(before,after) for field in ['pts','dts']]
        assert max(shifts)-min(shifts)<=Fraction(2,1000),('Copy timestamps changed relative mapping',ix)
        translations.extend(shifts)
        packet_evidence.append({'stream_index':ix,'source_packet_start':starts[0],'packet_count':len(output_packets),'sha256_payloads_identical':True,'dts_monotonic':True})
    assert max(translations)-min(translations)<=Fraction(2,1000),'Copy streams use different timeline origins'
    return {'common_timestamp_translation_seconds':str(translations[0]),'layout':out,'full_decode':True,'frame_identity':identity,'decoded_frame_md5_identical':True,'packets':packet_evidence}
