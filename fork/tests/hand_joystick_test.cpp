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
	put(invalid, 4, 8, 4);
	assert(!decode_joysticks(invalid, now));
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
		endpoint.poll(now, 1);
		assert(!endpoint.get(1, now));
		assert(endpoint.get(0, now));
		put(packet, 4, 1, 4);
		put(packet, 32, 2, 8);
		assert(send(fd, packet.data(), packet.size(), 0) == 40);
		endpoint.poll(now, 3);
		assert(endpoint.get(0, now)->axes[0] == 0 && !endpoint.get(0, now)->touched);
		put(packet, 4, 0, 4);
		put(packet, 32, 3, 8);
		assert(send(fd, packet.data(), packet.size(), 0) == 40);
		endpoint.poll(now, 3);
		assert(!endpoint.get(0, now));
		put(packet, 4, 7, 4);
		put(packet, 32, 4, 8);
		assert(send(fd, packet.data(), packet.size(), 0) == 40);
		endpoint.poll(now, 3);
		assert(endpoint.get(0, now));
		close(fd);
		endpoint.poll(now, 3);
		assert(!endpoint.get(0, now));
	}
	std::filesystem::remove_all(directory);
}
