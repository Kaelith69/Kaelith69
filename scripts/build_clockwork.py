from __future__ import annotations
import math, random, io
from pathlib import Path
import cairosvg
from PIL import Image
OUT = Path('assets')
W, H = 1280, 720
CX, CY = 640, 360
TAU = math.tau
rng = random.Random(20261119)
INK = '#080D1D'
NAVY = '#0B132B'
SPACE_MID = '#101A34'
SPACE_LIFT = '#17213E'
VOID = '#02040A'
FRAME = '#273957'
COG = '#526487'
HALFTONE = '#60718E'
TEAL_DARK = '#167F91'
TEAL = '#55C9C0'
MINT = '#9BE7D7'
GOLD = '#F4C653'
PALE_GOLD = '#FFE18A'
WARM_LIGHT = '#FFD18A'
ORANGE = '#EF8148'
AMBER = '#E98645'
CRIMSON = '#A94446'
MAROON = '#8B3453'
CREAM = '#FFF0D1'
PAPER = '#FFF7E5'
ICE = '#8DA6BC'
PERI = '#9BAED0'
STEEL = '#A5C4D4'
SILVER = '#B8C6D2'
GREY = '#D8DCE1'
VIOLET = '#6C3D87'
MUTED_VIOLET = '#55366F'
def fmt(x):
    return f'{x:.3f}'.rstrip('0').rstrip('.')
def polar(r, a):
    return r * math.cos(a), r * math.sin(a)
def cubic(p0,p1,p2,p3,u):
    v=1-u
    x=v**3*p0[0]+3*v*v*u*p1[0]+3*v*u*u*p2[0]+u**3*p3[0]
    y=v**3*p0[1]+3*v*v*u*p1[1]+3*v*u*u*p2[1]+u**3*p3[1]
    dx=3*v*v*(p1[0]-p0[0])+6*v*u*(p2[0]-p1[0])+3*u*u*(p3[0]-p2[0])
    dy=3*v*v*(p1[1]-p0[1])+6*v*u*(p2[1]-p1[1])+3*u*u*(p3[1]-p2[1])
    return x,y,dx,dy
PATH_A = [
    ((465,400),(515,320),(640,250),(755,270)),
    ((755,270),(850,285),(850,385),(790,430)),
    ((790,430),(700,495),(540,485),(465,400)),
]
PATH_B = [
    ((1000,520),(885,500),(756,475),(744,402)),
    ((744,402),(730,318),(807,230),(905,187)),
    ((905,187),(1025,134),(1150,205),(1130,338)),
    ((1130,338),(1112,465),(1058,530),(1000,520)),
]
def build_path_d(segs):
    s=segs[0][0]
    out=f'M {fmt(s[0])} {fmt(s[1])}'
    for _,p1,p2,p3 in segs:
        out += f' C {fmt(p1[0])} {fmt(p1[1])} {fmt(p2[0])} {fmt(p2[1])} {fmt(p3[0])} {fmt(p3[1])}'
    return out+' Z'
def sample_closed_path(segs, t, period):
    samples=[]
    total=0.0
    prev=None
    for si,seg in enumerate(segs):
        for j in range(101):
            if si>0 and j==0: continue
            u=j/100
            x,y,dx,dy=cubic(*seg,u)
            if prev is not None: total += math.hypot(x-prev[0],y-prev[1])
            samples.append((total,x,y,dx,dy))
            prev=(x,y)
    target=(t%period)/period*total
    for i in range(1,len(samples)):
        if samples[i][0] >= target:
            a=samples[i-1]; b=samples[i]
            f=(target-a[0])/(b[0]-a[0] or 1)
            x=a[1]+(b[1]-a[1])*f; y=a[2]+(b[2]-a[2])*f
            dx=a[3]+(b[3]-a[3])*f; dy=a[4]+(b[4]-a[4])*f
            return x,y,math.degrees(math.atan2(dy,dx))
    a=samples[-1]
    return a[1],a[2],math.degrees(math.atan2(a[4],a[3]))
