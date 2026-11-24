"""Project geoBoundaries ZWE ADM2 districts into the portal map space and simplify them.
Same projection as the province map (600 x 552). Output: [{name, d, cx, cy}]"""
import json, sys
from shapely.geometry import shape, mapping
from shapely.ops import transform
import warnings;warnings.filterwarnings('ignore')
K=81.05493754657579
def proj(x,y,z=None): return ((x-25.237597177423254)*.9454384408440947*K,(-15.60714602564883-y)*K)
src=json.load(open(sys.argv[1])); out=[]
for f in src['features']:
    g=transform(proj,shape(f['geometry'])).simplify(0.35,preserve_topology=True)
    polys=[g] if g.geom_type=='Polygon' else list(g.geoms)
    d=''
    for p in polys:
        if p.area<0.5: continue
        for ring in [p.exterior,*p.interiors]:  # keep holes (urban councils inside rural districts); drawn with evenodd
            pts=list(ring.coords)
            d+='M'+'L'.join(f'{x:.1f},{y:.1f}' for x,y in pts[:-1])+'Z'
    c=g.representative_point()
    out.append({'name':f['properties']['shapeName'],'d':d,'cx':round(c.x,1),'cy':round(c.y,1)})
out.sort(key=lambda r:r['name'])
json.dump(out,open(sys.argv[2],'w'),separators=(',',':'))
print(len(out),'districts', sum(len(r['d']) for r in out),'chars')
