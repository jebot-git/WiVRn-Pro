#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Private local capture worker. No microphone, uploads or automatic URL opening."""
import concurrent.futures, fcntl, json, math, os, queue, select, shutil, stat, struct, subprocess, sys, threading, time, uuid
from pathlib import Path
from PIL import Image
HEADER = struct.Struct('<8sIIQ4f')


def private(path, directory=False):
    info = path.lstat()
    if info.st_uid != os.getuid() or info.st_mode & 0o077 or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)):
        raise ValueError('Capture path must be private and owned by this user')


def read_frame(root):
    private(root, True)
    fd = os.open(root / 'hands-frame.rgba', os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        fcntl.flock(stream, fcntl.LOCK_SH | fcntl.LOCK_NB)
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError('Unsafe frame')
        raw = stream.read(HEADER.size)
        if len(raw) != HEADER.size: raise ValueError('Incomplete frame')
        magic, width, height, stamp, *fov = HEADER.unpack(raw)
        if magic != b'WVRHF001' or not 0 < width <= 8192 or not 0 < height <= 8192 or width * height * 4 > 128 * 1024**2:
            raise ValueError('Invalid frame size')
        if info.st_size != HEADER.size + width * height * 4 or not 0 <= time.monotonic_ns() - stamp < 250_000_000:
            raise ValueError('Stale or incomplete frame')
        if not all(math.isfinite(x) and abs(x) < 1.57 for x in fov) or not fov[0] < fov[1] or not fov[3] < fov[2]:
            raise ValueError('Invalid projection')
        pixels = stream.read(width * height * 4)
        if len(pixels) != width * height * 4: raise ValueError('Incomplete pixels')
    return Image.frombytes('RGBA', (width, height), pixels).convert('RGB'), fov, stamp


def crop_frame(image, fov, bounds, max_side=2048):
    if len(bounds) != 4 or not all(math.isfinite(v) for v in bounds): raise ValueError('Invalid frame bounds')
    left, right, top, bottom = map(math.tan, fov)
    x0, y0, x1, y1 = bounds  # Head-relative tangent coordinates; positive Y is up.
    if x1 - x0 < .02 or y1 - y0 < .02: raise ValueError('Frame is too small')
    w, h = image.size
    box = (max(0, math.floor((x0-left)/(right-left)*w)), max(0, math.floor((top-y1)/(top-bottom)*h)),
           min(w, math.ceil((x1-left)/(right-left)*w)), min(h, math.ceil((top-y0)/(top-bottom)*h)))
    if box[2]-box[0] < 8 or box[3]-box[1] < 8: raise ValueError('Frame is outside the view')
    result = image.crop(box)
    result.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    return result


def vp9_arguments(path, size, fps=15):
    return ['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-n', '-f', 'rawvideo', '-pixel_format', 'rgb24',
            '-video_size', f'{size[0]}x{size[1]}', '-framerate', str(fps), '-i', 'pipe:0', '-an', '-c:v', 'libvpx-vp9',
            '-deadline', 'realtime', '-cpu-used', '6', '-b:v', '0', '-crf', '32', '-pix_fmt', 'yuv420p', '-fs', '67108864', '-f', 'webm', str(path)]


class Recording:
    def __init__(self, path, image):
        image.thumbnail((1024, 1024))
        self.size = tuple(max(2, (s // 2) * 2) for s in image.size)
        self.path, self.started, self.frames = path, time.monotonic(), 0
        self.queue = queue.Queue(3)
        self.error = None
        self.last_index = -1
        self.process = subprocess.Popen(vp9_arguments(path, self.size), stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()
    def run(self):
        try:
            while True:
                value = self.queue.get()
                if value is None: break
                index, pixels = value
                while self.frames <= index:
                    self.process.stdin.write(pixels)
                    self.frames += 1
        except (BrokenPipeError, OSError) as error: self.error = str(error)
        finally:
            try: self.process.stdin.close()
            except OSError: pass
    def add(self, image):
        from PIL import ImageOps
        if self.process.poll() is not None: raise OSError('VP9 encoder stopped; check FFmpeg/libvpx support')
        pixels = ImageOps.fit(image, self.size, Image.Resampling.LANCZOS).tobytes()
        index = min(149, int((time.monotonic()-self.started)*15))
        if index <= self.last_index: return
        try: self.queue.put_nowait((index,pixels)); self.last_index = index
        except queue.Full: pass
    def finish(self):
        # This runs off the input/capture thread, including encoder backpressure.
        try:
            self.queue.put(None, timeout=2)
        except queue.Full:
            self.process.kill(); self.process.wait(); raise RuntimeError('Video encoder stopped accepting frames')
        self.thread.join(3)
        if self.thread.is_alive(): self.process.kill(); self.process.wait(); raise RuntimeError('Video encoder timed out')
        self.process.stdin = None
        try:
            _, error = self.process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill(); self.process.wait(); raise RuntimeError('Video encoder timed out')
        result = self.process.returncode
        if result or self.error or self.frames < 1: raise RuntimeError((error or b'VP9 recording failed').decode(errors='replace'))
        return self.path


def scan(image):
    import numpy as np
    try:
        import zxingcpp
        return [item.text for item in zxingcpp.read_barcodes(np.asarray(image)) if item.text][:8]
    except ImportError:
        import cv2
        text, _, _ = cv2.QRCodeDetector().detectAndDecode(np.asarray(image)[:, :, ::-1])
        return [text] if text else []


_vision_lock = threading.Lock()
_vision_cache = {}

def recognize(image, language):
    with _vision_lock:
        return _recognize(image, language)

def _recognize(image, language):
    # Reuse the installed local-only OCR/translation stack, never fetch models.
    here = Path(__file__).resolve()
    paths = [here.parents[2] / 'apps/spatial-subtitles/python', Path.home()/'.local/share/spatial-subtitles', Path('/usr/share/spatial-subtitles')]
    for path in paths:
        if (path/'local_recognition.py').is_file(): sys.path.insert(0, str(path)); break
    import contextlib
    with contextlib.redirect_stdout(sys.stderr):
        from settings import Settings
        from local_recognition import LocalRecognizer
        import numpy as np
        settings = Settings.load()
        key=(settings.local_device, settings.local_ocr_model)
        if _vision_cache.get('key') != key:
            _vision_cache.clear()
            _vision_cache['reader']=LocalRecognizer(device=settings.local_device, ocr_model=settings.local_ocr_model, download=False)
            _vision_cache['key']=key
        reader=_vision_cache['reader']
        result = reader.image(np.asarray(image))
        original = '\n'.join(region['text'] for region in result['regions'])
        translated = original
        if language != 'Original' and original:
            from translation import Translator
            target=(settings.local_translation_model,reader.device)
            if _vision_cache.get('translation_key')!=target:
                _vision_cache['translator']=Translator(target[0],target[1],reader.root/'cache')
                _vision_cache['translation_key']=target
            translated = _vision_cache['translator'].translate(original, language)
    return original, translated


class Worker:
    def __init__(self, root):
        self.root = root
        private(root, True)
        self.cache = root / 'hands-frame'
        self.cache.mkdir(mode=0o700, exist_ok=True)
        private(self.cache, True)
        self.lock = os.open(self.cache/'worker.lock', os.O_CREAT|os.O_RDWR|os.O_CLOEXEC|os.O_NOFOLLOW, 0o600)
        fcntl.flock(self.lock, fcntl.LOCK_EX|fcntl.LOCK_NB)
        self.results, self.current = [], -1
        self.crop, self.active, self.sample, self.last_stamp = None, False, 0., 0
        self.mode, self.repeat, self.auto_scan, self.language = 'photo', False, True, 'English'
        self.recording = None
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=2)
        self.encoders = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        self.jobs = []
        self.last_scan, self.last_capture = 0., 0.
        self.open_id = 0
        self.notice, self.revision, self.finish_id = 'Both grips to frame; left trigger changes mode; right trigger captures', 0, 0
        self.pending_capture = None
        self.closed = False
    def emit(self):
        item = self.results[self.current] if 0 <= self.current < len(self.results) else {}
        text = item.get('original' if item.get('show_original') else 'text', '')
        page = item.get('page', 0)
        pages = [text[i:i+420] for i in range(0, len(text), 420)] or ['']
        preview = item.get('preview', '')
        bookmarked = next((r for r in reversed(self.results) if r['bookmark']), {})
        if item.get('bookmark'): bookmarked=item
        print(json.dumps(dict(status=self.notice, revision=self.revision, preview=preview,
            bookmark_preview=bookmarked.get('preview',''), text=pages[min(page, len(pages)-1)], page=f'{page+1}/{len(pages)}', count=len(self.results),
            selected=self.current, bookmarked=item.get('bookmark', False), finish_id=self.finish_id,
            bookmarks=sum(r['bookmark'] for r in self.results), open_id=self.open_id, mode=self.mode, repeat=self.repeat, auto_scan=self.auto_scan, language=self.language)), flush=True)
    def add_result(self, image, text='', original='', path=None):
        key = uuid.uuid4().hex
        preview = self.cache/(key+'.png')
        image.save(preview)
        preview.chmod(0o600)
        item = dict(preview=preview.name, path=str(path or preview), text=text, original=original or text, page=0, bookmark=False)
        # Bound unsaved panels; bookmarked panels are retained until explicitly closed.
        if len(self.results) >= 16:
            protected = {job[2] for job in self.jobs if job[0]=='video'}
            if self.recording: protected.add(self.record_preview)
            candidates = [i for i,r in enumerate(self.results) if not r['bookmark'] and r['preview'] not in protected]
            if not candidates: raise ValueError('Sixteen bookmarked panels; close one before capturing')
            self.remove(candidates[0])
        self.results.append(item); self.current = len(self.results)-1
        self.revision += 1; self.notice = 'Preview ready · Save keeps the file in Pictures/WiVRn-Pro/Hands Frame'
    def remove(self, index):
        item = self.results.pop(index)
        for value in [self.cache/item['preview'], Path(item['path'])]:
            if value.parent == self.cache: value.unlink(missing_ok=True)
        self.current = min(index, len(self.results)-1)
        self.revision += 1
    def capture(self, image):
        if self.mode == 'photo': self.add_result(image)
        elif len(self.jobs) < 2:
            if self.mode == 'text': self.jobs.append(('text', self.pool.submit(recognize, image.copy(), self.language), image.copy()))
            else: self.jobs.append(('scan', self.pool.submit(scan, image.copy()), image.copy()))
            self.notice = 'Recognizing locally…'
        else: self.notice = 'Recognition is busy; retry shortly'
    def stop_recording(self):
        if self.recording:
            recording, self.recording = self.recording, None
            self.jobs.append(('video', self.encoders.submit(recording.finish), self.record_preview))
            self.notice = 'Finishing silent VP9 WebM…'
    def command(self, value):
        kind = value.get('op')
        if kind == 'frame':
            self.sample = value.get('time', 0.)
            self.active = bool(value.get('active')) and 0 <= time.monotonic()-self.sample < .25
            self.crop = value.get('crop')
            self.mode = value.get('mode', 'photo')
            if self.mode not in ('photo','text','scan'): raise ValueError('Unknown mode')
            self.repeat = bool(value.get('repeat'))
            if not self.active: self.stop_recording()
            if self.active and value.get('event'):
                if value['event'] == 'stop': self.stop_recording(); return
                try:
                    image = self.image()
                except (OSError,ValueError):
                    if value['event']=='capture': self.pending_capture=(time.monotonic()+.6,list(self.crop),self.mode);self.notice='Waiting for a fresh VR frame…';return
                    raise
                if value['event'] == 'record':
                    if self.mode != 'photo' or self.recording is not None: return
                    if any(job[0]=='video' for job in self.jobs): raise ValueError('Previous recording is still finishing')
                    self.add_result(image); self.record_preview = self.results[self.current]['preview']
                    self.recording = Recording(self.cache/(uuid.uuid4().hex+'.webm'), image.copy())
                    self.recording.add(image)
                elif value['event'] == 'capture': self.capture(image)
                elif value['event'] == 'stop': self.stop_recording()
        elif kind == 'cancel': self.active = False; self.pending_capture=None; self.stop_recording()
        elif kind == 'quit': self.closed = True; self.active = False; self.stop_recording()
        elif kind == 'auto_scan': self.auto_scan = not self.auto_scan
        elif kind == 'language':
            languages = ['English','Japanese','Korean','Chinese','Thai','Serbian','Russian','Original']
            self.language = languages[(languages.index(self.language)+1)%len(languages)]
        elif kind == 'bookmarks':
            indices = [i for i,r in enumerate(self.results) if r['bookmark']]
            if indices: self.current = next((i for i in indices if i > self.current), indices[0]); self.revision += 1; self.open_id += 1
        elif self.current >= 0:
            item = self.results[self.current]
            if kind == 'save':
                source = Path(item['path'])
                directory = Path.home()/'Pictures/WiVRn-Pro/Hands Frame'/time.strftime('%Y-%m')
                directory.mkdir(parents=True, exist_ok=True)
                target = directory/(time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8]+source.suffix)
                if self.recording or any(job[0]=='video' for job in self.jobs): raise ValueError('Wait for the WebM encoder to finish before saving')
                with source.open('rb') as src, os.fdopen(os.open(target, os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_CLOEXEC,0o600),'wb') as dst: shutil.copyfileobj(src,dst)
                if source.parent==self.cache and source.name!=item['preview']: source.unlink(missing_ok=True)
                item['path'] = str(target); self.notice = f'Saved {target}'
            elif kind == 'bookmark': item['bookmark'] = not item['bookmark']; self.notice = 'Panel bookmarked' if item['bookmark'] else 'Bookmark removed'
            elif kind == 'close':
                if self.recording or any(job[0]=='video' for job in self.jobs): raise ValueError('Finish the recording before closing its preview')
                self.remove(self.current)
            elif kind == 'original': item['show_original'] = not item.get('show_original', False); item['page'] = 0
            elif kind in ('next','previous'):
                text = item.get('original' if item.get('show_original') else 'text', '')
                page = item['page'] + (1 if kind == 'next' else -1)
                if 0 <= page < max(1, math.ceil(len(text)/420)): item['page'] = page
                else: self.current = (self.current+(1 if kind=='next' else -1)) % len(self.results)
                self.revision += 1
            elif kind == 'copy':
                text = item.get('original' if item.get('show_original') else 'text', '')
                command = ['wl-copy','--type','text/plain;charset=utf-8'] if text else ['wl-copy','--type','image/png']
                subprocess.run(command,input=text.encode() if text else (self.cache/item['preview']).read_bytes(),check=True,timeout=3)
                self.notice = 'Copied to clipboard'
            elif kind == 'open':
                text = item.get('text','').strip()
                from urllib.parse import urlsplit
                target = text if urlsplit(text).scheme.lower() in ('https','http') and '\n' not in text else item['path']
                subprocess.Popen(['xdg-open',target],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            self.revision += 1
        self.emit()
    def image(self):
        image, fov, stamp = read_frame(self.root)
        self.last_stamp = stamp
        return crop_frame(image,fov,self.crop)
    def tick(self):
        now = time.monotonic()
        if not 0 <= now-self.sample < .25:
            self.active = False; self.stop_recording()
        if self.pending_capture:
            deadline,bounds,mode=self.pending_capture
            if now>deadline or not self.active:
                self.pending_capture=None; self.notice='No fresh VR frame; retry while the game is visible'
            else:
                try:
                    image,fov,_=read_frame(self.root)
                    old_mode=self.mode;self.mode=mode;self.capture(crop_frame(image,fov,bounds));self.mode=old_mode
                    self.pending_capture=None
                except (OSError,ValueError): pass
        if self.active:
            try:
                image = self.image()
                if self.recording:
                    self.recording.add(image)
                    self.notice = f'Recording silent VP9 WebM · {now-self.recording.started:.1f}/10 s'
                    if now-self.recording.started >= 10: self.stop_recording(); self.finish_id += 1; self.active = False
                elif self.auto_scan and now-self.last_scan >= .5 and len(self.jobs)<2:
                    self.last_scan = now; self.jobs.append(('auto',self.pool.submit(scan,image.copy()),image.copy()))
            except (OSError, ValueError) as error:
                self.notice = str(error)
                if self.recording: self.stop_recording(); self.finish_id += 1
        for kind, future, image in self.jobs[:]:
            if not future.done(): continue
            self.jobs.remove((kind,future,image))
            try:
                value = future.result()
                if kind == 'text': self.add_result(image, value[1], value[0])
                elif kind in ('scan','auto'):
                    if value and (kind != 'auto' or self.active): self.add_result(image, '\n'.join(value)); self.finish_id += 1; self.active = False
                    elif kind == 'scan': self.notice = 'No 2D code found in the frame'
                elif kind == 'video':
                    index = next((i for i,r in enumerate(self.results) if r['preview']==image),None)
                    if index is not None: self.results[index]['path'] = str(value); self.current = index; self.revision += 1
                    self.notice = 'Silent VP9 WebM ready · Save to keep it; Open to play'
            except Exception as error:
                self.notice = f'{kind}: {error}'
        self.emit()


def main():
    root = Path(os.environ['XDG_RUNTIME_DIR'])/'wivrn-pro'
    worker = Worker(root)
    try:
        pending = bytearray()
        deadline = time.monotonic()
        while not worker.closed:
            readable,_,_ = select.select([sys.stdin],[],[],max(0,deadline-time.monotonic()))
            if readable:
                block = os.read(sys.stdin.fileno(),65536)
                if not block: break
                pending.extend(block)
                if len(pending)>131072: raise ValueError('Command buffer overflow')
                while b'\n' in pending:
                    line,_,rest = pending.partition(b'\n'); pending=bytearray(rest)
                    try:
                        if len(line)>16384: raise ValueError('Command too large')
                        worker.command(json.loads(line))
                    except Exception as error: worker.notice = str(error); worker.emit()
            if time.monotonic()>=deadline:
                worker.tick(); deadline=time.monotonic()+1/15
    finally:
        worker.stop_recording()
        worker.encoders.shutdown(wait=True, cancel_futures=True)
        worker.pool.shutdown(wait=True, cancel_futures=True)
        for item in list(worker.results):
            for path in [worker.cache/item['preview'],Path(item['path'])]:
                if path.parent==worker.cache: path.unlink(missing_ok=True)
if __name__ == '__main__': main()