def star_layer(t, smil):
    out=['<g id="distant-stars">']
    for cluster in range(4):
        items=[]
        for i in range(23):
            x=rng.uniform(28,1252); y=rng.uniform(26,694)
            r=rng.choices([0.8,1.0,1.3,1.7,2.0],[30,28,24,13,5])[0]
            color=rng.choice([PERI,STEEL,CREAM,PALE_GOLD,TEAL])
            op=rng.uniform(0.22,0.65)
            phase=rng.uniform(0,TAU); period=rng.choice([4,6,8,12])
            tw=0.5+0.5*math.sin(TAU*t/period+phase)
            items.append(f'<circle cx="{fmt(x)}" cy="{fmt(y)}" r="{r}" fill="{color}" opacity="{fmt(op*(0.62+0.38*tw))}">')
            if smil:
                items[-1]+=f'<animate attributeName="opacity" values="{fmt(op*.62)};{fmt(op)};{fmt(op*.62)}" dur="{period}s" begin="{fmt(phase/TAU*period)}s" repeatCount="indefinite"/></circle>'
            else: items[-1]+='</circle>'
        drift_x=[7,-9,5,-6][cluster]*math.sin(TAU*t/[24,12,8,6][cluster])
        drift_y=[-5,4,8,-3][cluster]*math.sin(TAU*t/[12,8,6,24][cluster]+cluster)
        if smil:
            dx=[7,-9,5,-6][cluster]; dy=[-5,4,8,-3][cluster]; dur=[24,12,8,6][cluster]
            out.append(f'<g opacity="0.94"><animateTransform attributeName="transform" type="translate" values="0,0;{dx},{dy};0,0" dur="{dur}s" repeatCount="indefinite"/>')
        else:
            out.append(f'<g opacity="0.94" transform="translate({fmt(drift_x)} {fmt(drift_y)})">')
        out.extend(items); out.append('</g>')
    out.append('</g>')
    out.append('<g id="mid-distance-stars">')
    for i in range(12):
        x=rng.uniform(60,1220); y=rng.uniform(50,670)
        phase=rng.uniform(0,TAU); period=rng.choice([6,8,12,24])
        amp=rng.uniform(8,22); tilt=rng.uniform(-1,1)
        if not smil:
            xx=x+amp*math.sin(TAU*t/period+phase)
            yy=y+amp*0.55*math.sin(TAU*t/period*1.2+phase+tilt)
        else: xx,yy=x,y
        col=rng.choice([STEEL,PERI,CREAM,PALE_GOLD])
        r=rng.choice([2.2,2.8,3.2])
        out.append(f'<g transform="translate({fmt(xx)} {fmt(yy)})" opacity="0.68">')
        if smil:
            dx=amp; dy=amp*.55
            out.append(f'<animateMotion path="M 0 0 Q {fmt(dx)} {fmt(-dy)} 0 0" dur="{period}s" repeatCount="indefinite"/>')
        out.append(f'<path d="M 0 {-r*1.5:.2f} L {r*.55:.2f} 0 L 0 {r*1.5:.2f} L {-r*.55:.2f} 0 Z" fill="{col}"/>')
        out.append('</g>')
    out.append('</g>')
    out.append('<g id="near-field-particles">')
    for i in range(6):
        x=rng.uniform(50,1230); y=rng.uniform(35,685)
        phase=rng.uniform(0,TAU); period=rng.choice([4,6,8,12]); amp=rng.uniform(24,55)
        if not smil:
            xx=x+amp*math.sin(TAU*t/period+phase)
            yy=y+amp*.6*math.sin(TAU*t/period*0.75+phase+1)
        else: xx,yy=x,y
        r=rng.choice([2.5,3,3.5]); col=rng.choice([TEAL,PALE_GOLD,CREAM])
        out.append(f'<g transform="translate({fmt(xx)} {fmt(yy)})"><circle r="{r}" fill="{col}" opacity="0.3"/><circle r="{r*.46}" fill="{col}" opacity="0.85"/>')
        if smil: out.append(f'<animateMotion path="M 0 0 Q {fmt(amp)} {fmt(-amp*.7)} 0 0" dur="{period}s" repeatCount="indefinite"/>')
        out.append('</g>')
    out.append('</g>')
    out.append('<g id="stellar-streaks" stroke-linecap="round">')
    for i in range(4):
        x=[120,390,890,1150][i]; y=[180,600,90,430][i]
        col=[CREAM,STEEL,PALE_GOLD,TEAL][i]; period=[6,8,12,24][i]
        phase=i*0.7
        if not smil:
            p=(t/period+phase/TAU)%1
            op=max(0,math.sin(math.pi*p))**5*.45
            xx=x+(p-.5)*90; yy=y+(p-.5)*28
            out.append(f'<path d="M {fmt(xx-18)} {fmt(yy-6)} l 36 12" stroke="{col}" stroke-width="1.5" opacity="{fmt(op)}"/>')
        else:
            out.append(f'<path d="M {x-18} {y-6} l 36 12" stroke="{col}" stroke-width="1.5" opacity="0">')
            out.append(f'<animate attributeName="opacity" values="0;0.45;0" keyTimes="0;0.12;1" dur="{period}s" begin="{fmt(phase)}s" repeatCount="indefinite"/>')
            out.append(f'<animateTransform attributeName="transform" type="translate" values="-45,-14;45,14" dur="{period}s" begin="{fmt(phase)}s" repeatCount="indefinite"/></path>')
    out.append('</g>')
    return ''.join(out)
