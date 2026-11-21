#!/usr/bin/env python3
"""Read-only final GUI process sampling. UI actions are performed only by CUA."""
import argparse,hashlib,json,sys,time
from pathlib import Path
sys.dont_write_bytecode=True
sys.path.insert(0,str(Path(__file__).resolve().parent))
from resource_baseline import ps,tree
p=argparse.ArgumentParser();p.add_argument('--app',type=Path,required=True);p.add_argument('--evidence',type=Path,required=True);p.add_argument('--stop',type=Path,required=True);a=p.parse_args();target=str(a.app.resolve()/'Contents/MacOS/MediaStudio');start=time.monotonic();rows=[];seen=set();log=a.evidence/'gui-resource-samples.jsonl';assert not log.exists()
with log.open('w') as f:
 while not a.stop.exists() and time.monotonic()-start<1200:
  processes=ps();parents=[pid for pid,r in processes.items() if r['executable']==target]
  if parents:
   selected={v['pid']:v for pid in parents for v in tree(processes,pid)};seen.update(selected);row={'elapsed_seconds':time.monotonic()-start,'native_pids':parents,'processes':list(selected.values()),'resident_tree_sum_bytes':sum(v['rss_bytes'] for v in selected.values())};rows.append(row);f.write(json.dumps(row)+'\n');f.flush()
  time.sleep(.2)
current=ps();d={'scope':'Read-only ps sampling during actual CUA GUI; no20cycle GUI memory claim or performance guarantee','samples':len(rows),'peak_tree_resident_sum_bytes':max((r['resident_tree_sum_bytes'] for r in rows),default=None),'native_peak_rss_bytes':max((v['rss_bytes'] for r in rows for v in r['processes'] if v['pid'] in r['native_pids']),default=None),'sampled_pids':sorted(seen),'sampled_survivors':[current[pid] for pid in seen if pid in current],'normal_stop_requested':a.stop.exists()};(a.evidence/'gui-resource-summary.json').write_text(json.dumps(d,indent=2)+'\n');print(json.dumps(d))
