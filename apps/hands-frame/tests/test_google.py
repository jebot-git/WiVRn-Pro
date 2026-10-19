# SPDX-License-Identifier: GPL-3.0-or-later
import json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'python'))
import google_translation as api
class GoogleTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        p=patch.object(api,'KEY_FILE',Path(self.tmp.name)/'key.json');p.start();self.addCleanup(p.stop)
        api.save_key('test-only-not-a-credential')
        self.client=api.GoogleTranslator();p=patch.object(api.http.client,'HTTPSConnection');self.conn=p.start();self.addCleanup(p.stop)
        self.response=self.conn.return_value.getresponse.return_value;self.response.status=200
        self.response.read.return_value=json.dumps({'data':{'translations':[{'translatedText':'Hello &amp; world'}]}}).encode()
    def test_fixed_endpoint_header_plain_text_and_cache(self):
        self.assertEqual(self.client.translate('こんにちは','English'),'Hello & world')
        self.conn.assert_called_once_with('translation.googleapis.com',timeout=4)
        args,kwargs=self.conn.return_value.request.call_args
        self.assertEqual(args,('POST','/language/translate/v2'))
        self.assertEqual(kwargs['headers']['X-Goog-Api-Key'],'test-only-not-a-credential')
        self.assertEqual(json.loads(kwargs['body']),{'q':['こんにちは'],'target':'en','format':'text'})
        self.client.translate('こんにちは','English');self.conn.assert_called_once()
    def test_original_and_empty_skip_network(self):
        self.assertEqual(self.client.translate('hello','Original'),'hello');self.assertEqual(self.client.translate_many([],'English'),[]);self.conn.assert_not_called()
    def test_redirect_auth_and_quota_are_redacted_and_not_retried(self):
        for status in [302,401,403,429]:
            self.client=api.GoogleTranslator();self.conn.reset_mock();self.response.status=status
            for _ in range(2):
                with self.assertRaises(RuntimeError) as error:self.client.translate('private text','English')
                self.assertNotIn('private text',str(error.exception));self.assertNotIn('test-only',str(error.exception))
            self.conn.assert_called_once();self.response.read.assert_not_called()
if __name__=='__main__':unittest.main()