def background_and_orbits(t, smil):
    out=[]
    out.append(f'''<rect width="1280" height="720" fill="url(#space-gradient)"/>
<rect width="1280" height="720" fill="url(#nebula-violet)" opacity="0.55"/>
<path d="M 120 210 C 300 110 390 190 510 150 S 780 110 900 190" fill="none" stroke="{MUTED_VIOLET}" stroke-width="28" opacity="0.035"/>
<path d="M 160 590 C 350 520 410 620 610 565 S 960 520 1150 610" fill="none" stroke="{VIOLET}" stroke-width="20" opacity="0.035"/>
<rect width="1280" height="720" fill="url(#halftone)" opacity="0.26"/>''')
    for gx,gy,gr in [(55,350,190),(1230,350,185)]:
        out.append(f'<g transform="translate({gx} {gy})" opacity="0.13" stroke="{COG}" fill="none">')
        out.append(f'<circle r="{gr}" stroke-width="2"/><circle r="{gr-22}" stroke-width="1" stroke-dasharray="3 12"/>')
        for k in range(36):
            a=TAU*k/36; x1,y1=polar(gr-3,a); x2,y2=polar(gr+([10,16,10,6][k%4]),a)
            out.append(f'<path d="M {fmt(x1)} {fmt(y1)} L {fmt(x2)} {fmt(y2)}" stroke-width="2"/>')
        out.append('</g>')
    out.append(f'<g id="orbital-tracks" fill="none" stroke="{ICE}" stroke-width="1.7" opacity="0.26">')
    for r,dash in [(138,'2 8'),(220,'4 8'),(300,'2 9')]:
        out.append(f'<circle cx="{CX}" cy="{CY}" r="{r}" stroke-dasharray="{dash}"/>')
    out.append('</g>')
    out.append(f'<g stroke="{GOLD}" stroke-width="2" opacity="0.52">')
    for r in (138,220,300):
        for deg in range(0,360,30):
            a=math.radians(deg); x1,y1=polar(r-4,a); x2,y2=polar(r+4,a)
            out.append(f'<path d="M {fmt(CX+x1)} {fmt(CY+y1)} L {fmt(CX+x2)} {fmt(CY+y2)}"/>')
    out.append('</g>')
    return ''.join(out)
def asteroid_belt(t, smil):
    out=[f'<g id="asteroid-belt" transform="translate({CX} {CY})">']
    colors=[ORANGE,AMBER,TEAL,STEEL,MAROON,CREAM,ICE]
    for band,(rmin,rmax,speed,dirn) in enumerate([(236,250,24,1),(252,267,12,-1),(269,282,8,1)]):
        angle0=band*0.7
        rot=dirn*360*t/speed
        if smil:
            out.append(f'<g id="asteroid-band-{band}" transform="rotate({fmt(angle0)} 0 0)"><animateTransform attributeName="transform" type="rotate" from="{fmt(angle0)} 0 0" to="{fmt(angle0+dirn*360)} 0 0" dur="{speed}s" repeatCount="indefinite"/>')
        else:
            out.append(f'<g id="asteroid-band-{band}" transform="rotate({fmt(angle0+rot)})">')
        for i in range(32):
            base_angle=TAU*(i+rng.random()*.7)/32 + rng.uniform(-.035,.035)
            rad=rng.uniform(rmin,rmax)
            x,y=polar(rad,base_angle)
            size=rng.uniform(2.1,5.6)
            sides=rng.choice([4,5,6,7])
            pts=[]
            for k in range(sides):
                a=TAU*k/sides+rng.uniform(-.22,.22)
                rr=size*rng.uniform(.68,1.25)
                pts.append((x+rr*math.cos(a),y+rr*math.sin(a)))
            d='M '+' L '.join(f'{fmt(px)} {fmt(py)}' for px,py in pts)+' Z'
            color=rng.choice(colors)
            opacity=rng.uniform(.42,.82)
            spin=rng.choice([-1,1]); phase=rng.uniform(0,360); spin_period=rng.choice([4,6,8,12])
            if not smil:
                out.append(f'<g transform="translate({fmt(x)} {fmt(y)}) rotate({fmt(phase+spin*360*t/spin_period)}) translate({fmt(-x)} {fmt(-y)})">')
            else:
                out.append(f'<g transform="rotate({fmt(phase)} {fmt(x)} {fmt(y)})"><animateTransform attributeName="transform" type="rotate" from="{fmt(phase)} {fmt(x)} {fmt(y)}" to="{fmt(phase+spin*360)} {fmt(x)} {fmt(y)}" dur="{fmt(spin_period)}s" repeatCount="indefinite"/>')
            out.append(f'<path d="{d}" fill="{color}" opacity="{fmt(opacity)}" stroke="{INK}" stroke-width="1.4" stroke-linejoin="round"/>')
            out.append('</g>')
        out.append('</g>')
    out.append('</g>')
    return ''.join(out)
