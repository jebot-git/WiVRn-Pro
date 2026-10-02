// SPDX-License-Identifier: GPL-3.0-or-later
#include "wayvr_options.h"
#include "wayvr_worker.h"
#include <cassert>
#include <csignal>
#include <fstream>
#include <iostream>
#include <sys/prctl.h>
#include <thread>
#include <unistd.h>
static volatile sig_atomic_t stopping = 0;
static void stop(int signal)
{
	stopping = signal;
}
static pid_t read_pid(const std::filesystem::path & p)
{
	pid_t id = 0;
	std::ifstream(p) >> id;
	return id;
}
template <class F>
void wait_for(F f)
{
	auto until = std::chrono::steady_clock::now() + std::chrono::seconds(8);
	while (!f() && std::chrono::steady_clock::now() < until)
	{
		while (g_main_context_iteration(nullptr, false))
		{
		}
		std::this_thread::sleep_for(std::chrono::milliseconds(10));
	}
	assert(f());
}
int main(int argc, char ** argv)
{
	auto filename = std::getenv("TEST_WAYVR_PIDFILE");
	if (argc > 1)
	{
		assert(std::string(argv[1]) == "--openxr" && std::string(argv[2]) == "--wait");
		assert(std::string(std::getenv("XR_RUNTIME_JSON")) == "/test/manifest.json");
		assert(std::string(std::getenv("WIVRN_DASHBOARD_EXECUTABLE")) == "/test/dashboard");
		assert(std::string(std::getenv("WIVRN_MANAGED_WAYVR")) == "1");
		assert(std::string(std::getenv("WIVRN_SHORA_CONTROL_SOCKET")).ends_with("/wivrn-pro/ShoraControl"));
		int signal = 0;
		assert(prctl(PR_GET_PDEATHSIG, &signal) == 0 && signal == SIGTERM);
		assert(getpgrp() == getpid());
		std::signal(SIGTERM, stop);
		std::signal(SIGUSR1, stop);
		std::signal(SIGUSR2, stop);
		{
			std::ofstream output(filename);
			output << getpid();
		}
		while (!stopping)
			pause();
		return stopping == SIGUSR2 ? 75 : 0;
	}
	using namespace wivrn;
	assert(is_wayvr_application({"/home/me/WayVR-v26.8.0-x86_64.AppImage", "--show"}));
	assert(is_wayvr_application({"wayvr"}));
	assert(!is_wayvr_application({"/my/game"}) && !is_wayvr_application({"sh", "-c", "wayvr"}));
	wayvr_options defaults(nlohmann::json::object());
	assert(defaults.enabled && defaults.dashboard);
	wayvr_options imported(nlohmann::json::object(), {"/Apps/WayVR-v26.AppImage", "--headless"});
	assert(imported.flags == std::vector<std::string>{"--headless"});
	wayvr_options forced(nlohmann::json::object(), {"wayvr", "--openxr", "--wait", "--show", "--show"});
	assert(forced.flags == std::vector<std::string>{"--show"});
	wayvr_options explicit_options({{"wayvr", {{"enabled", false}, {"dashboard", false}, {"args", {"--show"}}}}}, {"wayvr", "--headless"});
	assert(!explicit_options.enabled && !explicit_options.dashboard && explicit_options.flags[0] == "--show");
	for (const auto * flag: {"--openvr", "--replace", "--multi", "--install", ";evil"})
	{
		bool rejected = false;
		try
		{
			wayvr_options bad({{"wayvr", {{"args", {flag}}}}});
		}
		catch (...)
		{
			rejected = true;
		}
		assert(rejected);
	}
	char name[] = "/tmp/wivrn-wayvr-supervisor-XXXXXX";
	auto tmp = std::filesystem::path(mkdtemp(name));
	auto pidfile = tmp / "worker.pid";
	setenv("TEST_WAYVR_PIDFILE", pidfile.c_str(), 1);
	auto executable = std::filesystem::read_symlink("/proc/self/exe").string();
	std::string status;
	auto make = [&](std::string exe) { return std::make_unique<wayvr_worker>(exe, "/test/manifest.json", "/test/dashboard", (tmp / "log").string(), std::vector<std::string>{}, [&](auto state) { status = state; }); };
	pid_t pid = 0;
	{
		auto worker = make(executable);
		wait_for([&] { return read_pid(pidfile) > 0; });
		auto first = read_pid(pidfile);
		kill(first, SIGKILL);
		wait_for([&] {pid = read_pid(pidfile); return pid > 0 && pid != first; });
		auto second = pid;
		kill(second, SIGUSR2); // WayVR's own Restart requests exit 75.
		wait_for([&] { pid = read_pid(pidfile); return pid > 0 && pid != second; });
	}
	assert(kill(pid, 0) < 0 && errno == ESRCH);
	std::filesystem::remove(pidfile);
	{
		auto worker = make(executable);
		wait_for([&] { return read_pid(pidfile) > 0; });
		pid = read_pid(pidfile);
		kill(pid, SIGUSR1);
		wait_for([&] { return status.starts_with("Stopped"); });
		assert(kill(pid, 0) < 0 && errno == ESRCH);
	}
	{
		auto worker = make("/missing-wayvr-worker");
		assert(status.starts_with("Restarting"));
		// Destroying a failed worker removes its pending retry source.
	}
	std::filesystem::remove_all(tmp);
	std::cout << "WayVR options and supervisor: legacy args, overrides, restart, normal exit, environment, process group, parent death, bounded cleanup passed\n";
}
