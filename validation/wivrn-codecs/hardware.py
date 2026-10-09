#!/usr/bin/env python3
"""Standalone Arc/Quest tests. Deliberately separate from the WiVRn adapter gate."""
import argparse
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time
import uuid

import numpy as np
from collect import host, client
from suite import verify

HERE = Path(__file__).resolve().parent


def save(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def distribution(values):
    values = sorted(values)
    if not values:
        return {"samples": 0, "measured": False}
    return dict(samples=len(values), measured=True, p99_low_confidence=len(values) < 1000,
                **{f"p{p}": values[math.ceil(len(values)*p/100)-1] for p in (50, 95, 99)})


def select_gpu(probe, vendor, device):
    candidates = [d for d in probe["devices"] if d["vendor_id"] == vendor and d["device_id"] == device and d["device_type"] != 4]
    if len(candidates) != 1:
        raise ValueError("selected DRM identity must match exactly one hardware Vulkan device")
    return candidates[0]


def select_decoder(probe, requested=None):
    candidates = [c for c in probe["codecs"] if c["hardware"] and not c["software"]
                  and not c["alias"] and c["size_1152x1920"] and not c["name"].endswith(".secure")]
    if requested:
        candidates = [c for c in candidates if c["name"] == requested]
    if not candidates:
        raise ValueError("no eligible hardware HEVC decoder")
    return sorted(candidates, key=lambda c: (not c["size_rate_1152x1920_90"], not c["low_latency"], c["name"]))[0]


def rgb_metrics(actual, reference):
    if actual.shape != reference.shape or actual.size == 0:
        raise ValueError("quality requires matching nonempty arrays")
    delta = actual.astype(np.float64) - reference.astype(np.float64)
    mse = float(np.square(delta).mean())
    return dict(samples=actual.size, exact=mse == 0, mse=mse,
                psnr_db=None if mse == 0 else 10*math.log10(255**2/mse),
                max_absolute_error=int(np.abs(delta).max()))


def validate_frames(result, expected, pts_us):
    rows = result["frames"]
    if result.get("status") != "pass" or not result.get("hardware") or not result.get("output_eos"):
        raise ValueError("decoder did not report hardware success and EOS")
    if result.get("sent_frames") != expected or result.get("decoded_frames") != expected or len(rows) != expected:
        raise ValueError("decoder frame count mismatch")
    if len(pts_us) != expected or [r["frame"] for r in rows] != list(range(expected)):
        raise ValueError("reference count or frame order mismatch")
    if any(b <= a for a, b in zip(pts_us, pts_us[1:])):
        raise ValueError("reference PTS must be strictly increasing")
    if not 1 <= result.get("max_pending_frames", 0) <= 4:
        raise ValueError("decoder pending queue exceeded its bound")
    for row, pts in zip(rows, pts_us):
        if abs(row["pts_us"] - pts) > 1:
            raise ValueError("decoder/reference timestamp mismatch")
        if row["output_ns"] < row["queued_ns"] or not math.isfinite(row["queue_to_output_ms"]) or row["queue_to_output_ms"] < 0:
            raise ValueError("invalid decoder timing")


class HardwareRun:
    def __init__(self, args):
        self.args = args
        self.out = args.out.resolve(); self.out.mkdir(parents=True, exist_ok=False)
        self.remote = "/data/local/tmp/codec-hardware-" + uuid.uuid4().hex
        self.adb = ["adb", "-s", args.serial]
        self.tests = []; self.details = {}; self.owned_remote = False; self.completed = False
        self.vulkan = args.bundles.resolve()/"vulkan-vr444-0.2.0"
        self.hevc = args.bundles.resolve()/"hevc-carrier-0.1.0"
        self.binary = self.vulkan/"linux-x86_64/vr444"
        self.env = dict(os.environ)
        save(self.out/"config.json", dict(serial=args.serial, remote=self.remote,
             bundles=str(args.bundles.resolve()), render_node=args.render_node,
             frames=args.frames, repeats=args.repeats, encoders=args.encoders))

    def record(self, name, status, **extra):
        self.tests.append(dict(test=name, status=status, **extra))
        self.summary()
        print(name + ": " + status, flush=True)

    def summary(self):
        status = "pass" if self.completed else "incomplete"
        if any(t["status"] == "unsupported" for t in self.tests) and self.completed: status = "pass_with_limitations"
        if any(t["status"] == "fail" for t in self.tests): status = "fail"
        value = dict(schema=1, status=status, scope="standalone hardware correctness; no WiVRn/OpenXR or optical qualification",
                     tests=self.tests, measurements=self.details)
        save(self.out/"summary.json", value)
        return value

    def command(self, name, argv, timeout=180, env=None, required=True):
        started = time.monotonic()
        with (self.out/(name+".log")).open("w") as log:
            try:
                code = subprocess.run([str(a) for a in argv], stdout=log, stderr=subprocess.STDOUT,
                                      timeout=timeout, env=env).returncode
            except (OSError, subprocess.TimeoutExpired) as error:
                log.write(str(error)); code = -1
        self.record(name, "pass" if code == 0 else "fail", argv=[str(a) for a in argv], exit_code=code,
                    elapsed_s=round(time.monotonic()-started, 3))
        if code and required: raise RuntimeError(name + " failed; see its log")
        return code

    def shell(self, name, argv, **kwargs):
        return self.command(name, self.adb+["shell", shlex.join([str(a) for a in argv])], **kwargs)

    def push(self, name, files, target=None):
        self.command(name, self.adb+["push", *files, target or self.remote+"/"])

    def pull(self, name, source, target):
        self.command(name, self.adb+["pull", source, target])

    def java(self, name, args, timeout=150, required=True):
        return self.shell(name, ["env", "CLASSPATH="+self.remote+"/codec-harness.jar", "timeout", str(timeout-10),
                                "app_process", "/system/bin", "CodecHarness", *args], timeout=timeout, required=required)

    def prepare(self):
        save(self.out/"harness-source-hashes.json", {str(p.relative_to(HERE)): digest(p)
             for p in (HERE/"hardware.py", HERE/"collect.py", HERE/"suite.py",
                       HERE/"hardware/CodecHarness.java", HERE/"hardware/build.sh")})
        for bundle in (self.vulkan, self.hevc):
            report = verify(bundle); save(self.out/(bundle.name+"-verify.json"), report)
            if report["status"] != "pass": raise ValueError("bundle checksum failure")
        self.command("adb-state", self.adb+["get-state"])
        self.shell("device-directory", ["mkdir", self.remote]); self.owned_remote = True
        self.push("vulkan-deploy", sorted((self.vulkan/"android-arm64").iterdir()))
        self.shell("device-executable", ["chmod", "755", self.remote+"/vr444", self.remote+"/vr444-probe"])
        save(self.out/"client-before.json", client(self.args.serial))
        save(self.out/"host.json", host(self.args.render_node, self.vulkan/"linux-x86_64/vr444-probe"))
        sysfs = Path("/sys/class/drm")/Path(self.args.render_node).name/"device"
        vendor, device = (int((sysfs/key).read_text(), 16) for key in ("vendor", "device"))
        if (vendor, device) != (0x8086, 0x56a0): raise ValueError("this test profile requires Arc A770 PCI 8086:56a0")
        self.command("host-vulkan-probe", [self.vulkan/"linux-x86_64/vr444-probe"])
        gpu = select_gpu(json.loads((self.out/"host-vulkan-probe.log").read_text()), vendor, device)
        self.env["VR444_DEVICE"] = str(gpu["index"])
        self.details["host_gpu"] = gpu
        self.shell("quest-vulkan-probe", ["timeout", "30", self.remote+"/vr444-probe"])
        probe = json.loads((self.out/"quest-vulkan-probe.log").read_text())
        if len(probe["devices"]) != 1 or probe["devices"][0]["device_type"] == 4:
            raise ValueError("expected one hardware headset Vulkan device")
        self.details["quest_gpu"] = probe["devices"][0]
        harness = HERE/"hardware/android/codec-harness.jar"
        if harness.is_file():
            report = verify(HERE)
            save(self.out/"suite-verify.json", report)
            if report["status"] != "pass": raise ValueError("packaged harness checksum failure")
        else:
            self.command("build-mediacodec", ["bash", HERE/"hardware/build.sh", self.out/"java-build"])
            harness = self.out/"java-build/codec-harness.jar"
        self.details["mediacodec_harness_sha256"] = digest(harness)
        self.push("mediacodec-deploy", [harness])
        self.java("mediacodec-probe", ["probe", self.remote+"/mediacodec.json"])
        self.pull("mediacodec-probe-pull", self.remote+"/mediacodec.json", self.out/"mediacodec.json")
        self.decoder = select_decoder(json.loads((self.out/"mediacodec.json").read_text()), self.args.decoder)
        self.details["hevc_decoder"] = self.decoder

    def vulkan_tests(self):
        timings = {}
        for side in ("host", "quest"):
            for mode in ("self-test", "foveation-test"):
                name = side+"-"+mode
                if side == "host": self.command(name, [self.binary, "--"+mode], env=self.env, required=False)
                else: self.shell(name, ["timeout", "180", self.remote+"/vr444", "--"+mode], timeout=190, required=False)
            for kernel in ("baseline", "coop64", "coop128"):
                for mode in ("gpu-test", "gpu-bench", "gpu-foveation"):
                    count = 1 if mode == "gpu-test" else self.args.repeats
                    for repeat in range(count):
                        name = f"{side}-{kernel}-{mode}-{repeat}"
                        if side == "host":
                            self.command(name, [self.binary, "--"+mode, self.binary.parent], env=dict(self.env, VR444_KERNEL=kernel), required=False)
                        else:
                            self.shell(name, ["env", "VR444_KERNEL="+kernel, "timeout", "180", self.remote+"/vr444", "--"+mode, self.remote], timeout=190, required=False)
                        text = (self.out/(name+".log")).read_text()
                        if mode != "gpu-test":
                            for key in ("encode_dispatch_ms", "decode_dispatch_ms", "submit_wait_ms"):
                                match = re.search(r"\b"+key+r"=([\d.]+)", text)
                                if match: timings.setdefault(f"{side}/{kernel}/{mode}/{key}", []).append(float(match[1]))
        self.details["vulkan_cold_dispatch_ms"] = {key: distribution(v) for key, v in timings.items()}
        self.details["vulkan_timing_note"] = "Repeated cold process/objects with CPU verification, not sustained VR pipeline latency"

    def cross_decode(self):
        # Host CPU wire encoder -> ADB file transfer -> Quest GPU -> host byte comparison.
        # All device-local GPU encode paths are checked against the same CPU reference above.
        width, height = 576, 640
        y, x = np.indices((height, width)); rgba = np.empty((height, width, 4), np.uint8)
        rgba[:, :, 0] = x % 256; rgba[:, :, 1] = y % 256; rgba[:, :, 2] = (x*17+y*31) % 256; rgba[:, :, 3] = 255
        source = self.out/"cross-input.rgba"; source.write_bytes(rgba.tobytes())
        for q in (1, 4, 8, 16):
            wire = self.out/f"cross-q{q}.vr44"; reference = self.out/f"cross-q{q}-reference.rgba"
            self.command(f"cross-q{q}-encode", [self.binary, "--encode", source, width, height, q, "--out", wire])
            self.command(f"cross-q{q}-reference", [self.binary, "--decode", wire, reference])
            self.push(f"cross-q{q}-push", [wire])
            for kernel in ("baseline", "coop64", "coop128"):
                name = f"cross-q{q}-{kernel}"; remote_output = self.remote+"/"+name+".rgba"
                if self.shell(name, ["env", "VR444_KERNEL="+kernel, "timeout", "180", self.remote+"/vr444", "--gpu-decode",
                                     self.remote+"/"+wire.name, self.remote, remote_output], timeout=190, required=False): continue
                target = self.out/(name+".rgba"); self.pull(name+"-pull", remote_output, target)
                self.record(name+"-pixels", "pass" if digest(reference) == digest(target) else "fail",
                            reference_sha256=digest(reference), actual_sha256=digest(target))

    def hevc_tests(self):
        spec = importlib.util.spec_from_file_location("carrier_atlas", self.hevc/"atlas.py")
        atlas = importlib.util.module_from_spec(spec); spec.loader.exec_module(atlas)
        for encoder in self.args.encoders:
            folder = self.out/encoder
            if self.command(encoder+"-encode", [sys.executable, self.hevc/"atlas.py", folder, "--frames", self.args.frames,
                                               "--encoder", encoder, "--device", self.args.render_node], timeout=900, required=False): continue
            for rate in (50, 100, 150):
                try: self.hevc_case(folder, encoder, rate, atlas)
                except (ValueError, RuntimeError, OSError) as error:
                    self.record(f"{encoder}-{rate}-validation", "fail", error=str(error))

    def hevc_case(self, folder, encoder, rate, atlas):
        name = f"{encoder}-{rate}"; bitstream = folder/f"atlas-{rate}.hevc"; mp4 = folder/f"atlas-{rate}.mp4"
        self.command(name+"-mux", ["ffmpeg", "-v", "error", "-r", "90", "-i", bitstream, "-c:v", "copy", "-tag:v", "hvc1", mp4])
        self.command(name+"-ffprobe", ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_streams", "-show_frames", "-of", "json", mp4])
        metadata = json.loads((self.out/(name+"-ffprobe.log")).read_text())
        if metadata["streams"][0].get("color_range") != "pc": raise ValueError("HEVC stream is not signalled full range")
        pts = [round(float(f["best_effort_timestamp_time"])*1e6) for f in metadata["frames"]]
        raw = folder/f"atlas-{rate}-reference.y"
        self.command(name+"-reference", ["ffmpeg", "-v", "error", "-i", mp4, "-vf", "extractplanes=y", "-pix_fmt", "gray", "-f", "rawvideo", raw])
        if raw.stat().st_size != 1152*1920*self.args.frames: raise ValueError("software reference size mismatch")
        ref_hash = digest(raw)
        self.push(name+"-push", [mp4], self.remote+"/"+name+".mp4")
        modes = [False, True] if self.decoder["low_latency"] else [False]
        if not self.decoder["low_latency"]:
            self.record(name+"-low-latency", "unsupported", reason="decoder does not advertise low-latency; requested mode not tested")
        for low in modes:
            for capture in (True, False):
                run_name = name+f"-low{int(low)}-"+("capture" if capture else "timing")
                remote_out = self.remote+"/"+run_name
                code = self.java(run_name, ["decode", self.remote+"/"+name+".mp4", remote_out, self.decoder["name"],
                                           str(self.args.frames), str(low).lower(), str(capture).lower()], required=False)
                self.pull(run_name+"-pull", remote_out, self.out/run_name)
                if code: continue
                result = json.loads((self.out/run_name/"result.json").read_text())
                validate_frames(result, self.args.frames, pts)
                if not capture:
                    samples = [r["queue_to_output_ms"] for r in result["frames"][5:]]
                    self.details[run_name] = dict(queue_to_output_ms=distribution(samples), warmup_frames=5,
                                                elapsed_ms=result["elapsed_ms"], max_pending_frames=result["max_pending_frames"],
                                                note=result["timing_semantics"])
                    continue
                comparisons = []
                with raw.open("rb") as stream:
                    for row in result["frames"]:
                        if "capture" not in row: continue
                        frame = row["frame"]; info = row["capture"]
                        if (info["width"], info["height"]) != (1152, 1920): raise ValueError("incorrect image crop")
                        if info["image_format"] != 35: raise ValueError("expected YUV_420_888 luma")
                        stream.seek(frame*1152*1920); reference = stream.read(1152*1920)
                        reference_path = self.out/run_name/f"reference-{frame}.y"; reference_path.write_bytes(reference)
                        actual_path = self.out/run_name/info["file"]
                        actual = np.frombuffer(actual_path.read_bytes(), np.uint8).reshape(3, 640, 1152).transpose(1, 2, 0)
                        expected = np.frombuffer(reference, np.uint8).reshape(3, 640, 1152).transpose(1, 2, 0)
                        source = atlas.picture(1152, 640, frame)
                        comparisons.append(dict(frame=frame, hardware_vs_software=rgb_metrics(actual, expected),
                            source_to_decoded=[rgb_metrics(actual[:, e*576:(e+1)*576], source[:, e*576:(e+1)*576]) for e in (0, 1)]))
                if {c["frame"] for c in comparisons} != {0, self.args.frames//2, self.args.frames-1}:
                    raise ValueError("missing predetermined quality samples")
                report = dict(reference_full_sha256=ref_hash, samples=comparisons,
                              sampling="first, middle, last; RGB panels reconstructed directly from cropped luma")
                save(self.out/run_name/"quality.json", report)
                self.record(run_name+"-quality", "pass" if all(c["hardware_vs_software"]["exact"] for c in comparisons) else "fail")
        raw.unlink()  # Sampled reference planes plus whole-reference digest are retained.

    def finish(self):
        if self.owned_remote:
            save(self.out/"client-after.json", client(self.args.serial))
            if not self.args.keep_device_files:
                self.shell("device-cleanup", ["rm", "-rf", self.remote], required=False)
        value = self.summary()
        lines = ["Standalone codec hardware report", "Status: "+value["status"], value["scope"], "",
                 "Vulkan cold GPU dispatch timings (ms; CPU verification and object setup outside these timestamps):"]
        for key, stats in self.details.get("vulkan_cold_dispatch_ms", {}).items():
            lines.append(f"  {key}: n={stats['samples']} p50={stats['p50']:.3f} p95={stats['p95']:.3f}")
        lines += ["", "HEVC queue-to-output timings (paced 90 Hz, max 4 pending, first 5 frames excluded):"]
        for key, row in self.details.items():
            if isinstance(row, dict) and "queue_to_output_ms" in row:
                stats = row["queue_to_output_ms"]
                if stats["measured"]:
                    lines.append(f"  {key}: n={stats['samples']} p50={stats['p50']:.3f} p95={stats['p95']:.3f} ms")
        lines += ["", "These include scheduling/queueing/polling; they are not decoder-engine or display latency.",
                  "All p99 statistics with fewer than 1000 samples are low confidence.", "", "Failures and limitations:"]
        lines += ["  "+t["test"]+": "+t["status"]+" "+t.get("reason", t.get("error", "see log"))
                  for t in self.tests if t["status"] != "pass"]
        (self.out/"REPORT.txt").write_text("\n".join(lines)+"\n")
        save(self.out/"hashes.json", {str(p.relative_to(self.out)): digest(p) for p in sorted(self.out.rglob("*"))
                                     if p.is_file() and p.name != "hashes.json"})
        return value


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--bundles", type=Path, required=True); p.add_argument("--out", type=Path, required=True)
    p.add_argument("--serial", required=True); p.add_argument("--render-node", required=True)
    p.add_argument("--frames", type=int, default=30); p.add_argument("--repeats", type=int, default=3)
    p.add_argument("--encoders", nargs="+", choices=("libx265", "hevc_qsv"), default=["libx265", "hevc_qsv"])
    p.add_argument("--decoder"); p.add_argument("--keep-device-files", action="store_true")
    args = p.parse_args()
    if not 6 <= args.frames <= 900 or not 1 <= args.repeats <= 100: p.error("frames=6..900; repeats=1..100")
    run = HardwareRun(args)
    try:
        run.prepare()
        for name, action in (("vulkan", run.vulkan_tests), ("cross-decode", run.cross_decode), ("hevc", run.hevc_tests)):
            try: action()
            except (ValueError, RuntimeError, OSError) as error: run.record(name, "fail", error=str(error))
        run.completed = True
    except (ValueError, RuntimeError, OSError) as error: run.record("preparation", "fail", error=str(error))
    finally: result = run.finish()
    print(json.dumps(dict(status=result["status"], evidence=str(run.out)), indent=2))
    return 0 if result["status"] in ("pass", "pass_with_limitations") else 2


if __name__ == "__main__":
    raise SystemExit(main())
