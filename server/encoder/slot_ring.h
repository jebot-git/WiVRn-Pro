// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include <array>
#include <atomic>
#include <cstdint>
#include <optional>

namespace wivrn
{
// One producer and one consumer. Every successful reservation must be consumed,
// including skip and failed-submit slots. Resource/fence lifetime is external.
template <uint8_t Count>
class slot_ring
{
	enum : unsigned
	{
		idle,
		busy,
		skip
	};
	std::array<std::atomic<unsigned>, Count> states{};
	uint8_t producer = 0, consumer = 0;

public:
	bool ready() const
	{
		return states[(producer + 1) % Count].load() == idle;
	}
	std::optional<uint8_t> reserve()
	{
		const auto next = uint8_t((producer + 1) % Count);
		unsigned expected = idle;
		if (!states[next].compare_exchange_strong(expected, busy))
			return {};
		producer = next;
		return next;
	}
	void skip_reserved(uint8_t slot)
	{
		states[slot] = skip;
	}
	struct lease
	{
		slot_ring & owner;
		uint8_t slot;
		bool skipped;
		~lease()
		{
			owner.states[slot] = idle;
			owner.states[slot].notify_all();
		}
		lease(const lease &) = delete;
		lease(slot_ring & owner, uint8_t slot, bool skipped) :
		        owner(owner), slot(slot), skipped(skipped) {}
	};
	lease consume()
	{
		consumer = uint8_t((consumer + 1) % Count);
		return lease(*this, consumer, states[consumer] == skip);
	}
};
} // namespace wivrn
