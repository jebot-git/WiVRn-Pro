// SPDX-License-Identifier: GPL-3.0-or-later
#include "dome_supervisor.h"
#include <cassert>
#include <chrono>
#include <filesystem>
#include <fstream>
#include <thread>
static unsigned starts;
static void call(GDBusConnection *, const gchar *, const gchar *, const gchar *, const gchar * method, GVariant * parameters, GDBusMethodInvocation * invocation, gpointer)
{
	if (std::string(method) == "StartUnit")
	{
		const char *unit, *mode;
		g_variant_get(parameters, "(&s&s)", &unit, &mode);
		assert(std::string(unit) == "wivrn-pro-dome.service" && std::string(mode) == "fail");
		starts++;
		g_dbus_method_invocation_return_value(invocation, g_variant_new("(o)", "/org/freedesktop/systemd1/job/1"));
	}
	else
		g_dbus_method_invocation_return_value(invocation, g_variant_new("(s)", "{\"version\":1,\"backend\":\"drm\"}"));
}
static void spin(double seconds)
{
	auto until = std::chrono::steady_clock::now() + std::chrono::milliseconds(int(seconds * 1000));
	while (std::chrono::steady_clock::now() < until)
	{
		while (g_main_context_iteration(nullptr, false))
		{
		};
		std::this_thread::sleep_for(std::chrono::milliseconds(5));
	}
}
int main()
{
	char directory[] = "/tmp/wivrn-dome-supervisor-XXXXXX";
	assert(mkdtemp(directory));
	setenv("XDG_RUNTIME_DIR", directory, 1);
	auto test = g_test_dbus_new(G_TEST_DBUS_NONE);
	g_test_dbus_up(test);
	setenv("XDG_RUNTIME_DIR", directory, 1);
	auto bus = g_bus_get_sync(G_BUS_TYPE_SESSION, nullptr, nullptr);
	assert(bus);
	auto xml = g_dbus_node_info_new_for_xml("<node><interface name='org.freedesktop.systemd1.Manager'><method name='StartUnit'><arg type='s' direction='in'/><arg type='s' direction='in'/><arg type='o' direction='out'/></method></interface><interface name='org.domed.Compositor'><method name='GetIntegrationState'><arg type='s' direction='out'/></method></interface></node>", nullptr);
	assert(xml);
	GDBusInterfaceVTable table{};
	table.method_call = call;
	auto unit = g_dbus_connection_register_object(bus, "/org/freedesktop/systemd1", xml->interfaces[0], &table, nullptr, nullptr, nullptr);
	assert(unit);
	auto control = g_dbus_connection_register_object(bus, "/org/domed/Compositor", xml->interfaces[1], &table, nullptr, nullptr, nullptr);
	assert(control);
	auto manager = g_bus_own_name_on_connection(bus, "org.freedesktop.systemd1", G_BUS_NAME_OWNER_FLAGS_NONE, nullptr, nullptr, nullptr, nullptr);
	auto compositor = g_bus_own_name_on_connection(bus, "org.domed.Compositor", G_BUS_NAME_OWNER_FLAGS_NONE, nullptr, nullptr, nullptr, nullptr);
	spin(.2);
	auto supervisor = wivrn::dome_supervisor::start();
	spin(3);
	assert(starts == 0);
	auto root = std::filesystem::path(directory) / "dome";
	assert(std::filesystem::exists(root / "wivrn-context.json"));
	g_bus_unown_name(compositor);
	spin(1.2);
	assert(starts == 0); // give the login wrapper recovery time
	spin(4);
	assert(starts > 0);
	std::ofstream(root / "explicit-stop") << "stop\n";
	auto before = starts;
	spin(4);
	assert(starts == before);
	std::filesystem::remove(root / "explicit-stop");
	spin(5);
	assert(starts > before);
	supervisor->stop();
	before = starts;
	spin(2);
	assert(starts == before);
	supervisor.reset();
	g_bus_unown_name(manager);
	g_dbus_connection_unregister_object(bus, unit);
	g_dbus_connection_unregister_object(bus, control);
	g_dbus_node_info_unref(xml);
	g_object_unref(bus);
	g_test_dbus_down(test);
	g_object_unref(test);
	std::filesystem::remove_all(directory);
}
