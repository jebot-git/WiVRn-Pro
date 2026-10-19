// SPDX-License-Identifier: GPL-3.0-or-later
#include "hand_joystick.h"
#include <algorithm>
#include <bit>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <stdexcept>
#include <sys/file.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>
namespace wivrn
{
static constexpr int64_t ttl = 150'000'000;
static uint32_t u32(const uint8_t * p)
{
	return uint32_t(p[0]) | uint32_t(p[1]) << 8 | uint32_t(p[2]) << 16 | uint32_t(p[3]) << 24;
}
static uint64_t u64(const uint8_t * p)
{
	return u32(p) | uint64_t(u32(p + 4)) << 32;
}
std::optional<joystick_command> decode_joysticks(std::span<const uint8_t> p, int64_t now)
{
	if (p.size() != 40 || (std::memcmp(p.data(), "WVJ1", 4) && std::memcmp(p.data(), "WVJ2", 4)))
		return {};
	auto flags = u32(p.data() + 4);
	if (flags & ~(p[3] == '2' ? 16383u : 255u))
		return {};
	joystick_command c;
	c.enabled = flags & 1;
	c.controllers = flags & 32;
	c.walking = flags & 256;
	c.turning = c.walking && (flags & 512);
	c.pad_owned = (flags >> 10) & 3;
	c.ui_owned = (flags >> 12) & 3;
	c.suppressed = (flags >> 6) & 3;
	c.active = (flags >> 1) & 3;
	c.clicks = c.enabled ? (flags >> 3) & 3 : 0;
	c.time = int64_t(u64(p.data() + 8));
	c.sequence = u64(p.data() + 32);
	if (c.time <= 0 || c.time > now + 50'000'000 || now - c.time > ttl || !c.sequence)
		return {};
	for (unsigned h = 0; h < 2; h++)
		for (unsigned a = 0; a < 2; a++)
		{
			float v = std::bit_cast<float>(u32(p.data() + 16 + 8 * h + 4 * a));
			if (!std::isfinite(v) || std::abs(v) > 1)
				return {};
			c.axes[h][a] = (c.active & (1u << h)) && c.enabled ? v : 0;
		}
	return c;
}
hand_joystick::hand_joystick(std::filesystem::path directory)
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
		directory_fd = open(directory.c_str(), O_DIRECTORY | O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
		struct stat st{};
		if (directory_fd < 0 || fstat(directory_fd, &st) || st.st_uid != getuid() || (st.st_mode & 077))
			throw std::runtime_error("unsafe directory");
		lock_fd = openat(directory_fd, "hand-input.lock", O_CREAT | O_RDWR | O_CLOEXEC | O_NOFOLLOW, 0600);
		if (lock_fd < 0 || fstat(lock_fd, &st) || !S_ISREG(st.st_mode) || st.st_uid != getuid() || flock(lock_fd, LOCK_EX | LOCK_NB))
			throw std::runtime_error("endpoint already owned");
		if (!fstatat(directory_fd, "hand-input.sock", &st, AT_SYMLINK_NOFOLLOW))
		{
			if (!S_ISSOCK(st.st_mode) || st.st_uid != getuid())
				throw std::runtime_error("unsafe endpoint");
			if (unlinkat(directory_fd, "hand-input.sock", 0))
				throw std::runtime_error("unlink");
		}
		else if (errno != ENOENT)
			throw std::runtime_error("stat");
		auto path = (directory / "hand-input.sock").string();
		sockaddr_un address{};
		address.sun_family = AF_UNIX;
		if (path.size() >= sizeof(address.sun_path))
			throw std::runtime_error("path too long");
		std::memcpy(address.sun_path, path.c_str(), path.size() + 1);
		listener = socket(AF_UNIX, SOCK_SEQPACKET | SOCK_NONBLOCK | SOCK_CLOEXEC, 0);
		if (listener < 0 || bind(listener, reinterpret_cast<sockaddr *>(&address), sizeof(address)))
			throw std::runtime_error("bind");
		bound = true;
		if (fchmodat(directory_fd, "hand-input.sock", 0600, 0) || listen(listener, 1))
			throw std::runtime_error("listen");
		// The existing owner lock also serializes this endpoint's lifecycle.
		if (!fstatat(directory_fd, "walking.sock", &st, AT_SYMLINK_NOFOLLOW))
		{
			if (!S_ISSOCK(st.st_mode) || st.st_uid != getuid() || unlinkat(directory_fd, "walking.sock", 0))
				throw std::runtime_error("unsafe walking endpoint");
		}
		else if (errno != ENOENT)
			throw std::runtime_error("walking stat");
		auto gait_path = (directory / "walking.sock").string();
		if (gait_path.size() >= sizeof(address.sun_path))
			throw std::runtime_error("walking path too long");
		std::strcpy(address.sun_path, gait_path.c_str());
		gait_fd = socket(AF_UNIX, SOCK_DGRAM | SOCK_NONBLOCK | SOCK_CLOEXEC, 0);
		int credentials = 1;
		if (gait_fd < 0 || setsockopt(gait_fd, SOL_SOCKET, SO_PASSCRED, &credentials, sizeof(credentials)) ||
		    bind(gait_fd, reinterpret_cast<sockaddr *>(&address), sizeof(address)))
			throw std::runtime_error("walking bind");
		gait_bound = true;
		if (fchmodat(directory_fd, "walking.sock", 0600, 0))
			throw std::runtime_error("walking chmod");
	}
	catch (const std::exception & e)
	{
		std::fprintf(stderr, "Hand joystick unavailable: %s\n", e.what());
		cleanup();
	}
}
void hand_joystick::disconnect()
{
	if (client >= 0)
		close(client);
	client = -1;
	command = {};
	for (auto & gate: gait_gates)
		gate.reset();
	received = 0;
}
void hand_joystick::cleanup()
{
	disconnect();
	if (gait_fd >= 0)
		close(gait_fd);
	gait_fd = -1;
	if (gait_bound)
		unlinkat(directory_fd, "walking.sock", 0);
	gait_bound = false;
	if (listener >= 0)
		close(listener);
	listener = -1;
	if (bound)
		unlinkat(directory_fd, "hand-input.sock", 0);
	bound = false;
	if (lock_fd >= 0)
		close(lock_fd);
	lock_fd = -1;
	if (directory_fd >= 0)
		close(directory_fd);
	directory_fd = -1;
}
hand_joystick::~hand_joystick()
{
	cleanup();
}
void hand_joystick::poll(int64_t now, uint32_t hand_mask)
{
	std::lock_guard lock(mutex);
	hands = hand_mask;

	if (gait_fd >= 0)
		for (unsigned i = 0; i < 64; i++)
		{
			std::array<uint8_t, 49> bytes{};
			iovec io{bytes.data(), bytes.size()};
			alignas(cmsghdr) std::array<char, CMSG_SPACE(sizeof(ucred))> control{};
			msghdr msg{};
			msg.msg_iov = &io;
			msg.msg_iovlen = 1;
			msg.msg_control = control.data();
			msg.msg_controllen = control.size();
			auto size = recvmsg(gait_fd, &msg, MSG_DONTWAIT | MSG_TRUNC);
			if (size < 0)
				break;
			bool owner = false;
			for (auto * c = CMSG_FIRSTHDR(&msg); c; c = CMSG_NXTHDR(&msg, c))
				if (c->cmsg_level == SOL_SOCKET && c->cmsg_type == SCM_CREDENTIALS && c->cmsg_len == CMSG_LEN(sizeof(ucred)))
				{
					ucred peer{};
					std::memcpy(&peer, CMSG_DATA(c), sizeof(peer));
					owner = peer.uid == getuid();
				}
			auto sample = owner && !(msg.msg_flags & (MSG_TRUNC | MSG_CTRUNC)) ? decode_walking(std::span(bytes.data(), size_t(size)), now) : std::nullopt;
			if (sample && (sample->generation > gait.generation || (sample->generation == gait.generation && sample->sequence > gait.sequence)) && sample->time >= gait.time)
				gait = *sample;
			else
			{
				gait.flags = 0;
				for (auto & gate: gait_gates)
					gate.reset();
			}
		}
	if (listener < 0)
		return;
	int fd = accept4(listener, nullptr, nullptr, SOCK_NONBLOCK | SOCK_CLOEXEC);
	if (fd >= 0)
	{
		ucred peer{};
		socklen_t n = sizeof(peer);
		if (client >= 0 || getsockopt(fd, SOL_SOCKET, SO_PEERCRED, &peer, &n) || peer.uid != getuid())
			close(fd);
		else
		{
			client = fd;
			received = now;
		}
	}
	if (client < 0)
		return;
	for (unsigned n = 0; n < 8; n++)
	{
		std::array<uint8_t, 41> bytes{};
		auto size = recv(client, bytes.data(), bytes.size(), MSG_DONTWAIT | MSG_TRUNC);
		if (size < 0)
		{
			if (errno != EAGAIN && errno != EWOULDBLOCK)
				disconnect();
			break;
		}
		if (!size)
		{
			disconnect();
			break;
		}
		auto decoded = decode_joysticks(std::span(bytes.data(), std::min(size, ssize_t(bytes.size()))), now);
		if (!decoded || decoded->sequence <= command.sequence)
		{
			disconnect();
			break;
		}
		command = *decoded;
		received = now;
	}
	if (client >= 0)
	{
		if (now - received > 1'000'000'000)
		{
			disconnect();
			return;
		}
		uint8_t reply[8] = {'W', 'V', 'J', 'A', uint8_t(hands), uint8_t(command.enabled && now - command.time <= ttl), 0, 0};
		auto n = send(client, reply, sizeof(reply), MSG_DONTWAIT | MSG_NOSIGNAL);
		if (n < 0 && errno != EAGAIN && errno != EWOULDBLOCK)
			disconnect();
	}
}
std::optional<hand_joystick::value> hand_joystick::get(unsigned hand, int64_t now, std::array<float, 2> physical) const
{
	std::lock_guard lock(mutex);
	if (hand > 1)
		return {};
	auto & gate = gait_gates[hand];
	if (client < 0 || now - command.time > ttl || now - received > ttl)
	{
		gate.reset();
		return {};
	}
	bool native = hands & (1u << hand);
	bool physical_active = !native && (std::hypot(physical[0], physical[1]) > 0.15f);
	bool pad = native && command.enabled && ((command.active | command.clicks | command.pad_owned) & (1u << hand));
	bool ui = command.ui_owned & (1u << hand);
	bool fresh = gait.time > 0 && now >= gait.time && now - gait.time <= ttl;
	if (!fresh || physical_active || pad || ui || !command.walking)
		gate.reset();
	if (physical_active)
		return {};
	if (pad)
		return value{command.axes[hand], bool((command.active | command.clicks) & (1u << hand)), bool(command.clicks & (1u << hand)), command.time};
	if (command.walking && (hand == 0 || command.turning))
	{
		auto sample = gait;
		if (!fresh)
			sample.flags = 0;
		auto axes = gate.update(sample, hand == 0 || command.turning, ui, hand == 1);
		return value{axes, axes[0] != 0 || axes[1] != 0, false, now};
	}
	if (!native || !command.enabled)
		return {};
	return value{command.axes[hand], bool((command.active | command.clicks) & (1u << hand)), bool(command.clicks & (1u << hand)), command.time};
}
bool hand_joystick::walking_enabled(unsigned hand, int64_t now) const
{
	std::lock_guard lock(mutex);
	return hand < 2 && client >= 0 && command.walking && (hand == 0 || command.turning) && (hands & (1u << hand)) && now - command.time <= ttl && now - received <= ttl;
}
std::optional<bool> hand_joystick::controller(unsigned hand, int64_t now) const
{
	std::lock_guard lock(mutex);
	if (hand > 1 || client < 0 || !command.controllers || !(hands & (1u << hand)) || now - command.time > ttl || now - received > ttl)
		return {};
	return bool(command.suppressed & (1u << hand));
}
bool hand_joystick::controllers_enabled() const
{
	std::lock_guard lock(mutex);
	// Capability exposure follows the selected mode even while gesture samples
	// pause. The controller() input path retains its independent freshness guard.
	return client >= 0 && command.controllers;
}
} // namespace wivrn
