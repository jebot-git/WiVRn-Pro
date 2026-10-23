// SPDX-License-Identifier: GPL-3.0-or-later
#include "backend.h"
#include <algorithm>
#include <stdexcept>
namespace wivrn::dashboard
{
struct Backend::Operation
{
	std::shared_ptr<Backend> owner;
	Reply reply;
	// Keep write bytes alive even when a read failure disconnects the socket.
	std::string bytes;
};
Json variantJson(GVariant * v)
{
	if (g_variant_is_of_type(v, G_VARIANT_TYPE_VARIANT))
	{
		auto child = g_variant_get_variant(v);
		auto j = variantJson(child);
		g_variant_unref(child);
		return j;
	}
	if (g_variant_is_of_type(v, G_VARIANT_TYPE_BOOLEAN))
		return bool(g_variant_get_boolean(v));
	if (g_variant_is_of_type(v, G_VARIANT_TYPE_STRING))
		return g_variant_get_string(v, nullptr);
	if (g_variant_is_of_type(v, G_VARIANT_TYPE_UINT32))
		return g_variant_get_uint32(v);
	if (g_variant_is_of_type(v, G_VARIANT_TYPE_INT32))
		return g_variant_get_int32(v);
	if (g_variant_is_of_type(v, G_VARIANT_TYPE_DOUBLE))
		return g_variant_get_double(v);
	if (g_variant_is_of_type(v, G_VARIANT_TYPE("a{sv}")))
	{
		Json j = Json::object();
		GVariantIter iter;
		g_variant_iter_init(&iter, v);
		const char * key;
		GVariant * child;
		while (g_variant_iter_next(&iter, "{&sv}", &key, &child))
		{
			j[key] = variantJson(child);
			g_variant_unref(child);
		}
		return j;
	}
	return nullptr;
}
Backend::Backend(GDBusConnection * b, std::string path) : bus(G_DBUS_CONNECTION(g_object_ref(b))), socketPath(std::move(path)) {}
Backend::~Backend()
{
	shutdown();
	g_object_unref(cancel);
	g_object_unref(bus);
}
void Backend::shutdown()
{
	if (stopped)
		return;
	stopped = true;
	state = {};
	snapshot = {};
	notice = {};
	finished = {};
	g_cancellable_cancel(cancel);
	if (processDeadline)
	{
		g_source_remove(processDeadline);
		processDeadline = 0;
	}
	// This process owns only the systemctl client, never the WiVRn service.
	if (process)
		g_subprocess_force_exit(process);
	disconnect("");
}
void Backend::call(const char * dest, const char * path, const char * iface, const char * method, GVariant * args, Reply reply)
{
	auto op = new Operation{shared_from_this(), std::move(reply), {}};
	g_dbus_connection_call(bus, dest, path, iface, method, args, nullptr, G_DBUS_CALL_FLAGS_NO_AUTO_START, 3000, cancel, [](GObject * source, GAsyncResult * result, gpointer data) {
      std::unique_ptr<Operation> op(static_cast<Operation *>(data)); GError *error = nullptr;
      auto v = g_dbus_connection_call_finish(G_DBUS_CONNECTION(source), result, &error);
      if (!op->owner->stopped && op->reply) op->reply(v, error ? error->message : "");
      if (v) g_variant_unref(v);
      g_clear_error(&error); }, op);
}
void Backend::refresh()
{
	if (refreshing || stopped)
		return;
	refreshing = true;
	call(service, object, "org.freedesktop.DBus.Properties", "GetAll", g_variant_new("(s)", service), [self = shared_from_this()](GVariant * v, const std::string &) {
		self->refreshing = false;
		Json j = Json::object();
		if (v)
		{
			auto d = g_variant_get_child_value(v, 0);
			j = variantJson(d);
			g_variant_unref(d);
		}
		if (self->state)
			self->state(j);
	});
}
void Backend::method(const char * name, GVariant * args)
{
	call(service, object, service, name, args, [self = shared_from_this()](GVariant *, const std::string & error) {
		if (!error.empty() && self->notice)
			self->notice(error);
		self->refresh();
	});
}
void Backend::save(Json config, Json expected, std::function<void(bool)> done)
{
	call(service, object, "org.freedesktop.DBus.Properties", "Get", g_variant_new("(ss)", service, "JsonConfiguration"), [self = shared_from_this(), config = std::move(config), expected = std::move(expected), done = std::move(done)](GVariant * v, const std::string & error) {
		try
		{
			if (!v)
				throw std::runtime_error(error);
			auto child = g_variant_get_child_value(v, 0);
			auto current = variantJson(child);
			g_variant_unref(child);
			auto parsed = Json::parse(current.get<std::string>());
			if (parsed.is_null())
				parsed = Json::object();
			if (parsed != expected)
				throw std::runtime_error("WiVRn settings changed elsewhere. Reload before saving.");
			self->call(service, object, "org.freedesktop.DBus.Properties", "Set", g_variant_new("(ssv)", service, "JsonConfiguration", g_variant_new_string(config.dump().c_str())), [self, done](GVariant * reply, const std::string & error) {
				if (self->notice)
					self->notice(reply ? "WiVRn settings saved; apply on the next session." : error);
				done(reply != nullptr);
				self->refresh();
			});
		}
		catch (const std::exception & e)
		{
			if (self->notice)
				self->notice(e.what());
			done(false);
		}
	});
}
void Backend::serviceAction(const std::string & action)
{
	if (action != "start" && action != "stop" && action != "restart")
		throw std::invalid_argument("Invalid service action");
	if (process || stopped)
		return;
	GError * error = nullptr;
	process = g_subprocess_new(GSubprocessFlags(G_SUBPROCESS_FLAGS_STDOUT_PIPE | G_SUBPROCESS_FLAGS_STDERR_MERGE),
	                           &error,
	                           "systemctl",
	                           "--user",
	                           action.c_str(),
	                           "wivrn-pro.service",
	                           nullptr);
	if (!process)
	{
		if (notice)
			notice(error->message);
		g_clear_error(&error);
		return;
	}
	if (notice)
		notice("WiVRn service: " + action + "…");
	processDeadline = g_timeout_add(20000, [](gpointer data) -> gboolean {
    auto self = static_cast<Backend *>(data); self->processDeadline = 0;
    if (self->process) { g_subprocess_force_exit(self->process); if (self->notice) self->notice("systemctl timed out after 20 seconds."); }
    return G_SOURCE_REMOVE; }, this);
	auto op = new Operation{shared_from_this(), {}, {}};
	g_subprocess_communicate_utf8_async(process, nullptr, nullptr, [](GObject * source, GAsyncResult * result, gpointer data) {
      std::unique_ptr<Operation> op(static_cast<Operation *>(data)); auto self = op->owner;
      char *out = nullptr; GError *error = nullptr;
      bool ok = g_subprocess_communicate_utf8_finish(G_SUBPROCESS(source), result, &out, nullptr, &error);
      if (self->processDeadline) { g_source_remove(self->processDeadline); self->processDeadline = 0; }
      if (!self->stopped && self->notice) self->notice(ok && g_subprocess_get_successful(G_SUBPROCESS(source)) ?
        "WiVRn service action completed." : error ? error->message : out && *out ? out : "WiVRn service action failed.");
      g_free(out); g_clear_error(&error); g_clear_object(&self->process);
      if (!self->stopped) self->refresh(); }, op);
}
void Backend::connect()
{
	if (stopped || connecting || socket)
		return;
	connecting = true;
	client = g_socket_client_new();
	g_socket_client_set_timeout(client, 4);
	socketCancel = g_cancellable_new();
	auto address = g_unix_socket_address_new(socketPath.c_str());
	auto op = new Operation{shared_from_this(), {}, {}};
	g_socket_client_connect_async(client, G_SOCKET_CONNECTABLE(address), socketCancel, [](GObject * source, GAsyncResult * result, gpointer data) {
      std::unique_ptr<Operation> op(static_cast<Operation *>(data)); auto self = op->owner; GError *error = nullptr;
      auto sock = g_socket_client_connect_finish(G_SOCKET_CLIENT(source), result, &error);
      self->connecting = false; g_clear_object(&self->client);
      if (self->stopped) { if (sock) g_object_unref(sock); }
      else if (!sock) { self->disconnect(""); }
      else { self->socket = sock; self->buffer.clear(); self->read(); self->send({{"command","status"}}); }
      g_clear_error(&error); }, op);
	g_object_unref(address);
}
void Backend::disconnect(const std::string & message)
{
	if (socketCancel)
	{
		g_cancellable_cancel(socketCancel);
		g_clear_object(&socketCancel);
	}
	if (socket)
	{
		g_io_stream_close(G_IO_STREAM(socket), nullptr, nullptr);
		g_clear_object(&socket);
	}
	queue.clear();
	pending = nullptr;
	buffer.clear();
	deadline = 0;
	reconnectAt = g_get_monotonic_time() + 1000000;
	if (snapshot)
		snapshot(Json::object());
	if (!message.empty() && notice)
		notice(message);
}
bool Backend::send(Json command)
{
	if (!socket || queue.size() >= 16 || !command.is_object() || !command.contains("command"))
		return false;
	if (command["command"] == "status" && (!pending.is_null() || !queue.empty()))
		return false;
	queue.push_back(std::move(command));
	next();
	return true;
}
void Backend::next()
{
	if (!socket || !pending.is_null() || queue.empty())
		return;
	pending = std::move(queue.front());
	queue.pop_front();
	deadline = g_get_monotonic_time() + 4000000;
	auto op = new Operation{shared_from_this(), {}, pending.dump() + "\n"};
	g_output_stream_write_all_async(g_io_stream_get_output_stream(G_IO_STREAM(socket)), op->bytes.data(), op->bytes.size(), G_PRIORITY_DEFAULT, socketCancel, [](GObject * source, GAsyncResult * result, gpointer data) {
      std::unique_ptr<Operation> op(static_cast<Operation *>(data)); GError *error = nullptr; gsize written;
      if (!g_output_stream_write_all_finish(G_OUTPUT_STREAM(source), result, &written, &error) &&
          !g_error_matches(error, G_IO_ERROR, G_IO_ERROR_CANCELLED) && !op->owner->stopped) op->owner->disconnect(error->message);
      g_clear_error(&error); }, op);
}
void Backend::read()
{
	if (!socket || stopped)
		return;
	auto op = new Operation{shared_from_this(), {}, {}};
	g_input_stream_read_bytes_async(g_io_stream_get_input_stream(G_IO_STREAM(socket)), 8192, G_PRIORITY_DEFAULT, socketCancel, [](GObject * source, GAsyncResult * result, gpointer data) {
      std::unique_ptr<Operation> op(static_cast<Operation *>(data)); auto self = op->owner; GError *error = nullptr;
      auto bytes = g_input_stream_read_bytes_finish(G_INPUT_STREAM(source), result, &error);
      if (self->stopped || g_error_matches(error, G_IO_ERROR, G_IO_ERROR_CANCELLED)) { if (bytes) g_bytes_unref(bytes); g_clear_error(&error); return; }
      gsize size = 0; auto p = bytes ? static_cast<const char *>(g_bytes_get_data(bytes, &size)) : nullptr;
      if (p && size) self->buffer.append(p, size);
      if (bytes) g_bytes_unref(bytes);
      if (error || !size) { self->disconnect(error ? error->message : "Tracking worker disconnected; reconnecting…"); g_clear_error(&error); return; }
      if (self->buffer.size() > 1024*1024) { self->disconnect("Tracking response exceeds 1 MiB."); return; }
      size_t end;
      while ((end = self->buffer.find('\n')) != std::string::npos) {
        auto line = self->buffer.substr(0,end); self->buffer.erase(0,end+1);
        try {
          auto j = Json::parse(line); if (!j.at("status").is_object() || !j.at("ok").is_boolean()) throw std::runtime_error("Malformed tracking reply");
          auto command = self->pending.value("command",std::string("status")); self->pending = nullptr; self->deadline = 0;
          if (self->snapshot) self->snapshot(j["status"]);
          auto err = j.value("error",Json()); auto text = err.is_string() ? err.get<std::string>() : "";
          if (self->finished) self->finished(command,j["ok"].get<bool>(),text);
          if (!j["ok"].get<bool>() && self->notice) self->notice(text);
          self->next();
        } catch (const std::exception &e) { self->disconnect(std::string("Invalid tracking response: ")+e.what()); return; }
      }
      self->read(); }, op);
}
void Backend::tick()
{
	if (stopped)
		return;
	auto now = g_get_monotonic_time();
	if (now >= refreshAt)
	{
		refreshAt = now + 1500000;
		refresh();
	}
	if (!socket && !connecting && now >= reconnectAt)
		connect();
	if (deadline && now >= deadline)
	{
		disconnect("Tracking command timed out; reconnecting…");
		return;
	}
	if (socket && now >= statusAt)
	{
		statusAt = now + 50000;
		send({{"command", "status"}});
	}
}
} // namespace wivrn::dashboard
