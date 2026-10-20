// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include <filesystem>
#include <gio/gio.h>
#include <string>
#include <string_view>

namespace wivrn
{
inline bool desktop_autostart_allowed(std::string_view manager)
{
	return manager != "lightxr.service";
}

// Query the system manager, never the lingering user's stale desktop environment.
// Fall back to the selected unit during early boot, before the manager is loaded.
inline std::string display_manager()
{
	std::string manager;
	if (auto bus = g_bus_get_sync(G_BUS_TYPE_SYSTEM, nullptr, nullptr))
	{
		auto unit = g_dbus_connection_call_sync(bus, "org.freedesktop.systemd1",
		        "/org/freedesktop/systemd1", "org.freedesktop.systemd1.Manager", "GetUnit",
		        g_variant_new("(s)", "display-manager.service"), G_VARIANT_TYPE("(o)"),
		        G_DBUS_CALL_FLAGS_NONE, 1000, nullptr, nullptr);
		if (unit)
		{
			const char * path;
			g_variant_get(unit, "(&o)", &path);
			auto reply = g_dbus_connection_call_sync(bus, "org.freedesktop.systemd1", path,
			        "org.freedesktop.DBus.Properties", "Get",
			        g_variant_new("(ss)", "org.freedesktop.systemd1.Unit", "Id"),
			        G_VARIANT_TYPE("(v)"), G_DBUS_CALL_FLAGS_NONE, 1000, nullptr, nullptr);
			if (reply)
			{
				GVariant * value;
				g_variant_get(reply, "(v)", &value);
				if (g_variant_is_of_type(value, G_VARIANT_TYPE_STRING))
					manager = g_variant_get_string(value, nullptr);
				g_variant_unref(value);
				g_variant_unref(reply);
			}
			g_variant_unref(unit);
		}
		g_object_unref(bus);
	}
	if (manager.empty())
	{
		std::error_code error;
		auto selected = std::filesystem::read_symlink("/etc/systemd/system/display-manager.service", error);
		if (!error)
			manager = selected.filename().string();
	}
	return manager;
}

inline bool desktop_autostart_allowed()
{
	return desktop_autostart_allowed(display_manager());
}
}
