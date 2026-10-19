# SPDX-License-Identifier: GPL-3.0-or-later
"""Google Cloud Translation Basic v2; API key is carried in a header."""
import html, http.client
from settings import CONFIG
import api_keys
from api_translation import TextAPI
KEY_FILE=CONFIG.with_name('hands-frame-google-key.json')
LANGUAGES={'English':'en','Serbian':'sr','Japanese':'ja','Korean':'ko','Chinese':'zh-CN','Thai':'th','Russian':'ru'}
def save_key(value):api_keys.save(KEY_FILE,value)
def read_key():return api_keys.read(KEY_FILE,'Google Cloud Translation')
def key_stamp():return api_keys.stamp(KEY_FILE)
class GoogleTranslator(TextAPI):
    name='Google';host='translation.googleapis.com';path='/language/translate/v2';languages=LANGUAGES
    def __init__(self,cancelled=None):super().__init__(read_key(),cancelled)
    def payload(self,texts,target,source):
        body={'q':texts,'target':self.languages[target],'format':'text'}
        if source!='Auto':body['source']=self.languages[source]
        return body
    def headers(self):return {'X-Goog-Api-Key':self._key,'Content-Type':'application/json','User-Agent':'WiVRn-HandsFrame/1.0'}
    def results(self,value):return [html.unescape(t['translatedText']) for t in value['data']['translations']]