def sun(t, smil):
    out=[f'<g id="central-sun" transform="translate({CX} {CY})">']
    out.append('<circle r="146" fill="url(#sun-aura)" opacity="0.62"/>')
    out.append('<g id="sun-corona">')
    for i in range(24):
        a=TAU*i/24
        inner=82 if i%2==0 else 90
        outer=118 if i%3==0 else (108 if i%2==0 else 104)
        p1=polar(inner,a-.065); p2=polar(outer,a); p3=polar(inner,a+.065)
        col=[GOLD,PALE_GOLD,ORANGE][i%3]
        d=f'M {fmt(p1[0])} {fmt(p1[1])} L {fmt(p2[0])} {fmt(p2[1])} L {fmt(p3[0])} {fmt(p3[1])} Z'
        out.append(f'<path d="{d}" fill="{col}" stroke="{INK}" stroke-width="3" stroke-linejoin="round"/>')
    out.append('</g>')
    out.append(f'<circle r="88" fill="{INK}" stroke="{GOLD}" stroke-width="3"/>')
    rot=360*t/32
    if smil:
        out.append(f'<g id="sun-gear-ring"><animateTransform attributeName="transform" type="rotate" from="0" to="360" dur="8s" repeatCount="indefinite"/>')
    else: out.append(f'<g id="sun-gear-ring" transform="rotate({fmt(rot)})">')
    out.append(f'<circle r="77" fill="{GOLD}" stroke="{INK}" stroke-width="4"/>')
    out.append(f'<circle r="67" fill="{ORANGE}" stroke="{INK}" stroke-width="3"/>')
    for i in range(16):
        a=TAU*i/16
        x1,y1=polar(68,a); x2,y2=polar(76,a)
        out.append(f'<path d="M {fmt(x1)} {fmt(y1)} L {fmt(x2)} {fmt(y2)}" stroke="{INK}" stroke-width="3" stroke-linecap="round"/>')
    out.append(f'<circle r="57" fill="url(#sun-core)" stroke="{INK}" stroke-width="3"/>')
    out.append('</g>')
    inner_rot=-360*t/12
    if smil: out.append('<g id="sun-clock-face"><animateTransform attributeName="transform" type="rotate" from="0" to="-360" dur="12s" repeatCount="indefinite"/>')
    else: out.append(f'<g id="sun-clock-face" transform="rotate({fmt(inner_rot)})">')
    out.append(f'<circle r="43" fill="none" stroke="{CREAM}" stroke-width="2"/>')
    for i in range(12):
        a=TAU*i/12; x1,y1=polar(32,a); x2,y2=polar(42,a)
        out.append(f'<path d="M {fmt(x1)} {fmt(y1)} L {fmt(x2)} {fmt(y2)}" stroke="{INK}" stroke-width="3" stroke-linecap="round"/>')
    out.append(f'<circle r="27" fill="{WARM_LIGHT}" stroke="{INK}" stroke-width="3"/>')
    out.append(f'<path d="M -4 0 L 0 -20 L 5 0 L 0 15 Z" fill="{CRIMSON}" stroke="{INK}" stroke-width="2"/>')
    out.append(f'<circle r="6" fill="{CREAM}" stroke="{INK}" stroke-width="2"/>')
    out.append('</g></g>')
    return ''.join(out)
