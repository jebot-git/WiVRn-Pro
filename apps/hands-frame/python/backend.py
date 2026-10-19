#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Optional Hands Frame OCR package. Private request/reply pipes, no audio."""
import base64, contextlib, io, json, sys
from PIL import Image
from settings import Settings
from local_recognition import LocalRecognizer

class Pipeline:
    def __init__(self):self.reader=None;self.reader_key=None;self.translator=None;self.translation_key=None
    def recognize(self,image,language,provider):
        from settings import PROVIDERS
        if provider not in PROVIDERS:raise ValueError('Choose Google Cloud Translation or DeepL API Free')
        s=Settings.load()
        key=(s.ocr_backend,s.local_device,s.gpu_device_id,s.local_ocr_model,s.ocr_detector_size)
        if key!=self.reader_key:
            self.reader=LocalRecognizer(device=s.local_device,ocr_model=s.local_ocr_model,backend=s.ocr_backend,device_id=s.gpu_device_id)
            self.reader_key=key
        self.reader.source_language=s.source_language;self.reader.max_regions=s.ocr_max_regions;self.reader.detector_size=s.ocr_detector_size
        import numpy as np
        result=self.reader.image(np.asarray(image));original='\n'.join(region['text'] for region in result['regions'])
        translated=original
        if original and language!='Original':
            if provider=='DeepL API Free':from deepl_translation import DeepLTranslator as Translator, key_stamp
            else:from google_translation import GoogleTranslator as Translator, key_stamp
            target=(provider,key_stamp())
            if self.translation_key!=target:
                translator=Translator();self.translator=translator;self.translation_key=target
            # Translate individual regions so crowded frames stay within API limits.
            translated='\n'.join(self.translator.translate_many([r['text'] for r in result['regions']],language,s.source_language))
        return original,translated

def main():
    pipeline=Pipeline()
    while True:
        line=sys.stdin.buffer.readline(16*1024*1024+1)
        if not line:return
        if len(line)>16*1024*1024:raise ValueError('OCR request too large')
        try:
            request=json.loads(line)
            with contextlib.redirect_stdout(sys.stderr):
                data=base64.b64decode(request['png'],validate=True)
                with Image.open(io.BytesIO(data)) as image:
                    if image.format!='PNG' or max(image.size)>2048:raise ValueError('Invalid OCR image')
                    original,text=pipeline.recognize(image.convert('RGB'),request['language'],request['provider'])
            response={'original':original,'text':text}
        except Exception as error:response={'error':str(error)[:240]}
        print(json.dumps(response,ensure_ascii=True),flush=True)
if __name__=='__main__':main()
