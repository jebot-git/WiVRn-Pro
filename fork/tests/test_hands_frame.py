# SPDX-License-Identifier: GPL-3.0-or-later
import contextlib, importlib.util, io, json, os, shutil, struct, subprocess, tempfile, time, unittest
from pathlib import Path
from PIL import Image
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
            with self.assertRaises(ValueError):worker.read_frame(root)
            write(time.monotonic_ns());frame.write_bytes(frame.read_bytes()[:-1])
            with self.assertRaises(ValueError):worker.read_frame(root)
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
