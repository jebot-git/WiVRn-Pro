#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Run the production decoder-retirement loops with real ownership types and ASan.

Usage: decoder_retirement_test.py CLIENT_BUILD_DIR [--compiler g++]
CLIENT_BUILD_DIR must contain compile_commands.json from a configured client
build, including the PyroWave decoder. An Android build's dependency headers
also work: only include paths are reused, and compilation/execution use the
host compiler. Requires C++23, ASan, OpenXR loader and OpenSSL development files.
No Vulkan/OpenXR instance, device, application or service is started.

The GPU constructor is replaced through the real decoder factory interface.
The image-pool type, frame handle, shard accumulator and stream container are
production types. The anonymous handle and setup's retirement loops are
extracted verbatim rather than mirrored. This tests CPU ownership, not Vulkan
sampler lifetimes, worker scheduling or asynchronous GPU completion.
"""

import argparse
import json
import os
from pathlib import Path
import re
import resource
import shlex
import subprocess


ROOT = Path(__file__).resolve().parents[2]


def mask_literals(source):
    """Keep offsets/newlines while ignoring braces inside comments/literals."""
    pattern = r'//[^\n]*|/\*[\s\S]*?\*/|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\''
    return re.sub(pattern, lambda m: ''.join('\n' if c == '\n' else ' ' for c in m[0]), source)


def closing_brace(masked, opening):
    depth = 0
    for i in range(opening, len(masked)):
        if masked[i] == '{':
            depth += 1
        elif masked[i] == '}':
            depth -= 1
            if depth == 0:
                return i
    raise RuntimeError('Unbalanced production C++ scope')


def fragment(path, begin, end):
    source = path.read_text()
    line = source.count('\n', 0, begin) + 1
    return f'#line {line} {json.dumps(str(path))}\n' + source[begin:end] + '\n'


def extract_production(output):
    handle = ROOT / 'client/decoder/pyrowave/decoder.cpp'
    source = handle.read_text()
    masked = mask_literals(source)
    match = re.search(r'\bstruct\s+pyrowave_blit_handle\s*:', masked)
    if not match:
        raise RuntimeError('Production PyroWave handle changed; update the ownership fixture')
    opening = masked.index('{', match.end())
    end = closing_brace(masked, opening) + 1
    end = masked.index(';', end) + 1
    (output / 'production_handle.inc').write_text(
        'namespace {\n' + fragment(handle, match.start(), end) + '}\n')

    stream = ROOT / 'client/scenes/stream.cpp'
    source = stream.read_text()
    masked = mask_literals(source)
    setup = masked.index('void scenes::stream::setup(')
    opening = masked.index('{', setup)
    end = closing_brace(masked, opening)
    declaration = re.search(r'decltype\(decoders\)\s+retired\s*;', masked[opening:end])
    if not declaration:
        raise RuntimeError('Cannot locate the production retired-decoder group')
    lock_open = masked.index('{', opening + declaration.end())
    lock_close = closing_brace(masked, lock_open)
    replacement = re.search(r'decltype\(decoders\)\s+replacement\s*;', masked[lock_close:end])
    if not replacement:
        raise RuntimeError('Cannot isolate retirement from decoder replacement')
    begin = lock_close + 1
    finish = lock_close + replacement.start()
    if 'retired' not in masked[begin:finish]:
        raise RuntimeError('Production retirement scope is empty or changed')
    (output / 'production_retire.inc').write_text(fragment(stream, begin, finish))


def dependency_includes(entry):
    args = entry.get('arguments') or shlex.split(entry['command'])
    directory = Path(entry['directory'])
    result = []
    i = 1
    while i < len(args):
        option = args[i]
        if option in ('-I', '-isystem', '-iquote'):
            path = args[i + 1]
            i += 2
        elif option.startswith('-I'):
            path, option = option[2:], '-I'
            i += 1
        else:
            i += 1
            continue
        path = Path(path)
        if not path.is_absolute():
            path = directory / path
        result.extend([option, str(path)])
    return result


HARNESS = r'''
#include "decoder/pyrowave/decoder.h"
#include "scenes/stream.h"
#include "production_handle.inc"
#include "decoder/shard_accumulator.cpp"
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <type_traits>

// The real allocation wrapper always calls its backend, even for null images.
// Reject any non-null object: this fixture must never exercise GPU operations.
void basic_allocation_traits<vk::Image>::destroy(vk::raii::Image& image,
        VmaAllocation allocation, void* mapped) {
    if (*image || allocation || mapped) std::abort();
}
wivrn::decoder::~decoder() = default;

struct null_gpu_decoder : wivrn::decoder {
    decltype(std::declval<wivrn::pyrowave_decoder>().image_pool) pool;
    void push_data(std::span<std::span<const uint8_t>>, uint64_t, bool) override {}
    void frame_completed(const wivrn::from_headset::feedback&,
        const wivrn::to_headset::video_stream_data_shard::view_info_t&) override {}
    vk::Sampler sampler() override { return nullptr; }
};
static null_gpu_decoder* last_owner;
std::shared_ptr<wivrn::decoder> wivrn::decoder::make(vk::raii::Device&,
        vk::raii::PhysicalDevice&, uint32_t,
        const wivrn::to_headset::video_stream_description&, uint8_t,
        std::weak_ptr<scenes::stream>, wivrn::shard_accumulator*) {
    auto result = std::make_shared<null_gpu_decoder>();
    last_owner = result.get();
    return result;
}

static void exercise(const char* mode) {
    vk::raii::Device device(nullptr);
    vk::raii::PhysicalDevice physical(nullptr);
    xr::instance instance;
    decltype(std::declval<scenes::stream>().decoders) retired;
    for (size_t i = 0; i < retired.size(); ++i) {
        auto& item = retired[i];
        item.decoder = std::make_unique<wivrn::shard_accumulator>(device, physical,
            instance, 0, wivrn::to_headset::video_stream_description{},
            std::weak_ptr<scenes::stream>{}, i);
        for (size_t j = 0; j < item.latest_frames.size(); ++j) {
            auto& image = last_owner->pool[j];
            image.free = false;
            item.latest_frames[j] = std::make_shared<pyrowave_blit_handle>(
                wivrn::from_headset::feedback{},
                wivrn::to_headset::video_stream_data_shard::view_info_t{},
                vk::ImageView{}, vk::Image{}, vk::Extent2D{}, image.current_layout,
                vk::Semaphore{}, image.semaphore_val, image.free);
        }
    }
    // This is the post-fence state in setup: renderer refs are cleared, while
    // all rolling frames still belong to the retired decoder groups.
    if (!std::strcmp(mode, "old-order")) {
        for (auto& item : retired) item = {}; // Historical negative control.
    } else if (!std::strcmp(mode, "production")) {
#include "production_retire.inc"
        for (const auto& item : retired) {
            if (item.decoder) std::abort();
            for (const auto& frame : item.latest_frames)
                if (frame) std::abort();
        }
    } else if (std::strcmp(mode, "natural-destruction")) {
        std::abort();
    }
}
int main(int argc, char** argv) {
    if (argc != 2) return 2;
    exercise(argv[1]);
    std::printf("PASS %s\n", argv[1]);
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('build', type=Path, help='client build directory or compile_commands.json')
    parser.add_argument('--compiler', default=os.environ.get('CXX', 'g++'), help='host compiler with C++23 and ASan')
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'build/native-tests/decoder-retirement')
    options = parser.parse_args()
    database = options.build.resolve()
    if database.is_dir():
        database /= 'compile_commands.json'
    commands = json.loads(database.read_text())
    entry = next((c for c in commands if c['file'].endswith('/client/decoder/pyrowave/decoder.cpp')), None)
    if entry is None:
        parser.error('compile_commands.json must include the configured PyroWave client decoder')
    output = options.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    extract_production(output)
    source = output / 'ownership.cpp'
    source.write_text(HARNESS)
    binary = output / 'decoder-retirement-test'
    command = shlex.split(options.compiler) + [
        '-std=c++23', '-O1', '-g', '-fsanitize=address', '-fno-omit-frame-pointer',
        '-fno-access-control', '-ffunction-sections', '-fdata-sections', '-pthread',
        '-DVULKAN_HPP_NO_STRUCT_CONSTRUCTORS', '-DGLM_ENABLE_EXPERIMENTAL',
        '-DIMGUI_DEFINE_MATH_OPERATORS', '-DIMGUI_USE_WCHAR32',
        '-DXR_USE_GRAPHICS_API_VULKAN', '-DXR_USE_TIMESPEC=1',
        '-I' + str(ROOT / 'client'), '-I' + str(ROOT / 'common'), '-I' + str(ROOT / 'external'),
        *dependency_includes(entry), str(source), str(ROOT / 'common/crypto.cpp'),
        str(ROOT / 'common/smp.cpp'),
        '-Wl,--gc-sections', '-lopenxr_loader', '-lcrypto', '-o', str(binary)]
    (output / 'build-command.json').write_text(json.dumps(command, indent=2) + '\n')
    with (output / 'build.log').open('w') as log:
        result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, cwd=ROOT)
    if result.returncode:
        raise RuntimeError(f'Host ownership test build failed; see {output / "build.log"}')
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    env = os.environ.copy()
    env['ASAN_OPTIONS'] = 'detect_leaks=1:halt_on_error=1:abort_on_error=0'
    results = {}
    for mode in ('old-order', 'production', 'natural-destruction'):
        run = subprocess.run([str(binary), mode], env=env, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, timeout=30)
        (output / f'{mode}.log').write_text(run.stdout)
        uaf = 'ERROR: AddressSanitizer: heap-use-after-free' in run.stdout
        results[mode] = {'exit_code': run.returncode, 'asan_heap_use_after_free': uaf}
        if mode == 'old-order':
            if run.returncode == 0 or not uaf or 'pyrowave_blit_handle' not in run.stdout:
                raise RuntimeError(f'Historical destructor-order negative control did not reproduce; see {output / (mode + ".log")}')
        elif run.returncode or f'PASS {mode}' not in run.stdout:
            raise RuntimeError(f'Ownership regression in {mode}; see {output / (mode + ".log")}')
    (output / 'results.json').write_text(json.dumps(results, indent=2) + '\n')
    print('PASS decoder retirement: historical UAF reproduced; extracted production loops and natural destruction ASan-clean')
    print(f'Logs: {output}')


if __name__ == '__main__':
    main()
