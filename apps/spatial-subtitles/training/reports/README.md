# Local adaptation run — 6 October 2026

These aggregate reports contain metrics and provenance, not captured audio or
private transcripts. Model weights, datasets and detailed predictions remain
under `~/.local/share/spatial-subtitles`. Gradients used Intel Arc A770 through
PyTorch XPU; exports and deployment use OpenVINO GPU. All four requested source
languages (Japanese, Korean, Chinese and Thai) are included.

- Translation: 240 LoRA updates, 960 distinct pairs actually consumed (192 per
  direction, including English→Serbian). The prepared pool contains 2,560 train
  pairs. Korean/Chinese validation regressed; the local pretrained NLLB FP16
  export is selected. Smaller adapter strengths also failed validation.
- Speech: 160 updates over 480 training clips, 640 sample presentations; all 80
  validation clips evaluated in both PyTorch and OpenVINO. The trained INT8
  candidate regressed Chinese/Thai CER and is not active. A 24-layer encoder
  pruning experiment failed badly and is explicitly rejected.
- OCR: six epochs of CTC-head adaptation on 480 synthetic signs, 80 validation
  signs. CER did not improve or regress. The trained script router reached
  97.3% accuracy on 112 synthetic validation crops; confident routing covered
  77.7%, with no confident errors on this small set. Trained heads/router are
  active. Real scenes and handwriting remain unvalidated.
- OCR timing: 123 ms → 75 ms median on five synthetic multilingual signs
  (about 39% less inference time). VR was running; this is a small benchmark,
  not an end-to-end latency guarantee.
- Speech speed: phrase-level voice assignment avoids timestamp decoding.
  On 32 validation clips of the unselected candidate, median recognition plus
  speaker embedding was 10–14% faster. The setting is enabled on the existing
  deployed model; equivalent speedup on that model has not been established.

Validation informed selection, so these are development results rather than
independent final accuracy estimates. Keep the better local baseline when
adaptation regresses. No cloud inference or fallback is permitted.
