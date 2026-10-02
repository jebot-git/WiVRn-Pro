// SPDX-License-Identifier: GPL-3.0-or-later
#include "room_calibration.h"
#include <cassert>
#include <iostream>
#include <unistd.h>
using namespace wivrn;
using namespace room_calibration;
int main()
{
	spatial::snapshot s;
	s.active = true;
	s.head = {{{0, 0, 0, 1}, {1, 1.7f, 2}}, 15};
	s.markers[0].uuid[0] = 42;
	s.markers[0].persisted = true;
	s.markers[0].pose = {{{0, 0, 0, 1}, {3, 1, 2}}, 15};
	auto saved = spot::capture(s);
	assert(saved);
	auto located = saved->locate(s);
	assert(located);
	assert(std::abs(located->position.x - 1) < 1e-5 && std::abs(located->position.y) < 1e-5);
	// Coherent reference changes move the display, while retaining the room spot.
	XrPosef transform{{0, std::sin(.4f), 0, std::cos(.4f)}, {-2, 0, 5}};
	s.head.pose = compose(transform, s.head.pose);
	s.markers[0].pose.pose = compose(transform, s.markers[0].pose.pose);
	auto moved = saved->locate(s);
	auto expected = compose(transform, *located);
	assert(moved && std::abs(moved->position.x - expected.position.x) < 1e-5 && std::abs(moved->position.z - expected.position.z) < 1e-5);
	auto delta = compose(inverse(*moved), s.head.pose);
	assert(std::hypot(delta.position.x, delta.position.z) < 1e-5 && std::abs(delta.orientation.w - 1) < 1e-5);
	auto path = std::filesystem::temp_directory_path() / ("wivrn-calibration-" + std::to_string(getpid()));
	assert(saved->save(path));
	auto loaded = spot::load(path);
	assert(loaded && loaded->uuid == saved->uuid);
	assert(loaded->locate(s));
	s.markers[0].pose.flags = 3;
	assert(!loaded->locate(s));
	assert(!spot::capture(s));
	s.markers[0].pose.flags = 15;
	s.markers[0].uuid[0] = 43;
	assert(!loaded->locate(s));
	s.active = false;
	assert(!loaded->locate(s));
	assert(!spot::capture(s));
	s.active = true;
	s.head.pose.orientation = {0, 0, 0, 0};
	assert(!spot::capture(s));
	saved->relative.orientation.w = 0;
	assert(!saved->save(path));
	assert(spot::load(path));
	{
		std::ofstream f(path);
		f << "corrupt";
	}
	assert(!spot::load(path));
	std::filesystem::remove(path);
	std::cout << "Room calibration geometry, persistence, and tracking-loss checks passed\n";
}
