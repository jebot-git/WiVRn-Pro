/*
 * WiVRn VR streaming
 * Copyright (C) 2022  Guillaume Meunier <guillaume.meunier@centraliens.net>
 * Copyright (C) 2022  Patrick Nicolas <patricknicolas@laposte.net>
 *
 * This program is free software: you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation, either version 3 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program.  If not, see <https://www.gnu.org/licenses/>.
 */

#include "application.h"
#include "stream.h"
#include <spdlog/spdlog.h>
#include <cmath>
#include <algorithm>

void scenes::stream::read_actions()
{
	from_headset::inputs inputs;

	if (not is_gui_interactable() && !cutout_input_suppressed)
	{
		for (const auto & [id, action, action_type]: input_actions)
		{
			switch (action_type)
			{
				case XR_ACTION_TYPE_BOOLEAN_INPUT: {
					auto value = application::read_action_bool(action);
					if (value)
						inputs.values.push_back({id, (float)value->second, value->first});
				}
				break;

				case XR_ACTION_TYPE_FLOAT_INPUT: {
					auto value = application::read_action_float(action);
					if (value)
						inputs.values.push_back({id, value->second, value->first});
				}
				break;

				case XR_ACTION_TYPE_VECTOR2F_INPUT: {
					auto value = application::read_action_vec2(action);
					if (value)
					{
						inputs.values.push_back({id, value->second.x, value->first});
						inputs.values.push_back({(device_id)((int)id + 1), value->second.y, value->first});
					}
				}
				break;

				case XR_ACTION_TYPE_POSE_INPUT:
				default:
					break;
			}
		}
	}

	try
	{
		network_session->send_stream(std::move(inputs));
	}
	catch (std::exception & e)
	{
		spdlog::warn("Exception while sending inputs packet: {}", e.what());
	}
}

void scenes::stream::operator()(to_headset::haptics && haptics)
{
	auto range = haptics_actions.equal_range(haptics.id);
	for (auto it = range.first; it != range.second; ++it)
	{
		XrAction action = it->second.action;
		float old = it->second.amplitude;
		it->second.amplitude = haptics.amplitude;
		// Some runtimes may be slow to process actions
		// Skip it if not necessary
		if (old == 0 and haptics.amplitude == 0)
			continue;

		if (haptics.amplitude > 0)
			application::haptic_start(action, XR_NULL_PATH, haptics.duration.count(), haptics.frequency, std::min(1.0f, haptics.amplitude));
		else
			application::haptic_stop(action, XR_NULL_PATH);
	}
}

// Read placement inputs on the render thread, in the same STAGE as the anchors.
void scenes::stream::update_cutout(XrTime time)
{
    cutout_block_gui = false;
    if (!spatial || (!spatial->portal.designing && !cutout_input_suppressed)) return;
    cutout_input_suppressed = true;
    if (spatial->capture_active()) spatial->portal.cancel();
    std::array<wivrn::cutout::hand, 2> hands{};
    const std::array spaces{xr::spaces::grip_left, xr::spaces::grip_right};
    const std::array names{"left_trigger", "right_trigger"};
    for (size_t i = 0; i < 2; ++i)
    {
        auto value = application::read_action_float(get_action(names[i]).first);
        XrSpaceLocation location{XR_TYPE_SPACE_LOCATION};
        auto space = application::space(spaces[i]);
        if (space && value && XR_SUCCEEDED(xrLocateSpace(space, application::space(xr::spaces::world), time, &location))
            && wivrn::room_calibration::tracked({location.pose, location.locationFlags}))
            hands[i] = {location.pose.position, value->second, 1, true};
    }
    if ((!hands[0].valid || !hands[1].valid) && system.hand_tracking_supported())
    {
        if (!left_hand) left_hand = session.create_hand_tracker(XR_HAND_LEFT_EXT);
        if (!right_hand) right_hand = session.create_hand_tracker(XR_HAND_RIGHT_EXT);
        std::array trackers{&*left_hand, &*right_hand};
        // Do not mix controller and hand geometry within one gesture.
        hands = {};
        for (size_t i = 0; i < 2; ++i)
            if (auto joints = trackers[i]->locate(application::space(xr::spaces::world), time); joints && joints->is_input_source())
            {
                const auto & a = joints->joints[XR_HAND_JOINT_THUMB_TIP_EXT].first;
                const auto & b = joints->joints[XR_HAND_JOINT_INDEX_TIP_EXT].first;
                constexpr auto flags = XR_SPACE_LOCATION_POSITION_VALID_BIT | XR_SPACE_LOCATION_POSITION_TRACKED_BIT;
                if ((a.locationFlags & flags) == flags && (b.locationFlags & flags) == flags)
                {
                    auto p = a.pose.position, q = b.pose.position;
                    float distance = std::sqrt((p.x-q.x)*(p.x-q.x) + (p.y-q.y)*(p.y-q.y) + (p.z-q.z)*(p.z-q.z));
                    hands[i] = {{(p.x+q.x)/2, (p.y+q.y)/2, (p.z+q.z)/2}, std::clamp((.05f-distance)/.03f, 0.f, 1.f), 2, true};
                }
            }
    }
    cutout_block_gui = hands[0].press >= .75f && hands[1].press >= .75f;
    if (!spatial->portal.designing)
    {
        cutout_block_gui = true;
        if ((!hands[0].valid || hands[0].press < .25f) && (!hands[1].valid || hands[1].press < .25f))
            cutout_input_suppressed = cutout_block_gui = false;
        return;
    }
    spatial->portal.update(spatial->sample(), hands, time);
}
