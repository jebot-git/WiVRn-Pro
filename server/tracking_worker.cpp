// SPDX-License-Identifier: GPL-3.0-or-later
#include "tracking_worker.h"
#include "active_runtime.h"
#include "config_migration.h"
#include "utils/xdg_base_directory.h"
#include <fcntl.h>
#include <filesystem>
#include <fstream>
#include <signal.h>
#include <stdexcept>
#include <sys/stat.h>
#include <sys/wait.h>
#include <thread>
#include <unistd.h>
namespace wivrn
{
tracking_worker::tracking_worker()
{
	auto runtime = std::getenv("XDG_RUNTIME_DIR");
	auto dir = (runtime ? std::filesystem::path(runtime) : std::filesystem::path("/run/user") / std::to_string(getuid())) / "wivrn-pro";
	if (mkdir(dir.c_str(), 0700) != 0 && errno != EEXIST)
		throw std::runtime_error("Cannot create tracking runtime directory");
	struct stat st{};
	if (lstat(dir.c_str(), &st) || !S_ISDIR(st.st_mode) || st.st_uid != getuid() || (st.st_mode & 0077))
		throw std::runtime_error("Tracking runtime directory must be private and user-owned");
	auto config = xdg_config_home() / "wivrn-pro" / "shora.toml";
	migration::create_once(config, "height_m = 1.80\nsmoothing = 0.3\nprediction = 0.0\n[haritorax]\nenabled = false\n[face]\nenabled = false\n");
	auto paths = active_runtime::manifest_path();
	if (paths.empty())
		throw std::runtime_error("Matching WiVRn OpenXR manifest is missing");
	manifest = paths.front().string();
	arguments = {std::filesystem::read_symlink("/proc/self/exe").string(), "--shora-worker", std::to_string(getpid()), "--config", config.string(), "--ui", "headless", "--solarxr-socket", (dir / "ShoraRpc.backend").string(), "--feeder-socket", (dir / "ShoraInput.backend").string(), "--control-socket", (dir / "ShoraControl").string()};
	spatial_path = (dir / "bridge.sock").string();
	spawn();
}
void tracking_worker::spawn()
{
	if (stopping)
		return;
	std::vector<gchar *> argv;
	for (auto & arg: arguments)
		argv.push_back(arg.data());
	argv.push_back(nullptr);
	auto env = g_get_environ();
	env = g_environ_setenv(env, "XR_RUNTIME_JSON", manifest.c_str(), TRUE);
	env = g_environ_setenv(env, "WIVRN_SPATIAL_SOCKET", spatial_path.c_str(), TRUE);
	GError * error = nullptr;
	bool ok = g_spawn_async(nullptr, argv.data(), env, G_SPAWN_DO_NOT_REAP_CHILD, nullptr, nullptr, &pid, &error);
	g_strfreev(env);
	if (!ok)
	{
		g_warning("Tracking worker startup failed: %s", error->message);
		g_error_free(error);
		pid = 0;
		retry = g_timeout_add_seconds(2, [](gpointer data) -> gboolean {
            auto & self = *static_cast<tracking_worker *>(data); self.retry = 0; self.spawn(); return G_SOURCE_REMOVE; }, this);
		return;
	}
	watch = g_child_watch_add(pid, [](GPid child, gint status, gpointer data) {
        auto & self = *static_cast<tracking_worker *>(data);
        self.watch = 0; self.pid = 0; g_spawn_close_pid(child);
        g_warning("Tracking worker exited (%d); restarting tracking independently", status);
        if (!self.stopping) self.retry = g_timeout_add_seconds(2, [](gpointer data) -> gboolean {
            auto & self = *static_cast<tracking_worker *>(data); self.retry = 0; self.spawn(); return G_SOURCE_REMOVE;
        }, &self); }, this);
}
tracking_worker::~tracking_worker()
{
	stopping = true;
	if (retry)
		g_source_remove(retry);
	if (watch)
		g_source_remove(watch);
	if (!pid)
		return;
	kill(pid, SIGTERM);
	for (int i = 0; i < 40; ++i)
	{
		auto result = waitpid(pid, nullptr, WNOHANG);
		if (result == pid || (result < 0 && errno == ECHILD))
		{
			g_spawn_close_pid(pid);
			return;
		}
		std::this_thread::sleep_for(std::chrono::milliseconds(50));
	}
	kill(pid, SIGKILL);
	while (waitpid(pid, nullptr, 0) < 0 && errno == EINTR)
	{
	}
	g_spawn_close_pid(pid);
}
} // namespace wivrn
