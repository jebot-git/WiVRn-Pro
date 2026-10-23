// SPDX-License-Identifier: GPL-3.0-or-later
// Runs against the private fake services in test-fltk-dashboard.py.
#include "backend.h"
#include <chrono>
#include <iostream>
#include <stdexcept>
#include <thread>
using namespace wivrn::dashboard;
void require(bool value, const char * message)
{
	if (!value)
		throw std::runtime_error(message);
}
int main(int argc, char ** argv)
try
{
	require(argc == 2, "Expected test socket path");
	auto bus = g_bus_get_sync(G_BUS_TYPE_SESSION, nullptr, nullptr);
	require(bus, "Missing private bus");
	auto backend = std::make_shared<Backend>(bus, argv[1]);
	g_object_unref(bus);
	Json state, snapshot;
	std::string notice, lastCommand;
	bool ok = false;
	backend->state = [&](const Json & j) { state = j; };
	backend->snapshot = [&](const Json & j) { snapshot = j; };
	backend->notice = [&](const std::string & s) { notice = s; };
	backend->finished = [&](const std::string & s, bool success, const std::string &) {lastCommand=s;ok=success; };
	auto wait = [&](auto condition, int milliseconds = 5000) {auto until=g_get_monotonic_time()+milliseconds*1000;while(!condition()&&g_get_monotonic_time()<until){for(int i=0;i<64&&g_main_context_pending(nullptr);++i)g_main_context_iteration(nullptr,false);backend->tick();std::this_thread::sleep_for(std::chrono::milliseconds(1));}require(condition(),"Async operation timed out"); };
	wait([&] { return state.contains("JsonConfiguration") && snapshot.contains("bones"); });
	require(snapshot.at("bones").size() == 2, "Skeleton endpoints missing");
	auto original = Json::parse(state.at("JsonConfiguration").get<std::string>());
	auto config = original;
	config["hostname"] = "fltk-test";
	bool saved = false, complete = false;
	backend->save(config, original, [&](bool yes) {saved=yes;complete=true; });
	wait([&] { return complete; });
	require(saved, "D-Bus save failed");
	complete = false;
	backend->save(config, original, [&](bool yes) {saved=yes;complete=true; });
	wait([&] { return complete; });
	require(!saved && notice.find("changed elsewhere") != std::string::npos, "Concurrent edit was overwritten");
	backend->method("EnablePairing", g_variant_new("(i)", 120));
	require(backend->send({{"command", "yaw_reset"}}), "Failed to queue yaw reset");
	wait([&] { return lastCommand == "yaw_reset"; });
	require(ok, "Calibration rejected");
	require(backend->send({{"command", "set_settings"}, {"settings", {{"height_m", 1.9}}}}), "Failed to queue settings");
	wait([&] { return lastCommand == "set_settings"; });
	require(ok && snapshot["settings"]["height_m"] == 1.9, "Settings response lost");
	backend->serviceAction("restart");
	require(backend->serviceBusy(), "Service process not started");
	wait([&] { return !backend->serviceBusy(); });
	require(notice == "WiVRn service action completed.", "systemctl completion not reported");
	backend->serviceAction("stop");
	wait([&] { return !backend->serviceBusy(); });
	require(notice.find("intentional service failure") != std::string::npos, "Service error output lost");
	backend->send({{"command", "malformed"}});
	wait([&] { return !backend->connected(); });
	wait([&] { return backend->connected() && snapshot.contains("bones"); });
	backend->send({{"command", "oversized"}});
	wait([&] { return !backend->connected(); });
	wait([&] { return backend->connected() && snapshot.contains("bones"); });
	backend->send({{"command", "timeout"}});
	wait([&] { return !backend->connected(); }, 6000);
	wait([&] { return backend->connected() && snapshot.contains("bones"); });
	backend->shutdown();
	for (int i = 0; i < 100; ++i)
	{
		while (g_main_context_pending(nullptr))
			g_main_context_iteration(nullptr, false);
		std::this_thread::sleep_for(std::chrono::milliseconds(1));
	}
	std::cout << "PASS: D-Bus state/save/conflict, calibration/settings, systemctl success/failure, malformed/oversized/timeout reconnect, shutdown\n";
	return 0;
}
catch (const std::exception & e)
{
	std::cerr << e.what() << '\n';
	return 1;
}