def planet(t, smil, kind):
    if kind=='teal':
        radius=138; a0=math.radians(142); speed=24; pr=29
        out=[f'<g id="inner-orbit" transform="translate({CX} {CY})">']
        ang=math.degrees(a0)+(360*t/speed)
        if smil:
            out.append(f'<g transform="rotate({fmt(math.degrees(a0))})"><animateTransform attributeName="transform" type="rotate" from="{fmt(math.degrees(a0))}" to="{fmt(math.degrees(a0)+360)}" dur="{speed}s" repeatCount="indefinite"/>')
        else: out.append(f'<g transform="rotate({fmt(ang)})">')
        out.append(f'<g transform="translate({radius} 0)">')
        out.append(f'<circle r="{pr+3}" fill="{INK}" stroke="{INK}" stroke-width="3"/>')
        out.append(f'<circle r="{pr}" fill="url(#teal-planet)" stroke="{INK}" stroke-width="3"/>')
        out.append(f'<path d="M -24 -10 C -14 -31 15 -28 25 -5 C 7 -12 -6 -8 -12 6 C -18 14 -24 8 -24 -10 Z" fill="{MINT}" opacity="0.9"/>')
        out.append(f'<path d="M -3 2 C 10 0 25 9 23 22 C 10 34 -11 31 -20 14 C -15 7 -9 4 -3 2 Z" fill="{TEAL_DARK}" stroke="{INK}" stroke-width="1.3"/>')
        out.append(f'<path d="M -17 -15 Q -8 -25 2 -23" fill="none" stroke="{CREAM}" stroke-width="2.2" stroke-linecap="round" opacity="0.78"/>')
        if smil: out.append('<g id="teal-moon-orbit"><animateTransform attributeName="transform" type="rotate" from="0" to="360" dur="8s" repeatCount="indefinite"/>')
        else: out.append(f'<g id="teal-moon-orbit" transform="rotate({fmt(360*t/8)})">')
        out.append(f'<ellipse cx="0" cy="0" rx="48" ry="36" fill="none" stroke="{ICE}" stroke-width="1" stroke-dasharray="2 5" opacity="0.32"/>')
        out.append(f'<circle cx="45" cy="-5" r="6" fill="url(#ice-moon)" stroke="{INK}" stroke-width="1.5"/>')
        out.append('</g></g></g></g>')
        return ''.join(out)
    if kind=='orange':
        radius=220; a0=0; speed=24; pr=54
        out=[f'<g id="middle-orbit" transform="translate({CX} {CY})">']
        if smil: out.append('<g transform="rotate(0)"><animateTransform attributeName="transform" type="rotate" from="0" to="360" dur="24s" repeatCount="indefinite"/>')
        else: out.append(f'<g transform="rotate({fmt(360*t/speed)})">')
        out.append(f'<g transform="translate({radius} 0)">')
        out.append(f'<ellipse cx="0" cy="1" rx="86" ry="19" transform="rotate(-19)" fill="none" stroke="{TEAL_DARK}" stroke-width="8" opacity="0.92"/>')
        out.append(f'<ellipse cx="0" cy="-2" rx="91" ry="15" transform="rotate(-19)" fill="none" stroke="{GOLD}" stroke-width="5"/>')
        out.append(f'<circle r="{pr}" fill="url(#orange-planet)" stroke="{INK}" stroke-width="3.5"/>')
        out.append(f'<path d="M -43 -18 C -28 -40 18 -42 40 -21 C 23 -15 -19 -12 -43 -18 Z" fill="{WARM_LIGHT}" stroke="{INK}" stroke-width="1.7"/>')
        out.append(f'<path d="M -49 7 C -18 -1 20 1 49 9 C 34 23 -18 29 -45 17 Z" fill="{CRIMSON}" opacity="0.68"/>')
        out.append(f'<path d="M -31 -37 Q -13 -50 9 -48" fill="none" stroke="{CREAM}" stroke-width="3" stroke-linecap="round" opacity="0.88"/>')
        out.append(f'<path d="M 7 14 C 18 8 39 16 42 31 C 20 51 -11 52 -31 33 C -21 22 -7 19 7 14 Z" fill="{CRIMSON}" opacity="0.68"/>')
        out.append(f'<path d="M -77 9 C -36 29 36 29 77 9" transform="rotate(-19)" fill="none" stroke="{GOLD}" stroke-width="6" stroke-linecap="round"/>')
        out.append(f'<path d="M -69 15 C -30 34 30 34 69 15" transform="rotate(-19)" fill="none" stroke="{TEAL}" stroke-width="3" stroke-linecap="round"/>')
        out.append(f'<ellipse rx="78" ry="33" fill="none" stroke="{ICE}" stroke-width="1" stroke-dasharray="2 6" opacity="0.28"/>')
        out.append(f'<ellipse rx="103" ry="43" fill="none" stroke="{GOLD}" stroke-width="1" stroke-dasharray="2 8" opacity="0.2"/>')
        moon_specs=[(78,TAU*t/8,8,ICE,8,0.2),(103,-TAU*t/12+1.4,5,CREAM,12,1.4)]
        for mi,(rr,ang,moon_r,col,moon_period,phase) in enumerate(moon_specs):
            if smil:
                out.append('<g transform="scale(1 0.42)">')
                out.append(f'<g transform="rotate({fmt(math.degrees(phase))})">')
                out.append(f'<animateTransform attributeName="transform" type="rotate" from="{fmt(math.degrees(phase))}" to="{fmt(math.degrees(phase)+(360 if mi==0 else -360))}" dur="{moon_period}s" repeatCount="indefinite"/>')
                out.append(f'<circle cx="{rr}" cy="0" r="{moon_r}" fill="{col}" stroke="{INK}" stroke-width="2"/>')
                out.append(f'<path d="M {rr-moon_r*.5} {-moon_r*.3:.2f} q {moon_r*.5} {-moon_r*.8:.2f} {moon_r} 0" fill="none" stroke="{PAPER}" stroke-width="1.4" opacity="0.8"/>')
                out.append('</g></g>')
            else:
                mx=rr*math.cos(ang); my=rr*math.sin(ang)*.42
                out.append(f'<circle cx="{fmt(mx)}" cy="{fmt(my)}" r="{moon_r}" fill="{col}" stroke="{INK}" stroke-width="2"/>')
                out.append(f'<path d="M {fmt(mx-moon_r*.5)} {fmt(my-moon_r*.3)} q {fmt(moon_r*.5)} {-moon_r*.8} {fmt(moon_r)} 0" fill="none" stroke="{PAPER}" stroke-width="1.4" opacity="0.8"/>')
        out.append('</g></g></g>')
        return ''.join(out)
    if kind=='checker':
        radius=300; a0=-1.2; speed=24; pr=27
        ang=math.degrees(a0)+(360*t/speed)
        out=[f'<g id="outer-orbit" transform="translate({CX} {CY})">']
        if smil: out.append(f'<g transform="rotate({fmt(math.degrees(a0))})"><animateTransform attributeName="transform" type="rotate" from="{fmt(math.degrees(a0))}" to="{fmt(math.degrees(a0)+360)}" dur="24s" repeatCount="indefinite"/>')
        else: out.append(f'<g transform="rotate({fmt(ang)})">')
        out.append(f'<g transform="translate({radius} 0)">')
        out.append(f'<circle r="{pr+2}" fill="{INK}" stroke="{INK}" stroke-width="3"/>')
        out.append(f'<circle r="{pr}" fill="url(#checker-pattern)" stroke="{INK}" stroke-width="3"/>')
        out.append(f'<path d="M -19 -15 C -8 -31 15 -28 23 -10 L 20 20 C 2 33 -19 18 -22 0 Z" fill="{NAVY}" opacity="0.54"/>')
        out.append(f'<path d="M -15 -18 Q -3 -28 9 -24" fill="none" stroke="{CREAM}" stroke-width="2.3" stroke-linecap="round"/>')
        for si,(rr,aa,mr,period,phase) in enumerate([(44,TAU*t/8+0.2,4.5,8,0.2),(58,-TAU*t/12+1.8,3.5,12,1.8)]):
            if smil:
                out.append(f'<g transform="rotate({fmt(math.degrees(phase))})"><animateTransform attributeName="transform" type="rotate" from="{fmt(math.degrees(phase))}" to="{fmt(math.degrees(phase)+(360 if si==0 else -360))}" dur="{period}s" repeatCount="indefinite"/><circle cx="{rr}" cy="0" r="{mr}" fill="{STEEL}" stroke="{INK}" stroke-width="1.5"/></g>')
            else:
                mx=rr*math.cos(aa); my=rr*math.sin(aa)
                out.append(f'<circle cx="{fmt(mx)}" cy="{fmt(my)}" r="{mr}" fill="{STEEL}" stroke="{INK}" stroke-width="1.5"/>')
        out.append('</g></g></g>')
        return ''.join(out)
    raise ValueError(kind)
