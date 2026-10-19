// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include "walking_input.h"
#include <array>
#include <cstdint>
#include <filesystem>
#include <mutex>
#include <optional>
#include <span>
namespace wivrn
{
struct joystick_command
{
	bool enabled = false;
	bool controllers = false;
	bool walking = false, turning = false;
	uint32_t pad_owned = 0, ui_owned = 0;
	uint32_t suppressed = 0;
	uint32_t active = 0;
	uint32_t clicks = 0;
	int64_t time = 0;
	uint64_t sequence = 0;
	std::array<std::array<float, 2>, 2> axes{};
};
std::optional<joystick_command> decode_joysticks(std::span<const uint8_t>, int64_t now);
class hand_joystick
{
	mutable std::mutex mutex;
	int directory_fd = -1, lock_fd = -1, listener = -1, client = -1;
	bool bound = false;
	int gait_fd = -1;
	bool gait_bound = false;
	walking_sample gait;
	mutable std::array<walking_gate, 2> gait_gates;
	uint32_t hands = 0;
	joystick_command command;
	int64_t received = 0;
	void disconnect();
	void cleanup();

public:
	struct value
	{
		std::array<float, 2> axes;
		bool touched;
		bool clicked;
		int64_t time;
	};
	explicit hand_joystick(std::filesystem::path directory = {});
	~hand_joystick();
	void poll(int64_t now, uint32_t hand_mask);
	std::optional<value> get(unsigned hand, int64_t now, std::array<float, 2> physical = {}) const;
	bool walking_enabled(unsigned hand, int64_t now) const;
	// Present means controller emulation is enabled; value means gestures are blocked.
	std::optional<bool> controller(unsigned hand, int64_t now) const;
	bool controllers_enabled() const;
};
} // namespace wivrn
