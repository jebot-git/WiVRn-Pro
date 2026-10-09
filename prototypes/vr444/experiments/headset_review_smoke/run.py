#!/usr/bin/env python3
"""Run a pinned, bounded standalone Android smoke plan in an owned ADB directory."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import shlex
import subprocess
import time
import uuid


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    plan = json.loads(Path(args.plan).read_text())
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=False)
    remote = "/data/local/tmp/sub15-review-" + uuid.uuid4().hex
    adb = ["adb", "-s", plan["adb_target"]]
    receipt = {"schema": 1, "start_utc": datetime.now(timezone.utc).isoformat(),
               "plan_sha256": sha(args.plan), "runner_sha256": sha(__file__),
               "headset_used": True, "root_required": False, "openxr_used": False,
               "performance_qualification": False, "promotion_eligible": False,
               "remote_directory": remote, "commands": [], "inputs": plan["files"],
               "scope": "Standalone correctness/capability smoke; portable probes may retain hardcoded host-only labels. This receipt records actual device execution.",
               "status": "running"}

    def save():
        (out / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")

    def command(label, argv, timeout=30, checked=True):
        start = time.monotonic()
        entry = {"label": label, "argv": argv, "timeout_s": timeout}
        receipt["commands"].append(entry)
        with (out / (label + ".stdout")).open("wb") as stdout, (out / (label + ".stderr")).open("wb") as stderr:
            try:
                result = subprocess.run(argv, stdout=stdout, stderr=stderr, timeout=timeout)
                entry["returncode"] = result.returncode
            except subprocess.TimeoutExpired:
                entry["host_timeout"] = True
                entry["returncode"] = None
            finally:
                entry["elapsed_s"] = time.monotonic() - start
        entry["stdout_sha256"] = sha(out / (label + ".stdout"))
        entry["stderr_sha256"] = sha(out / (label + ".stderr"))
        save()
        if checked and entry["returncode"] != 0:
            raise RuntimeError(label + " failed; inspect raw output")
        return (out / (label + ".stdout")).read_text().strip()

    def shell(label, argv, **kw):
        return command(label, adb + ["shell", shlex.join(argv)], **kw)

    try:
        serial = shell("serial", ["getprop", "ro.serialno"])
        if serial != plan["expected_serial"]:
            raise RuntimeError("ADB device serial changed")
        receipt["device_serial"] = serial
        shell("fingerprint", ["getprop", "ro.build.fingerprint"])
        shell("battery-before", ["dumpsys", "battery"])
        shell("mkdir", ["mkdir", remote])
        parents = set()
        for i, item in enumerate(plan["files"]):
            rel = PurePosixPath(item["remote"])
            if rel.is_absolute() or ".." in rel.parts or not rel.parts:
                raise ValueError("file destination must be relative")
            if sha(item["local"]) != item["sha256"]:
                raise ValueError("local artifact hash mismatch: " + item["local"])
            dest = remote + "/" + str(rel)
            parent = str(PurePosixPath(dest).parent)
            if parent != remote and parent not in parents:
                shell("mkdir-" + str(i), ["mkdir", "-p", parent])
                parents.add(parent)
            command("push-" + str(i), adb + ["push", item["local"], dest], timeout=60)
            actual = shell("hash-" + str(i), ["sha256sum", dest]).split()[0]
            if actual != item["sha256"]:
                raise ValueError("device artifact hash mismatch")
            if item.get("executable"):
                shell("chmod-" + str(i), ["chmod", "700", dest])
        for run in plan["runs"]:
            seconds = run["timeout_s"]
            if not 1 <= seconds <= 180:
                raise ValueError("smoke timeout must be 1..180 seconds")
            argv = [part.replace("{remote}", remote) for part in run["argv"]]
            shell(run["name"], ["timeout", "-s", "KILL", str(seconds)] + argv,
                  timeout=seconds + 15, checked=False)
        receipt["inputs_unchanged"] = all(sha(f["local"]) == f["sha256"] for f in plan["files"])
        receipt["status"] = "completed"
    except Exception as error:
        receipt["status"] = "failed"
        receipt["error"] = str(error)
        raise
    finally:
        shell("battery-after", ["dumpsys", "battery"], checked=False)
        shell("cleanup", ["rm", "-rf", remote], checked=False)
        shell("cleanup-verified", ["test", "!", "-e", remote], checked=False)
        receipt["finish_utc"] = datetime.now(timezone.utc).isoformat()
        save()
        print(json.dumps({"status": receipt["status"], "receipt": str(out / "receipt.json")}))


if __name__ == "__main__":
    main()