def rocket(t, smil, which):
    segs=PATH_A if which=='a' else PATH_B
    period=12 if which=='a' else 24
    if not smil:
        x,y,angle=sample_closed_path(segs,t,period)
        transform=f'translate({fmt(x)} {fmt(y)}) rotate({fmt(angle)})'
    else:
        transform='translate(0 0)'
    out=[f'<g id="rocket-{which}-system" transform="{transform}">']
    if smil:
        d=build_path_d(segs)
        out.append(f'<path d="{d}" fill="none" stroke="none"/>')
        out.append(f'<animateMotion path="{d}" dur="{period}s" rotate="auto" repeatCount="indefinite"/>')
    flame=1+0.18*math.sin(TAU*t/(1.2 if which=='a' else 1.5))
    out.append(f'<g id="rocket-{which}-exhaust">')
    out.append(f'<path d="M -21 -5 Q {-29-9*flame} -12 {-43-15*flame} 0 Q {-29-8*flame} 12 -21 5 Z" fill="{ORANGE}" stroke="{INK}" stroke-width="2"/>')
    out.append(f'<path d="M -20 -2.8 Q {-30-7*flame} -5 {-34-10*flame} 0 Q {-29-6*flame} 5 -20 2.8 Z" fill="{PALE_GOLD}"/>')
    out.append('</g>')
    out.append(f'<path d="M -25 -8 L 15 -7 L 29 0 L 15 7 L -25 8 L -16 0 Z" fill="url(#rocket-hull)" stroke="{INK}" stroke-width="2.8" stroke-linejoin="round"/>')
    out.append(f'<path d="M -10 -7 L 10 -6 L 22 0 L 10 6 L -10 6 Z" fill="{CREAM}" opacity="0.78"/>')
    out.append(f'<path d="M -13 -8 L -24 -17 L -22 -5 Z M -13 8 L -24 17 L -22 5 Z" fill="{TEAL_DARK}" stroke="{INK}" stroke-width="2.2" stroke-linejoin="round"/>')
    out.append(f'<path d="M 4 -6 L 13 -6 L 21 0 L 13 6 L 4 6 Z" fill="{GOLD}" stroke="{INK}" stroke-width="1.3"/>')
    out.append(f'<circle cx="-2" cy="0" r="3.2" fill="{TEAL}" stroke="{INK}" stroke-width="1.3"/>')
    out.append(f'<path d="M -15 -4 L -6 -4" stroke="{PAPER}" stroke-width="1.5" stroke-linecap="round"/>')
    out.append('</g>')
    return ''.join(out)
