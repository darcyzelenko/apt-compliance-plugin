"""
Victorian Apartment Compliance Engine  v0.4
Apartment Design Guidelines for Victoria (2017)

Layer convention
───────────────
Rooms (closed LWPOLYLINE):
  APT_ROOM_MAINBED       main bedroom
  APT_ROOM_BED1..4       secondary bedrooms
  APT_ROOM_LIVING        open-plan living/dining/kitchen
  APT_ROOM_BATHROOM      bathroom
  APT_ROOM_ENSUITE       ensuite
  APT_ROOM_LAUNDRY       laundry
  APT_ROOM_ENTRY         entry/hallway
  APT_ROOM_SNORKEL_BED1  snorkel secondary area for BED1 (etc.)
  APT_ROOM_SNORKEL_MAINBED

Storage (closed LWPOLYLINE):
  APT_STORAGE_DESIGNATED wardrobe, linen, general
  APT_STORAGE_SERVICE    kitchen/bathroom cabinetry

Private open space (closed LWPOLYLINE):
  APT_POS                balcony (default)
  APT_POS_GROUND         ground-level courtyard (≥25m², 3m dim)
  APT_POS_PODIUM         podium/base (≥15m², 3m dim)
  APT_POS_ROOF           rooftop (≥10m², 2m dim)

Windows (LINE, two endpoints = window width at wall face):
  APT_WINDOW_MAINBED     window(s) for main bedroom
  APT_WINDOW_BED1..4     windows for secondary bedrooms
  APT_WINDOW_LIVING      windows for living area
  APT_WINDOW_BATHROOM    etc.

Doors (LINE, two endpoints = clear opening width in wall):
  APT_DOOR_ENTRY         entry door to apartment
  APT_DOOR_MAINBED       door to main bedroom
  APT_DOOR_BED1..4       doors to secondary bedrooms
  APT_DOOR_BATHROOM      door to bathroom
  APT_DOOR_ENSUITE       door to ensuite
  APT_DOOR_LIVING        door between entry/corridor and living
  APT_DOOR_POS           door from living to balcony/POS

Metadata lines:
  APT_NORTH              line whose direction = north vector
  APT_NOISE_ROAD         line from road noise source to building edge
  APT_NOISE_INDUSTRY     line from industrial zone boundary
  APT_NOISE_RAIL_PASSENGER
  APT_NOISE_RAIL_FREIGHT_METRO
  APT_NOISE_RAIL_FREIGHT_REGIONAL
"""

import math
from typing import List, Dict, Tuple, Optional, Any


# ═══════════════════════════════════════════════════════════════════════════════
# GEOMETRY PRIMITIVES
# ═══════════════════════════════════════════════════════════════════════════════

Pt = Tuple[float, float]

def poly_area(pts: List[Pt]) -> float:
    n = len(pts)
    if n < 3: return 0.0
    a = 0.0
    for i in range(n):
        j = (i+1) % n
        a += pts[i][0]*pts[j][1] - pts[j][0]*pts[i][1]
    return abs(a) / 2.0

def poly_centroid(pts: List[Pt]) -> Pt:
    n = len(pts); cx = cy = a = 0.0
    for i in range(n):
        j = (i+1) % n
        c = pts[i][0]*pts[j][1] - pts[j][0]*pts[i][1]
        cx += (pts[i][0]+pts[j][0])*c
        cy += (pts[i][1]+pts[j][1])*c
        a  += c
    a /= 2.0
    if abs(a) < 1e-9: return pts[0]
    return (cx/(6*a), cy/(6*a))

def poly_bbox(pts: List[Pt]) -> Tuple[float,float,float,float]:
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)

def seg_len(a: Pt, b: Pt) -> float:
    return math.hypot(b[0]-a[0], b[1]-a[1])

def midpoint(a: Pt, b: Pt) -> Pt:
    return ((a[0]+b[0])/2, (a[1]+b[1])/2)

def pt_in_poly(px: float, py: float, pts: List[Pt]) -> bool:
    inside = False; j = len(pts)-1
    for i in range(len(pts)):
        xi,yi = pts[i]; xj,yj = pts[j]
        if ((yi>py) != (yj>py)) and (px < (xj-xi)*(py-yi)/(yj-yi+1e-12)+xi):
            inside = not inside
        j = i
    return inside

def pt_seg_dist(px: float, py: float, a: Pt, b: Pt) -> float:
    dx=b[0]-a[0]; dy=b[1]-a[1]
    if dx==0 and dy==0: return math.hypot(px-a[0], py-a[1])
    t = max(0, min(1, ((px-a[0])*dx+(py-a[1])*dy)/(dx*dx+dy*dy)))
    return math.hypot(px-a[0]-t*dx, py-a[1]-t*dy)

def convex_hull(pts: List[Pt]) -> List[Pt]:
    pts = sorted(set(pts))
    if len(pts) < 3: return pts
    def cross(o,a,b): return (a[0]-o[0])*(b[1]-o[1])-(a[1]-o[1])*(b[0]-o[0])
    lo = []
    for p in pts:
        while len(lo)>=2 and cross(lo[-2],lo[-1],p)<=0: lo.pop()
        lo.append(p)
    hi = []
    for p in reversed(pts):
        while len(hi)>=2 and cross(hi[-2],hi[-1],p)<=0: hi.pop()
        hi.append(p)
    return lo[:-1]+hi[:-1]

def min_bounding_rect(pts: List[Pt]) -> Tuple[float,float]:
    hull = convex_hull(pts)
    if len(hull) < 3:
        a,b,c,d = poly_bbox(pts); return abs(c-a), abs(d-b)
    best = float('inf'); bw = bh = 0.0
    n = len(hull)
    for i in range(n):
        dx=hull[(i+1)%n][0]-hull[i][0]; dy=hull[(i+1)%n][1]-hull[i][1]
        l=math.hypot(dx,dy)
        if l<1e-9: continue
        ux,uy=dx/l,dy/l; vx,vy=-uy,ux
        pu=[p[0]*ux+p[1]*uy for p in hull]; pv=[p[0]*vx+p[1]*vy for p in hull]
        w=max(pu)-min(pu); h=max(pv)-min(pv)
        if w*h < best: best=w*h; bw,bh=w,h
    return bw, bh

def seg_near_seg(p1,p2,q1,q2,tol=0.15) -> bool:
    """True if q1,q2 are collinear with and close to the line through p1,p2."""
    def dpl(pt,a,b):
        dx=b[0]-a[0]; dy=b[1]-a[1]; l=math.hypot(dx,dy)
        if l<1e-9: return math.hypot(pt[0]-a[0],pt[1]-a[1])
        return abs((pt[1]-a[1])*dx-(pt[0]-a[0])*dy)/l
    return dpl(q1,p1,p2)<tol and dpl(q2,p1,p2)<tol

def overlap_len(p1,p2,q1,q2) -> float:
    dx=p2[0]-p1[0]; dy=p2[1]-p1[1]; l=math.hypot(dx,dy)
    if l<1e-9: return 0.0
    ux,uy=dx/l,dy/l
    def pr(p): return (p[0]-p1[0])*ux+(p[1]-p1[1])*uy
    a1,a2=sorted([pr(p1),pr(p2)]); b1,b2=sorted([pr(q1),pr(q2)])
    return max(0.0, min(a2,b2)-max(a1,b1))

def shared_boundary(polyA: List[Pt], polyB: List[Pt], tol=0.18) -> float:
    """Longest shared edge segment between two room polygons."""
    best = 0.0
    for i in range(len(polyA)):
        p1=polyA[i]; p2=polyA[(i+1)%len(polyA)]
        for j in range(len(polyB)):
            q1=polyB[j]; q2=polyB[(j+1)%len(polyB)]
            if seg_near_seg(p1,p2,q1,q2,tol):
                best = max(best, overlap_len(p1,p2,q1,q2))
    return best

def poly_min_width(pts: List[Pt]) -> float:
    w,h = min_bounding_rect(pts)
    return min(w,h)

def poly_max_depth(pts: List[Pt]) -> float:
    w,h = min_bounding_rect(pts)
    return max(w,h)

def max_depth_from_windows(poly: List[Pt], win_lines: List) -> Optional[float]:
    """Grid-sample interior points; return max distance to nearest window line."""
    if not win_lines: return None
    mnx,mny,mxx,mxy = poly_bbox(poly)
    step = max(0.05, min(mxx-mnx, mxy-mny)/24)
    max_d = 0.0; found = False
    x = mnx+step/2
    while x < mxx:
        y = mny+step/2
        while y < mxy:
            if pt_in_poly(x,y,poly):
                found = True
                d = min(pt_seg_dist(x,y,wl[0],wl[1]) for wl in win_lines if len(wl)>=2)
                max_d = max(max_d, d)
            y += step
        x += step
    return round(max_d,2) if found else None

def window_normal_angle(wl, north=(0.0,1.0)) -> float:
    """Compass angle (0=N, 90=E, 180=S, 270=W) of window outward normal."""
    dx=wl[1][0]-wl[0][0]; dy=wl[1][1]-wl[0][1]
    nx,ny = -dy,dx
    l=math.hypot(nx,ny)
    if l<1e-9: return 0.0
    nx/=l; ny/=l
    return math.degrees(math.atan2(nx,ny)) % 360

def window_aspect(wl, north=(0.0,1.0)) -> str:
    ang = window_normal_angle(wl, north)
    sectors = ['N','NE','E','SE','S','SW','W','NW']
    return sectors[int((ang+22.5)/45) % 8]

def windows_differ_aspect(w1,w2, north=(0.0,1.0)) -> bool:
    a1=window_normal_angle(w1,north); a2=window_normal_angle(w2,north)
    diff=abs(a1-a2)%360
    if diff>180: diff=360-diff
    return diff >= 60


