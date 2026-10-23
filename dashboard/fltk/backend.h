// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include <deque>
#include <functional>
#include <gio/gio.h>
#include <memory>
#include <nlohmann/json.hpp>
#include <string>
namespace wivrn::dashboard
{
using Json = nlohmann::json;
inline constexpr auto service = "io.github.wivrn.Server";
inline constexpr auto object = "/io/github/wivrn/Server";
class Backend : public std::enable_shared_from_this<Backend>
{
	struct Operation;
	GCancellable *cancel = g_cancellable_new(), *socketCancel = nullptr;
	GSocketClient * client = nullptr;
	GSocketConnection * socket = nullptr;
	GSubprocess * process = nullptr;
	guint processDeadline = 0;
	bool connecting = false, refreshing = false, stopped = false;
	std::string buffer, payload;
	std::deque<Json> queue;
	Json pending;
	gint64 deadline = 0, reconnectAt = 0, refreshAt = 0, statusAt = 0;
	void read();
	void next();
	void disconnect(const std::string & message);

public:
	GDBusConnection * bus = nullptr;
	std::string socketPath;
	std::function<void(const Json &)> state, snapshot;
	std::function<void(const std::string &)> notice;
	std::function<void(const std::string &, bool, const std::string &)> finished;
	bool connected() const
	{
		return socket != nullptr;
	}
	bool serviceBusy() const
	{
		return process != nullptr;
	}
	explicit Backend(GDBusConnection * connection, std::string path);
	~Backend();
	void shutdown();
	void tick();
	void connect();
	bool send(Json command);
	void refresh();
	using Reply = std::function<void(GVariant *, const std::string &)>;
	void call(const char * destination, const char * path, const char * interface, const char * method, GVariant * args, Reply reply);
	void method(const char * name, GVariant * args = nullptr);
	void save(Json config, Json expected, std::function<void(bool)> done);
	void serviceAction(const std::string & action);
};
Json variantJson(GVariant * value);
} // namespace wivrn::dashboard
