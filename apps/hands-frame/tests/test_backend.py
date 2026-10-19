# SPDX-License-Identifier: GPL-3.0-or-later
import sys,unittest,types
from pathlib import Path
from unittest import mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'python'))
from backend import Pipeline
from settings import Settings
from gpu_backend import select_ocr_device,ort_provider,ort_session
from PIL import Image

class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.pipeline=Pipeline();self.image=Image.new('RGB',(16,16));self.reader=mock.Mock()
        self.reader.return_value.image.return_value={'regions':[{'text':'recognized text'}]}
        self.patches=[mock.patch('backend.LocalRecognizer',self.reader),mock.patch('backend.Settings.load',return_value=Settings())]
        for p in self.patches:p.start();self.addCleanup(p.stop)
    def test_original_never_loads_api_credentials(self):
        with mock.patch('deepl_translation.DeepLTranslator') as deepl,mock.patch('google_translation.GoogleTranslator') as google:
            self.assertEqual(self.pipeline.recognize(self.image,'Original','DeepL API Free'),('recognized text','recognized text'))
            deepl.assert_not_called();google.assert_not_called()
    def test_selected_api_gets_text_and_credential_rotation_reloads_it(self):
        with mock.patch('deepl_translation.DeepLTranslator') as deepl,mock.patch('google_translation.GoogleTranslator') as google,mock.patch('deepl_translation.key_stamp',return_value=1) as stamp:
            deepl.return_value.translate_many.return_value=['translated'];google.return_value.translate_many.return_value=['google text']
            for _ in range(2):self.assertEqual(self.pipeline.recognize(self.image,'English','DeepL API Free'),('recognized text','translated'))
            deepl.assert_called_once_with();google.assert_not_called()
            deepl.return_value.translate_many.assert_called_with(['recognized text'],'English','Auto')
            stamp.return_value=2;self.pipeline.recognize(self.image,'English','DeepL API Free');self.assertEqual(deepl.call_count,2)
            self.assertEqual(self.pipeline.recognize(self.image,'English','Google Cloud Translation'),('recognized text','google text'))
            self.assertEqual(self.reader.call_count,1)
    def test_no_provider_fallback(self):
        with mock.patch('deepl_translation.DeepLTranslator',side_effect=ValueError('Add your DeepL key')),mock.patch('google_translation.GoogleTranslator') as google:
            with self.assertRaisesRegex(ValueError,'DeepL key'):self.pipeline.recognize(self.image,'English','DeepL API Free')
            google.assert_not_called()
        with self.assertRaises(ValueError):self.pipeline.recognize(self.image,'English','Local OpenVINO')
    def test_all_gpu_backend_choices_and_no_cpu_fallback(self):
        for backend in ['OpenVINO','CUDA','ROCm']:self.assertEqual(Settings.parse({'ocr_backend':backend}).ocr_backend,backend)
        ort=types.SimpleNamespace(get_available_providers=lambda:['CPUExecutionProvider'])
        with mock.patch.dict(sys.modules,{'onnxruntime':ort,'onnxruntime_ep_migraphx':None}):
            for backend in ['CUDA','ROCm']:
                with self.assertRaisesRegex(RuntimeError,'unavailable'):select_ocr_device(backend)
        for backend,provider in [('CUDA','CUDAExecutionProvider'),('ROCm','MIGraphXExecutionProvider'),('ROCm','ROCMExecutionProvider')]:
            ort.get_available_providers=lambda:[provider,'CPUExecutionProvider']
            with mock.patch.dict(sys.modules,{'onnxruntime':ort,'onnxruntime_ep_migraphx':None}):self.assertEqual(ort_provider(backend),provider)
    def test_vendor_sessions_select_gpu_index_and_disable_cpu_fallback(self):
        for backend,provider in [('CUDA','CUDAExecutionProvider'),('ROCm','MIGraphXExecutionProvider')]:
            ort=types.SimpleNamespace(get_available_providers=lambda:[provider],SessionOptions=mock.Mock(),InferenceSession=mock.Mock())
            ort.InferenceSession.return_value.get_providers.return_value=[provider,'CPUExecutionProvider']
            with mock.patch.dict(sys.modules,{'onnxruntime':ort,'onnxruntime_ep_migraphx':None}):
                session,actual=ort_session('/local/model.onnx',backend,2)
                self.assertEqual(actual,provider)
                ort.SessionOptions.return_value.add_session_config_entry.assert_called_once_with('session.disable_cpu_ep_fallback','1')
                self.assertEqual(ort.InferenceSession.call_args.kwargs['providers'],[(provider,{'device_id':'2'})])
                session.disable_fallback.assert_called_once()
                session.get_providers.return_value=['CPUExecutionProvider']
                with self.assertRaisesRegex(RuntimeError,'requested GPU provider'):ort_session('/local/model.onnx',backend,2)
    def test_speech_and_local_translation_settings_are_removed(self):
        for item in [{'local_speech_model':'/speech'},{'local_translation_model':'/translation'},{'translation_provider':'Local OpenVINO'}]:
            with self.assertRaises(ValueError):Settings.parse(item)
if __name__=='__main__':unittest.main()
