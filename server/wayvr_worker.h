// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include <functional>
#include <glib.h>
#include <string>
#include <vector>
namespace wivrn
{
// GLib main-thread owner. Independent process, bounded restart delay, no XR calls.
class wayvr_worker
{
	GPid pid = 0;
	guint watch = 0, retry = 0;
	unsigned failures = 0;
	std::vector<std::string> arguments;
	std::string manifest, dashboard, log_path;
	std::function<void(const std::string &)> changed;
	void spawn();
	void schedule_restart();

public:
	wayvr_worker(std::string executable, std::string manifest, std::string dashboard, std::string log_path, std::vector<std::string> flags, std::function<void(const std::string &)> changed);
	~wayvr_worker();
	wayvr_worker(const wayvr_worker &) = delete;
	wayvr_worker & operator=(const wayvr_worker &) = delete;
};
} // namespace wivrn
