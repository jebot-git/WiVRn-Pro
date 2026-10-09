"""On-demand deterministic RGB444 and timestamped gaze fixtures."""
import hashlib
import math
import numpy as np


def rgb_frame(scene, width, height, frame, eye, seed=444):
    if width < 16 or height < 16 or eye not in (0, 1) or frame < 0:
        raise ValueError("invalid geometry/frame/eye")
    y, x = np.indices((height, width))
    if scene == "rgb-ramp":
        rgb = np.stack((x*255//max(1,width-1), y*255//max(1,height-1), (x+y+eye*37)%256), -1)
    elif scene == "colour-text":
        # Fine coloured strokes, including one-pixel alternating chroma.
        stripes = ((x + eye) % 2)*255
        strokes = ((x//8 + y//12)%2)*255
        rgb = np.stack((stripes, 255-stripes, strokes), -1)
        rgb[y < height//4] = np.stack(((x*17)%256,(x*29)%256,(x*43)%256),-1)[y < height//4]
    elif scene == "moving-edges":
        edge = ((x-frame*7-eye*3)%width < width//3)
        rgb = np.stack((edge*255, ((y+frame*3)%height < height//3)*255, ((x+y+frame)%32<2)*255), -1)
    elif scene == "cut-noise":
        rng = np.random.default_rng(np.random.SeedSequence([seed, frame//45, eye]))
        rgb = rng.integers(0,256,(height,width,3),dtype=np.uint8)
    else:
        raise ValueError("unknown synthetic scene; vr-capture requires the hashed external corpus")
    return rgb.astype(np.uint8)


def gaze_sample(mode, frame, fps=90, start_ns=0):
    t = frame/fps
    angle = [0., 0.]
    if mode in ("pursuit", "asymmetric"):
        angle = [15*math.sin(2*math.pi*t/3), 8*math.cos(2*math.pi*t/4)]
    elif mode == "saccade":
        angle = [20 if (frame//18)%2 else -20, 0]
    elif mode == "outlier":
        angle = [1000 if frame%90 == 0 else 0, 0]
    elif mode not in ("fixation", "blink-dropout", "stale", "timestamp-reset"):
        raise ValueError("unknown gaze pattern")
    now = start_ns + round(t*1e9)
    stamp = now - (100000000 if mode == "stale" else 0)
    if mode == "timestamp-reset" and frame >= 90:
        stamp -= 1000000000
    valid = not (mode == "blink-dropout" and frame%180 < 18)
    eyes = [dict(angle_deg=angle, valid=valid, confidence=1. if valid else 0.)]
    eyes.append(dict(angle_deg=[angle[0]+(8 if mode=="asymmetric" else 0),angle[1]], valid=valid, confidence=1. if valid else 0.))
    return dict(frame=frame, sample_ns=stamp, expected_consume_ns=now, clock="fixture", eyes=eyes)


def quality(reference, decoded, zones):
    """Compare matching full RGB geometries. Zones come from recorded warp footprints.

    Do not substitute an arbitrary screen circle for an angular foveal mask.
    Report warp-only and codec-only comparisons separately at the adapter.
    """
    if reference.shape != decoded.shape or reference.ndim != 3 or reference.shape[-1] != 3 or reference.dtype != np.uint8 or decoded.dtype != np.uint8:
        raise ValueError("expected matching RGB8 images")
    if zones.shape != reference.shape[:2] or not np.isin(zones,[0,1,2]).all():
        raise ValueError("zones must be 0=periphery, 1=transition, 2=protected")
    result = {}
    for name, mask in (("all",np.ones(zones.shape,dtype=bool)),("protected",zones==2),("transition",zones==1),("periphery",zones==0)):
        n = int(mask.sum())
        if n == 0:
            result[name] = dict(pixels=0, measured=False)
            continue
        residual = decoded[mask].astype(np.float64)-reference[mask].astype(np.float64)
        mse = float(np.mean(residual**2))
        # RGB difference channels expose chroma damage hidden by luma PSNR.
        colour_error = np.stack((residual[:,0]-residual[:,1],residual[:,2]-residual[:,1]),-1)
        result[name] = dict(pixels=n, measured=True, mse=mse, exact=mse==0,
                            psnr_db=None if mse==0 else float(10*np.log10(255**2/mse)),
                            max_abs_error=int(np.abs(residual).max()),
                            colour_difference_rmse=float(np.sqrt(np.mean(colour_error**2))))
    result["reference_sha256"] = hashlib.sha256(reference.tobytes()).hexdigest()
    result["decoded_sha256"] = hashlib.sha256(decoded.tobytes()).hexdigest()
    return result
