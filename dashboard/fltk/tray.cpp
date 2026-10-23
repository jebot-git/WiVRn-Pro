// SPDX-License-Identifier: GPL-3.0-or-later
#include "tray.h"
#include <cstring>
#include <stdexcept>
namespace wivrn::dashboard
{
namespace
{
constexpr auto instance = "io.github.wivrn.ProDashboard";
constexpr auto instancePath = "/io/github/wivrn/ProDashboard";
constexpr auto control = R"XML(<node><interface name="io.github.wivrn.ProDashboard"><method name="Activate"><arg type="s" direction="in"/></method><method name="Surface"><arg type="s" direction="out"/></method></interface></node>)XML";
constexpr auto item = R"XML(<node><interface name="org.kde.StatusNotifierItem">
<property name="Category" type="s" access="read"/><property name="Id" type="s" access="read"/><property name="Title" type="s" access="read"/><property name="Status" type="s" access="read"/><property name="WindowId" type="u" access="read"/><property name="IconName" type="s" access="read"/><property name="IconThemePath" type="s" access="read"/><property name="IconPixmap" type="a(iiay)" access="read"/><property name="ItemIsMenu" type="b" access="read"/><property name="Menu" type="o" access="read"/>
<method name="Activate"><arg type="i" direction="in"/><arg type="i" direction="in"/></method><method name="SecondaryActivate"><arg type="i" direction="in"/><arg type="i" direction="in"/></method><method name="ContextMenu"><arg type="i" direction="in"/><arg type="i" direction="in"/></method><method name="Scroll"><arg type="i" direction="in"/><arg type="s" direction="in"/></method><signal name="NewIcon"/><signal name="NewStatus"><arg type="s"/></signal></interface></node>)XML";
constexpr auto menu = R"XML(<node><interface name="com.canonical.dbusmenu"><property name="Version" type="u" access="read"/><property name="TextDirection" type="s" access="read"/><property name="Status" type="s" access="read"/><property name="IconThemePath" type="as" access="read"/>
<method name="GetLayout"><arg type="i" direction="in"/><arg type="i" direction="in"/><arg type="as" direction="in"/><arg type="u" direction="out"/><arg type="(ia{sv}av)" direction="out"/></method><method name="AboutToShow"><arg type="i" direction="in"/><arg type="b" direction="out"/></method><method name="Event"><arg type="i" direction="in"/><arg type="s" direction="in"/><arg type="v" direction="in"/><arg type="u" direction="in"/></method><signal name="LayoutUpdated"><arg type="u"/><arg type="i"/></signal></interface></node>)XML";
const char * labels[] = {"Show WiVRn Pro", "Start WiVRn", "Stop WiVRn", "Restart WiVRn", "Quit dashboard"};
GVariant * node(int id, bool children)
{
	GVariantBuilder props, nodes;
	g_variant_builder_init(&props, G_VARIANT_TYPE("a{sv}"));
	g_variant_builder_init(&nodes, G_VARIANT_TYPE("av"));
	if (id > 0 && id <= 5)
	{
		g_variant_builder_add(&props, "{sv}", "label", g_variant_new_string(labels[id - 1]));
		g_variant_builder_add(&props, "{sv}", "enabled", g_variant_new_boolean(true));
	}
	if (id == 0 && children)
		for (int i = 1; i <= 5; ++i)
			g_variant_builder_add(&nodes, "v", node(i, false));
	return g_variant_new("(i@a{sv}@av)", id, g_variant_builder_end(&props), g_variant_builder_end(&nodes));
}
} // namespace
GVariant * Tray::property(GDBusConnection *, const gchar *, const gchar *, const gchar * iface, const gchar * key, GError **, gpointer data)
{
	(void)data;
	if (!strcmp(iface, "com.canonical.dbusmenu"))
	{
		if (!strcmp(key, "Version"))
			return g_variant_new_uint32(3);
		if (!strcmp(key, "TextDirection"))
			return g_variant_new_string("ltr");
		if (!strcmp(key, "Status"))
			return g_variant_new_string("normal");
		if (!strcmp(key, "IconThemePath"))
			return g_variant_new_strv(nullptr, 0);
	}
	if (!strcmp(key, "Id"))
		return g_variant_new_string("wivrn.pro");
	if (!strcmp(key, "Title"))
		return g_variant_new_string("WiVRn Pro");
	if (!strcmp(key, "Category"))
		return g_variant_new_string("SystemServices");
	if (!strcmp(key, "Status"))
		return g_variant_new_string("Active");
	if (!strcmp(key, "IconName"))
		return g_variant_new_string("wivrn-pro-menu");
	if (!strcmp(key, "IconThemePath"))
		return g_variant_new_string("");
	if (!strcmp(key, "WindowId"))
		return g_variant_new_uint32(0);
	if (!strcmp(key, "ItemIsMenu"))
		return g_variant_new_boolean(true);
	if (!strcmp(key, "Menu"))
		return g_variant_new_object_path("/StatusNotifierItem/Menu");
	if (!strcmp(key, "IconPixmap"))
		return g_variant_new_array(G_VARIANT_TYPE("(iiay)"), nullptr, 0);
	return nullptr;
}
void Tray::method(GDBusConnection *, const gchar *, const gchar *, const gchar * iface, const gchar * method, GVariant * args, GDBusMethodInvocation * reply, gpointer data)
{
	auto self = static_cast<Tray *>(data);
	if (!strcmp(iface, instance))
	{
		if (!strcmp(method, "Surface"))
		{
			g_dbus_method_invocation_return_value(reply, g_variant_new("(s)", self->surface.c_str()));
			return;
		}
		const char * requested;
		g_variant_get(args, "(&s)", &requested);
		if (self->surface == requested)
			self->action(1);
	}
	else if (!strcmp(iface, "com.canonical.dbusmenu"))
	{
		if (!strcmp(method, "AboutToShow"))
		{
			g_dbus_method_invocation_return_value(reply, g_variant_new("(b)", false));
			return;
		}
		if (!strcmp(method, "GetLayout"))
		{
			gint id, depth;
			GVariant * props;
			g_variant_get(args, "(ii@as)", &id, &depth, &props);
			g_variant_unref(props);
			g_dbus_method_invocation_return_value(reply, g_variant_new("(u@(ia{sv}av))", 1u, node(id, depth != 0)));
			return;
		}
		if (!strcmp(method, "Event"))
		{
			gint id;
			const char * event;
			GVariant * v;
			guint32 t;
			g_variant_get(args, "(i&svu)", &id, &event, &v, &t);
			g_variant_unref(v);
			if (!strcmp(event, "clicked") && id >= 1 && id <= 5)
				self->action(id);
		}
	}
	else if (strcmp(method, "Scroll"))
		self->action(1);
	g_dbus_method_invocation_return_value(reply, nullptr);
}
void Tray::add(const char * path, const char * xml)
{
	auto info = g_dbus_node_info_new_for_xml(xml, nullptr);
	if (!info)
		throw std::runtime_error("Invalid dashboard tray interface");
	infos.push_back(info);
	static const GDBusInterfaceVTable table = {method, property, nullptr, {nullptr}};
	GError * error = nullptr;
	auto id = g_dbus_connection_register_object(bus, path, info->interfaces[0], &table, this, nullptr, &error);
	if (!id)
	{
		std::string text = error->message;
		g_error_free(error);
		throw std::runtime_error(text);
	}
	registrations.push_back(id);
}
Tray::Tray(GDBusConnection * b, std::string s, std::function<void(int)> callback) : bus(G_DBUS_CONNECTION(g_object_ref(b))), action(std::move(callback)), surface(std::move(s))
{
	GError * error = nullptr;
	auto result = g_dbus_connection_call_sync(bus, "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "RequestName", g_variant_new("(su)", instance, 4u), G_VARIANT_TYPE("(u)"), G_DBUS_CALL_FLAGS_NONE, 3000, nullptr, &error);
	if (!result)
	{
		std::string text = error->message;
		g_error_free(error);
		throw std::runtime_error(text);
	}
	guint32 code;
	g_variant_get(result, "(u)", &code);
	g_variant_unref(result);
	owner = code == 1;
	if (!owner)
	{
		// Queued delivery lets the incumbent finish registering after RequestName.
		auto msg = g_dbus_message_new_method_call(instance, instancePath, instance, "Activate");
		g_dbus_message_set_body(msg, g_variant_new("(s)", surface.c_str()));
		g_dbus_connection_send_message(bus, msg, G_DBUS_SEND_MESSAGE_FLAGS_NONE, nullptr, nullptr);
		g_object_unref(msg);
		g_dbus_connection_flush_sync(bus, nullptr, nullptr);
		return;
	}
	add(instancePath, control);
	if (surface == "vr")
		return;
	add("/StatusNotifierItem", item);
	add("/StatusNotifierItem/Menu", menu);
	watcher = g_bus_watch_name_on_connection(bus, "org.kde.StatusNotifierWatcher", G_BUS_NAME_WATCHER_FLAGS_NONE, [](GDBusConnection * bus, const gchar *, const gchar *, gpointer data) {
      auto self=static_cast<Tray *>(data);self->available=true;
      g_dbus_connection_call(bus,"org.kde.StatusNotifierWatcher","/StatusNotifierWatcher","org.kde.StatusNotifierWatcher","RegisterStatusNotifierItem",g_variant_new("(s)","/StatusNotifierItem"),nullptr,G_DBUS_CALL_FLAGS_NONE,3000,nullptr,nullptr,nullptr); }, [](GDBusConnection *, const gchar *, gpointer data) { static_cast<Tray *>(data)->available = false; }, this, nullptr);
}
Tray::~Tray()
{
	if (watcher)
		g_bus_unwatch_name(watcher);
	for (auto id: registrations)
		g_dbus_connection_unregister_object(bus, id);
	for (auto info: infos)
		g_dbus_node_info_unref(info);
	if (owner)
		g_dbus_connection_call(bus, "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "ReleaseName", g_variant_new("(s)", instance), nullptr, G_DBUS_CALL_FLAGS_NONE, 1000, nullptr, nullptr, nullptr);
	g_object_unref(bus);
}
} // namespace wivrn::dashboard
