// SPDX-License-Identifier: GPL-3.0-or-later
#include "wayvr_worker.h"
#include <algorithm>
#include <cerrno>
#include <csignal>
#include <sys/prctl.h>
#include <sys/wait.h>
#include <thread>
#include <unistd.h>
namespace wivrn
{
wayvr_worker::wayvr_worker(std::string executable, std::string manifest, std::string dashboard, std::string log_path, std::vector<std::string> flags, std::function<void(const std::string &)> changed) :
        arguments{std::move(executable), "--openxr", "--wait"}, manifest(std::move(manifest)), dashboard(std::move(dashboard)), log_path(std::move(log_path)), changed(std::move(changed))
{
	arguments.insert(arguments.end(), flags.begin(), flags.end());
	spawn();
}
void wayvr_worker::schedule_restart()
{
	changed("Restarting after worker failure");
	auto delay = std::min(30u, 1u << std::min(failures++, 5u));
	retry = g_timeout_add_seconds(delay, [](gpointer data) -> gboolean {
        auto & self = *static_cast<wayvr_worker *>(data);
        self.retry = 0; self.spawn(); return G_SOURCE_REMOVE; }, this);
}
void wayvr_worker::spawn()
{
	std::vector<gchar *> argv;
	for (auto & arg: arguments)
		argv.push_back(arg.data());
	argv.push_back(nullptr);
	auto env = g_get_environ();
	env = g_environ_setenv(env, "XR_RUNTIME_JSON", manifest.c_str(), TRUE);
	env = g_environ_setenv(env, "WIVRN_MANAGED_WAYVR", "1", TRUE);
	env = g_environ_setenv(env, "WIVRN_DASHBOARD_EXECUTABLE", dashboard.c_str(), TRUE);
	env = g_environ_setenv(env, "WAYVR_LOGFILE", log_path.c_str(), TRUE);
	GError * error = nullptr;
	auto parent = getpid();
	auto ok = g_spawn_async(nullptr, argv.data(), env, G_SPAWN_DO_NOT_REAP_CHILD, [](gpointer data) {
            // Only async-signal-safe syscalls between fork and exec.
            auto parent = *static_cast<pid_t *>(data);
            if (setpgid(0, 0) || prctl(PR_SET_PDEATHSIG, SIGTERM) || getppid() != parent) _exit(127); }, &parent, &pid, &error);
	g_strfreev(env);
	if (!ok)
	{
		g_warning("WayVR startup failed: %s", error->message);
		g_error_free(error);
		pid = 0;
		schedule_restart();
		return;
	}
	changed("Worker running (OpenXR readiness is not yet confirmed)");
	watch = g_child_watch_add(pid, [](GPid child, gint status, gpointer data) {
        auto &self = *static_cast<wayvr_worker *>(data);
        self.pid = 0; self.watch = 0;
        // WayVR-owned desktop apps must not remain after its compositor exits.
        kill(-child, SIGTERM);
        kill(-child, SIGKILL);
        g_spawn_close_pid(child);
        if (WIFEXITED(status) && WEXITSTATUS(status) == 0) self.changed("Stopped (closed or another WayVR instance is active)");
        else { g_warning("WayVR exited (%d)", status); self.schedule_restart(); } }, this);
}
wayvr_worker::~wayvr_worker()
{
	if (retry)
		g_source_remove(retry);
	if (watch)
		g_source_remove(watch);
	if (!pid)
		return;
	kill(-pid, SIGTERM);
	bool reaped = false;
	for (int i = 0; i < 40; ++i)
	{
		auto result = waitpid(pid, nullptr, WNOHANG);
		if (result == pid || (result < 0 && errno == ECHILD))
		{
			reaped = true;
			break;
		}
		std::this_thread::sleep_for(std::chrono::milliseconds(50));
	}
	kill(-pid, SIGKILL);
	if (!reaped)
		while (waitpid(pid, nullptr, 0) < 0 && errno == EINTR)
		{
		}
	g_spawn_close_pid(pid);
}
} // namespace wivrn
