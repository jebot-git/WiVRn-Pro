// SPDX-License-Identifier: GPL-3.0-or-later
#include "tray.h"
#include <iostream>
#include <stdexcept>
using wivrn::dashboard::Tray;

GDBusConnection * connection()
{
	GError * error = nullptr;
	auto bus = g_dbus_connection_new_for_address_sync(g_getenv("DBUS_SESSION_BUS_ADDRESS"),
	    static_cast<GDBusConnectionFlags>(G_DBUS_CONNECTION_FLAGS_AUTHENTICATION_CLIENT | G_DBUS_CONNECTION_FLAGS_MESSAGE_BUS_CONNECTION),
	    nullptr, nullptr, &error);
	if (!bus)
		throw std::runtime_error(error ? error->message : "No private session bus");
	return bus;
}

int main(int argc, char ** argv)
try
{
	std::string first = argc > 1 ? argv[1] : "vr";
	std::string second = first == "vr" ? "desktop" : "vr";
	auto a = connection(), b = connection(), c = connection(), d = connection();
	int firstActivations = 0, secondActivations = 0;
	{
		Tray one(a, first, [&](int id) { if (id == 1) ++firstActivations; });
		Tray two(b, second, [&](int id) { if (id == 1) ++secondActivations; });
		if (!one.owner || !two.owner)
			throw std::runtime_error("VR and desktop must both own their surface instance");
		Tray duplicateOne(c, first, [](int) {}), duplicateTwo(d, second, [](int) {});
		if (duplicateOne.owner || duplicateTwo.owner)
			throw std::runtime_error("Repeated launches must activate the existing instance");
		auto until = g_get_monotonic_time() + 3 * G_USEC_PER_SEC;
		while ((firstActivations == 0 || secondActivations == 0) && g_get_monotonic_time() < until)
		{
			while (g_main_context_iteration(nullptr, false)) {}
			g_usleep(1000);
		}
		if (firstActivations != 1 || secondActivations != 1)
			throw std::runtime_error("Activation did not reach the matching surface exactly once");
	}
	for (auto bus : {a, b, c, d})
		g_object_unref(bus);
	std::cout << first << "-first surface coexistence and activation passed\n";
	return 0;
}
catch (const std::exception & error)
{
	std::cerr << error.what() << '\n';
	return 1;
}
