import json,sys,collections
def score(r):
    m=r['m']; d=0.0; why=[]
    if m.get('hOverflow',0)>0 or m.get('offscreen'): d+=4; why.append('débordement')
    n=m.get('smallTargetCount',0)
    if n: d+=min(2,0.25*n); why.append(f'{n} cibles < {m.get("targetMin")}px')
    if m.get('iosZoomFields'): d+=1; why.append('champs < 16px')
    if m.get('truncated'): d+=1; why.append('libellés tronqués')
    if m.get('tinyText'): d+=0.5; why.append('texte < 11px')
    ax=[a for a in r.get('axe',[])]
    if ax:
        sev=sum(2 if a.startswith(('serious','critical')) else 0.5 for a in ax); d+=min(3,sev); why.append('axe: '+', '.join(ax)[:80])
    errs=[e for e in r.get('errs',[]) if '401' not in e]
    if errs: d+=1; why.append('erreur console')
    p=r.get('perf') or {}
    if p.get('frames') and p.get('long',0)>0.5*p['frames'] and r['engine']=='chromium': d+=1; why.append('saccades')
    return max(0,10-d),why
def main(files):
    rs=[]
    for f in files: rs+=json.load(open(f))
    by=collections.defaultdict(list)
    for r in rs:
        s,w=score(r); by[(r['engine'],r['vp'])].append((s,r['page'],r['scheme'],w))
    order=['d1920','d1440','d1280','t1024','t768','m430','m390','m360','m320']
    print('engine  format  mean  min   worst page')
    for eng in ['chromium','webkit']:
        for vp in order:
            l=by.get((eng,vp))
            if not l: continue
            mean=sum(x[0] for x in l)/len(l); worst=min(l)
            print(f'{eng:8}{vp:7} {mean:5.1f} {worst[0]:5.1f}  {worst[1]}/{worst[2][:1]} {"; ".join(worst[3])[:90]}')
if __name__=='__main__': main(sys.argv[1:])
