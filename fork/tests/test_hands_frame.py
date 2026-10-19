# SPDX-License-Identifier: GPL-3.0-or-later
import contextlib, errno, fcntl, importlib.util, io, json, os, shutil, struct, subprocess, tempfile, time, unittest
from pathlib import Path
from PIL import Image
from unittest import mock
SOURCE=Path(__file__).resolve().parents[2]/'server/wayvr/hands-frame-worker.py'
spec=importlib.util.spec_from_file_location('hands_frame_worker',SOURCE)
worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)
class HandsFrameTests(unittest.TestCase):
    def test_crop_projects_tangent_coordinates_and_clamps(self):
        image=Image.new('RGB',(100,100));image.paste((255,0,0),(25,25,75,75))
        fov=[-.7853981634,.7853981634,.7853981634,-.7853981634]
        crop=worker.crop_frame(image,fov,[-.4,-.4,.4,.4])
        self.assertEqual(crop.getpixel((20,20)),(255,0,0))
        self.assertEqual(worker.crop_frame(image,fov,[-2,-2,2,2]).size,(100,100))
        for bounds in [[0,0,0,1],[float('nan'),0,1,1],[2,2,3,3]]:
            with self.assertRaises(ValueError):worker.crop_frame(image,fov,bounds)
    def test_transport_rejects_stale_partial_and_symlinked_frames(self):
        with tempfile.TemporaryDirectory() as path:
            root=Path(path);frame=root/'hands-frame.rgba'
            def write(stamp):
                frame.write_bytes(worker.HEADER.pack(b'WVRHF001',16,16,stamp,-.7,.7,.7,-.7)+bytes(16*16*4));frame.chmod(0o600)
            write(time.monotonic_ns())
            self.assertEqual(worker.read_frame(root)[0].size,(16,16))
            write(time.monotonic_ns()-300_000_000)
            with self.assertRaises(worker.FrameUnavailable):worker.read_frame(root)
            write(time.monotonic_ns());frame.write_bytes(frame.read_bytes()[:-1])
            with self.assertRaises(worker.FrameUnavailable):worker.read_frame(root)
            frame.unlink();frame.symlink_to(root/'outside')
            with self.assertRaises(OSError):worker.read_frame(root)
    def test_panels_bookmark_pagination_and_close_keep_saved_files(self):
        with tempfile.TemporaryDirectory() as path, contextlib.redirect_stdout(io.StringIO()):
            root=Path(path);instance=worker.Worker(root)
            instance.add_result(Image.new('RGB',(16,16)),'x'*900,'original')
            instance.command({'op':'next'});self.assertEqual(instance.results[0]['page'],1)
            instance.command({'op':'bookmark'});self.assertTrue(instance.results[0]['bookmark'])
            instance.command({'op':'original'});self.assertTrue(instance.results[0]['show_original'])
            preview=instance.cache/instance.results[0]['preview'];self.assertEqual(preview.stat().st_mode&0o077,0)
            instance.command({'op':'close'});self.assertFalse(preview.exists())
            instance.pool.shutdown();os.close(instance.lock)
    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'),'FFmpeg required')
    def test_recording_is_vp9_webm_with_no_audio_and_even_dimensions(self):
        with tempfile.TemporaryDirectory() as path:
            output=Path(path)/'test.webm';image=Image.new('RGB',(65,49),(20,90,170))
            recording=worker.Recording(output,image)
            for _ in range(5): recording.add(image);time.sleep(.07)
            recording.finish()
            result=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(output)]))
            self.assertEqual([s['codec_type'] for s in result['streams']],['video'])
            self.assertEqual(result['streams'][0]['codec_name'],'vp9')
            self.assertEqual(result['streams'][0]['width']%2,0)
            self.assertEqual(result['streams'][0]['height']%2,0)
            self.assertIn('webm',result['format']['format_name'])
    def test_stop_does_not_require_a_new_frame(self):
        with tempfile.TemporaryDirectory() as path, contextlib.redirect_stdout(io.StringIO()):
            instance=worker.Worker(Path(path))
            instance.command({'op':'frame','time':time.monotonic(),'active':True,'crop':None,'event':'stop'})
            instance.pool.shutdown();os.close(instance.lock)

