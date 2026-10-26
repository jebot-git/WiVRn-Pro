// SPDX-License-Identifier: GPL-3.0-or-later
#include "hand_joystick.h"
#include <bit>
#include <cassert>
#include <cstring>
#include <limits>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>
using namespace wivrn;
static void put(std::array<uint8_t, 40> & p, unsigned offset, uint64_t value, unsigned size)
{
	for (unsigned i = 0; i < size; i++)
		p[offset + i] = value >> (i * 8);
}
int main()
{
	const int64_t now = 2'000'000'000;
	std::array<uint8_t, 40> packet{};
	std::memcpy(packet.data(), "WVJ1", 4);
	put(packet, 4, 7, 4);
	put(packet, 8, now, 8);
	put(packet, 32, 1, 8);
	put(packet, 16, std::bit_cast<uint32_t>(0.6f), 4);
	put(packet, 28, std::bit_cast<uint32_t>(-0.4f), 4);
	auto decoded = decode_joysticks(packet, now);
	assert(decoded && decoded->axes[0][0] == 0.6f && decoded->axes[1][1] == -0.4f);
	assert(!decode_joysticks(packet, now + 151'000'000));
	assert(!decode_joysticks(packet, now - 51'000'000));
	auto invalid = packet;
	put(invalid, 16, std::bit_cast<uint32_t>(std::numeric_limits<float>::quiet_NaN()), 4);
	assert(!decode_joysticks(invalid, now));
	invalid = packet;
	put(invalid, 16, std::bit_cast<uint32_t>(1.1f), 4);
	assert(!decode_joysticks(invalid, now));
	invalid = packet;
	put(invalid, 4, 256, 4);
	assert(!decode_joysticks(invalid, now));
	// Clicks are independent of analogue-pad activity, and disabled means released.
	for (unsigned flags: {9u, 17u, 25u, 24u})
	{
		auto clicks = packet;
		put(clicks, 4, flags, 4);
		auto c = decode_joysticks(clicks, now);
		assert(c && c->clicks == ((flags & 1) ? flags >> 3 : 0));
		assert(c->axes[0][0] == 0 && c->axes[1][1] == 0);
	}
	char directory[] = "/tmp/wivrn-joystick-test-XXXXXX";
	assert(mkdtemp(directory));
	{
		hand_joystick endpoint(directory);
		int fd = socket(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC, 0);
		assert(fd >= 0);
		sockaddr_un addr{};
		addr.sun_family = AF_UNIX;
		auto path = std::string(directory) + "/hand-input.sock";
		std::strcpy(addr.sun_path, path.c_str());
		assert(connect(fd, reinterpret_cast<sockaddr *>(&addr), sizeof(addr)) == 0);
		assert(send(fd, packet.data(), packet.size(), 0) == 40);
		endpoint.poll(now, 3);
		assert(endpoint.get(0, now)->axes[0] == 0.6f);
		assert(endpoint.get(1, now)->axes[1] == -0.4f);
		assert(!endpoint.get(0, now + 151'000'000));
		assert(!endpoint.controllers_enabled());
		endpoint.poll(now, 1);
		assert(!endpoint.get(1, now));
		assert(endpoint.get(0, now));
		put(packet, 4, 9, 4);
		put(packet, 32, 2, 8);
		assert(send(fd, packet.data(), packet.size(), 0) == 40);
		endpoint.poll(now, 3);
		assert(endpoint.get(0, now)->clicked && endpoint.get(0, now)->touched);
		assert(endpoint.get(0, now)->axes[0] == 0 && !endpoint.get(1, now)->clicked);
		assert(!endpoint.get(0, now + 151'000'000));
		endpoint.poll(now, 2); // Physical left controller takes over.
		assert(!endpoint.get(0, now));
		put(packet, 4, 17, 4);
		put(packet, 32, 3, 8);
		assert(send(fd, packet.data(), packet.size(), 0) == 40);
		endpoint.poll(now, 3);
		assert(!endpoint.get(0, now)->clicked && endpoint.get(1, now)->clicked);
		put(packet, 4, 1, 4);
		put(packet, 32, 4, 8);
		assert(send(fd, packet.data(), packet.size(), 0) == 40);
		endpoint.poll(now, 3);
		assert(endpoint.get(0, now)->axes[0] == 0 && !endpoint.get(0, now)->touched);
		assert(!endpoint.get(0, now)->clicked && !endpoint.get(1, now)->clicked);
		put(packet, 4, 24, 4); // Click bits must be ignored when disabled.
		put(packet, 32, 5, 8);
		assert(send(fd, packet.data(), packet.size(), 0) == 40);
		endpoint.poll(now, 3);
		assert(!endpoint.get(0, now));
		put(packet, 4, 25, 4);
		put(packet, 32, 6, 8);
		assert(send(fd, packet.data(), packet.size(), 0) == 40);
		endpoint.poll(now, 3);
		assert(endpoint.get(0, now));
		assert(endpoint.get(0, now)->clicked && endpoint.get(1, now)->clicked);
		// Controller mode is independent of pads, with separate per-hand ownership.
		put(packet, 4, 32 | 64, 4);
		put(packet, 32, 7, 8);
		assert(send(fd, packet.data(), packet.size(), 0) == 40);
		endpoint.poll(now, 3);
		assert(!endpoint.get(0, now));
		assert(endpoint.controller(0, now).value());
		assert(!endpoint.controller(1, now).value());
		assert(!endpoint.controller(0, now + 151'000'000));
		assert(endpoint.controllers_enabled());
		assert(endpoint.controllers_enabled());
		endpoint.poll(now, 0); // Mode still suppresses advertising without a tracked hand.
		assert(endpoint.controllers_enabled());
		assert(!endpoint.controller(0, now));
		endpoint.poll(now, 2);
		assert(!endpoint.controller(0, now));
		// Both features can be enabled together, without changing axes/clicks.
		put(packet, 4, 32 | 9, 4);
		put(packet, 32, 8, 8);
		assert(send(fd, packet.data(), packet.size(), 0) == 40);
		endpoint.poll(now, 3);
		assert(endpoint.controller(0, now).has_value());
		assert(endpoint.get(0, now)->clicked);
		put(packet, 4, 32 | 64 | 128, 4); // Focus loss reserves gestures without changing mode.
		put(packet, 32, 9, 8);
		assert(send(fd, packet.data(), packet.size(), 0) == 40);
		endpoint.poll(now, 3);
		assert(endpoint.controllers_enabled());
		assert(endpoint.controller(0, now).value() && endpoint.controller(1, now).value());
		put(packet, 4, 9, 4); // Explicitly disabling controller mode restores hand tracking.
		put(packet, 32, 10, 8);
		assert(send(fd, packet.data(), packet.size(), 0) == 40);
		endpoint.poll(now, 3);
		assert(!endpoint.controllers_enabled());
		put(packet, 4, 32 | 9, 4);
		put(packet, 32, 11, 8);
		assert(send(fd, packet.data(), packet.size(), 0) == 40);
		endpoint.poll(now, 3);
		assert(endpoint.controllers_enabled());
		// WVJ3 hand tracking is independent of all existing virtual-pad bits.
        packet[3] = '3';
        put(packet, 4, 16384 | 32 | 9, 4);
        put(packet, 32, 12, 8);
        assert(send(fd, packet.data(), packet.size(), 0) == 40);
        endpoint.poll(now, 3);
        assert(!endpoint.hand_tracking_enabled());
        assert(endpoint.controllers_enabled() && endpoint.get(0, now)->clicked);
        endpoint.poll(now + 200'000'000, 3);
        assert(!endpoint.hand_tracking_enabled()); // mode survives input expiry
        assert(!endpoint.get(0, now + 200'000'000));
        close(fd);
		endpoint.poll(now, 3);
        assert(endpoint.hand_tracking_enabled());
		assert(!endpoint.controller(0, now));
		assert(!endpoint.controllers_enabled());
		assert(!endpoint.get(0, now));
	}
	std::filesystem::remove_all(directory);
}