# ═══════════════════════════════════════════════════════════════════════════════
# SOLAR GEOMETRY  (Melbourne lat −37.8°, June 21)
# ═══════════════════════════════════════════════════════════════════════════════

LAT = math.radians(-37.8)
DEC = math.radians(-23.45)

def solar_pos(hour):
    ha = math.radians((hour-12)*15)
    sin_alt = math.sin(LAT)*math.sin(DEC)+math.cos(LAT)*math.cos(DEC)*math.cos(ha)
    alt = math.asin(max(-1,min(1,sin_alt)))
    if alt<=0: return None,None
    cos_az=(math.sin(DEC)-math.sin(LAT)*math.sin(alt))/(math.cos(LAT)*math.cos(alt)+1e-12)
    az=math.acos(max(-1,min(1,cos_az)))
    if ha>0: az=2*math.pi-az
    return alt, az

def sun_hours_june21(win_lines, north=(0.0,1.0), step_min=30):
    if not win_lines: return 0.0
    total=0.0; step=step_min/60
    h=9.0
    while h<=15.0:
        alt,az=solar_pos(h)
        if alt is not None:
            sdx=math.sin(az); sdy=math.cos(az)
            for wl in win_lines:
                dx=wl[1][0]-wl[0][0]; dy=wl[1][1]-wl[0][1]
                for nx,ny in [(-dy,dx),(dy,-dx)]:
                    l=math.hypot(nx,ny)
                    if l<1e-9: continue
                    if (nx/l)*sdx+(ny/l)*sdy > 0.1:
                        total+=step; break
                else: continue
                break
        h+=step
    return round(total,2)


# ═══════════════════════════════════════════════════════════════════════════════
# DXF PARSER
# ═══════════════════════════════════════════════════════════════════════════════

def parse_dxf(text: str):
    """
    Returns:
      layers  – {LAYER: [ [(x,y),...] ]}   room/storage/pos polygons
      windows – {ROOM_KEY: [ [(x,y),(x,y)] ]}  window line segments
      doors   – {DOOR_KEY: { width, p1, p2, midpoint, angle }}
      north   – (nx,ny) unit vector
    """
    layers: Dict[str,List] = {}
    windows: Dict[str,List] = {}
    doors:   Dict[str,List] = {}
    north_pts = []

    lines = text.replace('\r\n','\n').replace('\r','\n').split('\n')
    lines = [l.strip() for l in lines]

    i = 0
    while i < len(lines):
        if lines[i]=='0' and i+1<len(lines):
            etype = lines[i+1].strip().upper()
            if etype in ('LWPOLYLINE','POLYLINE','LINE'):
                layer=None; verts=[]; x=x2=None
                j=i+2
                while j<len(lines):
                    code=lines[j].strip()
                    if j+1>=len(lines): break
                    val=lines[j+1].strip()
                    if code=='0': break
                    if code=='8':  layer=val.upper()
                    if code=='10':
                        try: x=float(val)
                        except: pass
                    if code=='20':
                        try:
                            y=float(val)
                            if x is not None: verts.append((x,y)); x=None
                        except: pass
                    if code=='11':
                        try: x2=float(val)
                        except: pass
                    if code=='21':
                        try:
                            y2=float(val)
                            if x2 is not None: verts.append((x2,y2)); x2=None
                        except: pass
                    j+=2

                if layer and verts:
                    if layer.startswith('APT_WINDOW_') and len(verts)>=2:
                        rk=layer[len('APT_WINDOW_'):]
                        windows.setdefault(rk,[]).append(verts[:2])

                    elif layer.startswith('APT_DOOR_') and len(verts)>=2:
                        dk=layer[len('APT_DOOR_'):]
                        p1,p2=verts[0],verts[1]
                        w=seg_len(p1,p2)
                        dx=p2[0]-p1[0]; dy=p2[1]-p1[1]
                        ang=math.degrees(math.atan2(dy,dx))%360
                        doors.setdefault(dk,[]).append({
                            'width':round(w,3), 'p1':p1, 'p2':p2,
                            'midpoint':midpoint(p1,p2), 'angle':ang
                        })

                    elif layer=='APT_NORTH':
                        north_pts.extend(verts)

                    elif layer.startswith('APT_') and len(verts)>=2:
                        layers.setdefault(layer,[]).append(verts)

                i=j; continue
        i+=1

    # North vector
    north=(0.0,1.0)
    if len(north_pts)>=2:
        dx=north_pts[-1][0]-north_pts[0][0]; dy=north_pts[-1][1]-north_pts[0][1]
        l=math.hypot(dx,dy)
        if l>1e-9: north=(dx/l,dy/l)

    # Combined wet rooms: if laundry overlaps/is inside bathroom or ensuite,
    # merge it into that layer for compliance purposes
    for wet in ['APT_ROOM_BATHROOM','APT_ROOM_ENSUITE']:
        if wet in layers and 'APT_ROOM_LAUNDRY' in layers:
            for lp in layers['APT_ROOM_LAUNDRY']:
                lc = poly_centroid(lp)
                for wp in layers[wet]:
                    if point_in_polygon(lc[0],lc[1],wp):
                        # Laundry is inside this wet room — merge areas
                        # Just flag it; the engine handles them separately anyway
                        layers.setdefault('APT_ROOM_LAUNDRY_IN_'+wet.replace('APT_ROOM_',''),[]).append(lp)
                        break

    # APT_DOOR_ENTRY_MARKER: if entry is drawn as a line (not polygon), 
    # treat as entry point flag rather than room
    if 'APT_DOOR_ENTRY_MARKER' in layers:
        # Entry marker = just a line indicating front door location
        # Create a small virtual entry polygon around it for adjacency purposes
        for line in layers.get('APT_DOOR_ENTRY_MARKER',[]):
            if len(line)>=2:
                mx=(line[0][0]+line[1][0])/2; my=(line[0][1]+line[1][1])/2
                s=0.3 # small 300mm virtual entry zone
                layers.setdefault('APT_ROOM_ENTRY',[]).append([[mx-s,my-s],[mx+s,my-s],[mx+s,my+s],[mx-s,my+s]])

    return layers, windows, doors, north


def auto_scale(layers, windows, doors, north):
    """Detect mm/cm and scale all geometry to metres."""
    all_pts=[p for polys in layers.values() for poly in polys for p in poly]
    if not all_pts: return layers,windows,doors,north,1.0
    span=max(p[0] for p in all_pts)-min(p[0] for p in all_pts)
    sc=0.001 if span>1000 else (0.01 if span>100 else 1.0)
    if sc==1.0: return layers,windows,doors,north,1.0
    def scp(polys): return [[(x*sc,y*sc) for x,y in p] for p in polys]
    def scw(segs):  return [[(x*sc,y*sc) for x,y in s] for s in segs]
    def scd(ds): return [{**d,'width':d['width']*sc,
                          'p1':(d['p1'][0]*sc,d['p1'][1]*sc),
                          'p2':(d['p2'][0]*sc,d['p2'][1]*sc),
                          'midpoint':(d['midpoint'][0]*sc,d['midpoint'][1]*sc)}
                         for d in ds]
    sl={k:scp(v) for k,v in layers.items()}
    sw={k:scw(v) for k,v in windows.items()}
    sd={k:scd(v) for k,v in doors.items()}
    return sl,sw,sd,north,sc


# ═══════════════════════════════════════════════════════════════════════════════
# JURISDICTION RULE SETS
# jurisdiction: 'VIC' | 'NSW' | 'BEST_PRACTICE'
# ═══════════════════════════════════════════════════════════════════════════════

