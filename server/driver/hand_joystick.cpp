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
	if (p.size() != 40 || std::memcmp(p.data(), "WVJ1", 4))
		return {};
	auto flags = u32(p.data() + 4);
	if (flags & ~31u)
		return {};
	joystick_command c;
	c.enabled = flags & 1;
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
	received = 0;
	menu_released_at = 0;
}
void hand_joystick::cleanup()
{
	disconnect();
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
bool hand_joystick::consume_menu_request(int64_t now)
{
	std::lock_guard lock(mutex);
	// Disabled/focus-lost/stale packets cannot open the menu or rearm a held
	// chord. Require a real neutral interval after focus returns: WayVR's
	// 150 ms gesture debounce alone must not reopen a just-dismissed menu.
	if (client < 0 || !command.enabled || hands != 3 || now - command.time > ttl || now - received > ttl)
	{
		menu_released_at = 0;
		return false;
	}
	if (!command.clicks)
	{
		if (!menu_released_at)
			menu_released_at = now;
		if (now - menu_released_at >= 300'000'000)
			menu_armed = true;
		return false;
	}
	menu_released_at = 0;
	if (command.clicks == 3 && menu_armed)
	{
		menu_armed = false;
		return true;
	}
	return false;
}
std::optional<hand_joystick::value> hand_joystick::get(unsigned hand, int64_t now) const
{
	std::lock_guard lock(mutex);
	if (hand > 1 || client < 0 || !command.enabled || !(hands & (1u << hand)) || now - command.time > ttl || now - received > ttl)
		return {};
	return value{command.axes[hand], bool((command.active | command.clicks) & (1u << hand)), bool(command.clicks & (1u << hand)), command.time};
}
} // namespace wivrn
