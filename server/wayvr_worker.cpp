// SPDX-License-Identifier: GPL-3.0-or-later
#include "wayvr_worker.h"
#include <algorithm>
#include <gio/gio.h>
#include <cerrno>
#include <filesystem>
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
	// A lingering server can start before desktop login. Read the current
	// user-manager display environment at each worker launch, not just boot.
	if (auto bus = g_bus_get_sync(G_BUS_TYPE_SESSION, nullptr, nullptr))
	{
		auto reply = g_dbus_connection_call_sync(bus, "org.freedesktop.systemd1",
		        "/org/freedesktop/systemd1", "org.freedesktop.DBus.Properties", "Get",
		        g_variant_new("(ss)", "org.freedesktop.systemd1.Manager", "Environment"),
		        G_VARIANT_TYPE("(v)"), G_DBUS_CALL_FLAGS_NONE, 1000, nullptr, nullptr);
		if (reply)
		{
			GVariant * value = nullptr;
			g_variant_get(reply, "(v)", &value);
			if (g_variant_is_of_type(value, G_VARIANT_TYPE("as")))
			{
				GVariantIter iter;
				g_variant_iter_init(&iter, value);
				const char * entry;
				while (g_variant_iter_next(&iter, "&s", &entry))
				{
					std::string item(entry);
					auto equal = item.find('=');
					if (equal == std::string::npos) continue;
					auto name = item.substr(0, equal);
					if (name == "DISPLAY" || name == "WAYLAND_DISPLAY" || name == "XAUTHORITY"
					    || name == "XDG_CURRENT_DESKTOP" || name == "XDG_SESSION_TYPE")
						env = g_environ_setenv(env, name.c_str(), item.c_str() + equal + 1, TRUE);
				}
			}
			g_variant_unref(value);
			g_variant_unref(reply);
		}
		g_object_unref(bus);
	}
	env = g_environ_setenv(env, "XR_RUNTIME_JSON", manifest.c_str(), TRUE);
	env = g_environ_setenv(env, "WIVRN_MANAGED_WAYVR", "1", TRUE);
	env = g_environ_setenv(env, "WIVRN_DASHBOARD_EXECUTABLE", dashboard.c_str(), TRUE);
	env = g_environ_setenv(env, "WAYVR_LOGFILE", log_path.c_str(), TRUE);
    auto runtime = std::getenv("XDG_RUNTIME_DIR");
    auto control = ((runtime ? std::filesystem::path(runtime) : std::filesystem::path("/run/user") / std::to_string(getuid())) / "wivrn-pro/ShoraControl").string();
    env = g_environ_setenv(env, "WIVRN_SHORA_CONTROL_SOCKET", control.c_str(), TRUE);
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
