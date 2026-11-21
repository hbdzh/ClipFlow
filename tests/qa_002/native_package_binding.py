#!/usr/bin/env python3
"""Bind final-package static evidence without inheriting earlier runtime claims."""
import argparse,json
from pathlib import Path

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--previous-runtime',type=Path,required=True)
    parser.add_argument('--final-audit',type=Path,required=True)
    parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args()
    previous=json.loads(args.previous_runtime.read_text());final=json.loads(args.final_audit.read_text())
    assert previous['result']=='PASS_LOCAL_DEVELOPMENT_PACKAGE'
    assert final['result']=='PASS_LOCAL_RUNTIME_DEPENDENCIES'
    old=previous['original_audit'];before=old['files'];after=final['files']
    added=sorted(set(after)-set(before));removed=sorted(set(before)-set(after))
    changed={p:{'previous_sha256':before[p],'final_sha256':after[p]} for p in sorted(set(before)&set(after)) if before[p]!=after[p]}
    assert not added and not removed
    assert set(changed)<={'Contents/Info.plist','Contents/MacOS/MediaStudio','Contents/_CodeSignature/CodeResources'}
    report={'schema_version':1,'result':'PASS_STATIC_FINAL_BINDING','evidence_level':'STATIC_ONLY_FINAL_PACKAGE',
      'final_app':final['app'],'final_bundle_id':final['bundle_id'],'file_count':final['file_count'],
      'final_audit':str(args.final_audit),'earlier_runtime_report':str(args.previous_runtime),
      'earlier_runtime_bundle_id':old['bundle_id'],'earlier_runtime_evidence_remains_separate':True,
      'added_files':added,'removed_files':removed,'changed_files':changed,
      'unchanged_files':len(set(before)&set(after))-len(changed),
      'all_fixed_media_AI_helpers_runtime_model_graphs_licenses_unchanged':True,
      'final_package_runtime_inherited_from_previous':False,
      'audio_policy':'No application, selftest, playback, AVPlayer.play, VoiceOver or audio output invoked by this binding or the static audit',
      'scope':'Final package signature/dependency/hash binding only; prior isolated re-signed v1 runtime stays v1 evidence. No final-package GUI, media/AI runtime, listening or new OS user acceptance claimed.'}
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:report[k] for k in ['result','final_bundle_id','file_count','unchanged_files','changed_files']},ensure_ascii=False))

if __name__=='__main__':main()
