import collections, statistics as st, time, sys
sys.path.insert(0,'/home/user/arbitredge-v4')
from research_v4 import tier_a as T
S='/tmp/claude-0/-home-user/c28c4087-344e-5ae0-b211-28141ecdc32c/scratchpad'
out,res,a6rows,by,ev,nzs=T.run(S+'/ta/panel.json.gz')
ident=[r for r in res if r['identifiable']]
print('identifiable resistance events (all, incl. repeats):',len(ident),'| distinct game-markets:',len({(r['e'],r['m']) for r in ident}),
      '| events by book:',dict(collections.Counter(r['book'] for r in ident)))
# per game-market first event (as in the dev test)
first={}
for r in sorted(ident,key=lambda r:r['t_first']):
    first.setdefault((r['e'],r['m']),r)
reasons=collections.Counter(); kept=[]
now=max(t for (e,m),ts in by.items() for t in ts)
for k,r in first.items():
    e,m=k; ts=by[k]; times=sorted(ts); kick=ev[e]['kickoff']
    later=[x for x in times if x>r['t_first']]
    if not later: reasons['no confirming fetch after first deviation']+=1; continue
    t=later[0]; ct=T.close_time(ts,kick)
    if ct is None: reasons['no observed close (no fetch within 2h of kickoff)']+=1; continue
    if t>=ct: reasons['confirmation at/after close fetch']+=1; continue
    own=[b for b in T.OWNER if b in ts[t]]
    if not own: reasons['no owner-book quote at signal time']+=1; continue
    if not ev[e]['final']:
        why='kickoff after results cutoff' if kick>time.mktime((2026,11,9,0,0,0,0,0,0)) else ('CFB result unmatched (FCS/name)' if ev[e]['sport']=='cfb' else 'NFL result missing')
        reasons['no final: '+why]+=1; kept.append((k,False)); continue
    kept.append((k,True))
print('first-event game-markets:',len(first),'| dropped:',dict(reasons),'| with signal & owner quote:',len(kept),'| graded:',sum(g for _,g in kept))
games=collections.Counter(e for (e,m),_ in kept)
print('independence: game-markets',len(kept),'from distinct games',len(games),'| games contributing both spread & total:',sum(1 for v in games.values() if v>1))
# E: execution realism for A6 bets and S2 confirmations: quote age (time since that book's quote last changed) & survival to next fetch
def exec_stats(rows):
    ages=[];surv=collections.Counter()
    for (e,m,t,book,side,pt,pr) in rows:
        ts=by[(e,m)]; times=sorted(ts)
        hist=[x for x in times if x<=t and book in ts[x]]
        cur=ts[t][book]; age=0
        for x in reversed(hist):
            q=ts[x][book]
            if (q['pt'],q['pa'],q['pb'])!=(cur['pt'],cur['pa'],cur['pb']): break
            age=t-x
        ages.append(age/60)
        nxt=[x for x in times if x>t]
        if not nxt: surv['no next fetch']+=1; continue
        q=ts[nxt[0]].get(book)
        if not q: surv['book absent next fetch']+=1
        elif (q['pt'],q['pa'],q['pb'])==(cur['pt'],cur['pa'],cur['pb']): surv['same price next fetch']+=1
        else: surv['price changed by next fetch']+=1
    return ages,surv
rows=[]
for k,v in a6rows.items():
    if k[2]!=0.0: continue
    for r in v:
        ts=by[(r['e'],r['m'])]; dt=T.decision_time(ts,ev[r['e']]['kickoff']); rows.append((r['e'],r['m'],dt,r['book'],r['side'],r['point'],r['price']))
ages,surv=exec_stats(rows)
print('A6 executable bets: n',len(rows),'| price unchanged for (min) median %.0f, 90th %.0f'%(st.median(ages),sorted(ages)[int(.9*len(ages))]),'| next fetch:',dict(surv))