class CaptureReliabilityTests(unittest.TestCase):
    def frame(self, root, name='hands-frame.rgba'):
        path=root/name
        path.write_bytes(worker.HEADER.pack(b'WVRHF001',16,16,time.monotonic_ns(),-.7,.7,.7,-.7)+bytes([40,80,120,255])*256)
        path.chmod(0o600)
        return path
    @contextlib.contextmanager
    def instance(self):
        with tempfile.TemporaryDirectory() as path, contextlib.redirect_stdout(io.StringIO()):
            root=Path(path); instance=worker.Worker(root);instance.auto_scan=False
            try: yield root,instance
            finally:
                instance.recording=None;instance.pool.shutdown();instance.encoders.shutdown();os.close(instance.lock)
    def test_busy_frame_retries_photo_without_errno(self):
        with self.instance() as (root,instance):
            path=self.frame(root)
            with path.open('rb') as stream:
                fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
                instance.command({'op':'frame','enabled':True,'active':True,'time':time.monotonic(),'crop':[-.7,-.7,.7,.7],'event':'capture'})
                self.assertIsNotNone(instance.pending_capture)
                instance.tick()
                self.assertNotIn('Errno',instance.notice)
                self.assertEqual(len(instance.results),0)
            self.frame(root);instance.tick()
            self.assertIsNone(instance.pending_capture)
            self.assertEqual(len(instance.results),1)
    def test_busy_frame_does_not_abort_recording(self):
        with self.instance() as (root,instance):
            instance.active=True;instance.sample=time.monotonic();instance.crop=[-.7,-.7,.7,.7]
            recording=mock.Mock(started=time.monotonic());instance.recording=recording
            with mock.patch.object(instance,'image',side_effect=BlockingIOError(errno.EAGAIN,'busy')):
                instance.tick()
            self.assertIs(instance.recording,recording)
            self.assertEqual(instance.finish_id,0)
            recording.finish.assert_not_called()
    def test_recording_start_waits_for_busy_first_frame(self):
        with self.instance() as (root,instance):
            path=self.frame(root)
            with path.open('rb') as stream, mock.patch.object(worker,'Recording') as recording:
                recording.return_value.started=time.monotonic()
                fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
                instance.command({'op':'frame','active':True,'time':time.monotonic(),'crop':[-.7,-.7,.7,.7],'event':'record'})
                self.assertIsNotNone(instance.pending_capture)
                fcntl.flock(stream,fcntl.LOCK_UN)
                instance.tick()
                recording.assert_called_once()
                self.assertIs(instance.recording,recording.return_value)
    def test_stop_cancels_a_recording_waiting_for_its_first_frame(self):
        with self.instance() as (root,instance):
            instance.command({'op':'frame','active':True,'time':time.monotonic(),'crop':[-.7,-.7,.7,.7],'event':'record'})
            self.assertIsNotNone(instance.pending_capture)
            instance.command({'op':'frame','active':True,'time':time.monotonic(),'crop':[-.7,-.7,.7,.7],'event':'stop'})
            self.assertIsNone(instance.pending_capture)
            self.frame(root)
            with mock.patch.object(worker,'Recording') as recording:
                instance.tick()
                recording.assert_not_called()
    def test_headset_source_reads_jpeg_and_stops_lease_on_source_change(self):
        with self.instance() as (root,instance):
            import io
            encoded=io.BytesIO();Image.new('RGB',(1024,1024),(40,80,120)).save(encoded,format='JPEG')
            path=root/'hands-frame-headset.jpg'
            path.write_bytes(worker.HEADER.pack(b'WVRHFJ01',1024,1024,time.monotonic_ns(),-.7,.7,.7,-.7)+encoded.getvalue());path.chmod(0o600)
            instance.command({'op':'source'})
            instance.command({'op':'frame','enabled':True,'active':False,'time':time.monotonic()})
            instance.tick()
            lease=root/'hands-frame-headset.lease'
            self.assertEqual(lease.stat().st_mode&0o077,0)
            self.assertEqual(len(lease.read_bytes()),9)
            self.assertEqual(lease.read_bytes()[-1],0)
            image,fov,_=instance.read_source()
            self.assertEqual(image.size,(1024,1024))
            self.assertAlmostEqual(fov[1],worker.math.radians(41))
            instance.command({'op':'source'});instance.tick()
            self.assertFalse(lease.exists())
    def test_permission_status_is_shown_instead_of_missing_file_errno(self):
        with self.instance() as (root,instance):
            path=root/'hands-frame-headset.status';path.write_text('Accept screen-sharing permission in the headset');path.chmod(0o600)
            instance.source='headset'
            with self.assertRaisesRegex(worker.FrameUnavailable,'Accept screen-sharing'):
                instance.read_source()


class RecognitionTests(unittest.TestCase):
    def test_bookmark_shelf_uses_a_bookmarked_image_after_new_capture(self):
        with tempfile.TemporaryDirectory() as path, contextlib.redirect_stdout(io.StringIO()) as output:
            instance=worker.Worker(Path(path))
            instance.add_result(Image.new('RGB',(16,16)), 'first')
            first=instance.results[0]['preview']
            instance.command({'op':'bookmark'})
            instance.add_result(Image.new('RGB',(16,16)), 'second')
            instance.emit()
            status=json.loads(output.getvalue().splitlines()[-1])
            self.assertEqual(status['bookmark_preview'],first)
            self.assertNotEqual(status['bookmark_preview'],status['preview'])
            instance.pool.shutdown();instance.encoders.shutdown();os.close(instance.lock)
    def test_actual_qr_scan_returns_payload_without_opening_it(self):
        try:
            import zxingcpp, numpy as np
        except ImportError:
            self.skipTest('Install the Hands Frame scanning requirements')
        image=Image.fromarray(np.asarray(zxingcpp.write_barcode(zxingcpp.BarcodeFormat.QRCode,'https://example.test/frame')))
        self.assertEqual(worker.scan(image),['https://example.test/frame'])

if __name__=='__main__':unittest.main()
