// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once

#include "xrt/xrt_defines.h"

namespace wivrn
{
// Headset packets may update WiVRn hands, but must preserve another driver's
// interaction profile (including when the headset reports no controllers).
inline xrt_device_name controller_profile(int role, int controller, int hand,
                                         xrt_device_name current, xrt_device_name reported)
{
	return role < 0 || role == controller || role == hand ? reported : current;
}

// -1: tracker/unselected device; 0/1: left/right controller role.
inline int lighthouse_controller_hand(xrt_device_type type, bool enabled, bool left_free, bool right_free)
{
	if (!enabled)
		return -1;
	switch (type)
	{
		case XRT_DEVICE_TYPE_LEFT_HAND_CONTROLLER: return 0;
		case XRT_DEVICE_TYPE_RIGHT_HAND_CONTROLLER: return 1;
		case XRT_DEVICE_TYPE_ANY_HAND_CONTROLLER: return left_free ? 0 : right_free ? 1 : -1;
		default: return -1;
	}
}
}
