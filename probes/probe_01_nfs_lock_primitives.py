"""Tier-0/1 probe: which concurrency primitives actually survive on THIS box's NFS?"""

import fcntl
import multiprocessing as mp
import os
import sqlite3
import sys
import time

N_PROC, N_INC = 12, 40
_B = None


def _init(b):
    # A multiprocessing.Barrier cannot be pickled through Pool.map, so it is passed via
    # the pool initializer and held module-level in each worker. This is the documented
    # way to share a synchronisation primitive with a Pool.
    global _B  # noqa: PLW0603
    _B = b


def sqlite_worker(db, mode):
    _B.wait()
    ok = err = 0
    first = ""
    for _ in range(N_INC):
        try:
            c = sqlite3.connect(db, timeout=20.0, isolation_level=None)
            c.execute(f"pragma journal_mode={mode}")
            c.execute("pragma busy_timeout=20000")
            c.execute("pragma synchronous=NORMAL")
            c.execute("BEGIN IMMEDIATE")
            v = c.execute("select v from ctr where id=1").fetchone()[0]
            c.execute("update ctr set v=? where id=1", (v + 1,))
            c.execute("COMMIT")
            c.close()
            ok += 1
        except Exception as e:
            err += 1
            first = first or f"{type(e).__name__}: {e}"
    return ok, err, first


def run_sqlite(root, mode):
    db = os.path.join(root, f"probe-{mode}.db")
    for suf in ("", "-wal", "-shm", "-journal"):
        try:
            os.unlink(db + suf)
        except FileNotFoundError:
            pass
    c = sqlite3.connect(db)
    c.execute(f"pragma journal_mode={mode}")
    c.execute("create table ctr(id int primary key, v int)")
    c.execute("insert into ctr values(1,0)")
    c.commit()
    c.close()
    b = mp.Barrier(N_PROC)
    with mp.Pool(N_PROC, initializer=_init, initargs=(b,)) as p:
        res = p.starmap(sqlite_worker, [(db, mode)] * N_PROC)
    final = sqlite3.connect(db).execute("select v from ctr where id=1").fetchone()[0]
    ok = sum(r[0] for r in res)
    err = sum(r[1] for r in res)
    firsts = {r[2] for r in res if r[2]}
    print(
        f"  sqlite[{mode:8s}] committed={ok:4d} errors={err:3d} counter={final:4d} LOST={ok - final}"
        + (f"\n      err: {sorted(firsts)[0][:110]}" if firsts else "")
    )


def flock_worker(path):
    _B.wait()
    ok = err = 0
    first = ""
    for _ in range(N_INC):
        try:
            with open(path + ".lock", "w") as f:
                fcntl.flock(f, fcntl.LOCK_EX)
                v = int(open(path).read() or 0)
                time.sleep(0.0008)
                open(path, "w").write(str(v + 1))
                fcntl.flock(f, fcntl.LOCK_UN)
            ok += 1
        except Exception as e:
            err += 1
            first = first or f"{type(e).__name__}: {e}"
    return ok, err, first


def run_flock(root):
    path = os.path.join(root, "flock-ctr.txt")
    open(path, "w").write("0")
    b = mp.Barrier(N_PROC)
    with mp.Pool(N_PROC, initializer=_init, initargs=(b,)) as p:
        res = p.map(flock_worker, [path] * N_PROC)
    final = int(open(path).read())
    ok = sum(r[0] for r in res)
    err = sum(r[1] for r in res)
    firsts = {r[2] for r in res if r[2]}
    print(
        f"  flock              acquired={ok:4d} errors={err:3d} counter={final:4d} LOST={ok - final}"
        + (f"\n      err: {sorted(firsts)[0][:110]}" if firsts else "")
    )


def excl_worker(args):
    root, kind = args
    _B.wait()
    won = 0
    for i in range(N_INC):
        target = os.path.join(root, f"{kind}-{i}.lck")
        try:
            if kind == "oexcl":
                os.close(os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644))
                won += 1
            else:
                tmp = os.path.join(root, f".t-{os.getpid()}-{i}")
                os.close(os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644))
                try:
                    os.link(tmp, target)
                except FileExistsError:
                    pass
                if os.stat(tmp).st_nlink == 2:
                    won += 1  # authoritative on NFS
                os.unlink(tmp)
        except FileExistsError:
            pass
    return won


def run_excl(root, kind):
    b = mp.Barrier(N_PROC)
    with mp.Pool(N_PROC, initializer=_init, initargs=(b,)) as p:
        total = sum(p.map(excl_worker, [(root, kind)] * N_PROC))
    print(
        f"  {kind:18s} winners={total:4d} (must be EXACTLY {N_INC}) -> {'OK' if total == N_INC else 'BROKEN'}"
    )


if __name__ == "__main__":
    for label, root in (("NFS (repo)", sys.argv[1]), ("ext (/tmp)", sys.argv[2])):
        os.makedirs(root, exist_ok=True)
        print(f"\n=== {label}: {root} ===")
        t0 = time.time()
        run_sqlite(root, "wal")
        run_sqlite(root, "truncate")
        run_flock(root)
        run_excl(root, "oexcl")
        run_excl(root, "link")
        print(f"  (arm wall-clock {time.time() - t0:.1f}s)")
