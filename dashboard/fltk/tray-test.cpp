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

int main()
try
{
	auto a = connection(), b = connection(), c = connection();
	int activations = 0;
	{
		Tray one(a, [&](int id) { if (id == 1) ++activations; });
		Tray duplicateOne(b, [](int) {}), duplicateTwo(c, [](int) {});
		if (!one.owner || duplicateOne.owner || duplicateTwo.owner)
			throw std::runtime_error("Desktop and DomeD XR must share one ordinary dashboard instance");
		auto until = g_get_monotonic_time() + 3 * G_USEC_PER_SEC;
		while (activations < 2 && g_get_monotonic_time() < until)
		{
			while (g_main_context_iteration(nullptr, false)) {}
			g_usleep(1000);
		}
		if (activations != 2)
			throw std::runtime_error("Repeated launches did not activate the shared instance");
	}
	for (auto bus : {a, b, c})
		g_object_unref(bus);
	std::cout << "Shared desktop/DomeD instance and duplicate activation passed\n";
	return 0;
}
catch (const std::exception & error)
{
	std::cerr << error.what() << '\n';
	return 1;
}
