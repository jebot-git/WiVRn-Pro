// SPDX-License-Identifier: GPL-3.0-or-later
#include "dome_supervisor.h"
#include <algorithm>
#include <filesystem>
#include <fstream>
#include <nlohmann/json.hpp>
namespace wivrn
{
namespace
{
using Hold = std::shared_ptr<dome_supervisor>;
auto directory()
{
	return std::filesystem::path(g_getenv("XDG_RUNTIME_DIR")) / "dome";
}
} // namespace
std::shared_ptr<dome_supervisor> dome_supervisor::start()
{
	auto self = std::make_shared<dome_supervisor>();
	if (!g_getenv("XDG_RUNTIME_DIR"))
		return self;
	self->timer = g_timeout_add_seconds(1, [](gpointer data) -> gboolean {static_cast<dome_supervisor*>(data)->tick();return G_SOURCE_CONTINUE; }, self.get());
	self->tick();
	return self;
}
void dome_supervisor::stop()
{
	stopped = true;
	g_cancellable_cancel(cancel);
	if (timer)
	{
		g_source_remove(timer);
		timer = 0;
	}
}
dome_supervisor::~dome_supervisor()
{
	stop();
	g_clear_object(&bus);
	g_object_unref(cancel);
}
void dome_supervisor::tick()
{
	if (stopped || pending || g_get_monotonic_time() < retry)
		return;
	if (bus && g_dbus_connection_is_closed(bus))
		g_clear_object(&bus);
	if (!bus)
	{
		pending = true;
		g_bus_get(G_BUS_TYPE_SESSION, cancel, [](GObject *, GAsyncResult * r, gpointer data) {
            std::unique_ptr<Hold> hold(static_cast<Hold*>(data));auto self=*hold;
            auto bus=g_bus_get_finish(r,nullptr);self->pending=false;
            if(self->stopped){if(bus)g_object_unref(bus);return;}
            self->bus=bus;if(bus){g_dbus_connection_set_exit_on_close(bus,false);self->tick();} }, new Hold(shared_from_this()));
		return;
	}
	pending = true;
	g_dbus_connection_call(bus, "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "NameHasOwner", g_variant_new("(s)", "org.domed.Compositor"), G_VARIANT_TYPE("(b)"), G_DBUS_CALL_FLAGS_NO_AUTO_START, 500, cancel, [](GObject * source, GAsyncResult * r, gpointer data) {
        std::unique_ptr<Hold> hold(static_cast<Hold*>(data));auto self=*hold;GVariant *reply=g_dbus_connection_call_finish(G_DBUS_CONNECTION(source),r,nullptr);self->pending=false;
        if(self->stopped){if(reply)g_variant_unref(reply);return;}
        if(!reply)return;
        gboolean owned=false;if(reply){g_variant_get(reply,"(b)",&owned);g_variant_unref(reply);}
        if(!owned){if(!self->missing_since)self->missing_since=g_get_monotonic_time();if(g_get_monotonic_time()-self->missing_since>=3*G_USEC_PER_SEC)self->ensure_unit();return;}
        self->missing_since=0;
        self->failures=0;
        // Cache the real backend for recovery. An older DomeD owner is still an
        // owner; never spawn a second compositor merely because an API is absent.
        self->pending=true;
        g_dbus_connection_call(self->bus,"org.domed.Compositor","/org/domed/Compositor","org.domed.Compositor","GetIntegrationState",nullptr,G_VARIANT_TYPE("(s)"),G_DBUS_CALL_FLAGS_NO_AUTO_START,500,self->cancel,
        [](GObject *source,GAsyncResult *r,gpointer data){
            std::unique_ptr<Hold> hold(static_cast<Hold*>(data));auto self=*hold;auto reply=g_dbus_connection_call_finish(G_DBUS_CONNECTION(source),r,nullptr);self->pending=false;
            if(reply&&!self->stopped){const char *text;g_variant_get(reply,"(&s)",&text);try{auto j=nlohmann::json::parse(text);auto backend=j.value("backend",std::string());if(j.value("version",0)==1&&(backend=="drm"||backend=="nested")){
                std::filesystem::create_directories(directory());auto tmp=directory()/"wivrn-context.tmp";std::ofstream f(tmp);f<<nlohmann::json{{"version",1},{"backend",backend}}.dump();f.close();std::filesystem::rename(tmp,directory()/"wivrn-context.json");
            }}catch(const std::exception &e){g_debug("DomeD recovery context: %s",e.what());}}
            if(reply)g_variant_unref(reply);
        },new Hold(self)); }, new Hold(shared_from_this()));
}
void dome_supervisor::ensure_unit()
{
	std::error_code ec;
	if (std::filesystem::exists(directory() / "explicit-stop", ec))
		return;
	pending = true;
	// Start an independent user unit. WiVRn's control-group teardown must never
	// kill the desktop compositor, and systemd arbitrates concurrent requests.
	g_dbus_connection_call(bus, "org.freedesktop.systemd1", "/org/freedesktop/systemd1", "org.freedesktop.systemd1.Manager", "StartUnit", g_variant_new("(ss)", "wivrn-pro-dome.service", "fail"), G_VARIANT_TYPE("(o)"), G_DBUS_CALL_FLAGS_NO_AUTO_START, 1000, cancel, [](GObject * source, GAsyncResult * r, gpointer data) {std::unique_ptr<Hold> hold(static_cast<Hold*>(data));auto self=*hold;GError *error=nullptr;auto reply=g_dbus_connection_call_finish(G_DBUS_CONNECTION(source),r,&error);self->pending=false;
        self->retry=g_get_monotonic_time()+std::min(30u,1u<<std::min(self->failures++,5u))*G_USEC_PER_SEC;
        if(error){g_debug("DomeD supervision: %s",error->message);g_error_free(error);}if(reply)g_variant_unref(reply); }, new Hold(shared_from_this()));
}
} // namespace wivrn
