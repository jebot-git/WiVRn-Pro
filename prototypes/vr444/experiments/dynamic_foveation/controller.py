# SPDX-License-Identifier: MIT
"""Offline bounded ROI policy. Angles require inverse-warp footprints from producer.

All timestamps MUST share a clock and target_ns must describe the intended display
time. Gaze.sample_ns is a known acquisition timestamp or synthetic ground truth;
XrEyeGazeSampleTimeEXT is an expressed pose time and MUST NOT be passed here as a
physical acquisition timestamp. Runtime-predicted samples are unsupported until a
separate uncertainty/clock contract avoids double prediction. Numeric uncertainty defaults are experiment settings, not calibrated Quest
measurements. No live device/transport is touched by this module.
"""
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class Gaze:
    x: float
    y: float
    sample_ns: int
    valid: bool = True


@dataclass(frozen=True)
class Config:
    core: float = 5.0
    guard: float = 2.0
    uncertainty: float = 0.75
    uncertainty_speed: float = 90.0
    transition: float = 18.0
    max_age_ms: float = 50.0
    velocity_cap: float = 900.0
    saccade_speed: float = 150.0
    predict_max_ms: float = 20.0
    predict_max_distance: float = 5.0
    peripheral_q: int = 128


def footprints(width, height, bounds=(-45., 45., -45., 45.), angular=True):
    """Rectilinear tangent-angle geometry, or explicitly linear pixel-space proxy.

    This is NOT the inverse of WiVRn's host warp. Captured host-warped frames must
    use angular=False and results carry domain='pixel_proxy'.
    """
    if any(type(v) is not int for v in (width, height)) or not (1 <= width <= 4096 and 1 <= height <= 4096 and width*height <= 4194304):
        raise ValueError('dimensions')
    left, right, bottom, top = bounds
    if not all(math.isfinite(v) and -89 < v < 89 for v in bounds) or left >= right or bottom >= top:
        raise ValueError('bounds')
    def interp(a, b, t):
        if not angular:
            return a + (b-a)*t
        return math.degrees(math.atan(math.tan(math.radians(a))*(1-t)+math.tan(math.radians(b))*t))
    return [(interp(left,right,x/width), interp(left,right,min(x+16,width)/width),
             interp(top,bottom,min(y+16,height)/height), interp(top,bottom,y/height))
            for y in range(0,height,16) for x in range(0,width,16)]


def projected_dot_extrema(x,y,tile):
    """Extrema against rectilinear rays normalize(tan(horizontal),tan(vertical),1).

    These are OpenXR-style tangent-plane coordinates, NOT yaw/elevation. Test
    corners, edge stationary points, and the interior aligned-gaze maximum.
    """
    l,r,b,t=(math.tan(math.radians(v)) for v in tile)
    gx,gy=math.tan(math.radians(x)),math.tan(math.radians(y))
    norm=math.sqrt(1+gx*gx+gy*gy)
    points=[(u,v) for u in (l,r) for v in (b,t)]
    for u in (l,r):
        denominator=gx*u+1
        if denominator:
            v=gy*(1+u*u)/denominator
            if b<=v<=t:points.append((u,v))
    for v in (b,t):
        denominator=gy*v+1
        if denominator:
            u=gx*(1+v*v)/denominator
            if l<=u<=r:points.append((u,v))
    if l<=gx<=r and b<=gy<=t:points.append((gx,gy))
    dots=[max(-1,min(1,(gx*u+gy*v+1)/(norm*math.sqrt(1+u*u+v*v)))) for u,v in points]
    return min(dots),max(dots)


def spherical_tile_distance(x,y,tile):
    """Exact minimum spherical eccentricity to a projected-angle tile."""
    return math.degrees(math.acos(projected_dot_extrema(x,y,tile)[1]))


