// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include "backend.h"
#include <vector>
namespace wivrn::dashboard
{
class Tray
{
	GDBusConnection * bus;
	std::vector<guint> registrations;
	std::vector<GDBusNodeInfo *> infos;
	guint watcher = 0;
	std::function<void(int)> action;
	static void method(GDBusConnection *, const gchar *, const gchar *, const gchar *, const gchar *, GVariant *, GDBusMethodInvocation *, gpointer);
	static GVariant * property(GDBusConnection *, const gchar *, const gchar *, const gchar *, const gchar *, GError **, gpointer);
	void add(const char * path, const char * xml);

public:
	bool owner = false, available = false;
	std::string surface;
	Tray(GDBusConnection *, std::string surface, std::function<void(int)> action);
	~Tray();
};
} // namespace wivrn::dashboard
