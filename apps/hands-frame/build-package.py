#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Create a separate, relocatable optional OCR package from an installed runtime."""
import argparse,hashlib,json,tarfile
from pathlib import Path
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--prefix',type=Path,default=Path.home()/'.local/opt/wivrn-pro');p.add_argument('--output',type=Path,required=True);a=p.parse_args()
root=a.prefix/'libexec/wivrn-pro/hands-frame-ocr'
assert (root/'venv/bin/python').exists() and (root/'models/ocr').is_dir() and (root/'python/backend.py').is_file()
assert not any('whisper' in x.name or 'translation-selected' in x.name or x.name in ('hands-frame-google-key.json','hands-frame-deepl-key.json','hands-frame.json') for x in root.rglob('*'))
a.output.parent.mkdir(parents=True,exist_ok=True)
with tarfile.open(a.output,'w:gz') as archive:
    archive.add(root,arcname='libexec/wivrn-pro/hands-frame-ocr',filter=lambda info:None if '__pycache__' in info.name.split('/') else info)
    launcher=a.prefix/'bin/wivrn-pro-hands-frame-settings'
    archive.add(launcher,arcname='bin/wivrn-pro-hands-frame-settings')
digest=hashlib.file_digest(a.output.open('rb'),'sha256').hexdigest()
a.output.with_suffix(a.output.suffix+'.sha256').write_text(digest+'  '+a.output.name+'\n')
print(a.output,'('+str(a.output.stat().st_size)+' bytes)')
