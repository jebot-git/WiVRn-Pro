#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Select adaptation strength on validation only, then export the selected model."""

import json
from pathlib import Path
import torch
from transformers import AutoTokenizer, AutoModelForSeq2SeqLM
from peft import PeftModel
from sacrebleu.metrics import CHRF

root = Path.home() / ".local/share/spatial-subtitles"
run = root / "training/translation-001"
data = json.loads((root / "training/translation.json").read_text())
report = json.loads((run / "report.json").read_text())
torch.set_num_threads(4)
langs = {
    "ja": "jpn_Jpan",
    "ko": "kor_Hang",
    "zh": "zho_Hans",
    "th": "tha_Thai",
    "en": "eng_Latn",
    "sr": "srp_Cyrl",
}
gpu = [
    i for i in range(torch.xpu.device_count()) if "A770" in torch.xpu.get_device_name(i)
]
assert len(gpu) == 1
device = f"xpu:{gpu[0]}"
tok = AutoTokenizer.from_pretrained(root / "training/nllb-base", local_files_only=True)
base = AutoModelForSeq2SeqLM.from_pretrained(
    root / "training/nllb-base", local_files_only=True, attn_implementation="sdpa"
)
model = PeftModel.from_pretrained(base, run / "adapter").to(device)
model.eval()
metric = CHRF(word_order=2)
selected = 0
results = {}
for strength in [0.1, 0.25, 0.5]:
    for module in model.modules():
        if hasattr(module, "set_scale"):
            module.set_scale("default", strength)
    scores = {}
    with torch.inference_mode():
        for lang in ["ja", "ko", "zh", "th", "en"]:
            rows = [
                r
                for r in data["rows"]
                if r["language"] == lang and r["split"] == "validation"
            ][:12]
            hyp = []
            for r in rows:
                tok.src_lang = langs[lang]
                x = tok(
                    r["source"], return_tensors="pt", truncation=True, max_length=128
                ).to(device)
                y = model.generate(
                    **x,
                    forced_bos_token_id=tok.convert_tokens_to_ids(langs[r["output"]]),
                    max_new_tokens=128,
                    num_beams=1,
                )
                hyp.append(tok.batch_decode(y, skip_special_tokens=True)[0])
            scores[lang] = metric.corpus_score(hyp, [[r["target"] for r in rows]]).score
            print(strength, lang, scores[lang], flush=True)
    results[str(strength)] = scores
    if all(
        scores[k] >= report["baseline_validation"][k]["chrf"] - 0.0001 for k in scores
    ):
        selected = strength
        break
for module in model.modules():
    if hasattr(module, "set_scale"):
        module.set_scale("default", selected)
merged = model.merge_and_unload().cpu()
dest = root / "training/translation-selected"
merged.save_pretrained(dest)
tok.save_pretrained(dest)
selection = dict(
    strength=selected,
    validation=results,
    baseline=report["baseline_validation"],
    reason="Largest tried non-regressing strength"
    if selected
    else "No non-regressing adaptation found; use pretrained local baseline",
    warning="Small validation set, not proof of broad translation quality",
)
(root / "training/translation-selection.json").write_text(
    json.dumps(selection, indent=2)
)
print(selection, flush=True)
from optimum.exporters.openvino import main_export
from optimum.intel.openvino.configuration import OVConfig

out = root / "models/translation-selected-fp16"
main_export(
    str(dest),
    out,
    task="text2text-generation-with-past",
    ov_config=OVConfig(dtype="fp16"),
    convert_tokenizer=False,
    trust_remote_code=False,
)
(out / "selection.json").write_text(json.dumps(selection, indent=2))
