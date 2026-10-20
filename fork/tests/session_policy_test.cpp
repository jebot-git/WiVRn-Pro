// SPDX-License-Identifier: GPL-3.0-or-later
#include "session_policy.h"
#include <cassert>
#include <future>
#include <mutex>
#include <thread>

int main()
{
	// Exercise real systemd D-Bus calls on an isolated test bus, without changing
	// the host's display manager or starting a greeter/runtime.
	auto test_bus = g_test_dbus_new(G_TEST_DBUS_NONE);
	g_test_dbus_up(test_bus);
	g_setenv("DBUS_SYSTEM_BUS_ADDRESS", g_test_dbus_get_bus_address(test_bus), TRUE);
	std::string manager = "lightxr.service";
	std::mutex mutex;
	std::promise<GMainLoop *> ready;
	auto future = ready.get_future();
	std::thread service([&] {
		auto context = g_main_context_new();
		g_main_context_push_thread_default(context);
		auto loop = g_main_loop_new(context, FALSE);
		auto connection = g_dbus_connection_new_for_address_sync(g_test_dbus_get_bus_address(test_bus),
		        GDBusConnectionFlags(G_DBUS_CONNECTION_FLAGS_AUTHENTICATION_CLIENT | G_DBUS_CONNECTION_FLAGS_MESSAGE_BUS_CONNECTION),
		        nullptr, nullptr, nullptr);
		assert(connection);
		auto name = g_dbus_connection_call_sync(connection, "org.freedesktop.DBus", "/org/freedesktop/DBus",
		        "org.freedesktop.DBus", "RequestName", g_variant_new("(su)", "org.freedesktop.systemd1", 0),
		        G_VARIANT_TYPE("(u)"), G_DBUS_CALL_FLAGS_NONE, 1000, nullptr, nullptr);
		assert(name);
		g_variant_unref(name);
		auto info = g_dbus_node_info_new_for_xml(
		        "<node><interface name='org.freedesktop.systemd1.Manager'>"
		        "<method name='GetUnit'><arg type='s' direction='in'/><arg type='o' direction='out'/></method>"
		        "</interface><interface name='org.freedesktop.systemd1.Unit'>"
		        "<property name='Id' type='s' access='read'/></interface></node>", nullptr);
		struct state { std::string & manager; std::mutex & mutex; } data{manager, mutex};
		const GDBusInterfaceVTable vtable{
		        .method_call = [](GDBusConnection *, const gchar *, const gchar *, const gchar *, const gchar *,
		                               GVariant * parameters, GDBusMethodInvocation * invocation, gpointer) {
			        const char * unit;
			        g_variant_get(parameters, "(&s)", &unit);
			        assert(std::string_view(unit) == "display-manager.service");
			        g_dbus_method_invocation_return_value(invocation, g_variant_new("(o)", "/org/freedesktop/systemd1/unit/display"));
		        },
		        .get_property = [](GDBusConnection *, const gchar *, const gchar *, const gchar *, const gchar *,
		                                GError **, gpointer user) -> GVariant * {
			        auto & s = *static_cast<state *>(user);
			        std::lock_guard lock(s.mutex);
			        return g_variant_new_string(s.manager.c_str());
		        },
		        .set_property = nullptr,
		        .padding = {},
		};
		assert(g_dbus_connection_register_object(connection, "/org/freedesktop/systemd1", info->interfaces[0], &vtable, &data, nullptr, nullptr));
		assert(g_dbus_connection_register_object(connection, "/org/freedesktop/systemd1/unit/display", info->interfaces[1], &vtable, &data, nullptr, nullptr));
		ready.set_value(loop);
		g_main_loop_run(loop);
		g_dbus_connection_close_sync(connection, nullptr, nullptr);
		g_object_unref(connection);
		g_dbus_node_info_unref(info);
		g_main_loop_unref(loop);
		g_main_context_pop_thread_default(context);
		g_main_context_unref(context);
	});
	auto loop = future.get();
	assert(wivrn::display_manager() == "lightxr.service");
	assert(!wivrn::desktop_autostart_allowed());
	for (auto other: {"sddm.service", "gdm.service", "lightdm.service", "plasmalogin.service"})
	{
		{ std::lock_guard lock(mutex); manager = other; }
		assert(wivrn::display_manager() == other);
		assert(wivrn::desktop_autostart_allowed());
	}
	assert(wivrn::desktop_autostart_allowed(""));
	g_main_context_invoke(g_main_loop_get_context(loop), [](gpointer p) -> gboolean {
		g_main_loop_quit(static_cast<GMainLoop *>(p)); return G_SOURCE_REMOVE;
	}, loop);
	service.join();
	g_test_dbus_down(test_bus);
	g_object_unref(test_bus);
}
