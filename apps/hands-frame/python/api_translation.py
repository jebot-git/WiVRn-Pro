# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded, text-only translation over fixed HTTPS endpoints."""
from collections import OrderedDict
import http.client, json, time


class TextAPI:
    def __init__(self,key,cancelled=None):
        self._key=key;self.cancelled=cancelled;self.cache=OrderedDict();self.retry_at=0;self.error=''
    def translate(self,text,target,source='Auto'):
        return self.translate_many([text],target,source)[0]
    def translate_many(self,texts,target,source='Auto'):
        if target=='Original' or source==target:return list(texts)
        if target not in self.languages or source!='Auto' and source not in self.languages:raise ValueError('Unsupported '+self.name+' language')
        if len(texts)>50 or any(not isinstance(t,str) or len(t)>4096 for t in texts):raise ValueError('Translation text exceeds request limits')
        keys=[(t,target,source) for t in texts]
        missing=list(dict.fromkeys(k for k in keys if k[0].strip() and k not in self.cache))
        if missing:
            if time.monotonic()<self.retry_at:raise RuntimeError(self.error)
            if self.cancelled is not None and self.cancelled.is_set():raise RuntimeError('Translation settings changed')
            body=json.dumps(self.payload([k[0] for k in missing],target,source),ensure_ascii=False).encode()
            if len(body)>128*1024:raise ValueError('Translation request exceeds 128 KiB')
            connection=http.client.HTTPSConnection(self.host,timeout=4)
            try:
                connection.request('POST',self.path,body=body,headers=self.headers())
                response=connection.getresponse()
                if response.status!=200:
                    # Never log response bodies, submitted text or API keys. No redirects/retries.
                    reason={400:'rejected the language or request',401:'rejected the API key',403:'rejected the API key or access',429:'rate limit reached',456:'Free character quota exhausted'}.get(response.status,'is unavailable (HTTP '+str(response.status)+')')
                    raise RuntimeError(self.name+' '+reason)
                raw=response.read(256*1024+1)
                if len(raw)>256*1024:raise ValueError('Oversized response')
                values=self.results(json.loads(raw))
                if len(values)!=len(missing) or any(not isinstance(x,str) or not x.strip() for x in values):raise ValueError('Invalid translations')
                for key,text in zip(missing,values):self.cache[key]=text.strip()[:4096]
            except RuntimeError as error:
                self.error=str(error);self.retry_at=time.monotonic()+15;raise
            except (OSError,http.client.HTTPException,ValueError,KeyError,TypeError,AttributeError):
                self.error=self.name+' connection failed or returned an invalid response';self.retry_at=time.monotonic()+15
                raise RuntimeError(self.error) from None
            finally:connection.close()
        result=[self.cache[k] if k[0].strip() else k[0] for k in keys]
        for key in keys:
            if key in self.cache:self.cache.move_to_end(key)
        while len(self.cache)>256:self.cache.popitem(last=False)
        return result
