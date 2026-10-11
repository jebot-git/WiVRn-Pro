// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include <glib.h>
#include <string>
#include <vector>
namespace wivrn
{
// Main-process owner. Rust runs only after exec in an isolated worker.
class tracking_worker
{
	GPid pid = 0;
	guint watch = 0, retry = 0;
	bool stopping = false;
	std::vector<std::string> arguments;
	std::string manifest, spatial_path, tracking_mode;
	void spawn();

public:
	explicit tracking_worker(std::string mode = "slimevr");
	~tracking_worker();
	tracking_worker(const tracking_worker &) = delete;
	tracking_worker & operator=(const tracking_worker &) = delete;
};
} // namespace wivrn