def make_map(tiles, gaze, previous, target_ns, *, eye, geometry_id, domain='angular', config=Config()):
    if eye not in (0,1) or not isinstance(geometry_id,str) or not geometry_id or domain not in ('angular','pixel_proxy'):
        raise ValueError('identity/domain')
    if not 1 <= len(tiles) <= 16384:
        raise ValueError('tile count')
    for l,r,b,t in tiles:
        if not all(math.isfinite(v) and -89<v<89 for v in (l,r,b,t)) or l>r or b>t:
            raise ValueError('footprint')
    c=config
    if not all(math.isfinite(v) and 0<=v<=10000 for k,v in vars(c).items()) or not (0<c.core<=45 and 0<c.transition<=90 and 0<c.max_age_ms<=1000 and c.predict_max_ms<=c.max_age_ms):
        raise ValueError('config')
    if type(c.peripheral_q) is not int or c.peripheral_q not in (1,2,4,8,16,32,64,128,256,512,1024):
        raise ValueError('quantizer')
    def valid(g):
        return isinstance(g,Gaze) and g.valid and type(g.sample_ns) is int and 0<=g.sample_ns<(1<<63) and all(math.isfinite(v) and -89<v<89 for v in (g.x,g.y))
    if type(target_ns) is not int or not 0<=target_ns<(1<<63):
        raise ValueError('target timestamp')
    age=(target_ns-gaze.sample_ns)/1e6 if valid(gaze) else None
    fallback=age is None or age<0 or age>c.max_age_ms
    out=dict(eye=eye, geometry_id=geometry_id, domain=domain, sample_ns=gaze.sample_ns if valid(gaze) else None,
             target_ns=target_ns, age_ms=age, fallback=fallback, fallback_reason='invalid_or_stale' if fallback else None)
    if fallback:
        return dict(out,q=[1]*len(tiles),protected=[True]*len(tiles),radius=None,saccade=False,predicted=None,distances=None)
    vx=vy=0.0
    if valid(previous):
        dt=(gaze.sample_ns-previous.sample_ns)/1e9
        if .001<=dt<=.05:
            vx,vy=(gaze.x-previous.x)/dt,(gaze.y-previous.y)/dt
    speed=math.hypot(vx,vy)
    saccade=speed>=c.saccade_speed
    if speed>c.velocity_cap and speed:
        vx*=c.velocity_cap/speed;vy*=c.velocity_cap/speed
    dx,dy=vx*min(age,c.predict_max_ms)/1000,vy*min(age,c.predict_max_ms)/1000
    distance=math.hypot(dx,dy)
    if distance>c.predict_max_distance and distance:
        dx*=c.predict_max_distance/distance;dy*=c.predict_max_distance/distance
    predicted=(max(-88.99,min(88.99,gaze.x+dx)),max(-88.99,min(88.99,gaze.y+dy)))
    points=[(gaze.x,gaze.y),predicted]
    if saccade and valid(previous):
        points.append((previous.x,previous.y))
    left,right=min(p[0] for p in points),max(p[0] for p in points)
    bottom,top=min(p[1] for p in points),max(p[1] for p in points)
    # AABB corridor deliberately overprotects diagonals. The expansion covers a
    # configured bounded uncertainty model, not unbounded biological eye motion.
    radius=c.core+c.guard+c.uncertainty+age/1000*max(c.uncertainty_speed,min(speed,c.velocity_cap) if saccade else 0)
    if domain=='angular':
        # A spherical cap encloses the projected-angle corridor; compute its
        # radius from the exact minimum ray dot product over the whole rectangle.
        # Triangle inequality then gives a conservative lower bound to each tile.
        # This deliberately overprotects; Euclidean yaw/pitch can UNDERprotect
        # off-axis gaze and is not suitable for an angular lossless-core claim.
        center=((left+right)/2,(bottom+top)/2)
        corridor_cap_radius=math.degrees(math.acos(projected_dot_extrema(*center,(left,right,bottom,top))[0]))
        distances=[max(0,spherical_tile_distance(*center,tile)-corridor_cap_radius) for tile in tiles]
    else:
        distances=[math.hypot(max(left-r,l-right,0),max(bottom-t,b-top,0)) for l,r,b,t in tiles]
    protected=[d<=radius for d in distances]
    q=[]
    for d in distances:
        u=max(0,min(1,(d-radius)/c.transition))
        smooth=u*u*(3-2*u)
        # Floor towards higher quality; Q is monotonic in footprint distance.
        q.append(1<<min(int(math.floor(smooth*math.log2(c.peripheral_q))),int(math.log2(c.peripheral_q))))
    return dict(out,q=q,protected=protected,radius=radius,saccade=saccade,predicted=predicted,distances=distances)
