#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Run only after a coordinated CPU window; records bounded host observations."""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import time
import summarize

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[3]


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('corpus',type=Path);p.add_argument('output_prefix',type=Path);p.add_argument('--binaries',type=Path,default=Path('/tmp/wivrn-rowblocks-encoder-workers-build'));a=p.parse_args()
    source_names=['rowblocks_encoder_workers/'+n for n in ('encoder.hpp','bench.cpp','test.cpp','build.sh','build-android.sh','measure.py','summarize.py')]
    source_names+=['rowblocks_workers/workers.hpp','rgb_rowblocks/rowblocks.hpp','rgb_rowfilter/rowfilter.hpp','rgb_rowfilter/simd.hpp','latency_decode/direct.cpp','latency_decode/direct.hpp','native_lz4/wpc.hpp','native_lz4/vendor/lz4.c','native_lz4/vendor/lz4.h']
    sources=[HERE.parent/n for n in source_names]
    ref=ROOT/'validation/wivrn-codecs/integration/source-overlay/common/codec_test/vr444';sources += [ref/'codec.cpp',ref/'codec.hpp']
    source_hashes={str(f.relative_to(ROOT)):sha(f) for f in sources};binaries={name:sha(a.binaries/name) for name in ('bench','test','test-tsan','bench-android','test-android')}
    paths=sorted(a.corpus.rglob('*.payload'))
    if not 1<=len(paths)<=256 or any(not 0<f.stat().st_size<=16*1024*1024 for f in paths):raise ValueError('corpus bounds')
    corpus={str(f.relative_to(a.corpus)):sha(f) for f in paths}
    outputs={kind:Path(str(a.output_prefix)+'-'+kind+'.json') for kind in ('benchmark','summary','receipt')}
    if any(f.exists() for f in outputs.values()):raise ValueError('refuse existing output')
    command=[str((a.binaries/'bench').resolve()),str(a.corpus.resolve()),'31'];started=datetime.datetime.now(datetime.timezone.utc).isoformat();start=time.monotonic()
    result=subprocess.run(command,capture_output=True,text=True,timeout=180);elapsed=time.monotonic()-start
    if result.returncode or result.stderr:raise ValueError('benchmark failed or emitted diagnostic: '+result.stderr)
    data=json.loads(result.stdout);summary=summarize.summarize(data)
    if {c[k] for c in data['cases'] for k in ('left_source','right_source')}!=set(corpus):raise ValueError('benchmark source coverage differs')
    if any(sha(a.corpus/n)!=h for n,h in corpus.items()) or any(sha(ROOT/n)!=h for n,h in source_hashes.items()) or any(sha(a.binaries/n)!=h for n,h in binaries.items()):raise ValueError('input/source/binary changed during measurement')
    outputs['benchmark'].write_text(result.stdout);outputs['summary'].write_text(json.dumps(summary,indent=2)+'\n')
    receipt=dict(schema=1,status='pass',started_utc=started,elapsed_wall_seconds=elapsed,command=command,corpus_sha256=corpus,source_sha256=source_hashes,binary_sha256=binaries,benchmark_sha256=sha(outputs['benchmark']),summary_sha256=sha(outputs['summary']),inputs_sources_binaries_unchanged=True,
        actual_captured_pairs=summary['actual_captured_pairs'],synthetic_duplicated_eye_profiles=summary['synthetic_duplicated_eye_profiles'],coordinated_window='GPU and compositor teams explicitly idle before launch; ordinary desktop and services uncontrolled.',
        compiler=subprocess.check_output(['clang++','--version'],text=True).splitlines()[0],platform=platform.platform(),cpu_model=next((s.split(':',1)[1].strip() for s in Path('/proc/cpuinfo').read_text().splitlines() if s.startswith('model name')),'unknown'),
        limitations='Single host with no affinity/frequency/thermal controls. Server and client stages run sequentially on this host, not separated real devices. CPU wall includes two process CPU clock reads per stage. All RRB client arms use four workers. No network/GPU/compositor/runtime or headset result.',headset_accessed=False,network_accessed=False,android_runtime_tested=False)
    outputs['receipt'].write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps(dict(status='pass',elapsed_wall_seconds=elapsed,cases=len(data['cases']),repetitions=31)),flush=True)


if __name__=='__main__':main()