def black_hole():
    x,y=1080,560
    return f'''<g id="distant-black-hole" transform="translate({x} {y})">
<ellipse rx="49" ry="15" transform="rotate(-21)" fill="none" stroke="{TEAL}" stroke-width="4" opacity="0.55"/>
<ellipse rx="42" ry="12" transform="rotate(-21)" fill="none" stroke="{ORANGE}" stroke-width="5"/>
<ellipse rx="35" ry="9" transform="rotate(-21)" fill="none" stroke="{GOLD}" stroke-width="2"/>
<circle r="19" fill="{VOID}" stroke="{CREAM}" stroke-width="2.2"/>
<circle r="14" fill="{VOID}"/>
<circle cx="-3" cy="-2" r="2" fill="{CREAM}" opacity="0.3"/>
</g>'''
def make_svg(t=0, smil=False):
    rng.seed(20261119)
    defs=f'''<defs>
<radialGradient id="space-gradient" cx="50%" cy="50%" r="72%"><stop offset="0%" stop-color="{SPACE_LIFT}"/><stop offset="47%" stop-color="{SPACE_MID}"/><stop offset="100%" stop-color="{INK}"/></radialGradient>
<radialGradient id="nebula-violet" cx="32%" cy="24%" r="62%"><stop offset="0%" stop-color="{VIOLET}" stop-opacity="0.18"/><stop offset="56%" stop-color="{MUTED_VIOLET}" stop-opacity="0.07"/><stop offset="100%" stop-color="{MUTED_VIOLET}" stop-opacity="0"/></radialGradient>
<radialGradient id="sun-aura"><stop offset="0%" stop-color="{GOLD}" stop-opacity="0.32"/><stop offset="53%" stop-color="{ORANGE}" stop-opacity="0.16"/><stop offset="100%" stop-color="{ORANGE}" stop-opacity="0"/></radialGradient>
<radialGradient id="sun-core" cx="38%" cy="32%"><stop offset="0%" stop-color="{CREAM}"/><stop offset="42%" stop-color="{PALE_GOLD}"/><stop offset="100%" stop-color="{GOLD}"/></radialGradient>
<radialGradient id="teal-planet" cx="30%" cy="25%"><stop offset="0%" stop-color="{MINT}"/><stop offset="48%" stop-color="{TEAL}"/><stop offset="100%" stop-color="{TEAL_DARK}"/></radialGradient>
<radialGradient id="orange-planet" cx="30%" cy="25%"><stop offset="0%" stop-color="{WARM_LIGHT}"/><stop offset="55%" stop-color="{ORANGE}"/><stop offset="100%" stop-color="{CRIMSON}"/></radialGradient>
<radialGradient id="checker-planet" cx="32%" cy="25%"><stop offset="0%" stop-color="{CREAM}"/><stop offset="100%" stop-color="{ICE}"/></radialGradient>
<radialGradient id="ice-moon" cx="30%" cy="25%"><stop offset="0%" stop-color="{CREAM}"/><stop offset="100%" stop-color="{ICE}"/></radialGradient>
<linearGradient id="rocket-hull" x1="0" y1="-1" x2="0" y2="1"><stop offset="0%" stop-color="{PAPER}"/><stop offset="55%" stop-color="{CREAM}"/><stop offset="100%" stop-color="{SILVER}"/></linearGradient>
<pattern id="halftone" width="9" height="9" patternUnits="userSpaceOnUse" patternTransform="rotate(18)"><circle cx="4.5" cy="4.5" r="1.65" fill="{HALFTONE}" fill-opacity="0.23"/></pattern>
<pattern id="checker-pattern" width="12" height="12" patternUnits="userSpaceOnUse" patternTransform="rotate(22)"><rect width="12" height="12" fill="{MAROON}"/><rect width="6" height="6" fill="{CREAM}"/><rect x="6" y="6" width="6" height="6" fill="{CREAM}"/></pattern>
</defs>'''
    defs=defs.replace('url(#checker-planet)', 'url(#checker-pattern)')
    content=[defs,background_and_orbits(t,smil),star_layer(t,smil),asteroid_belt(t,smil),sun(t,smil)]
    content += [planet(t,smil,'teal'),planet(t,smil,'orange'),planet(t,smil,'checker')]
    content += [rocket(t,smil,'a'),rocket(t,smil,'b'),black_hole()]
    content.append(f'''<g id="frame-details" fill="none" stroke="{FRAME}" stroke-width="2" opacity="0.75">
<rect x="12" y="12" width="1256" height="696" rx="24"/>
<path d="M 34 70 V 34 H 70 M 1210 34 H 1246 V 70 M 34 650 V 686 H 70 M 1210 686 H 1246 V 650" stroke-width="3"/>
</g>''')
    aria='A mechanical solar system in a retro science fiction comic book style'
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="1280" height="720" viewBox="0 0 1280 720" role="img" aria-label="{aria}" preserveAspectRatio="xMidYMid meet">
{''.join(content)}
</svg>'''
from tempfile import TemporaryDirectory
import subprocess
OUT.mkdir(parents=True, exist_ok=True)
master = make_svg(0, True)
(OUT / 'clockwork-cosmos-final.svg').write_text(master, encoding='utf-8')
preview_svg = make_svg(0, False)
cairosvg.svg2png(
    bytestring=preview_svg.encode('utf-8'),
    write_to=str(OUT / 'clockwork-cosmos-final-preview.png'),
    output_width=1280,
    output_height=720,
)
with TemporaryDirectory(prefix='clockwork-cosmos-') as temp_dir:
    frame_dir = Path(temp_dir) / 'frames'
    frame_dir.mkdir()
    for frame in range(240):
        if frame % 40 == 0:
            print(f'Rendering frame {frame}/240', flush=True)
        svg = make_svg(frame / 10.0, False)
        cairosvg.svg2png(
            bytestring=svg.encode('utf-8'),
            write_to=str(frame_dir / f'frame{frame:03d}.png'),
            output_width=640,
            output_height=360,
        )
    palette = Path(temp_dir) / 'palette.png'
    ffmpeg = [
        'ffmpeg', '-hide_banner', '-loglevel', 'error',
        '-framerate', '10', '-i', str(frame_dir / 'frame%03d.png'),
        '-vf', 'palettegen=stats_mode=diff:max_colors=256',
        '-frames:v', '1', '-update', '1', '-y', str(palette),
    ]
    subprocess.run(ffmpeg, check=True)
    encode = [
        'ffmpeg', '-hide_banner', '-loglevel', 'error',
        '-framerate', '10', '-i', str(frame_dir / 'frame%03d.png'),
        '-i', str(palette),
        '-filter_complex', '[0:v][1:v]paletteuse=dither=sierra2_4a',
        '-loop', '0', '-y', str(OUT / 'clockwork-cosmos-final.gif'),
    ]
    subprocess.run(encode, check=True)
with Image.open(OUT / 'clockwork-cosmos-final.gif') as gif:
    if gif.size != (640, 360) or gif.n_frames != 240 or gif.info.get('loop') != 0:
        raise RuntimeError('GIF validation failed: expected 640x360, 240 frames, infinite loop')
    print(f'GIF validated: {gif.size}, {gif.n_frames} frames, {gif.info.get("duration")} ms/frame, loop={gif.info.get("loop")}')
print('Generated:', *(str(OUT / name) for name in [
    'clockwork-cosmos-final.svg',
    'clockwork-cosmos-final.gif',
    'clockwork-cosmos-final-preview.png',
]))
