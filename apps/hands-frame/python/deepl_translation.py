# SPDX-License-Identifier: GPL-3.0-or-later
"""DeepL API Free; OCR images never leave the PC."""
import http.client
from settings import CONFIG
import api_keys
from api_translation import TextAPI
KEY_FILE=CONFIG.with_name('hands-frame-deepl-key.json')
LANGUAGES={'English':'EN','Serbian':'SR','Japanese':'JA','Korean':'KO','Chinese':'ZH','Thai':'TH','Russian':'RU'}
def save_key(value):api_keys.save(KEY_FILE,value)
def read_key():return api_keys.read(KEY_FILE,'DeepL API Free')
def key_stamp():return api_keys.stamp(KEY_FILE)
class DeepLTranslator(TextAPI):
    name='DeepL';host='api-free.deepl.com';path='/v2/translate';languages=LANGUAGES
    def __init__(self,cancelled=None):super().__init__(read_key(),cancelled)
    def payload(self,texts,target,source):
        body={'text':texts,'target_lang':self.languages[target],'model_type':'prefer_quality_optimized'}
        if source!='Auto':body['source_lang']=self.languages[source]
        return body
    def headers(self):return {'Authorization':'DeepL-Auth-Key '+self._key,'Content-Type':'application/json','User-Agent':'WiVRn-HandsFrame/1.0'}
    def results(self,value):return [t['text'] for t in value['translations']]
