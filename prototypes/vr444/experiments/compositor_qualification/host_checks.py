#!/usr/bin/env python3
"""Fresh standalone host correctness only; no native integration or devices."""
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[3]
S='prototypes/vr444/experiments/selection_policy/'
P='prototypes/vr444/experiments/pacer_timing/'
OVERLAY=ROOT/'validation/wivrn-codecs/integration/source-overlay'
# Fixed actual programs and success contracts. No arbitrary source/command input.
PROGRAMS=(
 ('selection',S+'test_selection_policy.cpp',r'selection policy PASS: [1-9][0-9]* semantic cases; 20000 independent legacy replays; [1-9][0-9]* bounded selections checked\n'),
 ('attempts',S+'render_attempt_test.cpp',r'Render-attempt accounting PASS: startup, all skip reasons, submissions, abort/duplicate/missing completion, clock links, overflow, schema-3 serialization\n'),
 ('horizon',S+'horizon_policy_test.cpp',r'Horizon policy PASS: 20000 disabled and full-horizon equivalence cases, [1-9][0-9]* fractional admissions, boundary/alpha/malformed-config/overflow checks\n'),
 ('successor',S+'ready_successor_test.cpp',r'Ready-successor PASS: 20000 disabled/filter-off equivalence histories; [1-9][0-9]* advancing choices with independent complete-successor witnesses; boundary/alpha/overflow checks\n'),
 ('cardinality',S+'cardinality_test.cpp',r'1024 exhaustive skip/cardinality populations and deterministic boundary checks passed\n'),
 ('novelty',S+'novelty_test.cpp',r'4096 exhaustive source-novelty skip/cardinality populations and deterministic boundary checks passed\n'),
 ('publication',S+'publication_ledger_test.cpp',r'publication ledger bounds, immutable marks, reset epochs and two-writer prefix export passed\n'),
 ('pacer',P+'pacer_trace_test.cpp',r'pacer trace checks passed; record bytes=112\n'),
)


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def require_result(run,pattern):
    if run.returncode!=0 or run.stderr or re.fullmatch(pattern,run.stdout) is None:
        raise ValueError('host test failed, diagnosed or omitted its complete success contract')


def expected_ids():
    return [name+'-address-undefined' for name,_,_ in PROGRAMS]+['publication-thread','pacer-thread']


def check_population(checks):
    if [row.get('id') for row in checks]!=expected_ids():raise ValueError('host program/sanitizer population omitted, duplicated or reordered')



def compiler_argv(compiler,source,binary,sanitizer):
    return [compiler,'-std=c++20','-O1','-g','-Wall','-Wextra','-Werror','-pedantic','-fno-omit-frame-pointer',
        '-fsanitize='+sanitizer,'-pthread']+(['-DWIVRN_CODEC_TESTS=1'] if source==P+'pacer_trace_test.cpp' else [])+[
        '-I'+str(OVERLAY/'common'),'-I'+str(OVERLAY/'server/compositor'),str(ROOT/source),'-o',str(binary)]


def execute(build):
    build=Path(build).resolve();build.mkdir(parents=True,exist_ok=False)
    compiler=shutil.which('clang++')
    if not compiler:raise ValueError('clang++ is required for fresh host correctness')
    compiler=str(Path(compiler).absolute());compiler_file=Path(compiler).resolve();compiler_hash=sha(compiler_file)
    version=subprocess.run([compiler,'--version'],capture_output=True,text=True,check=True).stdout
    env=dict(os.environ,ASAN_OPTIONS='detect_leaks=0:halt_on_error=1',UBSAN_OPTIONS='halt_on_error=1',TSAN_OPTIONS='halt_on_error=1')
    rows=[]
    cases=[(*row,'address,undefined') for row in PROGRAMS]+[(*row,'thread') for row in PROGRAMS if row[0] in ('publication','pacer')]
    for name,source,pattern,sanitizer in cases:
        identity=name+'-'+sanitizer.replace(',','-');binary=build/identity
        command=compiler_argv(compiler,source,binary,sanitizer)
        compiled=subprocess.run(command,cwd=ROOT,capture_output=True,text=True,timeout=180)
        if compiled.returncode or compiled.stderr:raise ValueError('host compile failed/diagnosed: '+identity+' '+compiled.stderr[-4000:])
        binary_hash=sha(binary);run=subprocess.run([str(binary)],cwd=build,env=env,capture_output=True,text=True,timeout=180)
        require_result(run,pattern)
        if sha(binary)!=binary_hash:raise ValueError('executed host binary changed')
        rows.append(dict(id=identity,source=source,sanitizer=sanitizer,compiler_argv=command,
            compile_returncode=compiled.returncode,compile_stdout=compiled.stdout,compile_stderr=compiled.stderr,
            command=[str(binary)],binary_sha256=binary_hash,returncode=run.returncode,stdout=run.stdout,stderr=run.stderr,status='pass'))
    check_population(rows)
    if sha(compiler_file)!=compiler_hash:raise ValueError('host compiler changed')
    return dict(status='pass',checks=rows,compiler_invocation=compiler,compiler_binary_sha256=compiler_hash,
        compiler_version=version,leak_detection=False,address_undefined_checks=True,thread_checks=True,
        standalone_programs=8,sanitizer_executions=10,linked_native_application=False,
        actual_native_constructor_executed=False,android_compile=False,headset_operations=False,
        scope='Current standalone headers/prototypes rebuilt and executed under ASan/UBSan and both concurrent recorder tests under TSan. Historical cross-language fixtures and actual native constructor/VFS syntax are not rerun here.')
