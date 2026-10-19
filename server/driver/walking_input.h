// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include <array>
#include <bit>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <optional>
#include <span>
namespace wivrn
{
struct walking_sample
{
	uint32_t flags = 0;
	int64_t time = 0;
	uint64_t generation = 0, sequence = 0, steps = 0;
	float forward = 0, turn = 0;
};
inline std::optional<walking_sample> decode_walking(std::span<const uint8_t> bytes, int64_t now)
{
	if (bytes.size() != 48 || std::memcmp(bytes.data(), "WVW1", 4))
		return {};
	auto u32 = [&](unsigned at) { return uint32_t(bytes[at]) | uint32_t(bytes[at + 1]) << 8 | uint32_t(bytes[at + 2]) << 16 | uint32_t(bytes[at + 3]) << 24; };
	auto u64 = [&](unsigned at) { return uint64_t(u32(at)) | uint64_t(u32(at + 4)) << 32; };
	walking_sample s{u32(4), int64_t(u64(8)), u64(16), u64(24), u64(32), std::bit_cast<float>(u32(40)), std::bit_cast<float>(u32(44))};
	if ((s.flags & ~7u) || s.time <= 0 || s.time > now + 50'000'000 || now - s.time > 150'000'000 || !s.generation || !s.sequence ||
	    !std::isfinite(s.forward) || !std::isfinite(s.turn) || s.forward < 0 || s.forward > 1 || std::abs(s.turn) > 1 ||
	    (!(s.flags & 2) && s.turn != 0))
		return {};
	return s;
}
// One owner per stick. Each interruption requires two NEW alternating steps.
struct walking_gate
{
	bool available = false, armed = false;
	uint64_t generation = 0, baseline = 0;
	void reset()
	{
		available = false;
		armed = false;
	}
	std::array<float, 2> update(const walking_sample & s, bool enabled, bool blocked, bool turning)
	{
		if (!enabled || blocked || !(s.flags & 1))
		{
			reset();
			return {};
		}
		if (!available || generation != s.generation || s.steps < baseline)
		{
			available = true;
			armed = false;
			generation = s.generation;
			baseline = s.steps;
		}
		if (s.steps >= baseline + 2 && (s.flags & 2))
			armed = true;
		if (!armed)
			return {};
		if (turning)
			return {(s.flags & 2) && (s.flags & 4) ? s.turn : 0, 0};
		return {0, s.forward};
	}
};
} // namespace wivrn
