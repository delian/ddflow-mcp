# state_now.py: prints a wave/phase progress summary (done, running, blocked, top blockers) from the event log; read-only.
# Operator/coordinator helper kept for reference; run from the repo root.
# Reads the log through ddflow.api._base._load; read-only.
import json,re,functools,sys,collections
from pathlib import Path
from ddflow.api._base import _load
sys.setrecursionlimit(100000)
log,cfg,st=_load(Path("."),"coordinator")
I=st.items
def live(it): return it.state not in ("done","abandoned") and not getattr(it,"removed",False)
L={i:it for i,it in I.items() if live(it)}
tasks={i:it for i,it in L.items() if it.kind=="task"}
phases={i:it for i,it in I.items() if it.kind=="phase"}
def tag(it):
    for t in it.tags:
        m=re.fullmatch(r"wave:(\d+)",t)
        if m: return int(m.group(1))
@functools.lru_cache(None)
def depth(i,stack=()):
    if i in stack: return 0
    it=I.get(i)
    if it is None: return 0
    needs=list(it.needs)
    if it.kind=="task" and it.parent: needs+=list(I[it.parent].needs) if it.parent in I else []
    ds=[1+depth(n,stack+(i,)) for n in needs if n in L]
    return max(ds) if ds else 0
done_all=sum(1 for it in I.values() if it.kind=="task" and it.state=="done")
print("tasks done:",done_all,"| live:",len(tasks),"| running:",sum(t.state=="running" for t in tasks.values()),"| blocked:",sum(t.state=="blocked" for t in tasks.values()))
bd=collections.Counter(depth(i) for i in tasks); bt=collections.Counter(tag(t) for t in tasks.values())
dn=collections.Counter(tag(t) for t in I.values() if t.kind=="task" and t.state=="done")
rn=collections.Counter(depth(i) for i,t in tasks.items() if t.state=="running")
print("wave | live(true depth) | live(tag) | running | done(tag)")
for w in range(0,max(bd,default=0)+1): print(f"{w:>4} | {bd.get(w,0):>6} | {bt.get(w,0):>6} | {rn.get(w,0):>3} | {dn.get(w,0):>4}")
print("tag != depth:",sum(1 for i,t in tasks.items() if tag(t)!=depth(i)),"of",len(tasks))
ph=[]
for pid,p in phases.items():
    if not live(p): continue
    ts=[t for t in I.values() if t.kind=="task" and t.parent==pid]
    d=sum(t.state=="done" for t in ts); o=[depth(t.id) for t in ts if live(t)]
    ph.append((pid,d,len(ts),min(o) if o else None,max(o) if o else None,[n for n in p.needs if n in L]))
print("\nphases open:",len(ph),"| done phases:",sum(1 for p in phases.values() if p.state=='done'))
print("most progressed (done/total):")
for r in sorted([r for r in ph if r[2]],key=lambda r:-(r[1]/r[2]))[:12]: print(f"  {r[0]:<30} {r[1]:>3}/{r[2]:<3} waves {r[3]}-{r[4]}")
print("phases with all work ready (min wave 0) and none started:",sum(1 for r in ph if r[3]==0 and r[1]==0))
cnt=collections.Counter()
for t in tasks.values():
    for n in t.needs:
        if n in L: cnt[n]+=1
print("\ntop blockers:")
for n,c in cnt.most_common(12): print(f"  {c:>4} {n} [{L[n].state}] {L[n].title[:60]}")
print("ready (open, depth 0):",sum(1 for i,t in tasks.items() if t.state=="open" and depth(i)==0))
