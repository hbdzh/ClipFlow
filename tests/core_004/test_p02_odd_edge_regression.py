#!/usr/bin/env python3
"""Targeted P02 odd-content edge check against independent gray reference."""
import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from support import API, configuration, control, transcode


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(argv):
    return subprocess.run([str(x) for x in argv],capture_output=True,check=True).stdout


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--library',type=Path,required=True)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--work-dir',type=Path,required=True)
    parser.add_argument('--ffmpeg',type=Path,default=shutil.which('ffmpeg'))
    parser.add_argument('--ffprobe',type=Path,default=shutil.which('ffprobe'))
    args=parser.parse_args();root=args.work_dir.resolve();root.mkdir(parents=True,exist_ok=False)
    source=args.source.resolve(strict=True);before=sha(source);landscape=root/'landscape-source.mp4'
    run([args.ffmpeg,'-nostdin','-v','error','-i',source,'-vf',
         'transpose=clock,setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709',
         '-an','-c:v','libx264','-threads:v','1','-qp','0','-g','25','-bf','2','-pix_fmt','yuv420p',
         '-color_primaries','bt709','-color_trc','bt709','-colorspace','bt709','-color_range','tv',landscape])
    cases=[('portrait',source,719,1280,720,1280,'crop=2:1280:718:0'),
           ('landscape',landscape,1280,719,1280,720,'crop=1280:2:0:718')]
    rows=[];engine=API(args.library).create(configuration(ffmpeg=args.ffmpeg,ffprobe=args.ffprobe))
    try:
        for name,src,w,h,cw,ch,crop in cases:
            out=root/(name+'-output.mp4');row={'case':name,'status':'FAIL','source_sha256':sha(src)}
            try:
                req=transcode(src,out,'mp4_h264_720p_v1',{'mode':'all_supported'},(200000,1200000))
                rc,receipt=engine.submit(control('batch_submit',submission_id='odd_'+name,items=[req]));row['receipt']=receipt
                assert rc==0 and receipt['accepted'],receipt
                final=engine.wait(receipt['job_ids'][0]);row['terminal_state']=final['state'];row['error']=final.get('error')
                assert final['state']=='succeeded',final
                result=final['result'];plan=result['transcode_plan'];row['plan']=plan
                assert [plan[k] for k in ('content_width','content_height','canvas_width','canvas_height')]==[w,h,cw,ch]
                info=json.loads(run([args.ffprobe,'-v','error','-show_streams','-of','json',out]));streams=info['streams'];assert len(streams)==1
                v=streams[0];assert (v['codec_name'],v['width'],v['height'],v['pix_fmt'],v['sample_aspect_ratio'],v['nb_frames'])==('h264',cw,ch,'yuv420p','1:1','25')
                ref_filter=f'trim=start_frame=5:end_frame=30,setpts=PTS-STARTPTS,scale={w}:{h}:flags=bicubic+accurate_rnd,pad={cw}:{ch}:0:0:color=black,setsar=1,format=gray,{crop}'
                output_filter=f'format=gray,{crop}'
                def edge(path,vf):
                    return run([args.ffmpeg,'-nostdin','-v','error','-i',path,'-map','0:v:0','-an','-vf',vf,'-fps_mode','passthrough','-f','rawvideo','-'])
                a,b=edge(src,ref_filter),edge(out,output_filter);framebytes=2560;assert len(a)==len(b)==25*framebytes
                def check_frames(reference, actual):
                    metrics=[]
                    for index in range(25):
                        ref=reference[index*framebytes:(index+1)*framebytes]
                        got=actual[index*framebytes:(index+1)*framebytes]
                        if name=='portrait':
                            content_ref,content_out,pad=ref[0::2],got[0::2],got[1::2]
                        else:
                            content_ref,content_out,pad=ref[:1280],got[:1280],got[1280:]
                        mae=sum(abs(x-y) for x,y in zip(content_ref,content_out))/len(content_ref)
                        ref_mean=sum(content_ref)/len(content_ref);out_mean=sum(content_out)/len(content_out)
                        pad_black=sum(x<=12 for x in pad)/len(pad)
                        metrics.append({'frame':index,'reference_mean':ref_mean,'output_mean':out_mean,
                                        'content_edge_mae':mae,'padding_black_fraction':pad_black})
                        assert ref_mean>60 and mae<20,(name,index,'content edge mismatch',mae,ref_mean,out_mean)
                        assert pad_black>0.95,(name,index,'padding not black',pad_black)
                    return metrics
                metrics=check_frames(a,b)
                row.update(frame_metrics=metrics,max_frame_edge_mae=max(x['content_edge_mae'] for x in metrics),
                           min_frame_padding_black_fraction=min(x['padding_black_fraction'] for x in metrics),output_sha256=sha(out))
                for kind in ('content','padding'):
                    broken=bytearray(b)
                    if name=='portrait': broken[(0 if kind=='content' else 1):framebytes:2]=bytes([0 if kind=='content' else 255])*1280
                    else: broken[(0 if kind=='content' else 1280):(1280 if kind=='content' else framebytes)]=bytes([0 if kind=='content' else 255])*1280
                    try: check_frames(a,broken)
                    except AssertionError: pass
                    else: raise AssertionError((name,'single-frame negative accepted',kind))
                row['single_frame_negatives_rejected']=True
                run([args.ffmpeg,'-nostdin','-v','error','-xerror','-i',out,'-map','0','-f','null','-'])
                row['status']='PASS'
            except Exception as exc:row['failure']=repr(exc)
            rows.append(row);print(json.dumps(row,ensure_ascii=False),flush=True)
    finally:
        destroy=engine.destroy();(root/'results.json').write_text(json.dumps({'cases':rows,'destroy':destroy,'source_unchanged':sha(source)==before},indent=2))
    assert destroy==0 and sha(source)==before and all(x['status']=='PASS' for x in rows)


if __name__=='__main__':main()
