# SPDX-License-Identifier: GPL-3.0-or-later
"""Anonymous speaker embeddings on the selected OpenVINO GPU.

The WeSpeaker ResNet34 backbone uses 80-bin Kaldi filterbanks. A separately
trained residual projection may adapt it to CJK speech. No identity database,
network access, microphone capture, or persistent voice enrolment is used.
"""
from pathlib import Path


def features(samples):
    import kaldi_native_fbank as knf
    import numpy as np
    opts = knf.FbankOptions()
    opts.frame_opts.dither = 0
    opts.frame_opts.window_type = 'hamming'
    opts.mel_opts.num_bins = 80
    opts.mel_opts.is_librosa = False
    opts.mel_opts.use_slaney_mel_scale = False
    opts.mel_opts.norm = ''
    bank = knf.OnlineFbank(opts)
    bank.accept_waveform(16000, (np.asarray(samples, dtype=np.float32) * 32768).tolist())
    f = np.stack([bank.get_frame(i) for i in range(bank.num_frames_ready)])
    return (f - f.mean(axis=0, keepdims=True)).astype(np.float32)


class SpeakerModel:
    def __init__(self, directory, device):
        import openvino as ov
        self.directory = Path(directory)
        core = ov.Core()
        model = core.read_model(self.directory/'speaker.onnx')
        model.reshape([1,398,80])
        self.model = core.compile_model(model, device,
                                       {'PERFORMANCE_HINT': 'LATENCY'})
        if self.model.get_property('EXECUTION_DEVICES') != [device]:
            raise RuntimeError('Speaker model did not compile on the requested GPU')
        self.projection = None
        if (self.directory/'projection.xml').is_file():
            self.projection = core.compile_model(self.directory/'projection.xml', device)
            if self.projection.get_property('EXECUTION_DEVICES') != [device]:
                raise RuntimeError('Speaker projection did not compile on the requested GPU')

    def embed(self, samples):
        import numpy as np
        if len(samples) < 16000 or np.sqrt(np.mean(np.square(samples))) < .0001:
            return None
        # Bound work and average windows, rather than allocate a long dynamic graph.
        vectors = []
        for start in range(0, len(samples), 16000*4):
            part = samples[start:start+16000*4]
            if len(part) < 16000: continue
            f = features(part)
            # Fixed shape avoids expensive GPU recompilation for each phrase.
            if len(f)<398: f=np.tile(f,(int(np.ceil(398/len(f))),1))[:398]
            else: f=f[:398]
            vector = self.model([f[None]])[0][0].copy()
            vector /= max(float(np.linalg.norm(vector)), 1e-8)
            if self.projection is not None:
                vector = self.projection([vector[None]])[0][0].copy()
                vector /= max(float(np.linalg.norm(vector)), 1e-8)
            vectors.append(vector)
        if not vectors: return None
        vector = np.mean(vectors, axis=0)
        vector /= max(float(np.linalg.norm(vector)), 1e-8)
        return vector.tolist()


class SpeakerTracker:
    """Bounded session-only cosine clustering; uncertain matches stay unlabelled.

    Kept in the parent alongside the cloud voice state so configuration reloads
    reset enrolment even when the GPU worker remains alive.
    """
    def __init__(self, threshold=.65, capacity=4):
        self.threshold = threshold
        self.capacity = capacity
        self.centres = []

    def assign(self, vector):
        import math
        if not isinstance(vector, list) or len(vector) != 256 or not all(
                isinstance(x, (float, int)) and math.isfinite(x) for x in vector):
            return 0
        norm = math.sqrt(sum(x*x for x in vector))
        if norm < 1e-8: return 0
        vector = [x/norm for x in vector]
        scores = [sum(a*b for a,b in zip(vector,c)) for c in self.centres]
        if scores and max(scores) >= self.threshold:
            best = max(range(len(scores)), key=scores.__getitem__)
            ordered = sorted(scores, reverse=True)
            if len(ordered)>1 and ordered[0]-ordered[1]<.06: return 0
            centre = [a*.9+b*.1 for a,b in zip(self.centres[best],vector)]
            norm = math.sqrt(sum(x*x for x in centre))
            self.centres[best] = [x/norm for x in centre]
            return best+1
        # Avoid creating duplicate voices for a borderline match.
        if scores and max(scores) >= self.threshold-.08: return 0
        if len(self.centres) >= self.capacity: return 0
        self.centres.append(vector)
        return len(self.centres)
