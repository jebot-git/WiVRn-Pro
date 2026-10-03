// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include <cstdint>

namespace wivrn
{
// Encoder-thread owned. Time is monotonic milliseconds; explicit inputs make
// retry limits and reconnect/watchdog behavior testable without a GPU.
class recovery_policy
{
public:
	enum class state
	{
		healthy,
		requested,
		rebuilding,
		awaiting_decode,
		failed
	};
	state status = state::healthy;
	unsigned attempts = 0;
	int64_t retry_at = 0;
	int64_t progress_at = 0;
	int64_t healthy_since = 0;
	uint64_t first_frame = 0;
	bool armed = false;

	void resume(int64_t now)
	{
		armed = false;
		progress_at = now;
		healthy_since = 0;
	}
	void request(int64_t now, bool manual = false)
	{
		if (manual)
			attempts = 0;
		if (status == state::failed && !manual)
			return;
		if (status != state::requested)
			retry_at = now + 100;
		status = state::requested;
		healthy_since = 0;
	}
	void submitted(int64_t now, uint64_t frame)
	{
		if (!armed)
		{
			armed = true;
			progress_at = now;
			first_frame = frame;
		}
	}
	void decoded(int64_t now, uint64_t frame)
	{
		if (!armed || frame < first_frame || status == state::requested || status == state::failed)
			return;
		progress_at = now;
		status = state::healthy;
		if (!healthy_since)
			healthy_since = now;
		if (now - healthy_since >= 5000)
			attempts = 0;
	}
	void tick(int64_t now, bool connected, bool demand)
	{
		if (!connected || !demand)
		{
			progress_at = now;
			healthy_since = 0;
			return;
		}
		if (armed && (status == state::healthy || status == state::awaiting_decode) && now - progress_at >= 2000)
			request(now);
	}
	bool due(int64_t now, bool connected) const
	{
		return connected && status == state::requested && now >= retry_at;
	}
	bool begin()
	{
		if (attempts >= 3)
		{
			status = state::failed;
			return false;
		}
		++attempts;
		status = state::rebuilding;
		return true;
	}
	void finished(int64_t now, bool success)
	{
		armed = false;
		progress_at = now;
		status = success ? state::awaiting_decode : (attempts >= 3 ? state::failed : state::requested);
		retry_at = now + 250 * attempts;
	}
	bool blocked() const
	{
		return status == state::requested || status == state::rebuilding || status == state::failed;
	}
	const char * name() const
	{
		switch (status)
		{
			case state::healthy:
				return "healthy";
			case state::requested:
				return "recovery requested";
			case state::rebuilding:
				return "rebuilding";
			case state::awaiting_decode:
				return "awaiting decoded frame";
			case state::failed:
				return "failed (manual recovery available)";
		}
		return "unknown";
	}
};
} // namespace wivrn
