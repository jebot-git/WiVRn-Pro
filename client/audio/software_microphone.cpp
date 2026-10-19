// SPDX-License-Identifier: GPL-3.0-or-later
#include "software_microphone.h"
#include <algorithm>
#include <memory>
#include <speex/speex_preprocess.h>
#include <stdexcept>

namespace
{
struct preprocess_deleter
{
	void operator()(SpeexPreprocessState * state) const { speex_preprocess_state_destroy(state); }
};
using preprocess_ptr = std::unique_ptr<SpeexPreprocessState, preprocess_deleter>;

preprocess_ptr make_filter(size_t frames, int rate)
{
	preprocess_ptr state(speex_preprocess_state_init(int(frames), rate));
	if (not state)
		throw std::runtime_error("Cannot create microphone noise suppressor");
	int on = 1, off = 0, attenuation = -18;
	speex_preprocess_ctl(state.get(), SPEEX_PREPROCESS_SET_DENOISE, &on);
	speex_preprocess_ctl(state.get(), SPEEX_PREPROCESS_SET_NOISE_SUPPRESS, &attenuation);
	speex_preprocess_ctl(state.get(), SPEEX_PREPROCESS_SET_AGC, &off);
	speex_preprocess_ctl(state.get(), SPEEX_PREPROCESS_SET_DEREVERB, &off);
	// No VAD gating, gain boost, or pretend echo cancellation without a reference.
	return state;
}
} // namespace

wivrn::software_microphone::software_microphone(int rate, bool denoise, sender send) :
        sample_rate(rate), frame_size(rate > 0 ? rate / 100 : 0), denoise(denoise), send(std::move(send))
{
	if (rate < 8000 or rate > 192000 or rate % 100 != 0 or not this->send)
		throw std::invalid_argument("Unsupported microphone rate or sender");
	// Allocate once here so an initialization failure is visible to the caller.
	if (denoise)
		make_filter(frame_size, rate);
	worker = std::thread([this] { run(); });
}

wivrn::software_microphone::~software_microphone()
{
	set_enabled(false);
	stopping = true;
	notify();
	worker.join();
}

void wivrn::software_microphone::notify()
{
	wake.fetch_add(1);
	wake.notify_one();
}

void wivrn::software_microphone::set_enabled(bool enabled)
{
	// Only the control thread writes epochs. Preserve parity on duplicate controls.
	auto old = epoch.load();
	if (bool(old & 1) != enabled or (enabled and send_failed))
	{
		epoch = old + (bool(old & 1) == enabled ? 2 : 1);
		if (enabled)
			send_failed = false;
		notify();
	}
}

void wivrn::software_microphone::enqueue(std::span<const int16_t> samples, int64_t timestamp)
{
	auto current_epoch = epoch.load();
	if (not(current_epoch & 1) or stopping or send_failed)
		return;
	size_t offset = 0;
	while (offset < samples.size())
	{
		chunk item{};
		item.count = std::min(frame_size, samples.size() - offset);
		std::copy_n(samples.data() + offset, item.count, item.samples.data());
		item.timestamp = timestamp + int64_t(offset) * 1'000'000'000 / sample_rate;
		item.epoch = current_epoch;
		item.sequence = sequence++;
		item.queued = std::chrono::steady_clock::now();
		if (not pending.write(std::move(item)))
			dropped_chunks.fetch_add(1);
		offset += item.count;
	}
	notify();
}

void wivrn::software_microphone::run()
{
	preprocess_ptr filter;
	std::array<int16_t, max_frame_size> frame{};
	size_t filled = 0;
	int64_t frame_timestamp = 0;
	uint64_t last_epoch = 0, next_sequence = 0;
	while (not stopping)
	{
		auto observed = wake.load();
		while (auto item = pending.read())
		{
			if (stopping)
				return;
			if (item->epoch != epoch.load() or not(item->epoch & 1))
				continue;
			if (send_failed)
				continue;
			if (std::chrono::steady_clock::now() - item->queued > std::chrono::milliseconds(30))
			{
				dropped_chunks.fetch_add(1);
				continue;
			}
			try
			{
				if (item->epoch != last_epoch or item->sequence != next_sequence)
				{
					filled = 0;
					if (denoise)
						filter = make_filter(frame_size, sample_rate);
				}
				last_epoch = item->epoch;
				next_sequence = item->sequence + 1;
				if (not denoise)
				{
					if (item->epoch == epoch.load())
						send(std::span(item->samples).first(item->count), item->timestamp);
					continue;
				}
				size_t offset = 0;
				while (offset < item->count)
				{
					if (filled == 0)
						frame_timestamp = item->timestamp + int64_t(offset) * 1'000'000'000 / sample_rate;
					auto count = std::min(frame_size - filled, item->count - offset);
					std::copy_n(item->samples.data() + offset, count, frame.data() + filled);
					filled += count;
					offset += count;
					if (filled == frame_size)
					{
						speex_preprocess_run(filter.get(), frame.data());
						if (item->epoch == epoch.load())
							send(std::span(frame).first(frame_size), frame_timestamp);
						filled = 0;
					}
				}
			}
			catch (...)
			{
				send_failed = true;
				// Callback observes failure and stops capture. Keep this worker alive
				// so a later explicit start can begin a new epoch after reconnection.
			}
		}
		if (not stopping)
			wake.wait(observed);
	}
}
