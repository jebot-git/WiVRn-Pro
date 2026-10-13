// SPDX-License-Identifier: GPL-3.0-or-later
#include "performance_feed.h"
#include <algorithm>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <fcntl.h>
#include <nlohmann/json.hpp>
#include <sys/file.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

namespace wivrn
{
static int64_t monotonic_ns()
{
	timespec ts{};
	clock_gettime(CLOCK_MONOTONIC, &ts);
	return int64_t(ts.tv_sec) * 1'000'000'000 + ts.tv_nsec;
}
void stream_performance::update(const from_headset::feedback & f, int64_t now)
{
	// Ignore older frames outside the bounded reorder window. Updated feedback for
	// the same frame can raise times_displayed; count only the additional repeats.
	auto it = std::find_if(seen.begin(), seen.end(), [&](auto & s) { return s.id == f.frame_index; });
	if (it == seen.end())
	{
		if (seen.size() == 256 && f.frame_index < seen.front().id)
			return;
		it = seen.insert(std::upper_bound(seen.begin(), seen.end(), f.frame_index, [](uint64_t id, auto & s) { return id < s.id; }), {f.frame_index, 0});
		frames++;
	}
	auto extra = [](uint8_t n) { return n > 1 ? n - 1 : 0; };
	if (f.times_displayed > it->displayed)
	{
		if (it->displayed == 0)
			presented_frames++;
		repeats += extra(f.times_displayed) - extra(it->displayed);
		it->displayed = f.times_displayed;
	}
	if (seen.size() > 256)
		seen.pop_front();
	// Receive, decoder and display reports arrive independently and out of order.
	// Retain the newest valid measurement for each stage, not only the latest frame.
	const std::array<std::pair<int64_t, int64_t>, 4> measured{{{f.encode_begin, f.encode_end}, {f.send_begin, f.received_last_packet}, {f.sent_to_decoder, f.received_from_decoder}, {f.encode_begin, f.displayed}}};
	for (unsigned i = 0; i < measured.size(); i++)
	{
		auto [start, end] = measured[i];
		auto & t = timings[i];
		if (start > 0 && end >= start && end - start <= 2'000'000'000 &&
		    (!t.updated_ns || f.frame_index >= t.frame))
			t = {f.frame_index, start, end, now};
	}
	// All intervals below telescope to encode -> predicted display for ONE
	// frame. Never replace its first-presentation result with a repeat.
	const std::array<int64_t, 8> chain{f.encode_begin, f.encode_end, f.send_begin,
	                                 f.received_last_packet, f.sent_to_decoder,
	                                 f.received_from_decoder, f.blitted, f.displayed};
	if (f.times_displayed == 1 && chain.front() > 0 &&
	    std::is_sorted(chain.begin(), chain.end()) &&
	    chain.back() - chain.front() <= 2'000'000'000 &&
	    (!first_display_updated_ns || f.frame_index > first_display.frame_index))
	{
		first_display = f;
		first_display_updated_ns = now;
	}
	if (!updated_ns || f.frame_index >= last.frame_index)
	{
		last = f;
		updated_ns = now;
	}
}
performance_feed::performance_feed(pacing_app_factory & p, std::filesystem::path directory) :
        pacers(p)
{
	try
	{
		if (directory.empty())
		{
			auto runtime = std::getenv("XDG_RUNTIME_DIR");
			if (!runtime)
				throw std::runtime_error("no runtime directory");
			directory = std::filesystem::path(runtime) / "wivrn-pro";
		}
		if (mkdir(directory.c_str(), 0700) && errno != EEXIST)
			throw std::runtime_error("mkdir");
		directory_fd = open(directory.c_str(), O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
		struct stat st{};
		if (directory_fd < 0 || fstat(directory_fd, &st) || st.st_uid != getuid() || (st.st_mode & 077))
			throw std::runtime_error("unsafe directory");
		lock_fd = openat(directory_fd, "performance.lock", O_CREAT | O_RDWR | O_NOFOLLOW | O_CLOEXEC, 0600);
		if (lock_fd < 0 || fstat(lock_fd, &st) || !S_ISREG(st.st_mode) || st.st_uid != getuid() || flock(lock_fd, LOCK_EX | LOCK_NB))
			throw std::runtime_error("publisher already active");
		worker = std::thread([this] { run(); });
	}
	catch (const std::exception & e)
	{
		std::fprintf(stderr, "Performance telemetry unavailable: %s\n", e.what());
		cleanup();
	}
}
performance_feed::~performance_feed()
{
	{
		std::lock_guard lock(mutex);
		stopping = true;
	}
	wake.notify_all();
	if (worker.joinable())
	{
		worker.join();
		unlinkat(directory_fd, "performance.json", 0);
		unlinkat(directory_fd, "performance.tmp", 0);
	}
	cleanup();
}
void performance_feed::cleanup()
{
	if (lock_fd >= 0)
		close(lock_fd);
	if (directory_fd >= 0)
		close(directory_fd);
	lock_fd = directory_fd = -1;
}
void performance_feed::on_feedback(const from_headset::feedback & f, float target)
{
	if (f.stream_index >= streams.size() || !f.received_first_packet)
		return;
	std::lock_guard lock(mutex);
	streams[f.stream_index].update(f, monotonic_ns());
	target_hz = target;
}
static nlohmann::json duration_ms(int64_t start, int64_t end)
{
	if (start <= 0 || end < start || end - start > 2'000'000'000)
		return nullptr;
	return double(end - start) / 1e6;
}
void performance_feed::run()
{
	const auto session = monotonic_ns();
	bool warned = false;
	std::unique_lock lock(mutex);
	while (!stopping)
	{
		auto copy = streams;
		float target = target_hz;
		lock.unlock();
		try
		{
			auto apps = nlohmann::json::array();
			for (auto & a: pacers.performance_snapshot())
				apps.push_back({{"id", a.id}, {"completed", a.completed}, {"updated_ns", a.updated_ns}, {"period_ms", double(a.period_ns) / 1e6}, {"interval_ms", double(a.interval_ns) / 1e6}, {"cpu_ms", double(a.cpu_ns) / 1e6}, {"gpu_ms", double(a.gpu_ns) / 1e6}});
			auto video = nlohmann::json::array();
			for (unsigned i = 0; i < copy.size(); i++)
			{
				const auto & s = copy[i];
				if (!s.updated_ns)
					continue;
				auto latency = [&](unsigned i) -> nlohmann::json {
					const auto & t = s.timings[i];
					if (!t.updated_ns || monotonic_ns() - t.updated_ns > 2'000'000'000)
						return nullptr;
					return duration_ms(t.start, t.end);
				};
				auto stage_frame = [&](unsigned i) -> nlohmann::json {
					const auto & t = s.timings[i];
					if (!t.updated_ns || monotonic_ns() - t.updated_ns > 2'000'000'000)
						return nullptr;
					return t.frame;
				};
				nlohmann::json first = nullptr;
				if (s.first_display_updated_ns && monotonic_ns() - s.first_display_updated_ns <= 2'000'000'000)
				{
					const auto & f = s.first_display;
					first = {{"frame_index", f.frame_index}, {"updated_ns", s.first_display_updated_ns},
					         {"encode_ms", duration_ms(f.encode_begin, f.encode_end)},
					         {"encode_to_send_ms", duration_ms(f.encode_end, f.send_begin)},
					         {"delivery_ms", duration_ms(f.send_begin, f.received_last_packet)},
					         {"decoder_queue_ms", duration_ms(f.received_last_packet, f.sent_to_decoder)},
					         {"decode_ms", duration_ms(f.sent_to_decoder, f.received_from_decoder)},
					         {"decoded_to_blit_ms", duration_ms(f.received_from_decoder, f.blitted)},
					         {"blit_to_predicted_display_ms", duration_ms(f.blitted, f.displayed)},
					         {"encode_to_predicted_display_ms", duration_ms(f.encode_begin, f.displayed)}};
				}
				video.push_back({{"id", i}, {"updated_ns", s.updated_ns}, {"frames", s.frames}, {"presented_frames", s.presented_frames}, {"repeats", s.repeats}, {"encode_ms", latency(0)}, {"delivery_ms", latency(1)}, {"decode_ms", latency(2)}, {"encode_display_ms", latency(3)},
				                 {"stage_frames", {{"encode", stage_frame(0)}, {"delivery", stage_frame(1)}, {"decode", stage_frame(2)}, {"encode_display", stage_frame(3)}}}, {"first_display", first}});
			}
			auto data = nlohmann::json({{"version", 1}, {"pid", getpid()}, {"session", session}, {"updated_ns", monotonic_ns()}, {"target_hz", target}, {"apps", apps}, {"streams", video}}).dump();
			// Exclusive temporary file, replaced atomically; never follow a symlink.
			unlinkat(directory_fd, "performance.tmp", 0);
			int fd = openat(directory_fd, "performance.tmp", O_CREAT | O_EXCL | O_WRONLY | O_CLOEXEC | O_NOFOLLOW, 0600);
			if (fd < 0)
				throw std::runtime_error("open snapshot");
			size_t written = 0;
			while (written < data.size())
			{
				auto n = write(fd, data.data() + written, data.size() - written);
				if (n < 0 && errno == EINTR)
					continue;
				if (n <= 0)
					break;
				written += n;
			}
			close(fd);
			if (written != data.size() || renameat(directory_fd, "performance.tmp", directory_fd, "performance.json"))
				throw std::runtime_error("publish snapshot");
			warned = false;
		}
		catch (const std::exception & e)
		{
			if (!warned)
				std::fprintf(stderr, "Performance telemetry: %s\n", e.what());
			warned = true;
		}
		lock.lock();
		wake.wait_for(lock, std::chrono::milliseconds(200), [this] { return stopping; });
	}
}
} // namespace wivrn
