"""Development-only: requalification at the next fetch (delayed execution) and signal overlap. RESEARCH ONLY."""
import collections, sys
from research_v4 import tier_a as T
P = sys.argv[1]
out, res, a6rows, by, ev, nzs = T.run(P)
LIVE0 = 1793058000
WIN = ((18 * 3600, 30 * 3600), (4 * 3600, 8 * 3600), (45 * 60, 90 * 60))

def gap(snap):
    p, r = T.mu_pin(snap), T.ref_mu(snap, books=T.REC)
    return None if p is None or r is None else p - r

def l1(snap, m, nz):
    c = [T.best_owner(snap, m, s, nz) for s in T.SIDES[m]]
    c = [(s, x) for s, x in zip(T.SIDES[m], c) if x]
    return max(c, key=lambda z: z[1]['ev']) if c else None

inst = collections.defaultdict(set); rq = collections.Counter(); tot = collections.Counter()
for (e, m), ts in by.items():
    v = ev[e]; kick = v['kickoff']
    if kick < LIVE0:
        continue
    times = sorted(t for t in ts if t < kick); nz = nzs[(v['sport'], m)]
    for lo, hi in WIN:
        c = [t for t in times if lo <= kick - t <= hi]
        if not c:
            continue
        t = c[0]; nxt = [x for x in times if x > t]
        g = gap(ts[t])
        if g is not None and abs(g) >= 0.5 and 'P1' not in {x[0] for x in inst[(e, m)]}:
            inst[(e, m)].add(('P1', t)); tot['P1'] += 1
            g2 = gap(ts[nxt[0]]) if nxt else None
            if g2 is not None and abs(g2) >= 0.5 and g2 * g > 0: rq['P1'] += 1
        b = l1(ts[t], m, nz)
        if b and b[1]['ev'] >= 0.01 and 'L1' not in {x[0] for x in inst[(e, m)]}:
            inst[(e, m)].add(('L1', t)); tot['L1'] += 1
            b2 = l1(ts[nxt[0]], m, nz) if nxt else None
            if b2 and b2[1]['ev'] >= 0.01 and b2[0] == b[0]: rq['L1'] += 1
seen = set()
for r in sorted(res, key=lambda r: r['t_first']):
    if r['book'] != 'pinnacle' or not r['identifiable'] or ev[r['e']]['kickoff'] < LIVE0 or (r['e'], r['m']) in seen:
        continue
    ts = by[(r['e'], r['m'])]; times = sorted(x for x in ts if x < ev[r['e']]['kickoff'])
    conf = [x for x in times if x > r['t_first']]                 # confirmation fetch = 2nd fetch of the run
    if not conf:
        continue
    seen.add((r['e'], r['m'])); inst[(r['e'], r['m'])].add(('P4', conf[0])); tot['P4'] += 1
    nxt = [x for x in times if x > conf[0]]
    if nxt and 'pinnacle' in ts[nxt[0]]:
        ref = T.ref_mu(ts[nxt[0]], exclude='pinnacle'); d = ts[nxt[0]]['pinnacle']['mu'] - ref if ref is not None else 0
        if abs(d) >= 0.5 and d * r['dev_mu'] > 0: rq['P4'] += 1
print('requalify at next fetch (delayed execution):', {k: '%d/%d = %.0f%%' % (rq[k], tot[k], 100 * rq[k] / tot[k]) for k in tot})
combo = collections.Counter(tuple(sorted({x[0] for x in s})) for s in inst.values() if s)
print('overlap of signals on the same game-market:', dict(combo))
wk = 2.15
per = collections.Counter()
for (e, m), s in inst.items():
    for sig, _ in s:
        per[(sig, ev[e]['sport'], m)] += 1
for k in sorted(per):
    print('%s %s %s: %.1f/wk' % (k[0], k[1], k[2], per[k] / wk))