RULES = {
    'VIC': {
        'name': 'Victoria — Apartment Design Guidelines 2017',
        'ref':  'ADG Vic 2017 · Clause 55 & 58',
        'bedroom': {
            'MAINBED':{'width':3.0,'depth':3.4,'area':None,'robe':1.8,'label':'Main Bedroom'},
            'BED1':   {'width':3.0,'depth':3.0,'area':None,'robe':1.5,'label':'Bedroom 1'},
            'BED2':   {'width':3.0,'depth':3.0,'area':None,'robe':1.5,'label':'Bedroom 2'},
            'BED3':   {'width':3.0,'depth':3.0,'area':None,'robe':1.5,'label':'Bedroom 3'},
            'BED4':   {'width':3.0,'depth':3.0,'area':None,'robe':1.5,'label':'Bedroom 4'},
        },
        'storage': {
            0:{'total':8, 'internal':5,  'internal_pct':None,'label':'Studio'},
            1:{'total':10,'internal':6,  'internal_pct':None,'label':'1 Bedroom'},
            2:{'total':14,'internal':9,  'internal_pct':None,'label':'2 Bedroom'},
            3:{'total':18,'internal':12, 'internal_pct':None,'label':'3+ Bedroom'},
        },
        'living': {
            1:{'width':3.3,'area':10,'label':'Studio/1-bed'},
            2:{'width':3.6,'area':12,'label':'2+ bed'},
        },
        'pos': {
            'APT_POS':       {1:{'a':8,'d':1.8},2:{'a':8,'d':2.0},3:{'a':12,'d':2.4}},
            'APT_POS_GROUND':{1:{'a':25,'d':3.0},2:{'a':25,'d':3.0},3:{'a':25,'d':3.0}},
            'APT_POS_PODIUM':{1:{'a':15,'d':3.0},2:{'a':15,'d':3.0},3:{'a':15,'d':3.0}},
            'APT_POS_ROOF':  {1:{'a':10,'d':2.0},2:{'a':10,'d':2.0},3:{'a':10,'d':2.0}},
        },
        'min_apt_area': None,           # VIC does not mandate total apt area
        'glass_area_pct': None,         # VIC does not mandate glass/floor ratio
        'open_plan_max': 9.0,
        'sun_hours': 2.0,
        'ceiling_habitable': None,      # VIC does not hard-check ceiling height
        'ceiling_non_hab': None,
        'cross_vent_depth': 18.0,
        'openable_area_pct': None,      # VIC does not mandate % openable
    },
    'NSW': {
        'name': 'New South Wales — Apartment Design Guide 2015',
        'ref':  'NSW ADG 2015 · Part 4',
        'bedroom': {
            'MAINBED':{'width':3.0,'depth':None,'area':10.0,'robe':1.8,'label':'Main Bedroom'},
            'BED1':   {'width':3.0,'depth':None,'area':9.0, 'robe':1.5,'label':'Bedroom 1'},
            'BED2':   {'width':3.0,'depth':None,'area':9.0, 'robe':1.5,'label':'Bedroom 2'},
            'BED3':   {'width':3.0,'depth':None,'area':9.0, 'robe':1.5,'label':'Bedroom 3'},
            'BED4':   {'width':3.0,'depth':None,'area':9.0, 'robe':1.5,'label':'Bedroom 4'},
        },
        'storage': {
            # NSW volumes are lower but 50% must be internal
            0:{'total':4, 'internal':None,'internal_pct':0.50,'label':'Studio'},
            1:{'total':6, 'internal':None,'internal_pct':0.50,'label':'1 Bedroom'},
            2:{'total':8, 'internal':None,'internal_pct':0.50,'label':'2 Bedroom'},
            3:{'total':10,'internal':None,'internal_pct':0.50,'label':'3+ Bedroom'},
        },
        'living': {
            1:{'width':3.6,'area':None,'label':'Studio/1-bed'},   # NSW: no min area, but wider
            2:{'width':4.0,'area':None,'label':'2+ bed'},
        },
        'pos': {
            # NSW ADG p.93 — 1-bed 8m²/2.0m, 2-bed 10m²/2.0m, 3-bed+ 12m²/2.4m
            'APT_POS':       {1:{'a':8,'d':2.0},2:{'a':10,'d':2.0},3:{'a':12,'d':2.4}},
            'APT_POS_GROUND':{1:{'a':15,'d':3.0},2:{'a':15,'d':3.0},3:{'a':15,'d':3.0}},
            'APT_POS_PODIUM':{1:{'a':15,'d':3.0},2:{'a':15,'d':3.0},3:{'a':15,'d':3.0}},
            'APT_POS_ROOF':  {1:{'a':10,'d':2.0},2:{'a':10,'d':2.0},3:{'a':10,'d':2.0}},
        },
        # NSW ADG p.89 — minimum internal apt areas
        'min_apt_area': {0:35, 1:50, 2:70, 3:90},
        # NSW ADG p.89 — glazing ≥10% of room floor area
        'glass_area_pct': 0.10,
        'open_plan_max': 8.0,           # NSW: 8m vs VIC 9m
        'sun_hours': 2.0,               # Sydney/Newcastle/Wollongong; 3.0 elsewhere
        'ceiling_habitable': 2.7,       # NSW requires 2.7m habitable
        'ceiling_non_hab': 2.4,
        'cross_vent_depth': 18.0,
        'openable_area_pct': 0.05,      # NSW: openable area ≥5% of floor area
    },
    'BEST_PRACTICE': {
        # Inherits NSW (stricter) and adds additional quality targets
        # Thresholds marked ★ go beyond either ADG
        'name': 'Best Practice (NSW base + quality targets)',
        'ref':  'NSW ADG 2015 + industry best practice',
        'bedroom': {
            'MAINBED':{'width':3.2,'depth':None,'area':12.0,'robe':2.0,'label':'Main Bedroom'},  # ★ larger
            'BED1':   {'width':3.0,'depth':None,'area':10.0,'robe':1.8,'label':'Bedroom 1'},      # ★
            'BED2':   {'width':3.0,'depth':None,'area':10.0,'robe':1.8,'label':'Bedroom 2'},
            'BED3':   {'width':3.0,'depth':None,'area':9.0, 'robe':1.5,'label':'Bedroom 3'},
            'BED4':   {'width':3.0,'depth':None,'area':9.0, 'robe':1.5,'label':'Bedroom 4'},
        },
        'storage': {
            0:{'total':4, 'internal':None,'internal_pct':0.50,'label':'Studio'},
            1:{'total':6, 'internal':None,'internal_pct':0.50,'label':'1 Bedroom'},
            2:{'total':8, 'internal':None,'internal_pct':0.50,'label':'2 Bedroom'},
            3:{'total':10,'internal':None,'internal_pct':0.50,'label':'3+ Bedroom'},
        },
        'living': {
            1:{'width':3.6,'area':None,'label':'Studio/1-bed'},
            2:{'width':4.0,'area':None,'label':'2+ bed'},
        },
        'pos': {
            'APT_POS':       {1:{'a':10,'d':2.0},2:{'a':12,'d':2.0},3:{'a':15,'d':2.4}},  # ★ larger
            'APT_POS_GROUND':{1:{'a':20,'d':3.0},2:{'a':20,'d':3.0},3:{'a':20,'d':3.0}},
            'APT_POS_PODIUM':{1:{'a':15,'d':3.0},2:{'a':15,'d':3.0},3:{'a':15,'d':3.0}},
            'APT_POS_ROOF':  {1:{'a':12,'d':2.0},2:{'a':12,'d':2.0},3:{'a':12,'d':2.0}},
        },
        'min_apt_area': {0:40, 1:55, 2:75, 3:95},  # ★ slightly larger
        'glass_area_pct': 0.125,        # ★ 12.5% vs NSW 10%
        'open_plan_max': 8.0,
        'sun_hours': 3.0,               # ★ 3 hours (NSW regional standard)
        'ceiling_habitable': 2.7,
        'ceiling_non_hab': 2.4,
        'cross_vent_depth': 18.0,
        'openable_area_pct': 0.05,
    }
}

POS_TYPE_LABEL={'APT_POS':'Balcony','APT_POS_GROUND':'Ground courtyard',
                'APT_POS_PODIUM':'Podium POS','APT_POS_ROOF':'Rooftop POS'}

DOOR_MIN = 0.85
ACCESS_PATH = 1.2
BATH_OPT_A_CIRC = 1.2
BATH_OPT_B_W = 1.0
BATH_OPT_B_L = 2.7
ROOM_DEPTH_RATIO = 2.5
BREEZE_MIN = 5.0
BREEZE_MAX = 18.0
DF_LIVING = 2.0
DF_BED    = 1.0

# Convenience accessors — default to VIC; overridden by get_rules()
BEDROOM_MIN = RULES['VIC']['bedroom']
STORAGE_REQ  = RULES['VIC']['storage']
LIVING_MIN   = RULES['VIC']['living']
POS_TYPES    = RULES['VIC']['pos']
OPEN_PLAN_MAX = 9.0
SUN_HOURS_MIN = 2.0


def get_rules(jurisdiction: str) -> dict:
    """Return the rule set for the given jurisdiction key."""
    return RULES.get(jurisdiction.upper(), RULES['VIC'])

HABITABLE = ['MAINBED','BED1','BED2','BED3','BED4','LIVING']
ROOM_LABEL = {
    'MAINBED':'Main Bedroom','LIVING':'Living Area',
    'BED1':'Bedroom 1','BED2':'Bedroom 2','BED3':'Bedroom 3','BED4':'Bedroom 4',
    'BATHROOM':'Bathroom','ENSUITE':'Ensuite','LAUNDRY':'Laundry','ENTRY':'Entry',
}

SHGC = {'N':0.6,'NE':0.5,'NW':0.5,'E':0.8,'W':0.8,'SE':0.6,'SW':0.6,'S':0.3,'UNKNOWN':0.5}

NOISE_ZONES = {
    'INDUSTRY':        {'dist':300,'label':'Industrial zone'},
    'ROAD':            {'dist':300,'label':'Major road (≥40k AADT)'},
    'RAIL_PASSENGER':  {'dist':80, 'label':'Passenger railway'},
    'RAIL_FREIGHT_METRO':{'dist':135,'label':'Metro freight railway'},
    'RAIL_FREIGHT_REGIONAL':{'dist':80,'label':'Regional freight railway'},
}

IDEAL_ADJ = {
    frozenset(['APT_ROOM_LIVING','APT_POS']),
    frozenset(['APT_ROOM_LIVING','APT_ROOM_ENTRY']),
    frozenset(['APT_ROOM_MAINBED','APT_ROOM_ENSUITE']),
}
BAD_ADJ = {
    frozenset(['APT_ROOM_MAINBED','APT_ROOM_LAUNDRY']),
    frozenset(['APT_ROOM_BED1','APT_ROOM_LAUNDRY']),
}
ALL_ROOM_LAYERS=['APT_ROOM_MAINBED','APT_ROOM_BED1','APT_ROOM_BED2','APT_ROOM_BED3',
                 'APT_ROOM_BED4','APT_ROOM_LIVING','APT_ROOM_BATHROOM','APT_ROOM_ENSUITE',
                 'APT_ROOM_LAUNDRY','APT_ROOM_ENTRY','APT_POS','APT_POS_GROUND',
                 'APT_POS_PODIUM','APT_POS_ROOF']
