#!/usr/bin/env python3
"""Qualification protocol bridge. Refuses capabilities the live smoke cannot prove."""
import argparse
import json
from pathlib import Path
import subprocess
from live import PACKAGE, save, sha
ROOT=Path(__file__).resolve().parent

def probe(request):
    receipt_path=ROOT/'validation.json'
    receipt=json.loads(receipt_path.read_text()) if receipt_path.exists() else {}
    valid={}
    for mode, record in receipt.get('runs',{}).items():
        path=Path(record['summary'])
        valid[mode]=path.is_file() and sha(path)==record['sha256'] and json.loads(path.read_text()).get('status')=='pass'
    serial=receipt.get('serial','230YC01D8F01UE')
    result=subprocess.run(['adb','-s',serial,'get-state'],capture_output=True,text=True,timeout=10)
    connected=result.returncode==0 and result.stdout.strip()=='device'
    evidence_dir=Path(request['output_dir']).resolve();evidence_dir.mkdir(parents=True,exist_ok=True)
    try:
        vendor=Path('/sys/class/drm/renderD129/device/vendor').read_text().strip()
        device=Path('/sys/class/drm/renderD129/device/device').read_text().strip()
        host_ok=vendor=='0x8086' and device=='0x56a0'
    except OSError: vendor=device='unavailable';host_ok=False
    headset=subprocess.run(['adb','-s',serial,'shell','getprop','ro.product.model'],capture_output=True,text=True,timeout=10)
    client_ok=connected and headset.returncode==0 and headset.stdout.strip()=='Quest Pro'
    identity=evidence_dir/'target-identity.json'
    save(identity,{'host_render_node':'/dev/dri/renderD129','vendor':vendor,'device':device,'headset_serial':serial,'headset_model':headset.stdout.strip()})
    evidence=[{'path':str(identity),'sha256':sha(identity)}]
    capabilities={}
    for mode in ('hevc-carrier','vulkan-vr444'):
        capabilities[mode]={
            'wivrn_integrated':bool(valid.get(mode)),
            'dynamic_foveation':False,'atomic_frame_warp':bool(valid.get(mode)),
            'stereo_frame_ids':bool(valid.get(mode)),'clock_sync':False,
            'bounded_telemetry':False,'quality_capture':False,'network_bytes':False,
            'runtime_provenance':False,'rate_modes':[],'kernels':[],
            'gaze_patterns':[],'network_profiles':[],'lifecycle_tests':[], 'measurements':[],
            'supported_separate_profile':'suite.py live-integration',
            'scope':'576x640 per-eye correctness smoke, sparse content cadence, RGB capture before defoveation'}
    return {'schema':1,'provenance':'hardware' if connected and receipt else 'unverified',
            'host':{'model':'Intel Arc A770','verified':host_ok,'evidence':evidence},
            'client':{'model':'Meta Quest Pro','verified':client_ok,'evidence':evidence},
            'implementations':capabilities,'integration_receipt':receipt,
            'note':'Historical functional validation is not fresh full-suite readiness. Full qualification requires clock uncertainty, bounded telemetry, source-to-presented capture, wire accounting, corpus and optional optical rig.'}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=('probe','run','stop'))
    p.add_argument('--request',type=Path,required=True);p.add_argument('--response',type=Path,required=True)
    a=p.parse_args();request=json.loads(a.request.read_text())
    if request.get('schema')!=1: raise ValueError('schema 1 required')
    if a.action=='probe': result=probe(request)
    elif a.action=='run': result={'schema':1,'status':'blocked','run_id':request.get('run_id'), 'reason':'Requested qualification profile is not implemented; use the explicit live-integration correctness profile.'}
    else: result={'schema':1,'restored':True,'evidence':['This qualification adapter never starts sessions; live-integration owns and records its own cleanup.']}
    save(a.response,result)
if __name__=='__main__':main()
