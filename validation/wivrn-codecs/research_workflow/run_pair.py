#!/usr/bin/env python3
"""Offline stereo RGB8 adapter runner (POSIX). Explicit commands only; not a sandbox."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import resource
import signal
import stat
import string
import subprocess
import sys
import time


def positive_int(value):
    result = int(value)
    if result <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return result


def positive_seconds(value):
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise argparse.ArgumentTypeError("must be finite and positive")
    return result


def argv_json(value):
    result = json.loads(value)
    if not isinstance(result, list) or not result or any(
            not isinstance(item, str) or "\0" in item for item in result):
        raise argparse.ArgumentTypeError("expected a nonempty JSON array of strings")
    return result


def render_argv(template, values):
    # Whole path arguments forbid suffixes such as {output}/../elsewhere.
    for name in ("input", "output"):
        if "{" + name + "}" not in template:
            raise ValueError("argv must contain standalone {" + name + "}")
    for index, token in enumerate(template):
        for _, field, spec, conversion in string.Formatter().parse(token):
            if field is None:
                continue
            if index == 0 or field not in values or spec or conversion:
                raise ValueError("unsupported argv placeholder")
            if field in ("input", "output") and token != "{" + field + "}":
                raise ValueError("path placeholders must be whole arguments")
    return [token.format_map(values) for token in template]


def read_regular(path, budget, exact=None):
    # Check before open so a FIFO cannot block; O_NOFOLLOW also rejects link races.
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError(str(path) + ": expected a regular file, not a symlink")
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
        opened = os.fstat(stream.fileno())
        if not stat.S_ISREG(opened.st_mode) or (
                opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
            raise ValueError(str(path) + ": file changed while opening")
        if opened.st_size > budget:
            raise ValueError(str(path) + ": exceeds file size budget")
        data = stream.read(budget + 1)
    if len(data) > budget or (exact is not None and len(data) != exact):
        raise ValueError(str(path) + ": invalid byte count")
    return data


def digest(data):
    return hashlib.sha256(data).hexdigest()


def file_record(path, data):
    return {"path": str(path), "bytes": len(data), "sha256": digest(data)}


def command(argv, directory, timeout, budget, timing):
    def limit_file_size():
        resource.setrlimit(resource.RLIMIT_FSIZE, (budget, budget))

    started = time.monotonic_ns()
    timing["argv"] = argv
    try:
        with subprocess.Popen(
                argv, cwd=directory, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True, preexec_fn=limit_file_size) as process:
            try:
                code = process.wait(timeout=timeout)
            except BaseException:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
                raise
            finally:
                # Adapters must complete their work synchronously.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            timing["returncode"] = code
            if code:
                raise RuntimeError("adapter exited with status " + str(code))
    finally:
        timing["elapsed_ns"] = time.monotonic_ns() - started


def run_pair(args):
    # Resolve the parent only; never resolve an existing output symlink away.
    requested = Path(args.output).absolute()
    directory = requested.parent.resolve() / requested.name
    directory.mkdir(mode=0o700)  # exclusive; no overwrite, including symlinks
    receipt = {
        "schema": "wivrn-offline-stereo-receipt-v1",
        "status": "running", "width": args.width, "height": args.height,
        "source_format": "tightly-packed-row-major-rgb8",
        "measurements": {
            "timing": "monotonic subprocess wall time including startup and file I/O; offline diagnostic only",
            "bytes": "actual encoder output payload; no packetizer, wire bytes, or network measurement",
            "quality": "RGB8 source equality and byte error only; not Q8 equivalence or displayed quality",
            "hardware_pipeline_latency_measured": False,
            "headset_latency_measured": False,
        },
        "limits": {"command_timeout_seconds": args.timeout_seconds,
                   "encoded_bytes_per_eye": args.max_encoded_bytes,
                   "decoded_bytes_per_eye": args.max_decoded_bytes},
        "stereo": {"payload_cap_bytes": args.stereo_payload_cap,
                   "admitted": False, "admitted_eyes": [], "reason": "incomplete"},
        "eyes": {"left": {}, "right": {}},
    }
    scope = "setup"
    code = 1
    # Keep a receipt descriptor open even if an adapter command fails.
    with (directory / "receipt.json").open("x", encoding="utf-8") as destination:
        try:
            expected = args.width * args.height * 3
            if expected > args.max_decoded_bytes:
                raise ValueError("raw frame exceeds decoded byte budget")
            sources = {}
            for eye in ("left", "right"):
                scope = eye + ".source"
                source = Path(getattr(args, eye)).absolute()
                data = read_regular(source, args.max_decoded_bytes, expected)
                sources[eye] = data
                snapshot = directory / (eye + ".source.rgb")
                with snapshot.open("xb") as stream:
                    stream.write(data)
                receipt["eyes"][eye]["source"] = file_record(source, data)
                receipt["eyes"][eye]["snapshot"] = file_record(snapshot, data)
            for eye in ("left", "right"):
                entry = receipt["eyes"][eye]
                source = directory / (eye + ".source.rgb")
                encoded = directory / (eye + ".bitstream")
                decoded = directory / (eye + ".decoded.rgb")
                values = {"width": str(args.width), "height": str(args.height), "eye": eye}
                for stage, template, input_path, output_path, budget in (
                    ("encode", args.encoder_argv, source, encoded, args.max_encoded_bytes),
                    ("decode", args.decoder_argv, encoded, decoded, args.max_decoded_bytes),
                ):
                    scope = eye + "." + stage
                    if output_path.parent.resolve() != directory or output_path.exists() or output_path.is_symlink():
                        raise ValueError("output path is not fresh and confined")
                    argv = render_argv(template, dict(values, input=str(input_path), output=str(output_path)))
                    timing = entry.setdefault("commands", {}).setdefault(stage, {})
                    command(argv, directory, args.timeout_seconds, budget, timing)
                    if output_path.parent.resolve() != directory:
                        raise ValueError("adapter redirected the output directory")
                    data = read_regular(output_path, budget, expected if stage == "decode" else None)
                    entry[stage + "_output"] = file_record(output_path, data)
                    if stage == "encode":
                        encoded_data = data
                if read_regular(encoded, args.max_encoded_bytes) != encoded_data:
                    raise ValueError("decoder changed its bitstream input")
                if read_regular(source, args.max_decoded_bytes, expected) != sources[eye]:
                    raise ValueError("adapter changed the source snapshot")
                total_error = max_error = 0
                for original, reconstructed in zip(sources[eye], data):
                    error = abs(original - reconstructed)
                    total_error += error
                    max_error = max(max_error, error)
                entry["rgb8_error"] = {
                    "exact_equal": data == sources[eye],
                    "mean_absolute_error": total_error / expected,
                    "max_absolute_error": max_error,
                }
                entry["valid"] = True
            # Later commands share this directory and may invalidate earlier eyes.
            # Recheck every delivered artifact before admitting either eye.
            for eye, entry in receipt["eyes"].items():
                scope = eye + ".final_artifacts"
                entry["valid"] = False
                for field, budget, exact in (
                    ("snapshot", args.max_decoded_bytes, expected),
                    ("encode_output", args.max_encoded_bytes, None),
                    ("decode_output", args.max_decoded_bytes, expected),
                ):
                    artifact = entry[field]
                    path = Path(artifact["path"])
                    if path.parent.resolve() != directory:
                        raise ValueError("artifact directory was redirected")
                    final_data = read_regular(path, budget, exact)
                    if len(final_data) != artifact["bytes"] or digest(final_data) != artifact["sha256"]:
                        raise ValueError(eye + "." + field + ": artifact changed before admission")
                entry["valid"] = True
            scope = "stereo.admission"
            total = sum(entry["encode_output"]["bytes"] for entry in receipt["eyes"].values())
            cap = args.stereo_payload_cap
            admitted = cap is None or total <= cap
            receipt["stereo"].update(
                encoded_payload_bytes=total, admitted=admitted,
                admitted_eyes=["left", "right"] if admitted else [],
                reason="within_cap" if admitted and cap is not None else
                       "no_cap_requested" if admitted else "payload_cap_exceeded")
            receipt["status"] = "completed" if admitted else "rejected"
            code = 0 if admitted else 2
        except (Exception, KeyboardInterrupt) as error:
            receipt["status"] = "failed"
            receipt["error"] = {"scope": scope, "type": type(error).__name__, "message": str(error)}
            receipt["stereo"].update(admitted=False, admitted_eyes=[], reason="invalid_pair")
        finally:
            json.dump(receipt, destination, indent=2, sort_keys=True)
            destination.write("\n")
    return code


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    for eye in ("left", "right"):
        result.add_argument("--" + eye, required=True, help="raw packed RGB8 file")
    for size in ("width", "height"):
        result.add_argument("--" + size, type=positive_int, required=True)
    for stage in ("encoder", "decoder"):
        result.add_argument("--" + stage + "-argv", type=argv_json, required=True,
                            help="JSON argv; standalone {input}/{output}, optional {width}/{height}/{eye}")
    result.add_argument("--output", required=True, help="new directory; parent must exist")
    result.add_argument("--timeout-seconds", type=positive_seconds, default=30.0)
    result.add_argument("--max-encoded-bytes", type=positive_int, default=16 * 1024 * 1024)
    result.add_argument("--max-decoded-bytes", type=positive_int, default=16 * 1024 * 1024)
    result.add_argument("--stereo-payload-cap", type=positive_int)
    return result


def main():
    args = parser().parse_args()
    try:
        return run_pair(args)
    except OSError as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
