// SPDX-License-Identifier: GPL-3.0-or-later
#include "controller_roles.h"
#include <cassert>
#include <initializer_list>

int main()
{
	using namespace wivrn;
	// Lighthouse left + Quest right: keep Index bindings through hand tracking,
	// missing headset controllers, and headset reconnection.
	for (auto report : {XRT_DEVICE_INVALID, XRT_DEVICE_TOUCH_CONTROLLER, XRT_DEVICE_EXT_HAND_INTERACTION})
	{
		assert(controller_profile(8, 1, 2, XRT_DEVICE_INDEX_CONTROLLER, report) == XRT_DEVICE_INDEX_CONTROLLER);
		assert(controller_profile(3, 3, 4, XRT_DEVICE_TOUCH_CONTROLLER, report) == report);
		assert(controller_profile(4, 3, 4, XRT_DEVICE_EXT_HAND_INTERACTION, report) == report);
		assert(controller_profile(-1, 3, 4, XRT_DEVICE_INVALID, report) == report);
	}
	assert(lighthouse_controller_hand(XRT_DEVICE_TYPE_LEFT_HAND_CONTROLLER, true, true, true) == 0);
	assert(lighthouse_controller_hand(XRT_DEVICE_TYPE_RIGHT_HAND_CONTROLLER, true, true, true) == 1);
	assert(lighthouse_controller_hand(XRT_DEVICE_TYPE_ANY_HAND_CONTROLLER, true, true, true) == 0);
	assert(lighthouse_controller_hand(XRT_DEVICE_TYPE_ANY_HAND_CONTROLLER, true, false, true) == 1);
	assert(lighthouse_controller_hand(XRT_DEVICE_TYPE_ANY_HAND_CONTROLLER, true, false, false) == -1);
	assert(lighthouse_controller_hand(XRT_DEVICE_TYPE_GENERIC_TRACKER, true, true, true) == -1);
	for (auto type : {XRT_DEVICE_TYPE_LEFT_HAND_CONTROLLER, XRT_DEVICE_TYPE_RIGHT_HAND_CONTROLLER, XRT_DEVICE_TYPE_ANY_HAND_CONTROLLER})
		assert(lighthouse_controller_hand(type, false, true, true) == -1);
}
