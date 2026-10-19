# restamp_api.py: re-stamps each live task's wave:N tag to its true dependency depth via ddflow update (coordinator one-off).
# Operator/coordinator helper kept for reference; run from the repo root.
# GR-wave-derived retires restamp_api.py.
import re,functools,sys,subprocess
from pathlib import Path
from ddflow.api._base import _load
sys.setrecursionlimit(100000)
log,cfg,st=_load(Path("."),"coordinator")
I=st.items
def live(it): return it.state not in ("done","abandoned") and not getattr(it,"removed",False)
L={i:it for i,it in I.items() if live(it)}
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
    if it.kind=="task" and it.parent and it.parent in I: needs+=list(I[it.parent].needs)
    ds=[1+depth(n,stack+(i,)) for n in needs if n in L]
    return max(ds) if ds else 0
n=f=0
for i,it in L.items():
    if it.kind!="task": continue
    d=depth(i); old=tag(it)
    if old==d: continue
    a=["ddflow","--agent","coordinator","update",i,"--add-tags",f"wave:{d}"]
    if old is not None: a+=["--remove-tags",f"wave:{old}"]
    r=subprocess.run(a,capture_output=True,text=True); n+=1
    if r.returncode: f+=1; print("FAIL",i,r.stderr[:80],flush=True)
print("restamped",n,"failed",f,flush=True)