ROOM_TYPE_MAP={'APT_ROOM_MAINBED':'bedroom','APT_ROOM_BED1':'bedroom','APT_ROOM_BED2':'bedroom',
               'APT_ROOM_BED3':'bedroom','APT_ROOM_BED4':'bedroom','APT_ROOM_LIVING':'living',
               'APT_ROOM_BATHROOM':'wet','APT_ROOM_ENSUITE':'wet','APT_ROOM_LAUNDRY':'wet',
               'APT_ROOM_ENTRY':'circulation','APT_POS':'outdoor','APT_POS_GROUND':'outdoor',
               'APT_POS_PODIUM':'outdoor','APT_POS_ROOF':'outdoor'}
DISPLAY_NAME={'APT_ROOM_MAINBED':'Main Bed','APT_ROOM_BED1':'Bed 1','APT_ROOM_BED2':'Bed 2',
              'APT_ROOM_BED3':'Bed 3','APT_ROOM_BED4':'Bed 4','APT_ROOM_LIVING':'Living',
              'APT_ROOM_BATHROOM':'Bathroom','APT_ROOM_ENSUITE':'Ensuite',
              'APT_ROOM_LAUNDRY':'Laundry','APT_ROOM_ENTRY':'Entry',
              'APT_POS':'Balcony','APT_POS_GROUND':'Courtyard',
              'APT_POS_PODIUM':'Podium','APT_POS_ROOF':'Rooftop'}
PRIVACY={'bedroom':4,'living':2,'wet':3,'circulation':1,'outdoor':0,'other':0}


# ═══════════════════════════════════════════════════════════════════════════════
# APARTMENT CLASSIFICATION
# ═══════════════════════════════════════════════════════════════════════════════

def count_bedrooms(layers):
    n=0
    if 'APT_ROOM_MAINBED' in layers: n+=1
    for i in range(1,6):
        if f'APT_ROOM_BED{i}' in layers: n+=1
    return n

