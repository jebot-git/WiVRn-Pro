#!/usr/bin/env python3
"""Conservative offline extension decisions; this never enables device features."""
import argparse
import hashlib
import json
import re
from pathlib import Path

EXTENSIONS = {
    "timeline": "VK_KHR_timeline_semaphore",
    "synchronization2": "VK_KHR_synchronization2",
    "host_image_copy": "VK_EXT_host_image_copy",
}


def historical(log):
    """Extract one complete advertised-name section, keeping all feature bits unknown."""
    starts = [i for i, line in enumerate(log.splitlines()) if "Available Vulkan device extensions:" in line]
    if len(starts) != 1:
        raise ValueError("expected exactly one historical device-extension section")
    lines = log.splitlines()
    names = set()
    device = None
    for line in lines[starts[0]+1:]:
        end = re.search(r"Initializing Vulkan with device (.+)$", line)
        if end:
            device = end.group(1)
            break
        match = re.search(r"\b(VK_[A-Za-z0-9_]+) \(version [0-9]+\)$", line)
        if not match:
            raise ValueError("incomplete or unexpected historical extension section")
        names.add(match.group(1))
    if not device or not names:
        raise ValueError("historical device identity/section terminator missing")
    result = {
        "schema": 1,
        "physical_query_only": False,
        "logical_device_created": False,
        "historical_only": True,
        "scope": "read-only existing capture; names advertised then, feature/format queries unknown",
        "devices": [{
            "index": 0, "name": device, "device_type": None, "api_version": None,
            "image_format_list_advertised": "VK_KHR_image_format_list" in names,
            "max_timeline_difference": None,
            "host_copy_srgb_optimal": {"queried": False},
            **{key: {"advertised": name in names, "queried": False, "supported": None, "enabled": False}
               for key, name in EXTENSIONS.items()},
        }],
    }
    return result


def evaluate(snapshot):
    if type(snapshot.get("schema")) is not int or snapshot["schema"] != 1 or snapshot.get("logical_device_created") is not False:
        raise ValueError("expected physical-only capability snapshot")
    devices = snapshot.get("devices")
    if not isinstance(devices, list) or not devices:
        raise ValueError("missing devices")
    results = []
    for device in devices:
        def decision(status, reason):
            return dict(status=status, reason=reason, production_enabled=False)

        def feature(key):
            value = device.get(key)
            if not isinstance(value, dict):
                return decision("blocked_unqueried", "feature evidence missing")
            if value.get("enabled") is not False:
                raise ValueError("physical-only snapshot must not claim feature enablement")
            # This experiment intentionally uses the extension entry-point route.
            # A future core-only implementation needs a different explicit gate.
            if value.get("advertised") is not True:
                return decision("blocked_missing_extension", "required extension route not advertised")
            if value.get("queried") is not True or type(value.get("supported")) is not bool:
                return decision("blocked_unqueried", "advertisement does not establish feature support")
            if not value["supported"]:
                return decision("blocked_unsupported_feature", "queried feature bit is false")
            return decision("offline_candidate", "advertised and queried supported; device feature still not enabled")

        timeline = feature("timeline")
        if timeline["status"] == "offline_candidate":
            limit = device.get("max_timeline_difference")
            if type(limit) is not int:
                timeline = decision("blocked_unqueried", "timeline difference limit missing")
            elif limit < 3:
                timeline = decision("blocked_limit", "three-slot pending range exceeds reported limit")
        sync = feature("synchronization2")
        if sync["status"] == "offline_candidate" and timeline["status"] != "offline_candidate":
            sync = decision("blocked_dependency", "this submit2 retirement prototype also requires timeline eligibility")
        host = feature("host_image_copy")
        if host["status"] == "offline_candidate":
            image = device.get("host_copy_srgb_optimal", {})
            if image.get("queried") is not True:
                host = decision("blocked_unqueried", "exact SRGB optimal image format/usage query missing")
            elif type(image.get("format")) is not int or image["format"] != 43 or image.get("usage") != "HOST_TRANSFER|SAMPLED":
                host = decision("blocked_query_mismatch", "format query does not match proposed image")
            elif type(image.get("vk_result")) is not int or image["vk_result"] != 0:
                host = decision("blocked_image_format", "required optimal image format/usage rejected")
            elif image.get("general_destination") is not True:
                host = decision("blocked_layout", "proposed GENERAL host-copy destination unavailable")
            elif image.get("optimal_device_access") is not True:
                host = decision("needs_image_access_benchmark", "query does not promise ordinary-image device access efficiency; no speed prediction")
            else:
                host = decision("offline_candidate", "format/layout support and optimal-device-access guarantee queried; host copy and full sample workload still need testing")
        format_list = decision("requires_image_format_query", "extension name alone does not validate mutable storage/SRGB views") if device.get("image_format_list_advertised") is True else decision("blocked_missing_extension", "optional format-list route not advertised")
        results.append(dict(name=device.get("name"), timeline_retirement=timeline,
                            synchronization2_retirement=sync, host_copy_srgb=host,
                            image_format_list=format_list))
    return dict(schema=1, historical_only=snapshot.get("historical_only") is True,
                devices=results, performance_claim=False, headset_access=False,
                production_enabled=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--historical-log", action="store_true")
    args = parser.parse_args()
    raw = args.input.read_bytes()
    snapshot = historical(raw.decode()) if args.historical_log else json.loads(raw)
    result = evaluate(snapshot)
    result["input_sha256"] = hashlib.sha256(raw).hexdigest()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
