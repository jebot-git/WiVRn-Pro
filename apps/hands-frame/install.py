#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Install the optional OCR package independently of WiVRn and its client."""
from pathlib import Path
import argparse, json, os, shutil, subprocess, sys
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--prefix',type=Path,default=Path.home()/'.local/opt/wivrn-pro')
p.add_argument('--backend',choices=['openvino','cuda','rocm'],default='openvino')
p.add_argument('--models',type=Path,required=True,help='Directory containing the base OCR model files (no speech/translation weights)')
p.add_argument('--trained-models',type=Path,help='Optional trained recognition heads')
p.add_argument('--rocm-wheel',help='AMD ONNX Runtime MIGraphX wheel URL/path matching the installed ROCm release')
p.add_argument('--migrate-legacy',action='store_true',help='Copy existing OCR settings and DeepL key to Hands Frame; never delete old files')
a=p.parse_args();source=Path(__file__).resolve().parent
if a.backend=='rocm' and not a.rocm_wheel:p.error('ROCm requires --rocm-wheel from the matching AMD release (see README)')
if not a.models.is_dir():p.error('Base OCR model directory is missing')
if a.trained_models and not all((a.trained_models/name).is_file() for name in ['cjk.onnx','korean.onnx']):p.error('Trained OCR export must contain cjk.onnx and korean.onnx')
package=a.prefix.resolve()/'libexec/wivrn-pro/hands-frame-ocr';package.mkdir(parents=True,exist_ok=True)
shutil.copytree(source/'python',package/'python',dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__'))
shutil.copytree(a.models,package/'models/ocr',dirs_exist_ok=True)
if a.trained_models:shutil.copytree(a.trained_models,package/'models/ocr-trained',dirs_exist_ok=True)
for name in ['README.md','requirements.txt','requirements-openvino.txt','requirements-cuda.txt']:shutil.copy2(source/name,package/name)
subprocess.run(['uv','venv','--python','3.12','--relocatable','--allow-existing',str(package/'venv')],check=True)
requirements=source/('requirements-'+a.backend+'.txt' if a.backend!='rocm' else 'requirements.txt')
cmd=['uv','pip','install','--python',str(package/'venv/bin/python'),'-r',str(requirements)]
if a.backend=='rocm':cmd.append(a.rocm_wheel)
subprocess.run(cmd,check=True)
# The settings GUI uses the system Qt runtime; inference remains in the isolated venv.
launcher=a.prefix/'bin/wivrn-pro-hands-frame-settings';launcher.parent.mkdir(parents=True,exist_ok=True)
launcher.write_text('#!/bin/sh\nexec /usr/bin/python3 "$(dirname "$(readlink -f "$0")")/../libexec/wivrn-pro/hands-frame-ocr/python/gui.py" "$@"\n');launcher.chmod(0o755)
sys.path.insert(0,str(package/'python'))
from dataclasses import asdict
from settings import Settings,CONFIG,atomic_json,private_json
values=asdict(Settings.load())
if a.migrate_legacy:
    old=Path(os.environ.get('XDG_CONFIG_HOME',Path.home()/'.config'))/'spatial-subtitles'
    try:legacy=private_json(old/'settings.json')
    except FileNotFoundError:legacy={}
    for name in ['local_device','source_language','ocr_max_regions','ocr_detector_size']:
        if name in legacy:values[name]=legacy[name]
    if not CONFIG.with_name('hands-frame-deepl-key.json').exists() and (old/'deepl-key.json').exists():
        import deepl_translation
        deepl_translation.save_key(private_json(old/'deepl-key.json',4096)['key'])
values['ocr_backend']={'openvino':'OpenVINO','cuda':'CUDA','rocm':'ROCm'}[a.backend]
values['local_ocr_model']=str(package/'models/ocr-trained') if a.trained_models else ''
Settings.parse(values).save()
commit=subprocess.run(['git','rev-parse','HEAD'],cwd=source,capture_output=True,text=True).stdout.strip()
(package/'package.json').write_text(json.dumps({'source_commit':commit,'name':'wivrn-pro-hands-frame-ocr','backend':a.backend,'python':'3.12','speech':False,'local_translation':False},indent=2)+'\n')
print('Installed optional Hands Frame OCR package:',package)
