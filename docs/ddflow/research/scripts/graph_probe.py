import ast, subprocess, sys, time, json
from pathlib import Path
from collections import defaultdict, deque
sys.path.insert(0, '.')
ROOT = Path('.')
def git(*a): return subprocess.run(['git','-C',str(ROOT),*a],capture_output=True,text=True).stdout
t0=time.perf_counter()
files=[f for f in git('ls-files','*.py').split('\n') if f and (f.startswith('ddflow/') or f.startswith('tests/'))]
def mod(p):
    parts=list(Path(p).with_suffix('').parts)
    if parts[-1]=='__init__': parts=parts[:-1]
    return '.'.join(parts)
known={mod(p):p for p in files}
bare={}  # tests/ dir is not a package? map stem
if not any(f=='tests/__init__.py' for f in files):
    for p in files:
        if p.startswith('tests/') and p.count('/')==1: bare[Path(p).stem]=p
def resolve(n):
    parts=n.split('.')
    for k in range(len(parts),0,-1):
        c='.'.join(parts[:k])
        if c in known: return known[c]
    return None
fwd=defaultdict(set); rev=defaultdict(set); dyn=defaultdict(list)
t1=time.perf_counter()
for p in files:
    try: tree=ast.parse((ROOT/p).read_text(errors='replace'))
    except SyntaxError: continue
    pkg=mod(p).split('.') if p.endswith('__init__.py') else mod(p).split('.')[:-1]
    for n in ast.walk(tree):
        names=[]
        if isinstance(n,ast.Import): names=[a.name for a in n.names]
        elif isinstance(n,ast.ImportFrom):
            base=n.module or ''
            if n.level: base='.'.join(pkg[:len(pkg)-(n.level-1)]+([n.module] if n.module else []))
            names=[base]+[f'{base}.{a.name}' for a in n.names]
        elif isinstance(n,ast.Call):
            f=n.func
            nm=getattr(f,'attr',getattr(f,'id',''))
            if nm in('import_module','__import__'): dyn[p].append(nm)
        for nm in names:
            q=resolve(nm) or bare.get(nm)
            if q and q!=p: fwd[p].add(q); rev[q].add(p)
t2=time.perf_counter()
edges=sum(len(v) for v in fwd.values())
tests=[f for f in files if f.startswith('tests/') and Path(f).name.startswith('test_')]
src=[f for f in files if f.startswith('ddflow/')]
print(f'files {len(files)} src {len(src)} tests {len(tests)} edges {edges}; list {t1-t0:.3f}s parse+graph {t2-t1:.2f}s; dynamic-import files {len(dyn)}')
import pickle; print('pickle bytes', len(pickle.dumps((dict(fwd),dict(rev)))))
def closure(starts, g=rev):
    seen=set(starts); q=deque(starts)
    while q:
        x=q.popleft()
        for y in g.get(x,()):
            if y not in seen: seen.add(y); q.append(y)
    return seen
tset=set(tests)
for hot in ['ddflow/core/records.py','ddflow/api/review.py']:
    c=closure([hot]); 
    print(hot,'direct importers',len(rev[hot]),'transitive modules',len([x for x in c if x.startswith('ddflow/')])-1,'tests',len(c&tset),'/',len(tset))
# in-degree distribution
deg=sorted(((len(v),k) for k,v in rev.items() if k.startswith('ddflow/')),reverse=True)
print('top hubs',deg[:8]); 
print('median transitive tests per src module:')
import statistics
cnts=[len(closure([s])&tset) for s in src if s in known.values()]
print(statistics.median(cnts), 'p90',sorted(cnts)[int(.9*len(cnts))],'mean',round(statistics.mean(cnts)), 'max',max(cnts))
# merges
merges=git('log','--first-parent','--merges','--format=%H','-400','main').split()
import random
random.seed(1)
sample=[]
for m in merges:
    names=git('diff','--name-only',m+'^1',m).split()
    ch=[f for f in names if f.endswith('.py')]
    chs=[f for f in ch if f in known.values() and f.startswith('ddflow/')]
    cht=[f for f in ch if f.startswith('tests/') and f in tset]
    if chs and cht and len(names)<60: sample.append((m,chs,cht))
    if len(sample)>=60: break
sample=random.sample(sample,min(10,len(sample)))
from ddflow.services import testselect as TS
listed=files+[f for f in git('ls-files').split('\n') if f]
res=[]
for m,chs,cht in sample:
    c=closure(chs)&tset
    # existing selector (HEAD graph, git/idents stubs)
    allchanged=chs+cht
    sel=TS.select(ROOT,'HEAD',max_files=0,hub_importers=100,max_hops=2,changed=allchanged,listed=sorted(set(listed)),idents={},membership=[],phrases={})
    s={t.path for t in sel.tests} if sel else set()
    # existing selector WITHOUT counting changed tests themselves (the fair test): changed only src
    sel2=TS.select(ROOT,'HEAD',max_files=0,hub_importers=100,max_hops=2,changed=chs,listed=sorted(set(listed)),idents={},membership=[],phrases={})
    s2={t.path for t in sel2.tests} if sel2 else set()
    full2=bool(sel2.full) if sel2 else None
    T=set(cht)
    r=lambda S: (len(T&S)/len(T), len(S))
    res.append((m[:8],len(chs),len(T),r(c),r(s2),full2))
    print(m[:8],'src',len(chs),'chg-tests',len(T),'| closure recall %.2f reach %d | existing(src-only) recall %.2f reach %d full=%s'%(r(c)+r(s2)+(full2,)))
n=max(len(res),1)
print('mean recall closure %.2f existing %.2f; mean reach closure %.0f existing %.0f'%(sum(x[3][0] for x in res)/n,sum(x[4][0] for x in res)/n,sum(x[3][1] for x in res)/n,sum(x[4][1] for x in res)/n))
