import collections, sys, math
from statistics import NormalDist as N
sys.path.insert(0,'/home/user/arbitredge-v4')
from research_v4 import tier_a as T
S='/tmp/claude-0/-home-user/c28c4087-344e-5ae0-b211-28141ecdc32c/scratchpad'
out,res,a6rows,by,ev,nzs=T.run(S+'/ta/panel.json.gz')
LIVE0=1793058000  # 2026-10-26T23:40Z first live fetch
live_t=[t for k,ts in by.items() for t in ts if t>=LIVE0]; weeks=(max(live_t)-LIVE0)/604800
print('live period weeks %.2f'%weeks)
r1=collections.Counter(); r2=collections.Counter(); r3=collections.Counter()
for (e,m),ts in by.items():
    v=ev[e]; kick=v['kickoff']
    if kick<LIVE0: continue
    key=(v['sport'],m)
    # S1: any decision window with |pin - recmed| >= 0.5
    for lo,hi in ((18*3600,30*3600),(4*3600,8*3600),(45*60,90*60)):
        c=[t for t in sorted(ts) if lo<=kick-t<=hi]
        if c:
            p,rm=T.mu_pin(ts[c[0]]),T.ref_mu(ts[c[0]],books=T.REC)
            if p is not None and rm is not None and abs(p-rm)>=0.5: r1[key]+=1; break
seen=set()
for r in res:
    if r['identifiable'] and ev[r['e']]['kickoff']>=LIVE0 and (r['e'],r['m']) not in seen:
        seen.add((r['e'],r['m'])); r2[(ev[r['e']]['sport'],r['m'])]+=1
for k,v in a6rows.items():
    if k[2]==0.01:
        for r in v:
            if ev[r['e']]['kickoff']>=LIVE0: r3[(r['sport'],r['m'])]+=1
k_pos=18; k_ls=4
for name,c,k in (('S1 gap',r1,k_pos),('S2 maker-active',r2,k_pos),('S3 EV>=1%',r3,k_ls)):
    za=N().inv_cdf(1-0.05/k); zb=N().inv_cdf(.8)
    n1=math.ceil(((za+zb)*0.022/0.01)**2)
    print(name,'tests in family',k,'n for +1%% CLV per test: %d'%n1)
    for key in sorted(set(list(c))|{('nfl','spreads'),('nfl','totals'),('cfb','spreads'),('cfb','totals')}):
        rate=c[key]/weeks; print('   %s %s: %.1f/wk -> weeks to n: %s'%(key[0],key[1],rate,'%.0f'%(n1/rate) if rate else 'never'))
