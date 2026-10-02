// SPDX-License-Identifier: GPL-3.0-or-later
// Tests the production GLib supervisor with a harmless worker personality.
#include "active_runtime.h"
#include "config_migration.h"
#include "tracking_worker.h"
#include <cassert>
#include <csignal>
#include <iostream>
#include <sys/prctl.h>
#include <sys/wait.h>
#include <thread>
std::filesystem::path xdg_config_home()
{
	return std::getenv("XDG_CONFIG_HOME");
}
std::vector<std::filesystem::path> wivrn::active_runtime::manifest_path()
{
	return {"/unused-test-manifest.json"};
}
static volatile sig_atomic_t stopping = 0;
static void stop(int)
{
	stopping = 1;
}
static pid_t worker_pid(const std::filesystem::path & path)
{
	std::ifstream input(path);
	pid_t pid = 0;
	input >> pid;
	return pid;
}
template <typename Predicate>
static void wait_for(Predicate predicate)
{
	auto until = std::chrono::steady_clock::now() + std::chrono::seconds(8);
	while (!predicate())
	{
		assert(std::chrono::steady_clock::now() < until);
		g_main_context_iteration(nullptr, false);
		std::this_thread::sleep_for(std::chrono::milliseconds(10));
	}
}
int main(int argc, char ** argv)
{
	auto pidfile = std::filesystem::path(std::getenv("TEST_WORKER_PIDFILE") ? std::getenv("TEST_WORKER_PIDFILE") : "/unused");
	if (argc > 2 && std::string_view(argv[1]) == "--shora-worker")
	{
		prctl(PR_SET_PDEATHSIG, SIGTERM);
		assert(getppid() == std::stoi(argv[2]));
		assert(std::string_view(std::getenv("XR_RUNTIME_JSON")) == "/unused-test-manifest.json");
		std::signal(SIGTERM, stop);
		{
			std::ofstream file(pidfile);
			file << getpid();
		}
		while (!stopping)
			pause();
		return 0;
	}
	char name[] = "/tmp/wivrn-supervisor-XXXXXX";
	auto temp = std::filesystem::path(mkdtemp(name));
	setenv("XDG_RUNTIME_DIR", temp.c_str(), 1);
	setenv("XDG_CONFIG_HOME", temp.c_str(), 1);
	pidfile = temp / "worker.pid";
	setenv("TEST_WORKER_PIDFILE", pidfile.c_str(), 1);
	pid_t second = 0;
	{
		wivrn::tracking_worker supervisor;
		wait_for([&] { return worker_pid(pidfile) > 0; });
		auto first = worker_pid(pidfile);
		kill(first, SIGKILL);
		wait_for([&] { second = worker_pid(pidfile); return second > 0 && second != first; });
		assert(kill(second, 0) == 0);
	}
	assert(kill(second, 0) == -1 && errno == ESRCH);
	assert(std::filesystem::exists(temp / "wivrn-pro/shora.toml"));
	std::filesystem::remove_all(temp);
	std::cout << "Supervisor: automatic worker restart, manifest isolation and bounded clean stop passed\n";
}
