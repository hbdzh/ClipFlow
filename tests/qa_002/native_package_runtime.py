#!/usr/bin/env python3
"""Isolated local developer-package runtime gate; never launches the GUI."""
import argparse, array, json, math, os, plistlib, shutil, subprocess, time, uuid, wave
from pathlib import Path
from native_package_audit import audit, digest

def files(root):
    return {str(p.relative_to(root)): digest(p) for p in sorted(root.rglob('*')) if p.is_file()}

def uninstall_test_copy(report,check):
    install=Path(report['install_copy']);work=Path(report['work']);container=Path(report['container']);bid=report['bundle_id']
    preferences=container/'Data/Library/Preferences'/(bid+'.plist')
    preferences.parent.mkdir(parents=True,exist_ok=True)
    values=plistlib.loads(preferences.read_bytes()) if preferences.exists() else {}
    values['qa002_uninstall_retention_marker']=bid
    preferences.write_bytes(plistlib.dumps(values))
    report['uninstall_preference_marker']='Test-only marker written after all app processes exit; not a production-setting persistence claim'
    report['uninstall_data_scope']='All regular files in the owned app Data/ tree; protected OS containermanager metadata is not user data and is not read'
    preserved={str(p):digest(p) for p in (container/'Data').rglob('*') if p.is_file()}
    check('uninstall-target-is-owned-test-copy',not install.is_symlink() and install.is_relative_to(work)
          and work.parent==Path('/private/tmp') and work.name.startswith('mediastudio-native-package-')
          and plistlib.loads((install/'Contents/Info.plist').read_bytes())['CFBundleIdentifier']==bid)
    shutil.rmtree(install)
    check('local-dev-app-copy-removed',not install.exists())
    check('uninstall-preserved-input-export-history-and-profile',all(Path(p).is_file() and digest(Path(p))==h for p,h in preserved.items()) and container.exists())
    report['preserved_after_uninstall']=preserved
    check('original-candidate-all-files-unchanged',files(Path(report['original_app']))==report['original_audit']['files'])
    report['result']='PASS_LOCAL_DEVELOPMENT_PACKAGE'

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--app',type=Path)
    parser.add_argument('--report',type=Path)
    parser.add_argument('--probe',type=Path)
    parser.add_argument('--resume-uninstall',type=Path)
    args=parser.parse_args()
    if args.resume_uninstall:
        report=json.loads(args.resume_uninstall.read_text())
        assert report['result']=='FAIL' and '.com.apple.containermanagerd.metadata.plist' in report.get('error','')
        assert all(value=='PASS' for value in report['checks'].values())
        assert all(report['checks'].get(name)=='PASS' for name in ['missing-bundled-ffmpeg-refuses-without-Homebrew-borrow','missing-bundled-ffprobe-refuses-without-Homebrew-borrow','runtime-did-not-mutate-installed-bundle','ai-process-2-driver-passed'])
        report['harness_recovery']={'previous_result':report['result'],'previous_error':report.pop('error'),
            'action':'Resume only same already-tested copy uninstall after narrowing data preservation to owned Data/; no media rerun or acceptance weakening'}
        def check(name,value):
            report['checks'][name]='PASS' if value else 'FAIL'
            args.resume_uninstall.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
            if not value:raise AssertionError(name)
        uninstall_test_copy(report,check)
        args.resume_uninstall.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps({'result':report['result'],'checks':len(report['checks']),'resumed_uninstall':True}));return
    if not all([args.app,args.report,args.probe]):parser.error('--app, --report and --probe are required')
    original=args.app.resolve(strict=True)
    token=uuid.uuid4().hex[:12]; bid='local.mediastudio.qa002.native.package.'+token
    work=Path('/private/tmp')/('mediastudio-native-package-'+token)
    install=work/'QA Install With Spaces'/'MediaStudio.app'
    container=Path.home()/'Library/Containers'/bid
    data=container/'Data/tmp'/('QA Offline Inputs '+token)
    report={'schema_version':1,'result':'RUNNING','bundle_id':bid,'original_app':str(original),
            'install_copy':str(install),'work':str(work),'container':str(container),
            'scope':'Local developer app, independent new app profile and process restart; no new OS account, OS restart, GUI or public distribution claim',
            'commands':[],'checks':{}}
    args.report.parent.mkdir(parents=True,exist_ok=True)
    def record():args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    def run(argv,env=None,timeout=240,ok=True):
        start=time.monotonic();r=subprocess.run(list(map(str,argv)),env=env,capture_output=True,text=True,timeout=timeout)
        item={'argv':list(map(str,argv)),'exit':r.returncode,'stdout':r.stdout,'stderr':r.stderr,'elapsed_seconds':time.monotonic()-start}
        report['commands'].append(item);record()
        if ok and r.returncode:raise RuntimeError('Command failed: '+str(argv[0]))
        return item
    def check(name,value):
        report['checks'][name]='PASS' if value else 'FAIL';record()
        if not value:raise AssertionError(name)
    def execute(kind,arguments):
        proof=data/(kind+'-network.json')
        env={k:v for k,v in os.environ.items() if not k.startswith(('MEDIASTUDIO_','DYLD_','QA002_'))}
        env.update(PATH='/usr/bin:/bin',DYLD_INSERT_LIBRARIES=str(probe),QA002_EXPECTED_EXECUTABLE='MediaStudio',QA002_NETWORK_PROBE=str(proof))
        report.setdefault('execution_environment',{'PATH':'/usr/bin:/bin','MEDIASTUDIO_component_overrides':'all removed','DYLD_INSERT_LIBRARIES':'test-only readonly loopback witness','global_network_settings':'unchanged'})
        result=run([install/'Contents/MacOS/MediaStudio',*arguments],env=env,timeout=300)
        payload=json.loads(result['stdout']);check(kind+'-driver-passed',payload.get('passed') is True)
        evidence=json.loads(proof.read_text());report.setdefault('network_denials',{})[kind]=evidence
        check(kind+'-sandbox-IP-denied',evidence['network_denied'] is True)
        return payload
    try:
        report['host']={name:run(argv)['stdout'].strip() for name,argv in [
            ('os',['sw_vers']),('architecture',['uname','-m'])]}
        check('new-test-ID-has-no-existing-container',not container.exists())
        baseline=audit(original);report['original_audit']=baseline
        check('original-runtime-dependency-audit',baseline['result']=='PASS_LOCAL_RUNTIME_DEPENDENCIES')
        original_files=files(original);install.parent.mkdir(parents=True)
        shutil.copytree(original,install,symlinks=True)
        check('relocated-copy-initially-byte-identical',files(install)==original_files)
        info_path=install/'Contents/Info.plist';info=plistlib.loads(info_path.read_bytes());info['CFBundleIdentifier']=bid
        info_path.write_bytes(plistlib.dumps(info,sort_keys=False))
        entitlements=work/'qa-copy-entitlements.plist';entitlements.write_bytes(plistlib.dumps(baseline['entitlements']))
        run(['codesign','--force','--sign','-','--entitlements',entitlements,install])
        run(['codesign','--verify','--deep','--strict',install])
        installed_files=files(install);changed=[k for k in installed_files if installed_files[k]!=original_files.get(k)]
        report['test_copy_identity_changes']=changed
        check('only-new-profile-identity-and-outer-signature-changed',set(changed)<= {'Contents/Info.plist','Contents/MacOS/MediaStudio','Contents/_CodeSignature/CodeResources'})
        data.mkdir(parents=True);probe=data/'network-witness.dylib';shutil.copyfile(args.probe.resolve(strict=True),probe)
        run(['codesign','--force','--sign','-',probe])
        source=data/'Synthetic 30fps AV.mp4'
        # Fixture generator is explicitly outside the package/runtime acceptance.
        run(['/opt/homebrew/bin/ffmpeg','-nostdin','-hide_banner','-loglevel','error','-f','lavfi','-i','testsrc2=size=320x180:rate=30:duration=4',
             '-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=4','-c:v','libx264','-threads','1','-g','30','-pix_fmt','yuv420p','-c:a','aac','-y',source])
        short=data/'Synthetic stereo short.wav';long=data/'Synthetic stereo cancel.wav'
        for path,seconds in [(short,3),(long,30)]:
            samples=array.array('h')
            for i in range(seconds*44100):
                samples.append(round(6000*math.sin(2*math.pi*440*i/44100)))
                samples.append(round(5000*math.sin(2*math.pi*660*i/44100)))
            with wave.open(str(path),'wb') as out:
                out.setnchannels(2);out.setsampwidth(2);out.setframerate(44100);out.writeframes(samples.tobytes())
        sources={str(p):digest(p) for p in [source,short,long]};report['self_authored_source_hashes']=sources
        history=data/'history.json';media_reports=[];output_paths=[]
        for sequence in [1,2]:
            actions=[{'action':'import','paths':[str(source)]},{'action':'range','start_us':1100000,'end_us':2600000}]
            for mode in ['streamCopyV1','accurateReencode','mp4_h264_standard_v1','mp4_h264_720p_v1']:
                target=data/f'process-{sequence}-{mode}.mp4';output_paths.append(target)
                actions += [{'action':'mode' if mode in ['streamCopyV1','accurateReencode'] else 'preset','value':mode},{'action':'export','destination':str(target),'wait':True}]
            scenario=data/f'process-{sequence}-scenario.json';scenario.write_text(json.dumps({'schema_version':1,'history_path':str(history),'actions':actions}))
            payload=execute(f'media-process-{sequence}',['--queue-self-test','--scenario',str(scenario)]);media_reports.append(payload)
            for target in output_paths[-4:]:
                tasks=[t for s in payload['steps'] for t in s['tasks'] if t['destinationName']==target.name and t['state']=='succeeded']
                check(target.name+'-real-Core-success',bool(tasks) and any(t.get('result',{}).get('validation',{}).get('decodeAllStreams') is True for t in tasks))
            ai=execute(f'ai-process-{sequence}',['--ai-self-test','--ai-source',str(short),'--ai-cancel-source',str(long),'--ai-export',str(data/f'AI Export {sequence}')])
            check(f'ai-process-{sequence}-two-preview-players',all(ai[k]['playable'] and ai[k]['reached_playing'] and ai[k]['advanced_seconds']>0.1 for k in ['vocals_playback','accompaniment_playback']))
        report['media_driver_reports']=media_reports
        check('restart-used-distinct-production-app-processes',len({r['pid'] for r in report['network_denials'].values()})==4)
        for target in output_paths+list(data.glob('AI Export */**/*.wav')):
            inspection=run(['/opt/homebrew/bin/ffprobe','-v','error','-show_format','-show_streams','-of','json',target])
            check(target.name+'-has-streams',bool(json.loads(inspection['stdout'])['streams']))
            run(['/opt/homebrew/bin/ffmpeg','-nostdin','-v','error','-i',target,'-f','null','-'])
        check('source-hashes-unchanged',all(digest(Path(p))==h for p,h in sources.items()))
        check('runtime-did-not-mutate-installed-bundle',files(install)==installed_files)
        # Broken-package negative case is confined to this disposable signed copy.
        for component in ['ffmpeg','ffprobe']:
            helper=install/'Contents/MacOS'/component;backup=work/(component+'-removed-for-negative');helper.rename(backup)
            run(['codesign','--force','--sign','-','--entitlements',entitlements,install])
            broken=execute('missing-'+component,['--media-self-test','--source',str(source),'--destination',str(data/('must-not-create-'+component+'.mp4')),'--expect-import-failure'])
            features=broken.get('connection',{}).get('capabilities',{}).get('features',{})
            check('missing-bundled-'+component+'-refuses-without-Homebrew-borrow',
                  features.get('probe',{}).get('available') is False and features.get('probe',{}).get('reason')=='DEPENDENCY_MISSING'
                  and not (data/('must-not-create-'+component+'.mp4')).exists())
            backup.rename(helper);run(['codesign','--force','--sign','-','--entitlements',entitlements,install])
        uninstall_test_copy(report,check)
    except Exception as error:
        report['result']='FAIL';report['error']=str(error);raise
    finally:record()
    print(json.dumps({'result':report['result'],'bundle_id':bid,'checks':len(report['checks']),'report':str(args.report)},ensure_ascii=False))

if __name__=='__main__':main()
