"""Window A click order at S0=100, v0=100, T=20: contracts with |edge| > 1.105 (half-spread + 0.10 buffer),
sorted by edge; L1-only $ (200 lots each) and residual delta / vega of that L1 basket (at the new fair)."""
import math
def N(x): return 0.5*(1+math.erf(x/math.sqrt(2)))
def n(x): return math.exp(-x*x/2)/math.sqrt(2*math.pi)
def bs(k,S,K,d,v):
    T=d/252; w=v*math.sqrt(T); d1=(math.log(S/K)+w*w/2)/w
    return S*N(d1)-K*N(d1-w) if k=='C' else K*N(w-d1)-S*N(-d1)
def g(k,S,K,d,v):
    T=d/252; w=v*math.sqrt(T); d1=(math.log(S/K)+w*w/2)/w
    return (N(d1) if k=='C' else N(d1)-1), S*n(d1)*math.sqrt(T)/100
CON=[('C',60),('C',100),('C',140),('P',60),('P',100),('P',140)]
print("| dS | dVol | Click order (gross edge per contract) | L1 $ | L1-3 $ | L1 residual delta / vega |")
print("|---|---|---|---|---|---|")
for dS in (-10,-5,-2,0,2,5,10):
    for dv in (-20,0,20):
        if dS==0 and dv==0: continue
        S1=100*(1+dS/100); v1=1+dv/100
        rows=[]
        for k,K in CON:
            e=bs(k,S1,K,20,v1)-bs(k,100,K,20,1.0)
            if abs(e)>1.105: rows.append((abs(e),k,K,e))
        rows.sort(reverse=True)
        L1=sum(200*(a-1.005) for a,_,_,_ in rows)
        L3=sum(200*(a-1.005-j) for a,_,_,_ in rows for j in range(3) if a-1.005-j>0)
        nd=nv=0
        for a,k,K,e in rows:
            s=1 if e>0 else -1; d,vg=g(k,S1,K,20,v1); nd+=s*200*d; nv+=s*200*vg
        txt=", ".join("%s %d%s %.2f"%("B" if e>0 else "S",K,k,a) for a,k,K,e in rows) or "nothing"
        print("| %+d%% | %+d | %s | %.0f | %.0f | %+.0f / %+.1f |"%(dS,dv,txt,L1,L3,nd,nv))
