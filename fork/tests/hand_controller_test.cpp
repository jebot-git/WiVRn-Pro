// SPDX-License-Identifier: GPL-3.0-or-later
#include "hand_controller.h"
#include <cassert>
#include <limits>
using Gestures = wivrn::hand_controller_gestures;
static std::array<Gestures::point, 26> open_hand()
{
	std::array<Gestures::point, 26> j{};
	j[5] = {-.07f, .01f, 0};
	for (unsigned finger = 0; finger < 4; finger++)
		for (unsigned bone = 0; bone < 4; bone++)
			j[7 + finger * 5 + bone] = {-.03f + .02f * finger, .02f * bone, 0};
	return j;
}
int main()
{
	for (const char *app : std::array<const char *, 4>{nullptr, "game", "SteamVR", "wayvr-other"}) {
		assert(wivrn::application_hand_tracking_allowed(false, app));
		assert(!wivrn::application_hand_tracking_allowed(true, app));
	}
	assert(wivrn::application_hand_tracking_allowed(true, "wayvr"));
	int64_t now = 1'000'000'000;
	Gestures g;
	auto j = open_hand();
	auto update = [&](bool enabled = true, bool suppressed = false) {
		now += 10'000'000;
		g.sample(j, true, now);
		return g.update(now, enabled, suppressed);
	};
	assert(update().trigger == 0);
	for (unsigned finger = 0; finger < 3; finger++)
	{
		j = open_hand();
		update();
		j[5] = j[10 + finger * 5];
		auto v = update();
		assert(v.trigger_click == (finger == 0));
		assert(v.first == (finger == 1));
		assert(v.second == (finger == 2));
		assert(v.grip == 0);
	}
	// Analogue trigger and click hysteresis tolerate small pinch jitter.
	j = open_hand();
	update();
	j[5] = j[10];
	j[5].z += .36f * .06f;
	auto half = update();
	assert(half.trigger > .4f && half.trigger < .6f && !half.trigger_click);
	j[5] = j[10];
	assert(update().trigger_click);
	j[5].z += .30f * .06f;
	assert(update().trigger_click);
	j[5].z = .42f * .06f;
	assert(!update().trigger_click);
	// Disable, ownership changes and tracking loss release, and held gestures
	// cannot activate again until a neutral pose has been seen.
	j = open_hand();
	update();
	j[5] = j[10];
	assert(update().trigger_click);
	assert(update(false).trigger == 0);
	assert(update().trigger == 0);
	j = open_hand();
	update();
	j[5] = j[10];
	assert(update().trigger_click);
	assert(update(true, true).trigger == 0);
	assert(update().trigger == 0);
	j = open_hand();
	update();
	j[5] = j[10];
	assert(update().trigger_click);
	assert(g.update(now + 151'000'000, true, false).trigger == 0);
	now += 160'000'000;
	assert(update().trigger == 0);
	j = open_hand();
	update();
	j[5] = j[10];
	assert(update().trigger_click);
	now += 10'000'000;
	g.sample(j, false, now);
	assert(g.update(now, true, false).trigger == 0);
	assert(update().trigger == 0);
	// A closed fist gives analogue squeeze and click, without face buttons.
	j = open_hand();
	update();
	for (unsigned base: {12u, 17u, 22u})
	{
		auto x = j[base].x;
		j[base + 1] = {x, .025f, 0};
		j[base + 2] = {x, .025f, .025f};
		j[base + 3] = {x, 0, 0};
	}
	auto fist = update();
	assert(fist.grip == 1 && fist.grip_click);
	assert(!fist.first && !fist.second && !fist.trigger_click);
	// A pinch held while leaving a fist must also rearm.
	j = open_hand();
	j[5] = j[15];
	assert(!update().first);
	j = open_hand();
	update();
	j[5] = j[15];
	assert(update().first);
	j[7].x = std::numeric_limits<float>::quiet_NaN();
	assert(!update().first);
}
