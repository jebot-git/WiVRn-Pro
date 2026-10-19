# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit GPU selection for RapidOCR; never silently fall back to CPU."""
from pathlib import Path


def ort_provider(backend):
    import onnxruntime as ort
    if backend == 'ROCm':
        # New ROCm releases package MIGraphX as a separately registered ORT EP.
        try:
            import onnxruntime_ep_migraphx as plugin
        except ImportError:
            pass
        else:
            for name,path in zip(plugin.get_ep_names(),plugin.get_library_paths()):
                if name not in ort.get_available_providers():ort.register_execution_provider_library(name,path)
    names=('CUDAExecutionProvider',) if backend=='CUDA' else ('MIGraphXExecutionProvider','ROCMExecutionProvider')
    for name in names:
        if name in ort.get_available_providers():return name
    raise RuntimeError(f'{backend} OCR is unavailable: install its GPU runtime in the Hands Frame environment')


def select_ocr_device(backend, requested='A770', device_id=0):
    if backend == 'OpenVINO':
        import openvino as ov
        core=ov.Core();devices={d:core.get_property(d,'FULL_DEVICE_NAME') for d in core.available_devices if d.startswith('GPU')}
        if requested == 'A770':
            matches=[d for d,name in devices.items() if 'A770' in name]
            if len(matches)!=1:raise RuntimeError('Select an available OpenVINO GPU in Hands Frame settings')
            requested=matches[0]
        if requested not in devices:raise RuntimeError('Requested OpenVINO GPU is unavailable; CPU fallback is disabled')
        return requested,devices[requested]
    if backend not in ('CUDA','ROCm'):raise ValueError('Unsupported OCR GPU backend')
    if type(device_id) is not int or not 0<=device_id<=31:raise ValueError('Invalid OCR GPU device ID')
    return f'{backend}:{device_id}',ort_provider(backend)


def ort_session(path,backend,device_id):
    import onnxruntime as ort
    provider=ort_provider(backend)
    options=ort.SessionOptions();options.add_session_config_entry('session.disable_cpu_ep_fallback','1')
    session=ort.InferenceSession(str(path),sess_options=options,providers=[(provider,{'device_id':str(device_id)})])
    session.disable_fallback()
    actual=session.get_providers()
    if not actual or actual[0]!=provider:raise RuntimeError('OCR did not initialize the requested GPU provider')
    return session,provider


def install_adapter(owner):
    from rapidocr.inference_engine.base import FileInfo
    if owner.backend == 'OpenVINO':
        import rapidocr.inference_engine.openvino as adapter
        base=adapter.OpenVINOInferSession
    else:
        import rapidocr.inference_engine.onnxruntime as adapter
        base=adapter.OrtInferSession
    # Store the upstream class once; repeated model/settings reloads replace the
    # adapter without building a chain of subclasses that retain prior readers.
    if not hasattr(adapter,'_hands_frame_base'):adapter._hands_frame_base=base
    class GPUInferSession(adapter._hands_frame_base):
        def __init__(self,cfg):
            path=cfg.get('model_path')
            if not path:
                info=self.get_model_url(FileInfo(cfg.engine_type,cfg.ocr_version,cfg.task_type,cfg.lang_type,cfg.model_type))
                path=Path(cfg.model_root_dir)/Path(info['model_dir']).name
            if not Path(path).is_file():raise RuntimeError('OCR model missing; install the complete Hands Frame OCR package')
            if owner.backend == 'OpenVINO':
                import openvino as ov
                core=ov.Core();self.model=core.read_model(str(path))
                compiled=core.compile_model(self.model,owner.device,{'PERFORMANCE_HINT':'LATENCY','CACHE_DIR':str(owner.root/'cache')})
                actual=compiled.get_property('EXECUTION_DEVICES')
                if not actual or any(d!=owner.device for d in actual):raise RuntimeError('OCR did not compile on the requested GPU')
                owner.compiled_devices.append(list(actual));self.session=compiled.create_infer_request()
            else:
                self.session,provider=ort_session(path,owner.backend,owner.device_id)
                owner.compiled_devices.append([provider])
    if owner.backend=='OpenVINO':adapter.OpenVINOInferSession=GPUInferSession
    else:adapter.OrtInferSession=GPUInferSession
