#!/usr/bin/env python3
"""Synthetic convergence demo for HAL-learn-onset (stdlib only, deterministic).

True model: a signal-hit (detector hit, gate failure, fabricated reference) happens
per 5K-token step with p=1% below 350K fill and ramps by +2 points per 50K above it
(cap 12%). The estimator does NOT know this. It sees only (fill bucket, hit?) samples
from lanes it rotates at its current operating threshold T.

Estimator: reference rate r0 pooled over fill < 150K. A 50K bucket is ELEVATED when
the Wilson 95% lower bound of its rate exceeds the Wilson upper bound of r0 and it
has >= MIN_N steps; the CLEAN test is the mirror. Onset = start of the first
elevated bucket whose successor is elevated or unobserved. Operating threshold =
onset - MARGIN. Lowering is automatic; raising is +STEP per epoch, only with
--allow-raise and only on clean exploration evidence.
"""
import argparse, math, random

BUCKET, STEP_TOK, MIN_N, MARGIN, STEP = 50_000, 5_000, 150, 50_000, 25_000
TRUE_ONSET = 350_000


def p_hit(fill):
    if fill < TRUE_ONSET:
        return 0.01
    return min(0.12, 0.01 + 0.02 * ((fill - TRUE_ONSET) // BUCKET + 1))


def wilson(k, n, z=1.96):
    if n == 0:
        return 0.0, 1.0
    ph = k / n
    d = 1 + z * z / n
    c = ph + z * z / (2 * n)
    m = z * math.sqrt(ph * (1 - ph) / n + z * z / (4 * n * n))
    return (c - m) / d, (c + m) / d


def run(start_T, eps, allow_raise, epochs=12, lanes=20, seed=7, label=""):
    rnd = random.Random(seed)
    T = start_T
    obs = {}  # bucket start -> [hits, n]
    print(f"\n== {label}: start T={start_T//1000}K, exploration share={eps:.0%}, raising={'on' if allow_raise else 'off'}")
    print("epoch  T(K)  est_onset(K)  steps_beyond_T  hits_beyond_T")
    for ep in range(1, epochs + 1):
        beyond = beyond_hits = 0
        for _ in range(lanes):
            limit = T + (100_000 if rnd.random() < eps else 0)  # explorers run 100K past T
            fill = 30_000
            while fill < limit:
                hit = rnd.random() < p_hit(fill)
                b = fill // BUCKET * BUCKET
                o = obs.setdefault(b, [0, 0]); o[0] += hit; o[1] += 1
                if fill >= T:
                    beyond += 1; beyond_hits += hit
                fill += STEP_TOK
        ref = [sum(v[0] for b, v in obs.items() if b < 150_000), sum(v[1] for b, v in obs.items() if b < 150_000)]
        r0_hi = wilson(*ref)[1]
        onset = None
        bs = sorted(b for b in obs if obs[b][1] >= MIN_N)
        for b in bs:
            if b < 150_000:
                continue
            lo = wilson(*obs[b])[0]
            nxt = obs.get(b + BUCKET)
            if lo > r0_hi and (nxt is None or nxt[1] < MIN_N or wilson(*nxt)[0] > r0_hi):
                onset = b
                break
        if onset is not None:
            T = min(T, max(100_000, onset - MARGIN))  # lowering only
        elif allow_raise:
            top = max((b for b in bs), default=None)
            if top is not None and top + BUCKET >= T and wilson(*obs[top])[1] <= r0_hi + 0.02:
                T = T + STEP
        print(f"{ep:5d}  {T//1000:4d}  {('%dK' % (onset//1000)) if onset else '-':>11}  {beyond:14d}  {beyond_hits:13d}")
    return T


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--epochs", type=int, default=12)
    a = ap.parse_args()
    run(600_000, 0.0, False, a.epochs, lanes=3, label="A: prior too high (advertised-ish), 3 lanes per epoch")
    run(200_000, 0.0, False, a.epochs, label="B: prior too low, no exploration (stuck, safe but wasteful)")
    run(200_000, 0.15, True, a.epochs, label="C: prior too low, 15% exploration + opt-in bounded raising")
