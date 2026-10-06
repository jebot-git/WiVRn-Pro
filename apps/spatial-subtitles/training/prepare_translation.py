from huggingface_hub import snapshot_download, hf_hub_download
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pyarrow.parquet as pq, json, hashlib

root = Path.home() / ".local/share/spatial-subtitles"
root.mkdir(parents=True, exist_ok=True)
rev = "f8d333a098d19b4fd9a8b18f94170487ad3f821d"


def model():
    snapshot_download(
        "facebook/nllb-200-distilled-600M",
        revision=rev,
        local_dir=root / "training/nllb-base",
        allow_patterns=["*.json", "*.model", "pytorch_model.bin", "README.md"],
    )
    print("Translation base downloaded", flush=True)


def data():
    revision = "168de341b3db6859a9bac1c50a2ef5e3b47647e0"
    langs = {
        "en_us": "en",
        "ja_jp": "ja",
        "ko_kr": "ko",
        "cmn_hans_cn": "zh",
        "th_th": "th",
        "sr_rs": "sr",
    }

    def read(t):
        lang, split = t
        p = hf_hub_download(
            "google/fleurs",
            f"{lang}/{split}/0000.parquet",
            repo_type="dataset",
            revision=revision,
        )
        rows = pq.read_table(p, columns=["id", "raw_transcription"]).to_pylist()
        print(lang, split, len(rows), flush=True)
        return (langs[lang], split), {r["id"]: r["raw_transcription"] for r in rows}

    with ThreadPoolExecutor(max_workers=3) as pool:
        tables = dict(
            pool.map(
                read, [(l, s) for l in langs for s in ["train", "validation", "test"]]
            )
        )
    rows = []
    seen = set()
    for split, limit in [("test", 48), ("validation", 32), ("train", 512)]:
        for lang in ["ja", "ko", "zh", "th"]:
            source = tables[lang, split]
            target = tables["en", split]
            for id in sorted(source.keys() & target.keys()):
                text = source[id]
                en = target[id]
                h = hashlib.sha256((lang + text).encode()).hexdigest()
                if h in seen or not 5 < len(text) < 180 or not 5 < len(en) < 260:
                    continue
                rows.append(
                    dict(
                        language=lang,
                        source=text,
                        target=en,
                        output="en",
                        split=split,
                        id=id,
                        sha256=h,
                    )
                )
                seen.add(h)
                if (
                    sum(r["language"] == lang and r["split"] == split for r in rows)
                    >= limit
                ):
                    break
    # English to Serbian uses the same official split; supports local pivot.
    for split, limit in [("test", 48), ("validation", 32), ("train", 512)]:
        a, b = tables["en", split], tables["sr", split]
        count = 0
        for id in sorted(a.keys() & b.keys()):
            if 5 < len(a[id]) < 260 and 5 < len(b[id]) < 260:
                rows.append(
                    dict(
                        language="en",
                        source=a[id],
                        target=b[id],
                        output="sr",
                        split=split,
                        id=id,
                    )
                )
                count += 1
                if count == limit:
                    break
    d = dict(
        dataset="google/fleurs",
        revision=revision,
        license="CC-BY-4.0",
        base_revision=rev,
        attribution="Google FLEURS / Conneau et al. 2022; official split aligned by sentence ID",
        rows=rows,
    )
    (root / "training").mkdir(exist_ok=True)
    (root / "training/translation.json").write_text(
        json.dumps(d, ensure_ascii=False, indent=2)
    )
    print("Pairs", len(rows), flush=True)


with ThreadPoolExecutor(max_workers=2) as pool:
    list(pool.map(lambda f: f(), [model, data]))
