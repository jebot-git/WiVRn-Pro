#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Reproducible four-language NLLB adaptation on Intel XPU; export candidates."""

import argparse, json, random, time
from pathlib import Path
import torch
from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
from peft import LoraConfig, get_peft_model, TaskType
from sacrebleu.metrics import CHRF

CODES = {
    "ja": "jpn_Jpan",
    "ko": "kor_Hang",
    "zh": "zho_Hans",
    "th": "tha_Thai",
    "en": "eng_Latn",
    "sr": "srp_Cyrl",
}
p = argparse.ArgumentParser()
p.add_argument("--root", type=Path, required=True)
p.add_argument("--steps", type=int, default=240)
a = p.parse_args()
root = a.root
run = root / "training/translation-001"
run.mkdir(exist_ok=False)
random.seed(173)
torch.manual_seed(173)
torch.set_num_threads(4)
gpus = [
    i for i in range(torch.xpu.device_count()) if "A770" in torch.xpu.get_device_name(i)
]
assert len(gpus) == 1
device = f"xpu:{gpus[0]}"
tokenizer = AutoTokenizer.from_pretrained(
    root / "training/nllb-base", local_files_only=True
)
model = AutoModelForSeq2SeqLM.from_pretrained(
    root / "training/nllb-base", local_files_only=True, attn_implementation="sdpa"
).to(device)
model = get_peft_model(
    model,
    LoraConfig(
        task_type=TaskType.SEQ_2_SEQ_LM,
        r=8,
        lora_alpha=16,
        lora_dropout=0.05,
        target_modules=r"model.decoder.*\.(q_proj|v_proj)",
    ),
)
data = json.loads((root / "training/translation.json").read_text())
rows = data["rows"]
metric = CHRF(word_order=2)


def evaluate(split, limit=12):
    model.eval()
    scores = {}
    out = []
    with torch.inference_mode():
        for lang in CODES:
            group = [r for r in rows if r["split"] == split and r["language"] == lang][
                :limit
            ]
            if not group:
                continue
            hyps = []
            refs = []
            times = []
            for r in group:
                tokenizer.src_lang = CODES[lang]
                inputs = tokenizer(
                    r["source"], return_tensors="pt", truncation=True, max_length=128
                ).to(device)
                start = time.monotonic()
                gen = model.generate(
                    **inputs,
                    forced_bos_token_id=tokenizer.convert_tokens_to_ids(
                        CODES[r["output"]]
                    ),
                    max_new_tokens=128,
                    num_beams=1,
                )
                torch.xpu.synchronize()
                hyp = tokenizer.batch_decode(gen, skip_special_tokens=True)[0]
                hyps.append(hyp)
                refs.append(r["target"])
                times.append(time.monotonic() - start)
                out.append(dict(r, hypothesis=hyp))
            scores[lang] = dict(
                chrf=metric.corpus_score(hyps, [refs]).score,
                seconds=sum(times) / len(times),
                samples=len(group),
            )
    return scores, out


baseline, _ = evaluate("validation")
print("Baseline", baseline, flush=True)
prepared = {}
for lang in CODES:
    group = [r for r in rows if r["split"] == "train" and r["language"] == lang]
    random.shuffle(group)
    if group:
        prepared[lang] = group
optim = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad], lr=5e-5)
losses = []
started = time.monotonic()
model.train()
model.config.use_cache = False
langs = list(prepared)
for step in range(a.steps):
    lang = langs[step % len(langs)]
    group = prepared[lang]
    batch = [group[(step // len(langs) * 4 + i) % len(group)] for i in range(4)]
    tokenizer.src_lang = CODES[lang]
    tokenizer.tgt_lang = CODES[batch[0]["output"]]
    inputs = tokenizer(
        [r["source"] for r in batch],
        text_target=[r["target"] for r in batch],
        padding=True,
        truncation=True,
        max_length=128,
        return_tensors="pt",
    ).to(device)
    inputs["labels"][inputs["labels"] == tokenizer.pad_token_id] = -100
    optim.zero_grad(set_to_none=True)
    loss = model(**inputs).loss
    assert torch.isfinite(loss)
    loss.backward()
    torch.nn.utils.clip_grad_norm_(
        [p for p in model.parameters() if p.requires_grad], 1
    )
    optim.step()
    losses.append(loss.item())
    if step % 20 == 0:
        print(
            "Step",
            step,
            "loss",
            round(loss.item(), 3),
            "elapsed",
            round(time.monotonic() - started),
            flush=True,
        )
model.config.use_cache = True
after, _ = evaluate("validation")
test, examples = evaluate("test", 24)
report = dict(
    dataset={k: v for k, v in data.items() if k != "rows"},
    steps=a.steps,
    train_pairs={k: len(v) for k, v in prepared.items()},
    consumed_examples=a.steps * 4,
    trainable_parameters=sum(p.numel() for p in model.parameters() if p.requires_grad),
    baseline_validation=baseline,
    candidate_validation=after,
    candidate_test=test,
    losses=losses,
    warning="Small adaptation pilot; test held out from gradients and checkpoint selection. Speed must be measured with OpenVINO.",
    passes_gate=all(after[k]["chrf"] >= baseline[k]["chrf"] for k in baseline),
)
(run / "report.json").write_text(json.dumps(report, indent=2))
(run / "test.json").write_text(json.dumps(examples, ensure_ascii=False, indent=2))
model.save_pretrained(run / "adapter")
merged = model.merge_and_unload().cpu()
merged.save_pretrained(run / "merged")
tokenizer.save_pretrained(run / "merged")
print("Exporting OpenVINO candidate; use compress_translation.py for INT8", flush=True)
from optimum.exporters.openvino import main_export
from optimum.intel.openvino.configuration import OVConfig

out = root / "models/translation-cjk-th-001"
main_export(
    str(run / "merged"),
    out,
    task="text2text-generation-with-past",
    ov_config=OVConfig(dtype="fp16"),
    convert_tokenizer=False,
    trust_remote_code=False,
)
(out / "training-report.json").write_text(json.dumps(report, indent=2))
print("DONE", report, flush=True)
