// SPDX-License-Identifier: GPL-3.0-or-later
#include "hand_joystick.h"
#include <cassert>
#include <limits>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>
using namespace wivrn;
template <size_t N>
static void put(std::array<uint8_t, N> & p, unsigned at, uint64_t value, unsigned bytes)
{
	for (unsigned i = 0; i < bytes; i++)
		p[at + i] = value >> (8 * i);
}
int main()
{
	int64_t now = 2'000'000'000;
	std::array<uint8_t, 48> gait{};
	std::memcpy(gait.data(), "WVW1", 4);
	put(gait, 4, 7, 4);
	put(gait, 8, now, 8);
	put(gait, 16, 1, 8);
	put(gait, 24, 1, 8);
	put(gait, 32, 0, 8);
	put(gait, 40, std::bit_cast<uint32_t>(0.6f), 4);
	put(gait, 44, std::bit_cast<uint32_t>(0.4f), 4);
	auto sample = decode_walking(gait, now);
	assert(sample && sample->forward == 0.6f);
	assert(!decode_walking(gait, now + 151'000'000));
	assert(!decode_walking(gait, now - 51'000'000));
	auto bad = gait;
	put(bad, 40, std::bit_cast<uint32_t>(std::numeric_limits<float>::quiet_NaN()), 4);
	assert(!decode_walking(bad, now));
	bad = gait;
	put(bad, 4, 5, 4);
	assert(!decode_walking(bad, now)); // No turn without movement.
	walking_gate gate;
	assert(gate.update(*sample, true, false, false)[1] == 0);
	sample->steps = 1;
	assert(gate.update(*sample, true, false, false)[1] == 0);
	sample->steps = 2;
	assert(gate.update(*sample, true, false, false)[1] == 0.6f);
	assert(gate.update(*sample, true, true, false)[1] == 0);
	assert(gate.update(*sample, true, false, false)[1] == 0); // Release does not replay gait.
	sample->steps = 4;
	assert(gate.update(*sample, true, false, false)[1] == 0.6f);
	sample->generation++;
	assert(gate.update(*sample, true, false, false)[1] == 0);
	char directory[] = "/tmp/wivrn-walking-test-XXXXXX";
	assert(mkdtemp(directory));
	{
		hand_joystick endpoint(directory);
		int fd = socket(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC, 0);
		assert(fd >= 0);
		sockaddr_un address{};
		address.sun_family = AF_UNIX;
		std::strcpy(address.sun_path, (std::string(directory) + "/hand-input.sock").c_str());
		assert(!connect(fd, reinterpret_cast<sockaddr *>(&address), sizeof(address)));
		int walking = socket(AF_UNIX, SOCK_DGRAM | SOCK_CLOEXEC, 0);
		assert(walking >= 0);
		std::strcpy(address.sun_path, (std::string(directory) + "/walking.sock").c_str());
		std::array<uint8_t, 40> command{};
		std::memcpy(command.data(), "WVJ2", 4);
		uint64_t seq = 0;
		uint64_t steps = 0;
		auto send = [&](uint32_t flags = 256 | 512, uint32_t gait_flags = 7, uint32_t hands = 3) {
			now += 33'000'000;
			seq++;
			put(command, 4, flags, 4);
			put(command, 8, now, 8);
			put(command, 32, seq, 8);
			assert(::send(fd, command.data(), command.size(), 0) == 40);
			put(gait, 4, gait_flags, 4);
			put(gait, 8, now, 8);
			put(gait, 24, seq, 8);
			put(gait, 32, steps, 8);
			put(gait, 44, std::bit_cast<uint32_t>((gait_flags & 2) ? 0.4f : 0.f), 4);
			assert(sendto(walking, gait.data(), gait.size(), 0, reinterpret_cast<sockaddr *>(&address), sizeof(address)) == 48);
			endpoint.poll(now, hands);
		};
		send();
		assert(endpoint.walking_enabled(0, now));
		assert(endpoint.walking_enabled(1, now));
		assert(endpoint.get(0, now)->axes[1] == 0);
		assert(endpoint.get(1, now)->axes[0] == 0);
		steps = 1;
		send();
		assert(endpoint.get(0, now)->axes[1] == 0);
		endpoint.get(1, now);
		steps = 2;
		send();
		assert(endpoint.get(0, now)->axes[1] == 0.6f);
		assert(endpoint.get(1, now)->axes[0] == 0.4f);
		send(256 | 512, 5);
		assert(endpoint.get(1, now)->axes[0] == 0); // Stationary yaw cannot turn.
		// Pad owns the left hand even during its neutral arming stage.
		send(256 | 512 | 1 | 1024);
		assert(endpoint.get(0, now)->axes[1] == 0);
		send();
		assert(endpoint.get(0, now)->axes[1] == 0);
		steps = 4;
		send();
		assert(endpoint.get(0, now)->axes[1] == 0.6f);
		send(256 | 512 | 4096);
		assert(endpoint.get(0, now)->axes[1] == 0); // UI reservation.
		send();
		assert(endpoint.get(0, now)->axes[1] == 0);
		steps = 6;
		send(256 | 512, 7, 0);
		assert(endpoint.get(0, now)->axes[1] == 0.6f); // Physical controllers also support WIP.
		assert(!endpoint.get(0, now, {0.2f, 0}));      // Physical stick takes over.
		assert(endpoint.get(0, now)->axes[1] == 0);
		steps = 8;
		send(256 | 512, 7, 0);
		assert(endpoint.get(0, now)->axes[1] == 0.6f);
		assert(!endpoint.get(0, now + 151'000'000));
		send();
		assert(endpoint.get(0, now)->axes[1] == 0);
		// Fresh control traffic cannot keep an expired gait sample alive.
		now += 151'000'000;
		seq++;
		put(command, 8, now, 8);
		put(command, 32, seq, 8);
		assert(::send(fd, command.data(), command.size(), 0) == 40);
		endpoint.poll(now, 3);
		assert(endpoint.get(0, now)->axes[1] == 0);
		send();
		endpoint.get(0, now);
		steps += 2;
		send();
		assert(endpoint.get(0, now)->axes[1] == 0.6f);
		// Replaying an old datagram clears input and cannot reuse its steps.
		assert(sendto(walking, gait.data(), gait.size(), 0, reinterpret_cast<sockaddr *>(&address), sizeof(address)) == 48);
		endpoint.poll(now, 3);
		assert(endpoint.get(0, now)->axes[1] == 0);
		send();
		assert(endpoint.get(0, now)->axes[1] == 0);
		// Version 1 remains strict and cannot accidentally enable walking.
		command[3] = '1';
		assert(sendto(fd, command.data(), command.size(), 0, nullptr, 0) == 40);
		endpoint.poll(now, 3);
		assert(!endpoint.get(0, now));
		close(fd);
		close(walking);
	}
	std::filesystem::remove_all(directory);
}
