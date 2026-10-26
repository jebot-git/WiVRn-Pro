// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include <gio/gio.h>
#include <memory>
namespace wivrn
{
class dome_supervisor : public std::enable_shared_from_this<dome_supervisor>
{
	GDBusConnection * bus = nullptr;
	GCancellable * cancel = g_cancellable_new();
	guint timer = 0;
	bool stopped = false, pending = false;
	unsigned failures = 0;
	gint64 retry = 0, missing_since = 0;
	void tick();
	void ensure_unit();

public:
	static std::shared_ptr<dome_supervisor> start();
	void stop();
	~dome_supervisor();
};
} // namespace wivrn
