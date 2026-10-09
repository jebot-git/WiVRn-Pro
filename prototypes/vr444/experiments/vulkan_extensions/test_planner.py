import copy
import unittest
from planner import evaluate, historical


def current():
    feature = dict(advertised=True, queried=True, supported=True, enabled=False)
    return dict(schema=1, logical_device_created=False, devices=[dict(
        name="test GPU", timeline=copy.deepcopy(feature), synchronization2=copy.deepcopy(feature),
        host_image_copy=copy.deepcopy(feature), image_format_list_advertised=True,
        max_timeline_difference=100, host_copy_srgb_optimal=dict(queried=True, format=43,
        usage="HOST_TRANSFER|SAMPLED", vk_result=0, general_destination=True,
        optimal_device_access=True))])


class PlannerTests(unittest.TestCase):
    def test_current_capabilities_remain_disabled(self):
        result=evaluate(current())
        self.assertFalse(result["production_enabled"])
        for key in ("timeline_retirement", "synchronization2_retirement", "host_copy_srgb"):
            self.assertEqual(result["devices"][0][key]["status"], "offline_candidate")
            self.assertFalse(result["devices"][0][key]["production_enabled"])

    def test_advertisement_never_substitutes_for_query(self):
        data=current()
        for key in ("timeline", "synchronization2", "host_image_copy"):
            data["devices"][0][key]["queried"]=False
        for key in ("timeline_retirement", "synchronization2_retirement", "host_copy_srgb"):
            self.assertEqual(evaluate(data)["devices"][0][key]["status"], "blocked_unqueried")

    def test_negative_gates(self):
        modifications = [
            (lambda d: d["timeline"].update(advertised=False), "timeline_retirement", "blocked_missing_extension"),
            (lambda d: d["timeline"].update(supported=False), "timeline_retirement", "blocked_unsupported_feature"),
            (lambda d: d.update(max_timeline_difference=None), "timeline_retirement", "blocked_unqueried"),
            (lambda d: d.update(max_timeline_difference=2), "timeline_retirement", "blocked_limit"),
            (lambda d: d["timeline"].update(queried=False), "synchronization2_retirement", "blocked_dependency"),
            (lambda d: d["host_copy_srgb_optimal"].update(queried=False), "host_copy_srgb", "blocked_unqueried"),
            (lambda d: d["host_copy_srgb_optimal"].update(format=37), "host_copy_srgb", "blocked_query_mismatch"),
            (lambda d: d["host_copy_srgb_optimal"].update(vk_result=-11), "host_copy_srgb", "blocked_image_format"),
            (lambda d: d["host_copy_srgb_optimal"].update(general_destination=False), "host_copy_srgb", "blocked_layout"),
            (lambda d: d["host_copy_srgb_optimal"].update(optimal_device_access=False), "host_copy_srgb", "needs_image_access_benchmark"),
        ]
        for change, key, expected in modifications:
            with self.subTest(key=key, expected=expected):
                data=current(); change(data["devices"][0])
                self.assertEqual(evaluate(data)["devices"][0][key]["status"], expected)

    def test_contradictory_enablement_rejected(self):
        data=current(); data["devices"][0]["timeline"]["enabled"]=True
        with self.assertRaises(ValueError): evaluate(data)

    def test_historical_names_remain_unqueried(self):
        log="Available Vulkan device extensions:\n    VK_KHR_timeline_semaphore (version 2)\n    VK_KHR_synchronization2 (version 1)\nInitializing Vulkan with device Adreno\n"
        result=evaluate(historical(log))
        self.assertTrue(result["historical_only"])
        self.assertEqual(result["devices"][0]["timeline_retirement"]["status"], "blocked_unqueried")
        self.assertEqual(result["devices"][0]["host_copy_srgb"]["status"], "blocked_missing_extension")
        for broken in (log+log,log.split("Initializing")[0],log.replace("VK_KHR_timeline_semaphore (version 2)","bad extension row")):
            with self.assertRaises(ValueError): historical(broken)


if __name__ == "__main__": unittest.main()