def classify_apartment(layers, windows, north):
    """Classify orientation, single-aspect, corner, through status."""
    aspects=set()
    for rk, wlist in windows.items():
        for wl in wlist:
            aspects.add(window_aspect(wl, north))

    # Primary orientation = aspect of living-area windows, or most common
    living_asp=[]
    for wl in windows.get('LIVING',[]):
        living_asp.append(window_aspect(wl, north))
    primary=living_asp[0] if living_asp else (list(aspects)[0] if aspects else 'UNKNOWN')

    # Flatten to cardinal
    def cardinal(a):
        return a if a in ('N','E','S','W') else a[0] if a else 'N'
    cardinals={cardinal(a) for a in aspects}

    single_aspect=len(cardinals)<=1
    corner=len(cardinals)==2 and not any(
        frozenset([a,b]) in [frozenset(['N','S']),frozenset(['E','W'])]
        for a in cardinals for b in cardinals if a!=b)
    through=len(cardinals)==2 and any(
        frozenset([a,b]) in [frozenset(['N','S']),frozenset(['E','W'])]
        for a in cardinals for b in cardinals if a!=b)

    return {
        'primary_orientation': primary,
        'aspects': sorted(aspects),
        'single_aspect': single_aspect,
        'corner': corner,
        'through': through,
        'label': ('Single-aspect' if single_aspect else
                  'Corner apartment' if corner else
                  'Through apartment' if through else 'Multi-aspect'),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# COMPLIANCE CHECKS
# ═══════════════════════════════════════════════════════════════════════════════

# ── 1. Bedrooms ────────────────────────────────────────────────────────────────
def check_bedrooms(layers, doors):
    results=[]
    for bk, req in BEDROOM_MIN.items():
        ln=f'APT_ROOM_{bk}'
        if ln not in layers: continue
        for idx,poly in enumerate(layers[ln]):
            if len(poly)<3: continue
            area=poly_area(poly)
            w,h=min_bounding_rect(poly)
            width,depth=min(w,h),max(w,h)
            suffix=f' #{idx+1}' if len(layers[ln])>1 else ''

            # Door check
            dk=bk
            door_list=doors.get(dk,[])
            best_door=max((d['width'] for d in door_list), default=None)
            door_ok = best_door is None or best_door>=DOOR_MIN  # None = not modelled

            # Wardrobe storage check — look for storage polygons overlapping bbox
            minx,miny,maxx,maxy=poly_bbox(poly)
            robe_area=0.0
            for sp in layers.get('APT_STORAGE_DESIGNATED',[]):
                sc_x,sc_y=poly_centroid(sp)
                if minx<=sc_x<=maxx and miny<=sc_y<=maxy:
                    robe_area+=poly_area(sp)
            robe_len_est=robe_area/0.6 if robe_area>0 else 0.0  # assume 600mm deep
            robe_ok = robe_len_est>=req['robe'] if robe_area>0 else None

            req_depth = req.get('depth')
            req_area  = req.get('area')
            width_pass = width >= req['width']
            depth_pass = (depth >= req_depth) if req_depth is not None else True
            area_pass  = (area  >= req_area)  if req_area  is not None else True
            results.append({
                'room': req['label']+suffix,
                'layer': ln,
                'area_sqm': round(area,2),
                'width_m': round(width,2),
                'depth_m': round(depth,2),
                'req_width_m': req['width'],
                'req_depth_m': req_depth,
                'req_area_sqm': req_area,
                'width_pass': width_pass,
                'depth_pass': depth_pass,
                'area_pass': area_pass,
                'door_width_m': round(best_door,3) if best_door else None,
                'door_pass': door_ok,
                'robe_area_sqm': round(robe_area,2),
                'robe_len_est_m': round(robe_len_est,2),
                'robe_req_m': req['robe'],
                'robe_ok': robe_ok,
                'overall_pass': width_pass and depth_pass and area_pass and door_ok,
                'centroid': poly_centroid(poly),
                'vertices': poly,
            })
    return results


# ── 2. Living area ─────────────────────────────────────────────────────────────
def check_living(layers, bedroom_count):
    results=[]
    ln='APT_ROOM_LIVING'
    if ln not in layers: return results
    req=LIVING_MIN[1 if bedroom_count<=1 else 2]
    req_area = req.get('area')
    for idx,poly in enumerate(layers[ln]):
        if len(poly)<3: continue
        area=poly_area(poly)
        w,h=min_bounding_rect(poly)
        width=min(w,h)
        width_pass = width>=req['width']
        area_pass  = (area>=req_area) if req_area is not None else True
        results.append({
            'room':'Living Area',
            'layer':ln,
            'area_sqm':round(area,2),
            'width_m':round(width,2),
            'req_width':req['width'],
            'req_area':req_area,
            'width_pass':width_pass,
            'area_pass':area_pass,
            'overall_pass':width_pass and area_pass,
            'centroid':poly_centroid(poly),
            'vertices':poly,
        })
    return results


# ── 3. Room depth ──────────────────────────────────────────────────────────────
def check_room_depth(layers, windows, ceiling_h):
    results=[]
    limit=ROOM_DEPTH_RATIO*ceiling_h
    for rk in HABITABLE:
        ln=f'APT_ROOM_{rk}'
        if ln not in layers: continue
        wl=windows.get(rk,[])
        open_plan=(rk=='LIVING')
        for idx,poly in enumerate(layers[ln]):
            if len(poly)<3: continue
            w,h=min_bounding_rect(poly)
            geom_d=max(w,h)
            actual_d=max_depth_from_windows(poly,wl)
            used_d=actual_d if actual_d is not None else geom_d
            lim=OPEN_PLAN_MAX if (open_plan and ceiling_h>=2.7) else limit
            suffix=f' #{idx+1}' if len(layers[ln])>1 else ''
            results.append({
                'room': ROOM_LABEL.get(rk,rk)+suffix,
                'layer':ln,
                'room_key':rk,
                'geom_depth_m':round(geom_d,2),
                'actual_depth_m':round(actual_d,2) if actual_d is not None else None,
                'depth_used_m':round(used_d,2),
                'limit_m':round(lim,2),
                'has_windows':len(wl)>0,
                'overall_pass':used_d<=lim,
                'centroid':poly_centroid(poly),
                'vertices':poly,
                'note':('From window lines' if actual_d is not None
                        else 'No window layer — using longest room axis (conservative)'),
            })
    return results


# ── 4. Windows ─────────────────────────────────────────────────────────────────
def check_windows(layers, windows, north):
    results=[]
    for rk in HABITABLE:
        ln=f'APT_ROOM_{rk}'
        if ln not in layers: continue
        wlist=windows.get(rk,[])
        has_win=len(wlist)>0
        aspects=[window_aspect(wl,north) for wl in wlist]
        areas=[round(seg_len(wl[0],wl[1])*1.0,2) for wl in wlist]  # *assumed head ht later
        results.append({
            'room':ROOM_LABEL.get(rk,rk),
            'room_key':rk,
            'has_window':has_win,
            'window_count':len(wlist),
            'aspects':aspects,
            'window_lines':wlist,
            'overall_pass':has_win,
            'note':'' if has_win else 'No APT_WINDOW layer — borrowed light risk',
        })

    # Snorkel check
    snorkel_results=[]
    for rk in ['MAINBED','BED1','BED2','BED3','BED4']:
        sln=f'APT_ROOM_SNORKEL_{rk}'
        if sln not in layers: continue
        for idx,poly in enumerate(layers[sln]):
            w,h=min_bounding_rect(poly)
            width=min(w,h); depth=max(w,h)
            width_ok=width>=1.2
            ratio_ok=depth<=(1.5*width)
            snorkel_results.append({
                'room':f'Snorkel — {ROOM_LABEL.get(rk,rk)}',
                'width_m':round(width,2),
                'depth_m':round(depth,2),
                'req_width_m':1.2,
                'max_ratio':1.5,
                'width_pass':width_ok,
                'ratio_pass':ratio_ok,
                'overall_pass':width_ok and ratio_ok,
                'centroid':poly_centroid(poly),
                'vertices':poly,
            })
    return {'habitable':results,'snorkel':snorkel_results}


# ── 5. Storage ─────────────────────────────────────────────────────────────────
def check_storage(layers, ceiling_h, bedroom_count):
    des_area=svc_area=0.0
    des_polys=[]; svc_polys=[]
    for ln,polys in layers.items():
        for poly in polys:
            a=poly_area(poly)
            if ln=='APT_STORAGE_DESIGNATED':
                des_area+=a; des_polys.append({'vertices':poly,'area':round(a,3),'centroid':poly_centroid(poly)})
            elif ln=='APT_STORAGE_SERVICE':
                svc_area+=a; svc_polys.append({'vertices':poly,'area':round(a,3),'centroid':poly_centroid(poly)})
    tot_a=des_area+svc_area
    tot_v=tot_a*ceiling_h; des_v=des_area*ceiling_h
    req=STORAGE_REQ[min(bedroom_count,3)]
    req_internal = req.get('internal')
    req_pct      = req.get('internal_pct')  # NSW: 50% of total must be internal
    # Internal pass: fixed volume (VIC) or percentage (NSW)
    if req_internal is not None:
        internal_pass = des_v >= req_internal
    elif req_pct is not None:
        internal_pass = (des_v / tot_v >= req_pct) if tot_v > 0 else False
    else:
        internal_pass = True
    return {
        'dwelling_type':req['label'],
        'ceiling_h':ceiling_h,
        'designated_area':round(des_area,2),'service_area':round(svc_area,2),
        'total_area':round(tot_a,2),
        'designated_vol':round(des_v,2),'total_vol':round(tot_v,2),
        'req_internal':req_internal,'req_total':req['total'],
        'req_internal_pct': req_pct,
        'internal_pass':internal_pass,
        'total_pass':tot_v>=req['total'],
        'overall_pass':internal_pass and tot_v>=req['total'],
        'designated_polys':des_polys,'service_polys':svc_polys,
    }


# ── 6. Private open space ──────────────────────────────────────────────────────
def check_pos(layers, doors, bedroom_count):
    results=[]
    bk=max(1,min(bedroom_count,3))
    pos_layers=[l for l in layers if l.startswith('APT_POS')]
    for ln in pos_layers:
        reqs=POS_TYPES.get(ln,POS_TYPES['APT_POS'])
        req=reqs[bk]
        type_label=POS_TYPE_LABEL.get(ln,'POS')
        for idx,poly in enumerate(layers[ln]):
            if len(poly)<3: continue
            area=poly_area(poly)
            w,h=min_bounding_rect(poly)
            min_d=min(w,h)
            # Living-room connection check
            living_conn=bool(doors.get('POS'))
            # AC allowance — check if any fixture marker nearby (future: APT_AC layer)
            ac_present=False
            effective_area=area-(1.5 if ac_present else 0)
            suffix=f' #{idx+1}' if len(layers[ln])>1 else ''
            results.append({
                'space':type_label+suffix,
                'layer':ln,
                'area_sqm':round(area,2),
                'effective_area_sqm':round(effective_area,2),
                'min_dim_m':round(min_d,2),
                'req_area_sqm':req['a'],
                'req_dim_m':req['d'],
                'area_pass':effective_area>=req['a'],
                'dim_pass':min_d>=req['d'],
                'living_conn':living_conn,
                'living_conn_ok':living_conn,
                'ac_present':ac_present,
                'overall_pass':effective_area>=req['a'] and min_d>=req['d'],
                'centroid':poly_centroid(poly),
                'vertices':poly,
            })
    return results


# ── 7. Natural ventilation ─────────────────────────────────────────────────────
def check_ventilation(layers, windows, north):
    hab_wins=[]
    for rk in HABITABLE:
        ln=f'APT_ROOM_{rk}'
        if ln not in layers: continue
        wl=windows.get(rk,[])
        hab_wins.append({'room':ROOM_LABEL.get(rk,rk),'has_window':len(wl)>0,'count':len(wl)})

    all_wins=[]
    for rk,wlist in windows.items():
        for wl in wlist:
            all_wins.append({'line':wl,'room':rk,'aspect':window_aspect(wl,north)})

    paths=[]
    for i in range(len(all_wins)):
        for j in range(i+1,len(all_wins)):
            w1,w2=all_wins[i],all_wins[j]
            plen=seg_len(midpoint(w1['line'][0],w1['line'][1]),
                         midpoint(w2['line'][0],w2['line'][1]))
            diff=windows_differ_aspect(w1['line'],w2['line'],north)
            if diff and BREEZE_MIN<=plen<=BREEZE_MAX:
                paths.append({
                    'room_a':w1['room'],'room_b':w2['room'],
                    'aspect_a':w1['aspect'],'aspect_b':w2['aspect'],
                    'path_length_m':round(plen,2),
                    'midpoint_a':midpoint(w1['line'][0],w1['line'][1]),
                    'midpoint_b':midpoint(w2['line'][0],w2['line'][1]),
                })

    cv_pass=len(paths)>0
    all_hab_ok=all(h['has_window'] for h in hab_wins)
    return {
        'habitable_windows':hab_wins,
        'cross_ventilation':{'valid_paths':paths,'path_count':len(paths),'pass':cv_pass},
        'all_hab_ok':all_hab_ok,
        'overall_pass':all_hab_ok and cv_pass,
    }


# ── 8. Doors ───────────────────────────────────────────────────────────────────
def check_doors(doors):
    """Report all modelled door widths with pass/fail against min clear opening."""
    DOOR_LABELS={
        'ENTRY':'Apartment entry','MAINBED':'Main bedroom',
        'BED1':'Bedroom 1','BED2':'Bedroom 2','BED3':'Bedroom 3','BED4':'Bedroom 4',
        'BATHROOM':'Bathroom','ENSUITE':'Ensuite','LAUNDRY':'Laundry',
        'LIVING':'Living area','POS':'Balcony/POS',
    }
    BATH_OPT_A_DOOR=0.85; BATH_OPT_B_DOOR=0.82
    results=[]
    for dk,dlist in sorted(doors.items()):
        for d in dlist:
            is_bath=dk in ('BATHROOM','ENSUITE')
            req=BATH_OPT_A_DOOR
            results.append({
                'key':dk,
                'label':DOOR_LABELS.get(dk,dk),
                'width_m':round(d['width'],3),
                'req_m':req,
                'pass':d['width']>=req,
                'midpoint':d['midpoint'],
                'p1':d['p1'],'p2':d['p2'],
                'angle':d['angle'],
            })
    return results


# ── 9. Accessibility ───────────────────────────────────────────────────────────
def check_accessibility(layers, doors, ceiling_h):
    checks=[]
    def first(ln): return layers[ln][0] if ln in layers and layers[ln] else None
    def mw(poly): return round(poly_min_width(poly),2) if poly else None

    entry=first('APT_ROOM_ENTRY')
    mainbed=first('APT_ROOM_MAINBED')
    living=first('APT_ROOM_LIVING')
    # Prefer ensuite adjacent to mainbed; fall back to bathroom
    _bath=first('APT_ROOM_BATHROOM'); _ensuite=first('APT_ROOM_ENSUITE')
    # If ensuite is present and adjacent to mainbed, prefer it
    if _ensuite and mainbed and shared_boundary(mainbed,_ensuite)>0.1:
        bathroom=_ensuite; bath_label='Ensuite'
    elif _bath:
        bathroom=_bath; bath_label='Bathroom'
    elif _ensuite:
        bathroom=_ensuite; bath_label='Ensuite'
    else:
        bathroom=None; bath_label='Bathroom/Ensuite' 

    # Entry door
    entry_d=max((d['width'] for d in doors.get('ENTRY',[])),default=None)
    checks.append({'item':'Apartment entry door ≥ 850mm','pass':entry_d is not None and entry_d>=DOOR_MIN,
                   'detail':f'{round(entry_d*1000)}mm' if entry_d else 'APT_DOOR_ENTRY not modelled'})

    # Main bedroom door
    mb_d=max((d['width'] for d in doors.get('MAINBED',[])),default=None)
    checks.append({'item':'Main bedroom door ≥ 850mm','pass':mb_d is not None and mb_d>=DOOR_MIN,
                   'detail':f'{round(mb_d*1000)}mm' if mb_d else 'APT_DOOR_MAINBED not modelled'})

    # Min widths of rooms (path proxy)
    for label,poly,req in [('Living area',living,ACCESS_PATH),
                            ('Main bedroom',mainbed,ACCESS_PATH),
                            ('Bathroom',bathroom,ACCESS_PATH)]:
        w=mw(poly)
        checks.append({'item':f'{label} width ≥ 1200mm','pass':w is not None and w>=req,
                       'detail':f'{w}m' if w else 'Room not found'})

    # Connected path: entry → living → mainbed
    liv_entry_ok=False; liv_bed_ok=False; bed_bath_ok=False
    if living and entry:
        sb=shared_boundary(living,entry)
        if sb>=DOOR_MIN: liv_entry_ok=True
    if living and mainbed:
        sb=shared_boundary(living,mainbed)
        if sb>=DOOR_MIN: liv_bed_ok=True
        elif entry and mainbed:
            sbe=shared_boundary(living,entry); sbm=shared_boundary(entry,mainbed)
            if sbe>=DOOR_MIN and sbm>=DOOR_MIN: liv_bed_ok=True
    if mainbed and bathroom:
        sb=shared_boundary(mainbed,bathroom)
        if sb>=DOOR_MIN: bed_bath_ok=True

    checks.append({'item':'Clear path: Living → Main Bedroom (direct or via entry)',
                   'pass':liv_bed_ok,'detail':'OK' if liv_bed_ok else 'Path not found ≥ 850mm'})
    checks.append({'item':f'Main bedroom adjacent to adaptable {bath_label.lower()}',
                   'pass':bed_bath_ok,'detail':'OK' if bed_bath_ok else f'No shared boundary ≥ 850mm found. Check {bath_label} is adjacent to main bedroom.'})

    # Bathroom adaptability proxy
    bath_w=mw(bathroom); bath_a=round(poly_area(bathroom),2) if bathroom else 0
    bath_ok=bath_w is not None and bath_w>=1.2 and bath_a>=3.5
    checks.append({'item':f'{bath_label} ≥ 3.5m² and ≥ 1.2m wide (Table D4 proxy)',
                   'pass':bath_ok,'detail':f'{bath_a}m² / {bath_w}m wide' if bath_w else f'No {bath_label.lower()} found'})

    return {
        'checks':checks,'note':'Geometric proxy — verify door widths and fixture layouts manually.',
        'pass_count':sum(c['pass'] for c in checks),'total':len(checks),
        'overall_pass':all(c['pass'] for c in checks),
    }


# ── 10. Daylight ───────────────────────────────────────────────────────────────
def _df_grid(poly, win_lines, ceiling_h, step=0.4):
    """Return list of {x,y,df} grid points."""
    if not win_lines or not poly: return []
    mnx,mny,mxx,mxy=poly_bbox(poly)
    T=0.7; grid=[]
    x=mnx+step/2
    while x<mxx:
        y=mny+step/2
        while y<mxy:
            if pt_in_poly(x,y,poly):
                df=0.0
                for wl in win_lines:
                    if len(wl)<2: continue
                    wlen=seg_len(wl[0],wl[1]); wa=wlen*ceiling_h
                    mx=(wl[0][0]+wl[1][0])/2; my=(wl[0][1]+wl[1][1])/2
                    dist=max(math.hypot(x-mx,y-my),0.3)
                    dx=wl[1][0]-wl[0][0]; dy=wl[1][1]-wl[0][1]
                    nx,ny=-dy,dx; l=math.hypot(nx,ny)
                    cos_f=abs((mx-x)*(nx/l if l>1e-9 else 0)+(my-y)*(ny/l if l>1e-9 else 1))/dist if dist>0 else 1
                    df+=min((wa*T/dist**2)*cos_f/(2*math.pi)*100, 15)
                grid.append({'x':round(x,3),'y':round(y,3),'df':round(min(df,20),2)})
            y+=step
        x+=step
    return grid

def check_daylight(layers, windows, north, ceiling_h):
    rooms=[]
    for rk in HABITABLE:
        ln=f'APT_ROOM_{rk}'
        if ln not in layers: continue
        wl=windows.get(rk,[])
        is_living=(rk=='LIVING')
        tgt=DF_LIVING if is_living else DF_BED
        for idx,poly in enumerate(layers[ln]):
            if len(poly)<3: continue
            area=poly_area(poly)
            grid=_df_grid(poly,wl,ceiling_h)
            avg_df=round(sum(p['df'] for p in grid)/len(grid),2) if grid else 0.0
            min_df=round(min(p['df'] for p in grid),2) if grid else 0.0
            max_df=round(max(p['df'] for p in grid),2) if grid else 0.0
            sun_h=sun_hours_june21(wl,north) if is_living else None
            sun_pass=sun_h>=SUN_HOURS_MIN if sun_h is not None else None
            win_area=sum(seg_len(wl[0],wl[1])*ceiling_h for wl in wl if len(wl)>=2)
            wfar=round(win_area/area*100,1) if area>0 else 0.0
            # South-facing risk
            asp_south=any(window_aspect(w,north) in ('S','SE','SW') for w in wl)
            suffix=f' #{idx+1}' if len(layers[ln])>1 else ''
            rooms.append({
                'room':ROOM_LABEL.get(rk,rk)+suffix,'room_key':rk,'layer':ln,
                'area_sqm':round(area,2),'win_count':len(wl),
                'win_area_m2':round(win_area,2),'wfar_pct':wfar,
                'avg_df':avg_df,'min_df':min_df,'max_df':max_df,'df_target':tgt,
                'df_pass':avg_df>=tgt,
                'sun_hours':sun_h,'sun_pass':sun_pass,'south_facing_risk':asp_south,
                'df_grid':grid,
                'centroid':poly_centroid(poly),'vertices':poly,
                'overall_pass':avg_df>=tgt and (sun_pass if sun_pass is not None else True),
            })

    pos_results=[]
    for ln in [l for l in layers if l.startswith('APT_POS')]:
        for idx,poly in enumerate(layers[ln]):
            if len(poly)<3: continue
            edges=[[poly[i],poly[(i+1)%len(poly)]] for i in range(len(poly))]
            longest=max(edges,key=lambda e:seg_len(e[0],e[1]))
            sh=sun_hours_june21([longest],north)
            pos_results.append({'space':POS_TYPE_LABEL.get(ln,'POS'),
                                 'sun_hours':sh,'sun_pass':sh>=SUN_HOURS_MIN,
                                 'centroid':poly_centroid(poly)})

    overall=all(r['overall_pass'] for r in rooms)
    return {'rooms':rooms,'pos':pos_results,'overall_pass':overall}


# ── 11. Energy efficiency ──────────────────────────────────────────────────────
def check_energy(layers, windows, north, ceiling_h):
    asp_summary={}; total_a=north_a=ew_a=0.0
    win_list=[]
    for rk,wl in windows.items():
        for w in wl:
            if len(w)<2: continue
            wlen=seg_len(w[0],w[1]); wa=wlen*ceiling_h
            asp=window_aspect(w,north)
            total_a+=wa
            if asp in ('N','NE','NW'): north_a+=wa
            if asp in ('E','W'): ew_a+=wa
            asp_summary.setdefault(asp,{'area':0.0,'count':0,'rooms':[]})
            asp_summary[asp]['area']+=wa; asp_summary[asp]['count']+=1
            if rk not in asp_summary[asp]['rooms']: asp_summary[asp]['rooms'].append(rk)
            win_list.append({'room':rk,'aspect':asp,'win_area_m2':round(wa,2),'shgc':SHGC.get(asp,0.5)})

    north_pct=round(north_a/total_a*100,1) if total_a>0 else 0.0
    ew_pct=round(ew_a/total_a*100,1) if total_a>0 else 0.0
    liv_north=any(window_aspect(w,north) in ('N','NE','NW') for w in windows.get('LIVING',[]))
    risk='High' if ew_pct>=40 else ('Medium' if ew_pct>=25 else 'Low')

    checks=[
        {'item':'Living area has north-facing window','pass':liv_north,
         'detail':'North glazing in living area' if liv_north else 'No north-facing living window'},
        {'item':'North glazing ≥ 30% of total','pass':north_pct>=30,
         'detail':f'{north_pct}% of window area faces north (±45°)'},
        {'item':'East/West glazing < 40%','pass':ew_pct<40,
         'detail':f'{ew_pct}% faces E/W — cooling risk: {risk}'},
    ]
    return {
        'window_list':win_list,
        'aspect_summary':{k:{'area':round(v["area"],2),'count':v['count'],'rooms':v['rooms'],
                             'pct':round(v['area']/total_a*100,1) if total_a>0 else 0}
                          for k,v in asp_summary.items()},
        'north_glazing_pct':north_pct,'ew_glazing_pct':ew_pct,
        'living_north':liv_north,'cooling_risk':risk,
        'checks':checks,'overall_pass':all(c['pass'] for c in checks),
    }


# ── 12. Noise influence ────────────────────────────────────────────────────────
def check_noise(layers):
    noise_layers={k:v for k,v in layers.items() if k.startswith('APT_NOISE_')}
    if not noise_layers:
        return {'assessed':False,'sources':[],'in_range':False,'overall_pass':True,
                'note':'No APT_NOISE_* layers. Add a line from source to building to enable.'}
    sources=[]; any_in=False
    for ln,polys in noise_layers.items():
        key=ln[len('APT_NOISE_'):]
        cfg=NOISE_ZONES.get(key); 
        if not cfg: continue
        for poly in polys:
            if len(poly)<2: continue
            dist=seg_len(poly[0],poly[1])
            in_r=dist<=cfg['dist']
            if in_r: any_in=True
            sources.append({'source':cfg['label'],'layer':ln,'distance_m':round(dist,1),
                            'influence_m':cfg['dist'],'in_range':in_r,'pass':not in_r})
    return {'assessed':True,'sources':sources,'in_range':any_in,'overall_pass':not any_in,
            'note':('Within noise influence area — acoustic report required.' if any_in else '')}


# ── 13. Adjacency graph ────────────────────────────────────────────────────────
def build_adjacency(layers):
    nodes=[]
    for ln in ALL_ROOM_LAYERS:
        if ln not in layers: continue
        for idx,poly in enumerate(layers[ln]):
            if len(poly)<3: continue
            nid=f'{ln}_{idx}'
            suffix=f' #{idx+1}' if len(layers[ln])>1 else ''
            nodes.append({'id':nid,'label':DISPLAY_NAME.get(ln,ln)+suffix,
                          'layer':ln,'type':ROOM_TYPE_MAP.get(ln,'other'),
                          'area_sqm':round(poly_area(poly),2),
                          'centroid':list(poly_centroid(poly)),
                          'privacy':PRIVACY.get(ROOM_TYPE_MAP.get(ln,'other'),0),
                          'vertices':poly})

    edges=[]; existing_pairs=set()
    for i in range(len(nodes)):
        for j in range(i+1,len(nodes)):
            a,b=nodes[i],nodes[j]
            sb=shared_boundary(a['vertices'],b['vertices'])
            if sb>0.1:
                pair=frozenset([a['layer'],b['layer']])
                existing_pairs.add(pair)
                edges.append({'a':a['id'],'b':b['id'],
                              'a_label':a['label'],'b_label':b['label'],
                              'a_layer':a['layer'],'b_layer':b['layer'],
                              'shared_m':round(sb,2),
                              'is_ideal':pair in IDEAL_ADJ,
                              'is_bad':pair in BAD_ADJ,
                              'privacy_jump':abs(a['privacy']-b['privacy']),
                              'a_centroid':a['centroid'],'b_centroid':b['centroid']})

    present=set(layers.keys())
    ideal_missing=[{'a':DISPLAY_NAME.get(list(p)[0],list(p)[0]),
                    'b':DISPLAY_NAME.get(list(p)[1],list(p)[1])}
                   for p in IDEAL_ADJ
                   if list(p)[0] in present and list(p)[1] in present and p not in existing_pairs]

    # ── Circulation path checks ──────────────────────────────────────────
    circ_issues = []
    circ_ok = []

    # Helper: is layer A adjacent to layer B?
    def adjacent(la, lb):
        return any(
            (e['a_layer']==la and e['b_layer']==lb) or
            (e['a_layer']==lb and e['b_layer']==la)
            for e in edges
        )

    # 1. Entry should be adjacent to living area
    if 'APT_ROOM_ENTRY' in present and 'APT_ROOM_LIVING' in present:
        if adjacent('APT_ROOM_ENTRY','APT_ROOM_LIVING'):
            circ_ok.append('Entry is adjacent to living area')
        else:
            circ_issues.append({'severity':'error','title':'Entry not adjacent to living area',
                'detail':'The entry should connect directly to the living area. To reach living you currently pass through other rooms.'})

    # 2. Bedroom should not require walking through bathroom
    for bed in ['APT_ROOM_MAINBED','APT_ROOM_BED1','APT_ROOM_BED2','APT_ROOM_BED3']:
        if bed not in present: continue
        for bath in ['APT_ROOM_BATHROOM','APT_ROOM_ENSUITE']:
            if bath not in present: continue
            # Bad if: bathroom is adjacent to living AND bedroom is only adjacent to bathroom (not living)
            bath_adj_living  = adjacent(bath,'APT_ROOM_LIVING')
            bed_adj_bath     = adjacent(bed, bath)
            bed_adj_living   = adjacent(bed,'APT_ROOM_LIVING')
            bed_adj_entry    = adjacent(bed,'APT_ROOM_ENTRY')
            bed_label = DISPLAY_NAME.get(bed, bed)
            bath_label = DISPLAY_NAME.get(bath, bath)
            if bed_adj_bath and bath_adj_living and not bed_adj_living and not bed_adj_entry:
                circ_issues.append({'severity':'error',
                    'title':f'{bed_label} only reachable via {bath_label}',
                    'detail':f'To reach {bed_label} you must pass through {bath_label}. Redraw the plan so the bedroom connects to entry or living directly.'})

    # 3. Wet rooms (bathroom, laundry) should not be between living and bedroom
    for wet in ['APT_ROOM_BATHROOM','APT_ROOM_LAUNDRY']:
        if wet not in present: continue
        wet_adj_living = adjacent(wet,'APT_ROOM_LIVING')
        for bed in ['APT_ROOM_MAINBED','APT_ROOM_BED1','APT_ROOM_BED2']:
            if bed not in present: continue
            wet_adj_bed = adjacent(wet,bed)
            if wet_adj_living and wet_adj_bed:
                wet_label = DISPLAY_NAME.get(wet,wet)
                bed_label = DISPLAY_NAME.get(bed,bed)
                circ_issues.append({'severity':'warning',
                    'title':f'{wet_label} between living and {bed_label}',
                    'detail':f'{wet_label} sits between the living area and {bed_label}. This creates a poor privacy gradient and awkward circulation.'})

    # 4. Disconnected rooms (rooms with no edges at all = plan error)
    connected_ids = set()
    for e in edges:
        connected_ids.add(e['a']); connected_ids.add(e['b'])
    disconnected = [n for n in nodes if n['id'] not in connected_ids and n['layer'].startswith('APT_ROOM_')]
    for n in disconnected:
        circ_issues.append({'severity':'error',
            'title':f'{n["label"]} is disconnected',
            'detail':f'{n["label"]} has no shared boundary with any other room. Check that polygons share edges correctly in the DXF. The plan may need to be redrawn.'})

    return {'nodes':nodes,'edges':edges,
            'ideal_missing':ideal_missing,
            'bad_present':[e for e in edges if e['is_bad']],
            'circ_issues':circ_issues,
            'circ_ok':circ_ok,
            'has_circ_errors':any(i['severity']=='error' for i in circ_issues)}


# ── 14. Diagnostics ────────────────────────────────────────────────────────────
def compute_diagnostics(layers, windows, doors, north, ceiling_h, bedroom_count):
    diag=[]

    # Laundry inside bathroom/ensuite — flag as info (not error, it's common in compact apartments)
    for wet in ['APT_ROOM_BATHROOM','APT_ROOM_ENSUITE']:
        if wet not in layers or 'APT_ROOM_LAUNDRY' not in layers: continue
        for lp in layers['APT_ROOM_LAUNDRY']:
            lc=poly_centroid(lp)
            for wp in layers[wet]:
                if point_in_polygon(lc[0],lc[1],wp):
                    wet_label='Bathroom' if wet=='APT_ROOM_BATHROOM' else 'Ensuite'
                    diag.append({'id':'laundry_in_wet','severity':'info',
                        'title':f'Laundry drawn inside {wet_label}',
                        'detail':f'The laundry polygon overlaps the {wet_label}. This is common in compact apartments — the engine treats them as separate rooms. Ensure adequate space for washer/dryer and that the combined wet room meets minimum area requirements.'})
                    break

    # Single-aspect penalty
    aspects=set()
    for wl in windows.values():
        for w in wl: aspects.add(window_aspect(w,north))
    if len(set(a[0] for a in aspects))<=1 and aspects:
        diag.append({'id':'single_aspect','severity':'warning',
                     'title':'Single-aspect apartment',
                     'detail':'All windows face one direction. Higher daylight and ventilation risk.'})

    # Deep-plan living warning (>75% of limit)
    ln='APT_ROOM_LIVING'
    if ln in layers:
        for poly in layers[ln]:
            lim=OPEN_PLAN_MAX if ceiling_h>=2.7 else ROOM_DEPTH_RATIO*ceiling_h
            wl=windows.get('LIVING',[])
            d=max_depth_from_windows(poly,wl)
            if d is None:
                w2,h2=min_bounding_rect(poly); d=max(w2,h2)
            if d>0.75*lim and d<=lim:
                diag.append({'id':'deep_plan','severity':'warning',
                              'title':'Living area near depth limit',
                              'detail':f'Depth {round(d,2)}m is {round(d/lim*100)}% of the {round(lim,2)}m limit. Daylight to rear likely weak.'})

    # South-facing living room
    liv_wins=windows.get('LIVING',[])
    if liv_wins and all(window_aspect(w,north) in ('S','SE','SW') for w in liv_wins):
        diag.append({'id':'south_living','severity':'warning',
                     'title':'South-facing living area',
                     'detail':'Primary living glazing faces south. Reduced passive solar gain and daylight.'})

    # Balcony-to-room-depth ratio
    for pos_ln in [l for l in layers if l.startswith('APT_POS')]:
        for pp in layers[pos_ln]:
            pw,ph=min_bounding_rect(pp); bal_d=min(pw,ph)
            if bal_d>0 and 'APT_ROOM_LIVING' in layers:
                lp=layers['APT_ROOM_LIVING'][0]
                lw,lh=min_bounding_rect(lp); room_d=max(lw,lh)
                ratio=bal_d/room_d if room_d>0 else 0
                if ratio>0.25:
                    diag.append({'id':'balcony_depth','severity':'warning',
                                 'title':'Balcony depth may reduce living area daylight',
                                 'detail':f'Balcony projection ~{round(bal_d,2)}m vs room depth ~{round(room_d,2)}m ({round(ratio*100)}%). Check daylight penetration.'})
                    break

    # Missing door layers
    key_doors=['ENTRY','MAINBED']
    for dk in key_doors:
        if dk not in doors:
            diag.append({'id':f'door_missing_{dk}','severity':'info',
                         'title':f'APT_DOOR_{dk} not modelled',
                         'detail':'Add a door line to enable exact door-width compliance checks.'})

    # Residual space estimate
    room_areas=sum(poly_area(p) for ln,polys in layers.items()
                   for p in polys if ln.startswith('APT_ROOM_') and not 'SNORKEL' in ln)
    stor_areas=sum(poly_area(p) for ln,polys in layers.items()
                   for p in polys if ln.startswith('APT_STORAGE_'))
    # Estimate total floor plate from bbox of all room centroids
    all_cents=[poly_centroid(p) for ln,polys in layers.items()
               for p in polys if ln.startswith('APT_ROOM_')]
    if len(all_cents)>=2:
        xs=[c[0] for c in all_cents]; ys=[c[1] for c in all_cents]
        fp_est=(max(xs)-min(xs)+3)*(max(ys)-min(ys)+3)
        modelled=room_areas+stor_areas
        residual_pct=max(0,round((fp_est-modelled)/fp_est*100,1)) if fp_est>0 else 0
        if residual_pct>20:
            diag.append({'id':'residual_space','severity':'info',
                         'title':f'~{residual_pct}% unaccounted internal area',
                         'detail':'Area in floor plate not assigned to room polygons. Check layer coverage.'})

    return diag



# ── NSW-specific: minimum apartment area ──────────────────────────────────────
def check_apt_area(layers, bedroom_count, rules):
    min_areas = rules.get('min_apt_area')
    if not min_areas:
        return {'overall_pass':True,'assessed':False,
                'note':'Minimum apartment area not required under this jurisdiction.'}

    # Sum all habitable + wet room areas as proxy for NLA
    total = 0.0
    for ln, polys in layers.items():
        if ln.startswith('APT_ROOM_') and 'SNORKEL' not in ln:
            total += sum(poly_area(p) for p in polys)

    req = min_areas.get(min(bedroom_count, 3), 90)
    # Extra: +5m² per additional bathroom, +12m² per 4th+ bedroom (NSW)
    extra_baths = max(0, sum(1 for ln in layers if ln in
                             ('APT_ROOM_BATHROOM','APT_ROOM_ENSUITE')) - 1)
    req += extra_baths * 5

    passed = total >= req
    return {
        'overall_pass': passed,
        'assessed': True,
        'total_area_sqm': round(total, 2),
        'required_sqm': req,
        'bedroom_count': bedroom_count,
        'note': f'NSW ADG p.89 — minimum internal area for {bedroom_count}-bed apartment',
    }


# ── NSW-specific: glass area ≥10% of room floor area ─────────────────────────
def check_glass_area(layers, windows, rules, ceiling_h):
    min_pct = rules.get('glass_area_pct')
    if not min_pct:
        return {'overall_pass':True,'assessed':False,'rooms':[],
                'note':'Minimum glass area ratio not required under this jurisdiction.'}

    results = []
    for rk in ('MAINBED','BED1','BED2','BED3','BED4','LIVING'):
        ln = f'APT_ROOM_{rk}'
        if ln not in layers: continue
        win_lines = windows.get(rk, [])
        for idx, poly in enumerate(layers[ln]):
            room_area = poly_area(poly)
            if room_area < 0.5: continue
            glass_area = sum(
                seg_len(wl[0], wl[1]) * ceiling_h
                for wl in win_lines if len(wl) >= 2
            )
            glass_pct = glass_area / room_area if room_area > 0 else 0
            passed = glass_pct >= min_pct
            suffix = f' #{idx+1}' if len(layers[ln]) > 1 else ''
            results.append({
                'room': ROOM_LABEL.get(rk, rk) + suffix,
                'room_area_sqm': round(room_area, 2),
                'glass_area_sqm': round(glass_area, 2),
                'glass_pct': round(glass_pct * 100, 1),
                'req_pct': round(min_pct * 100, 1),
                'overall_pass': passed,
                'has_windows': len(win_lines) > 0,
                'centroid': list(poly_centroid(poly)),
                'vertices': poly,
            })

    overall = all(r['overall_pass'] for r in results) if results else True
    return {
        'overall_pass': overall,
        'assessed': True,
        'rooms': results,
        'req_pct': round(min_pct * 100, 1),
        'note': f'NSW ADG p.89 — glazing ≥{round(min_pct*100)}% of room floor area per habitable room',
    }


# ── NSW-specific: ceiling heights ─────────────────────────────────────────────
def check_ceiling_heights(layers, rules, ceiling_h):
    req_hab = rules.get('ceiling_habitable')
    req_non = rules.get('ceiling_non_hab')
    if not req_hab:
        return {'overall_pass':True,'assessed':False,
                'note':'Ceiling height not hard-checked under this jurisdiction.'}

    checks = []
    hab_layers = [l for l in layers if any(l == f'APT_ROOM_{k}'
                  for k in ('MAINBED','BED1','BED2','BED3','BED4','LIVING'))]
    non_hab_layers = [l for l in layers if l.startswith('APT_ROOM_') and l not in hab_layers]

    for ln in hab_layers:
        passed = ceiling_h >= req_hab
        checks.append({
            'room': ln.replace('APT_ROOM_',''),
            'type': 'habitable',
            'ceiling_h': ceiling_h,
            'required': req_hab,
            'pass': passed,
        })

    # Non-habitable: assume ceiling_h - 0.3 as proxy (often lower in service areas)
    non_hab_h = ceiling_h - 0.0  # We don't have a separate ceiling height — use same
    for ln in non_hab_layers[:3]:  # Limit output
        passed = non_hab_h >= (req_non or 2.4)
        checks.append({
            'room': ln.replace('APT_ROOM_',''),
            'type': 'non-habitable',
            'ceiling_h': non_hab_h,
            'required': req_non or 2.4,
            'pass': passed,
        })

    overall = all(c['pass'] for c in checks) if checks else True
    return {
        'overall_pass': overall,
        'assessed': True,
        'checks': checks,
        'ceiling_h_used': ceiling_h,
        'req_habitable': req_hab,
        'req_non_habitable': req_non,
        'note': 'NSW ADG p.87 — ceiling heights (adjust Ceiling Height parameter in Project Parameters)',
    }


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

def run_compliance(dxf_text: str, ceiling_h: float = 2.7, jurisdiction: str = 'VIC') -> dict:
    rules = get_rules(jurisdiction)
    # Override module-level constants with jurisdiction-specific values
    import compliance_engine as _ce
    _ce.BEDROOM_MIN   = rules['bedroom']
    _ce.STORAGE_REQ   = rules['storage']
    _ce.LIVING_MIN    = rules['living']
    _ce.POS_TYPES     = rules['pos']
    _ce.OPEN_PLAN_MAX = rules.get('open_plan_max', 9.0)
    _ce.SUN_HOURS_MIN = rules.get('sun_hours', 2.0)

    layers_raw, windows_raw, doors_raw, north = parse_dxf(dxf_text)
    if not layers_raw:
        return {'error':'No APT_ layers found. Check layer naming convention.'}

    layers, windows, doors, north, scale = auto_scale(layers_raw, windows_raw, doors_raw, north)
    beds = count_bedrooms(layers)

    results = {
        'meta': {
            'bedroom_count': beds,
            'ceiling_h': ceiling_h,
            'coord_scale': scale,
            'layers': sorted(layers.keys()),
            'window_layers': sorted(windows.keys()),
            'door_layers': sorted(doors.keys()),
            'north_vec': list(north),
            'jurisdiction': jurisdiction,
            'jurisdiction_name': rules['name'],
            'jurisdiction_ref': rules['ref'],
        },
        'classification': classify_apartment(layers, windows, north),
        'bedrooms':      check_bedrooms(layers, doors),
        'apt_area':      check_apt_area(layers, beds, rules),
        'glass_area':    check_glass_area(layers, windows, rules, ceiling_h),
        'ceiling':       check_ceiling_heights(layers, rules, ceiling_h),
        'living':        check_living(layers, beds),
        'room_depth':    check_room_depth(layers, windows, ceiling_h),
        'windows':       check_windows(layers, windows, north),
        'storage':       check_storage(layers, ceiling_h, beds),
        'pos':           check_pos(layers, doors, beds),
        'ventilation':   check_ventilation(layers, windows, north),
        'doors':         check_doors(doors),
        'accessibility': check_accessibility(layers, doors, ceiling_h),
        'daylight':      check_daylight(layers, windows, north, ceiling_h),
        'energy':        check_energy(layers, windows, north, ceiling_h),
        'noise':         check_noise(layers),
        'adjacency':     build_adjacency(layers),
        'diagnostics':   compute_diagnostics(layers, windows, doors, north, ceiling_h, beds),
    }

    s = results['summary'] = {}
    s['bedrooms_pass']      = all(r['overall_pass'] for r in results['bedrooms'])
    s['living_pass']        = all(r['overall_pass'] for r in results['living'])
    s['room_depth_pass']    = all(r['overall_pass'] for r in results['room_depth'])
    s['windows_pass']       = all(r['overall_pass'] for r in results['windows']['habitable'])
    s['storage_pass']       = results['storage']['overall_pass']
    s['pos_pass']           = all(r['overall_pass'] for r in results['pos']) if results['pos'] else True
    s['ventilation_pass']   = results['ventilation']['overall_pass']
    s['doors_pass']         = all(r['pass'] for r in results['doors']) if results['doors'] else True
    s['accessibility_pass'] = results['accessibility']['overall_pass']
    s['daylight_pass']      = results['daylight']['overall_pass']
    s['energy_pass']        = results['energy']['overall_pass']
    s['noise_pass']         = results['noise']['overall_pass']
    s['apt_area_pass']      = results['apt_area']['overall_pass']
    s['glass_area_pass']    = results['glass_area']['overall_pass']
    s['ceiling_pass']       = results['ceiling']['overall_pass']
    s['diagnostic_warnings'] = sum(1 for d in results['diagnostics'] if d['severity']=='warning')

    has_wins = bool(windows)
    has_doors = bool(doors)
    nsw_checks = rules.get('min_apt_area') is not None
    s['all_pass'] = all([
        s['bedrooms_pass'], s['living_pass'], s['storage_pass'],
        s['pos_pass'], s['room_depth_pass'],
        s['ventilation_pass'] if has_wins else True,
        s['windows_pass'] if has_wins else True,
        s['accessibility_pass'] if ('APT_ROOM_ENTRY' in layers or has_doors) else True,
        s['daylight_pass'] if has_wins else True,
        s['energy_pass'] if has_wins else True,
        s['noise_pass'],
        s['apt_area_pass'] if nsw_checks else True,
        s['glass_area_pass'] if nsw_checks and has_wins else True,
        s['ceiling_pass'] if nsw_checks else True,
    ])

    return results
