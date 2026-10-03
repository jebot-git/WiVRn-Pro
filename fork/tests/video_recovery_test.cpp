// SPDX-License-Identifier: GPL-3.0-or-later
#include "encoder/encode_batch.h"
#include "encoder/recovery_policy.h"
#include "encoder/slot_ring.h"
#include <array>
#include <cassert>
#include <iostream>
#include <memory>
#include <semaphore>
#include <stdexcept>
#include <thread>

int main()
{
	using wivrn::recovery_policy;
	recovery_policy p;
	p.submitted(100, 10);
	p.tick(2100, true, true);
	assert(p.status == recovery_policy::state::requested);
	assert(!p.due(2100, true) && !p.due(2300, false) && p.due(2300, true));
	assert(p.begin());
	p.finished(2300, true);
	p.submitted(2310, 20);
	p.decoded(2320, 19);
	assert(p.status == recovery_policy::state::awaiting_decode);
	p.decoded(2330, 20);
	assert(p.status == recovery_policy::state::healthy && p.attempts == 1);
	p.tick(4400, true, true);
	assert(p.begin());
	p.finished(4400, false);
	assert(!p.due(4500, true));
	assert(p.due(5000, true));
	assert(p.begin());
	p.finished(5000, false);
	assert(p.status == recovery_policy::state::failed);
	p.request(6000);
	assert(p.status == recovery_policy::state::failed);
	p.request(6000, true);
	assert(p.attempts == 0 && p.begin());
	p.finished(6100, true);
	p.submitted(6120, 30);
	p.tick(20000, false, true);
	assert(p.status == recovery_policy::state::awaiting_decode);
	p.resume(21000);
	p.tick(50000, true, false);
	assert(!p.armed);
	p.submitted(50010, 40);
	p.decoded(50020, 40);
	p.decoded(55030, 41);
	assert(p.attempts == 0);

	// Actual slot ring: backpressure never blocks; skip and exception paths free
	// their slots, and a queued skipped slot cannot be overwritten by producer.
	wivrn::slot_ring<2> ring;
	auto a = ring.reserve(), b = ring.reserve();
	assert(a && b && !ring.reserve());
	ring.skip_reserved(*a);
	{
		auto lease = ring.consume();
		assert(lease.skipped && lease.slot == *a);
	}
	assert(ring.reserve());
	try
	{
		auto lease = ring.consume();
		assert(!lease.skipped);
		throw std::runtime_error("encode");
	}
	catch (const std::exception &)
	{}
	assert(ring.reserve());

	struct fake
	{
		unsigned stream_idx;
		wivrn::slot_ring<2> slots;
		bool committed = false;
		void commit_encoded(bool keep)
		{
			committed = keep;
		}
	};
	for (unsigned broken = 0; broken < 3; ++broken)
	{
		std::array<std::unique_ptr<fake>, 3> streams;
		for (unsigned i = 0; i < 3; ++i)
		{
			streams[i] = std::make_unique<fake>();
			streams[i]->stream_idx = i;
			assert(streams[i]->slots.reserve());
		}
		unsigned consumed = 0, errors = 0;
		wivrn::encode_batch(streams, std::array{true, true, true}, [&](fake & f) {
   auto lease=f.slots.consume();++consumed;
   if (f.stream_idx==broken) throw std::runtime_error("injected codec reset failure"); }, [&](fake &, const std::exception &) { ++errors; });
		assert(consumed == 3 && errors == 1);
		for (auto & f: streams)
		{
			assert(!f->committed);
			assert(f->slots.reserve());
		}
		wivrn::encode_batch(streams, std::array{true, true, true}, [](fake & f) { auto lease = f.slots.consume(); }, [](fake &, const std::exception &) { assert(false); });
		for (auto & f: streams)
			assert(f->committed);
	}
	// Exercise the production ring with concurrent producer/consumer ownership.
	wivrn::slot_ring<2> concurrent;
	std::counting_semaphore<2> queued{0};
	std::thread producer([&] {
		for (unsigned i = 0; i < 10000; ++i)
		{
			std::optional<uint8_t> slot;
			while (!(slot = concurrent.reserve()))
				std::this_thread::yield();
			if (i % 3 == 0)
				concurrent.skip_reserved(*slot);
			queued.release();
		}
	});
	for (unsigned i = 0; i < 10000; ++i)
	{
		queued.acquire();
		auto lease = concurrent.consume();
		assert(lease.skipped == (i % 3 == 0));
	}
	producer.join();
	assert(concurrent.ready());

	std::cout << "PASS recovery budgets, sleep/reconnect, stale feedback, slot ownership, all-eye failure cleanup and stereo commit\n";
}
