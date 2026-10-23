// SPDX-License-Identifier: GPL-3.0-or-later
#include "gpu-window.h"
#include "skeleton.h"
#include "tray.h"
#include <FL/Fl_Box.H>
#include <FL/Fl_Button.H>
#include <FL/Fl_Check_Button.H>
#include <FL/Fl_Choice.H>
#include <FL/Fl_Hold_Browser.H>
#include <FL/Fl_Input.H>
#include <FL/Fl_Native_File_Chooser.H>
#include <FL/Fl_Scroll.H>
#include <FL/Fl_Tabs.H>
#include <FL/Fl_Text_Editor.H>
#include <FL/Fl_Value_Input.H>
#include <csignal>
#include <filesystem>
#include <fstream>
#include <sstream>
#include <utility>
using namespace wivrn::dashboard;
namespace
{
volatile sig_atomic_t interrupted = 0;
void enabled(Fl_Widget * p, bool value)
{
	if (value)
		p->activate();
	else
		p->deactivate();
}
struct Editor : Fl_Text_Editor
{
	Fl_Text_Buffer contents;
	std::string baseline;
	Json expected = Json::object();
	Editor(int x, int y, int w, int h) : Fl_Text_Editor(x, y, w, h)
	{
		buffer(&contents);
		wrap_mode(WRAP_AT_BOUNDS, 0);
	}
	~Editor() override
	{
		buffer(nullptr);
	}
	std::string text() const
	{
		auto p = contents.text();
		std::string s = p;
		free(p);
		return s;
	}
	bool clean() const
	{
		return text() == baseline;
	}
	void load(const Json & j)
	{
		expected = j;
		baseline = j.dump(2);
		contents.text(baseline.c_str());
	}
	Json json() const
	{
		auto j = Json::parse(text());
		if (!j.is_object())
			throw std::runtime_error("Editor must contain a JSON object");
		return j;
	}
};
struct Form
{
	struct Field
	{
		std::string path;
		Fl_Widget * widget;
		std::vector<Json> choices;
		Json initial;
		Json fallback;
	};
	std::deque<Field> fields;
	int x, y, w;
	explicit Form(int x, int y, int w) : x(x), y(y), w(w) {}
	template <class T>
	T * place(const char * label)
	{
		auto widget = new T(x, y + 22, w, 34);
		widget->copy_label(label);
		widget->align(FL_ALIGN_TOP_LEFT);
		y += 68;
		return widget;
	}
	void number(const char * label, const char * path, double lo, double hi, double step = 0.01)
	{
		auto p = place<Fl_Value_Input>(label);
		p->range(lo, hi);
		p->step(step);
		fields.push_back({path, p, {}, 0, 0});
	}
	void boolean(const char * label, const char * path)
	{
		auto p = new Fl_Check_Button(x, y, w, 36, label);
		y += 44;
		fields.push_back({path, p, {}, false, false});
	}
	void string(const char * label, const char * path)
	{
		fields.push_back({path, place<Fl_Input>(label), {}, "", ""});
	}
	void choice(const char * label, const char * path, std::initializer_list<std::pair<const char *, Json>> entries)
	{
		auto p = place<Fl_Choice>(label);
		std::vector<Json> values;
		for (auto & v: entries)
		{
			p->add(v.first);
			values.push_back(v.second);
		}
		fields.push_back({path, p, values, values.front(), values.front()});
	}
	Json get(const Field & f) const
	{
		if (auto p = dynamic_cast<Fl_Value_Input *>(f.widget))
			return p->value();
		if (auto p = dynamic_cast<Fl_Check_Button *>(f.widget))
			return bool(p->value());
		if (auto p = dynamic_cast<Fl_Input *>(f.widget))
			return p->value();
		auto index = static_cast<Fl_Choice *>(f.widget)->value();
		return index >= 0 && size_t(index) < f.choices.size() ? f.choices[index] : f.initial;
	}
	void defaults(const Json & j)
	{
		for (auto & f: fields)
		{
			Json::json_pointer p(f.path);
			if (j.contains(p))
				f.fallback = j.at(p);
		}
	}
	void load(const Json & j)
	{
		for (auto & f: fields)
		{
			Json::json_pointer path(f.path);
			auto v = j.contains(path) ? j.at(path) : f.fallback;
			if (auto p = dynamic_cast<Fl_Value_Input *>(f.widget))
			{
				if (v.is_number())
					p->value(v.get<double>());
			}
			else if (auto p = dynamic_cast<Fl_Check_Button *>(f.widget))
			{
				if (v.is_boolean())
					p->value(v.get<bool>());
			}
			else if (auto p = dynamic_cast<Fl_Input *>(f.widget))
			{
				if (v.is_string())
					p->value(v.get<std::string>().c_str());
			}
			else
			{
				auto choice = static_cast<Fl_Choice *>(f.widget);
				auto i = std::find(f.choices.begin(), f.choices.end(), v);
				choice->value(i == f.choices.end() ? -1 : int(i - f.choices.begin()));
			}
			f.initial = get(f);
		}
	}
	bool clean() const
	{
		return std::all_of(fields.begin(), fields.end(), [&](const Field & f) { return get(f) == f.initial; });
	}
	Json apply(Json j) const
	{
		for (auto & f: fields)
		{
			auto v = get(f);
			if (v != f.initial)
			{
				if (v.is_null() || (f.path == "/pyrowave-bitrate" && v == 0) || (f.path == "/pyrowave-foveation" && v == -1))
					j.erase(f.path.substr(1));
				else
					j[Json::json_pointer(f.path)] = v;
			}
		}
		return j;
	}
};
struct Dashboard : dx::GpuWindow
{
	std::shared_ptr<Backend> backend;
	Tray * tray = nullptr;
	bool quit = false, streamSaving = false, settingsSaving = false, profileBusy = false, motocSaving = false;
	bool trackingAvailable = false, calibrationReady = false;
	std::string profilePath;
	Json profileText = nullptr, latest = Json::object(), tracking = Json::object(), serviceState = Json::object();
	Fl_Box *status, *cards, *message, *calibrationStatus;
	Fl_Tabs * tabs;
	Fl_Group *streamPage, *trackingPage, *devicesPage, *logPage, *motocPage;
	Editor *stream, *settings, *devices, *motoc;
	Fl_Text_Display * logs;
	Fl_Hold_Browser * trackers;
	bool journalBusy = false;
	Fl_Text_Buffer logBuffer;
	Skeleton * skeleton;
	Fl_Choice *calibration, *preset, *presetBackend, *keys;
	Fl_Value_Input * delay;
	Fl_Button *calibrate, *cancelCalibration, *applySettings, *saveStream, *saveDevices, *saveMotoc;
	Form *streamForm, *trackingForm, *deviceForm, *motocForm;
	Json keyList = Json::array();
	std::vector<Json> presets;
	std::deque<std::function<void()>> actions;
	gint64 calibrationDeadline = 0;
	Json calibrationCommand;
	std::vector<Fl_Button *> serviceButtons;
	static constexpr const char * calibrationNames[] = {"Full standing reset", "Yaw alignment", "Mounting reset", "Save standing pose", "Save seated pose", "Save floor / lying pose", "Save high kneeling pose", "Save sitting-on-heels pose", "Save belly-down pose", "Save bent-knees belly pose", "Walking in place setup"};
	explicit Dashboard(std::shared_ptr<Backend> b, std::string path) : GpuWindow(1200, 900, "WiVRn Pro"), backend(std::move(b)), profilePath(std::move(path))
	{
		xclass("wivrn-pro");
		size_range(1100, 800);
		begin();
		status = new Fl_Box(24, 12, 1152, 36, "WiVRn Pro - Streaming, Shora & WayVR");
		status->align(FL_ALIGN_LEFT | FL_ALIGN_INSIDE);
		for (int i = 0; i < 4; ++i)
		{
			const char * labels[] = {"Start WiVRn", "Stop WiVRn", "Restart WiVRn", "Reconnect tracking"};
			auto p = button(24 + i * 288, 55, 278, 36, labels[i], [this, i] {if(i==3)backend->connect();else backend->serviceAction(i==0?"start":i==1?"stop":"restart"); });
			if (i < 3)
				serviceButtons.push_back(p);
		}
		cards = new Fl_Box(24, 100, 1152, 50, "Waiting for service and tracking worker");
		cards->box(FL_BORDER_BOX);
		cards->align(FL_ALIGN_LEFT | FL_ALIGN_INSIDE | FL_ALIGN_WRAP);
		const char * cmds[] = {"yaw_reset", "full_reset", "mounting_reset", "pause_tracking", "restart", "shutdown_trackers"};
		const char * labels[] = {"Yaw reset", "Full reset", "Mounting reset", "Pause tracking", "Reconnect serial", "Shut down trackers"};
		for (int i = 0; i < 6; ++i)
			button(24 + i * 192, 162, 182, 36, labels[i], [this, cmd = std::string(cmds[i])] { send({{"command", cmd}}); });
		tabs = new Fl_Tabs(16, 210, 1168, 628);
		tabs->begin();
		streamPage = new Fl_Group(16, 244, 1168, 594, "WiVRn streaming");
		streamPage->begin();
		auto scroll = new Fl_Scroll(24, 254, 492, 574);
		scroll->type(Fl_Scroll::VERTICAL);
		scroll->begin();
		streamForm = new Form(32, 260, 450);
		streamForm->choice("Headset profile", "/headset-profile", {{"Auto", "auto"}, {"Quest Pro", "quest-pro"}, {"Steam Frame", "steam-frame"}});
		streamForm->choice("Tracking bridge (restart)", "/tracking-mode", {{"SlimeVR", "slimevr"}, {"Motoc / Lighthouse", "motoc"}});
		streamForm->boolean("Use Lighthouse controllers", "/motoc-lighthouse-controllers");
		streamForm->boolean("Use TCP for streaming", "/tcp-only");
		streamForm->boolean("Forward input devices", "/hid-forwarding");
		streamForm->boolean("Runtime debug UI", "/debug-gui");
		streamForm->boolean("Run bundled WayVR", "/wayvr/enabled");
		streamForm->boolean("Open dashboard in WayVR", "/wayvr/dashboard");
		streamForm->string("Hostname", "/hostname");
		streamForm->number("Port", "/port", 1, 65535, 1);
		streamForm->number("PyroWave bitrate (bits/s; 0 = headset)", "/pyrowave-bitrate", 0, 1000000000, 1000000);
		streamForm->number("PyroWave foveation (-1 = headset)", "/pyrowave-foveation", -1, 80, 1);
		streamForm->choice("Bit depth", "/bit-depth", {{"Default", nullptr}, {"8", 8}, {"10", 10}});
		scroll->end();
		streamPage->begin();
		preset = new Fl_Choice(540, 272, 330, 36, "Encoder preset");
		preset->align(FL_ALIGN_TOP_LEFT);
		preset->add("Choose preset");
		presetBackend = new Fl_Choice(878, 272, 110, 36);
		presetBackend->add("qsv|nvenc|vaapi");
		presetBackend->value(0);
		button(996, 272, 170, 36, "Load preset", [this] { loadPreset(); });
		stream = new Editor(540, 320, 628, 228);
		stream->tooltip("Full configuration JSON. Unknown settings and custom encoder layouts are retained.");
		saveStream = button(540, 556, 310, 36, "Save WiVRn settings", [this] { saveStreaming(); });
		button(860, 556, 308, 36, "Reload settings", [this] { loadStreaming(latest); });
		keys = new Fl_Choice(540, 610, 628, 36, "Paired headsets");
		keys->align(FL_ALIGN_TOP_LEFT);
		button(540, 656, 308, 36, "Pair headset (2 min)", [this] { backend->method("EnablePairing", g_variant_new("(i)", 120)); });
		button(860, 656, 308, 36, "Disable pairing", [this] { backend->method("DisablePairing"); });
		button(540, 702, 308, 36, "Rename headset", [this] { renameKey(); });
		button(860, 702, 308, 36, "Forget headset", [this] { forgetKey(); });
		button(540, 748, 308, 36, "Disconnect headset", [this] { backend->method("Disconnect"); });
		button(860, 748, 308, 36, "Restart WayVR", [this] { backend->method("RestartWayVR"); });
		streamPage->resizable(stream);
		streamPage->end();
		trackingPage = new Fl_Group(16, 244, 1168, 594, "Skeleton & calibration");
		trackingPage->begin();
		for (int i = 0; i < 3; ++i)
			button(24 + i * 132, 254, 122, 34, i == 0 ? "Front" : i == 1 ? "Side"
			                                                             : "Orbit",
			       [this, i] { skeleton->view(i); });
		auto grid = new Fl_Check_Button(428, 254, 180, 34, "Floor grid");
		grid->value(1);
		bind(grid, [this, grid] {skeleton->grid=grid->value();skeleton->redraw(); });
		skeleton = new Skeleton(24, 298, 600, 528);
		scroll = new Fl_Scroll(636, 254, 538, 574);
		scroll->type(Fl_Scroll::VERTICAL);
		scroll->begin();
		calibration = new Fl_Choice(650, 278, 486, 36, "Guided calibration");
		calibration->align(FL_ALIGN_TOP_LEFT);
		for (auto s: calibrationNames)
			calibration->add(s);
		calibration->value(0);
		auto instructions = new Fl_Box(650, 322, 486, 70, "Stand still for standing / yaw reset. For mounting reset bend knees and lean forward in a ski pose. Save relaxed poses after standing and mounting calibration.");
		instructions->align(FL_ALIGN_LEFT | FL_ALIGN_INSIDE | FL_ALIGN_WRAP);
		delay = new Fl_Value_Input(780, 400, 100, 34, "Countdown (s)");
		delay->range(0, 10);
		delay->step(1);
		delay->value(3);
		calibrate = button(650, 446, 238, 36, "Start calibration", [this] { startCalibration(); });
		cancelCalibration = button(898, 446, 238, 36, "Cancel", [this] { cancelCountdown(); });
		cancelCalibration->deactivate();
		calibrationStatus = new Fl_Box(650, 492, 486, 80, "Waiting for tracker samples");
		calibrationStatus->align(FL_ALIGN_LEFT | FL_ALIGN_INSIDE | FL_ALIGN_WRAP);
		trackingForm = new Form(650, 582, 486);
		trackingForm->number("Body height (m)", "/height_m", .5, 3);
		trackingForm->number("Smoothing blend (0 = off)", "/smoothing", 0, 1, .05);
		trackingForm->number("Prediction (seconds)", "/prediction", 0, .2, .005);
		trackingForm->boolean("Enable drift correction", "/drift_correction");
		trackingForm->number("Drift amount", "/drift_amount", 0, 1, .05);
		trackingForm->boolean("Enable Stay Aligned", "/stay_aligned/enabled");
		trackingForm->boolean("Validate rest locks with anchors", "/stay_aligned/anchor_rest_lock");
		trackingForm->number("Alignment speed (degrees/s)", "/stay_aligned/rate_deg_per_sec", .01, 1);
		trackingForm->number("Walking step lift (degrees)", "/walking/lift_deg", 6, 35);
		trackingForm->number("Walking sensitivity", "/walking/sensitivity", .2, 3);
		trackingForm->number("Maximum forward stick", "/walking/max_stick", .1, 1);
		trackingForm->number("Stop response (ms)", "/walking/stop_ms", 150, 600, 1);
		trackingForm->number("Body turn deadzone (degrees)", "/walking/turn_deadzone_deg", 3, 25);
		trackingForm->number("Full body turn (degrees)", "/walking/turn_range_deg", 15, 90);
		trackingForm->number("Maximum turning stick", "/walking/max_turn", .1, 1);
		int ty = trackingForm->y;
		settings = new Editor(650, ty + 24, 486, 260);
		settings->tooltip("Full live tracking settings JSON; advanced settings remain editable.");
		applySettings = button(650, ty + 298, 238, 36, "Apply tracking settings", [this] { saveTracking(); });
		button(898, ty + 298, 238, 36, "Reload tracking settings", [this] { loadTracking(tracking.value("settings", Json::object())); });
		button(650, ty + 342, 238, 36, "Clear learned drift", [this] { send({{"command", "clear_drift"}}); });
		button(898, ty + 342, 238, 36, "Clear Stay Aligned", [this] { send({{"command", "clear_stay_aligned"}}); });
		button(650, ty + 386, 486, 36, "Export active tuning TOML", [this] { exportTuning(); });
		scroll->end();
		trackingPage->resizable(skeleton);
		trackingPage->end();
		devicesPage = new Fl_Group(16, 244, 1168, 594, "Devices & OSC");
		devicesPage->begin();
		scroll = new Fl_Scroll(24, 254, 492, 574);
		scroll->type(Fl_Scroll::VERTICAL);
		scroll->begin();
		deviceForm = new Form(32, 260, 450);
		deviceForm->boolean("Enable HaritoraX", "/haritorax/enabled");
		deviceForm->choice("HaritoraX model", "/haritorax/model", {{"HaritoraX 2", "x2"}, {"HaritoraX Wireless", "wireless"}});
		deviceForm->boolean("Enable face / eye tracking", "/face/enabled");
		deviceForm->choice("Face source", "/face/source", {{"Babble / EyeTrackVR", "babble"}, {"OpenXR", "openxr"}});
		deviceForm->choice("Face output", "/face/output", {{"Auto / OSCQuery", "auto"}, {"UniFT", "unift"}, {"VRChat JSON", "json"}, {"Both", "both"}});
		deviceForm->string("Avatar OSC translation sheet", "/face/translation_sheet");
		auto hint = new Fl_Box(32, deviceForm->y + 12, 450, 100, "Serial ports, advanced device settings and all profile options are editable in the JSON startup profile. Save, then restart WiVRn to apply.");
		hint->align(FL_ALIGN_LEFT | FL_ALIGN_INSIDE | FL_ALIGN_WRAP);
		scroll->end();
		devicesPage->begin();
		devices = new Editor(540, 264, 628, 300);
		trackers = new Fl_Hold_Browser(540, 576, 628, 116);
		trackers->tooltip("Tracker / body ID / active / battery / source");
		saveDevices = button(540, 708, 308, 36, "Save startup profile", [this] { saveProfile(); });
		button(860, 708, 308, 36, "Reload startup profile", [this] { profileRequest({{"action", "load"}}, true); });
		button(540, 754, 628, 36, "HaritoraX readback / hardware settings", [this] { hardwareDialog(); });
		devicesPage->resizable(devices);
		devicesPage->end();
		logPage = new Fl_Group(16, 244, 1168, 594, "Server log");
		logPage->begin();
		button(24, 254, 1144, 36, "Refresh WiVRn service log", [this] { readJournal(); });
		logs = new Fl_Text_Display(24, 300, 1144, 528);
		logs->buffer(&logBuffer);
		logPage->resizable(logs);
		logPage->end();
		motocPage = new Fl_Group(16, 244, 1168, 594, "Motoc / Lighthouse");
		motocPage->begin();
		motocForm = new Form(32, 260, 450);
		motocForm->boolean("Enable Motoc calibration", "/enabled");
		motocForm->boolean("Keep calibration alive", "/keep_alive");
		motocForm->string("Headset device serial", "/source");
		motocForm->string("Lighthouse tracker serial", "/target");
		motocForm->choice("Calibration mode", "/mode", {{"One-time", "once"}, {"Rigid headset tracker", "rigid"}, {"Room anchors (experimental)", "anchors"}});
		auto hint2 = new Fl_Box(32, motocForm->y + 12, 450, 100, "Select Motoc bridge mode in Streaming and restart WiVRn. Hold the target tracker firmly against the headset during calibration. Anchor mode requires three stable saved room markers.");
		hint2->align(FL_ALIGN_LEFT | FL_ALIGN_INSIDE | FL_ALIGN_WRAP);
		motoc = new Editor(540, 264, 628, 340);
		saveMotoc = button(540, 620, 628, 36, "Save Motoc options", [this] { saveMotocSettings(); });
		button(540, 670, 198, 36, "Calibrate", [this] { motocCommand("motoc_calibrate"); });
		button(748, 670, 198, 36, "Resume saved", [this] { motocCommand("motoc_continue"); });
		button(956, 670, 212, 36, "Stop calibration", [this] { send({{"command", "motoc_stop"}}); });
		motocPage->resizable(motoc);
		motocPage->end();
		tabs->end();
		begin();
		message = new Fl_Box(24, 848, 1152, 44, "Waiting for service");
		message->align(FL_ALIGN_LEFT | FL_ALIGN_INSIDE | FL_ALIGN_WRAP);
		resizable(tabs);
		end();
		callback([](Fl_Widget *, void * data) {auto self=static_cast<Dashboard *>(data);if(self->tray&&self->tray->available)self->hide();else self->requestQuit(); }, this);
		backend->notice = [this](const std::string & s) { notice(s); };
		backend->state = [this](const Json & s) { updateState(s); };
		backend->snapshot = [this](const Json & s) { updateSnapshot(s); };
		backend->finished = [this](const std::string & cmd, bool ok, const std::string & error) {
			if (cmd == "set_settings")
			{
				settingsSaving = false;
				if (ok)
					loadTracking(tracking.value("settings", Json::object()));
			}
			if (cmd == "set_motoc_settings")
			{
				motocSaving = false;
				if (ok)
				{
					auto j = tracking.value("motoc", Json::object()).value("settings", Json::object());
					motoc->load(j);
					motocForm->load(j);
				}
			}
			if (cmd != "status")
				notice(ok ? cmd + " completed." : error);
		};
		stream->load(Json::object());
		settings->load(Json::object());
		devices->load(Json::object());
		motoc->load(Json::object());
		streamForm->defaults({{"port", 9757}, {"motoc-lighthouse-controllers", true}, {"wayvr", {{"enabled", true}, {"dashboard", true}}}, {"pyrowave-foveation", -1}});
		streamForm->load(Json::object());
		trackingForm->defaults({{"height_m", 1.8}, {"smoothing", .3}, {"drift_amount", .5}, {"stay_aligned", {{"rate_deg_per_sec", .2}}}});
		trackingForm->load(Json::object());
		deviceForm->load(Json::object());
		motocForm->defaults({{"keep_alive", true}, {"source", "WiVRn HMD"}});
		motocForm->load(Json::object());
		loadPresets();
		profileRequest({{"action", "load"}}, true);
		updateSnapshot(Json::object());
		dx::style(this);
	}
	void draw() override
	{
		// The shared GPU window clears its whole framebuffer. Redraw all children
		// so a live skeleton or status update cannot erase undamaged controls.
		damage(FL_DAMAGE_ALL);
		dx::GpuWindow::draw();
	}
	~Dashboard() override
	{
		for (auto task: tasks)
		{
			task->ui = nullptr;
			g_subprocess_force_exit(task->process);
		}
		delete streamForm;
		delete trackingForm;
		delete deviceForm;
		delete motocForm;
		logs->buffer(nullptr);
		backend->shutdown();
	}
	void bind(Fl_Widget * p, std::function<void()> action)
	{
		actions.push_back([this, action = std::move(action)] {try{action();}catch(const std::exception &e){notice(e.what());} });
		p->callback([](Fl_Widget *, void * p) { (*static_cast<std::function<void()> *>(p))(); }, &actions.back());
	}
	Fl_Button * button(int x, int y, int w, int h, const char * label, std::function<void()> action)
	{
		auto p = new Fl_Button(x, y, w, h, label);
		bind(p, std::move(action));
		return p;
	}
	void notice(const std::string & s)
	{
		message->copy_label(s.c_str());
		logBuffer.append((s + "\n").c_str());
		if (logBuffer.length() > 128 * 1024)
			logBuffer.remove(0, logBuffer.length() - 96 * 1024);
		logs->insert_position(logBuffer.length());
		logs->show_insert_position();
	}
	bool unsaved() const
	{
		return !stream->clean() || !streamForm->clean() || !settings->clean() || !trackingForm->clean() || !devices->clean() || !deviceForm->clean() || !motoc->clean() || !motocForm->clean();
	}
	void requestQuit()
	{
		if (!unsaved() || fl_choice("Discard unsaved dashboard edits?", "Keep editing", "Discard and quit", nullptr) == 1)
		{
			quit = true;
			hide();
		}
	}
	void present()
	{
		show();
		Fl::first_window(this);
	}
	void send(Json j)
	{
		if (!backend->send(std::move(j)))
			notice("Tracking worker unavailable or command queue is full.");
	}
	void loadStreaming(const Json & j)
	{
		stream->load(j);
		streamForm->load(j);
	}
	void loadTracking(const Json & j)
	{
		settings->load(j);
		trackingForm->load(j);
	}
	void updateState(const Json & s)
	{
		serviceState = s;
		bool running = s.contains("JsonConfiguration");
		status->copy_label((std::string("WiVRn Pro - ") + (running ? s.value("HeadsetConnected", false) ? "headset connected" : "service available" : "service unavailable") + (s.value("PairingEnabled", false) ? " / pairing PIN: " + s.value("Pin", std::string()) : "")).c_str());
		if (running)
			try
			{
				auto j = Json::parse(s.at("JsonConfiguration").get<std::string>());
				if (j.is_null())
					j = Json::object();
				if (!j.is_object())
					throw std::runtime_error("Service configuration must be an object");
				latest = j;
				if (stream->clean() && streamForm->clean() && !streamSaving && stream->expected != j)
					loadStreaming(j);
			}
			catch (const std::exception & e)
			{
				notice(e.what());
			}
		if (s.contains("KnownKeysJson"))
			try
			{
				auto j = Json::parse(s["KnownKeysJson"].get<std::string>());
				if (j != keyList)
				{
					keyList = j;
					keys->clear();
					for (auto & k: j)
						keys->add(k.at(0).get<std::string>().c_str());
					keys->value(0);
				}
			}
			catch (const std::exception &)
			{}
		enabled(saveStream, running && !streamSaving);
		updateCards();
	}
	void updateCards()
	{
		cards->copy_label(("Body: " + std::string(backend->connected() ? "connected" : "waiting") + " | Headset: " + serviceState.value("ClientModel", std::string("unavailable")) + " | WayVR: " + serviceState.value("WayVRStatus", std::string("unavailable"))).c_str());
	}
	void saveStreaming()
	{
		if (streamSaving)
			return;
		auto value = streamForm->apply(stream->json());
		auto text = stream->text();
		streamSaving = true;
		saveStream->deactivate();
		backend->save(value, stream->expected, [this, value, text](bool ok) {streamSaving=false;if(ok){stream->expected=value;if(stream->text()==text){stream->load(value);streamForm->load(value);}} });
	}
	void updateSnapshot(const Json & s)
	{
		tracking = s;
		updateCards();
		trackers->clear();
		for (const auto & t: s.value("trackers", Json::array()))
		{
			auto name = t.value("name", std::string("Tracker"));
			std::string row = name + " / " + t.value("body_part", Json()).dump() + " / " + (t.value("active", false) ? "active" : "inactive") + " / battery " + t.value("battery", Json()).dump() + " / " + t.value("source", Json()).dump();
			trackers->add(row.c_str());
		}
		trackingAvailable = backend->connected() && s.contains("settings");
		skeleton->update(s);
		bool active = false;
		for (auto & t: s.value("trackers", Json::array()))
			active |= t.value("active", false);
		auto health = s.value("spatial_reference", Json());
		calibrationReady = active && (health.is_null() || (health.value("ready", false) && s.value("hmd_pose_received", false)));
		if (!calibrationReady && calibrationDeadline)
			cancelCountdown();
		enabled(calibrate, calibrationReady && !calibrationDeadline);
		enabled(applySettings, trackingAvailable && !settingsSaving);
		enabled(saveMotoc, trackingAvailable && !motocSaving);
		if (!trackingAvailable)
		{
			settingsSaving = false;
			motocSaving = false;
		}
		if (trackingAvailable && settings->clean() && trackingForm->clean() && !settingsSaving && settings->expected != s["settings"])
			loadTracking(s["settings"]);
		if (s.contains("motoc"))
		{
			auto m = s["motoc"].value("settings", Json::object());
			if (motoc->clean() && motocForm->clean() && !motocSaving && motoc->expected != m)
			{
				motoc->load(m);
				motocForm->load(m);
			}
		}
		if (!calibrationDeadline)
			calibrationStatus->copy_label((s.value("walking_status", std::string()) + "\n" + (calibrationReady ? "Ready for calibration" : "Waiting for trackers / stable HMD reference")).c_str());
	}
	void saveTracking()
	{
		if (!trackingAvailable || settingsSaving)
			return;
		auto j = trackingForm->apply(settings->json());
		if (j.value("drift_correction", false) && j.value("stay_aligned", Json::object()).value("enabled", false))
			throw std::runtime_error("Choose either drift correction or Stay Aligned.");
		settingsSaving = backend->send({{"command", "set_settings"}, {"settings", j}});
		if (settingsSaving)
		{
			notice("Applying tracking settings…");
		}
		else
			notice("Tracking worker unavailable.");
	}
	void saveMotocSettings()
	{
		auto j = motocForm->apply(motoc->json());
		if (latest.value("headset-profile", std::string()) == "steam-frame" && j.value("mode", std::string()) == "anchors")
			throw std::runtime_error("Room anchor mode is unavailable on Steam Frame.");
		motocSaving = backend->send({{"command", "set_motoc_settings"}, {"motoc", j}});
		if (motocSaving)
		{
			notice("Applying Motoc settings…");
		}
		else
			notice("Tracking worker unavailable.");
	}
	void motocCommand(const char * cmd)
	{
		auto m = tracking.value("motoc", Json::object()).value("settings", Json::object());
		if (tracking.value("tracking_mode", std::string()) != "motoc" || !m.value("enabled", false))
			throw std::runtime_error("Enable Motoc bridge and calibration settings first.");
		send({{"command", cmd}});
	}
	void startCalibration()
	{
		if (!calibrationReady)
			return;
		int i = calibration->value();
		if (i <= 2)
		{
			const char * commands[] = {"full_reset", "yaw_reset", "mounting_reset"};
			calibrationCommand = {{"command", commands[i]}};
		}
		else if (i == 10)
			calibrationCommand = {{"command", "calibrate_walking"}};
		else
		{
			const char * poses[] = {"standing", "sitting", "flat", "kneeling", "heels", "prone", "prone_bent"};
			calibrationCommand = {{"command", "save_relaxed_pose"}, {"pose", poses[i - 3]}};
		}
		calibrationDeadline = g_get_monotonic_time() + gint64(delay->value() * 1000000);
		calibrate->deactivate();
		calibration->deactivate();
		delay->deactivate();
		cancelCalibration->activate();
	}
	void cancelCountdown()
	{
		calibrationDeadline = 0;
		cancelCalibration->deactivate();
		calibration->activate();
		delay->activate();
		calibrationStatus->copy_label("Calibration countdown cancelled");
	}
	void tick()
	{
		backend->tick();
		enabled(settings, trackingAvailable && !settingsSaving);
		for (auto & f: trackingForm->fields)
			enabled(f.widget, trackingAvailable && !settingsSaving);
		enabled(motoc, trackingAvailable && !motocSaving);
		for (auto & f: motocForm->fields)
			enabled(f.widget, trackingAvailable && !motocSaving);
		enabled(stream, !streamSaving);
		for (auto & f: streamForm->fields)
			enabled(f.widget, !streamSaving);
		enabled(devices, !profileBusy);
		for (auto & f: deviceForm->fields)
			enabled(f.widget, !profileBusy);
		for (auto p: serviceButtons)
			enabled(p, !backend->serviceBusy());
		if (calibrationDeadline)
		{
			auto left = calibrationDeadline - g_get_monotonic_time();
			if (left <= 0)
			{
				auto j = calibrationCommand;
				cancelCountdown();
				send(j);
			}
			else
				calibrationStatus->copy_label(("Hold your pose - calibrating in " + std::to_string(int((left + 999999) / 1000000)) + " seconds").c_str());
		}
	}
	void readJournal()
	{
		if (journalBusy)
			return;
		journalBusy = true;
		process({"journalctl", "--user-unit=wivrn-pro.service", "--no-pager", "-n", "200"}, "", [this](const std::string & out) {journalBusy=false;logBuffer.text(out.c_str()); });
	}
	void loadPresets()
	{
		for (auto directory: {WIVRN_DASHBOARD_PRESETS, WIVRN_DASHBOARD_SOURCE_PRESETS})
		{
			if (!std::filesystem::is_directory(directory))
				continue;
			std::vector<std::filesystem::path> paths;
			for (auto & p: std::filesystem::directory_iterator(directory))
				if (p.path().extension() == ".json")
					paths.push_back(p.path());
			std::sort(paths.begin(), paths.end());
			for (auto & p: paths)
				try
				{
					std::ifstream file(p);
					Json j;
					file >> j;
					presets.push_back(j);
					preset->add(j.at("title").get<std::string>().c_str());
				}
				catch (const std::exception & e)
				{
					notice(e.what());
				}
			break;
		}
		preset->value(0);
	}
	void loadPreset()
	{
		int i = preset->value() - 1;
		if (i < 0 || size_t(i) >= presets.size())
			return;
		auto j = streamForm->apply(stream->json());
		auto values = presets[i].at("host_config");
		auto & encoder = values["encoder"];
		if (encoder.value("codec", std::string()) != "pyrowave")
		{
			const char * names[] = {"qsv", "nvenc", "vaapi"};
			encoder["encoder"] = names[std::clamp(presetBackend->value(), 0, 2)];
			if (presetBackend->value() != 0 && encoder.contains("options"))
			{
				auto & opts = encoder["options"];
				for (auto it = opts.begin(); it != opts.end();)
					if (it.key() != "rate_control" && it.key() != "qp")
						it = opts.erase(it);
					else
						++it;
			}
		}
		for (auto & v: values.items())
			j[v.key()] = v.value();
		stream->contents.text(j.dump(2).c_str());
		streamForm->load(j);
		notice("Preset loaded into draft. Save settings for the next session.");
	}
	void renameKey()
	{
		int i = keys->value();
		if (i < 0 || size_t(i) >= keyList.size())
			return;
		auto name = fl_input("Headset name", keyList[i][0].get<std::string>().c_str());
		if (name && *name)
			backend->method("RenameKey", g_variant_new("(ss)", keyList[i][1].get<std::string>().c_str(), name));
	}
	void forgetKey()
	{
		int i = keys->value();
		if (i < 0 || size_t(i) >= keyList.size())
			return;
		if (fl_choice("Forget the selected headset?", "Cancel", "Forget", nullptr) == 1)
			backend->method("RevokeKey", g_variant_new("(s)", keyList[i][1].get<std::string>().c_str()));
	}
	struct Task
	{
		Dashboard * ui;
		std::function<void(const std::string &)> done;
		GSubprocess * process;
		guint timeout = 0;
	};
	std::vector<Task *> tasks;
	void process(const std::vector<std::string> & args, const std::string & input, std::function<void(const std::string &)> done)
	{
		std::vector<const char *> argv;
		for (auto & s: args)
			argv.push_back(s.c_str());
		argv.push_back(nullptr);
		GError * error = nullptr;
		auto p = g_subprocess_newv(argv.data(), GSubprocessFlags(G_SUBPROCESS_FLAGS_STDIN_PIPE | G_SUBPROCESS_FLAGS_STDOUT_PIPE | G_SUBPROCESS_FLAGS_STDERR_PIPE), &error);
		if (!p)
		{
			std::string message = error->message;
			g_error_free(error);
			throw std::runtime_error(message);
		}
		auto task = new Task{this, std::move(done), p};
		tasks.push_back(task);
		task->timeout = g_timeout_add(20000, [](gpointer data) -> gboolean {auto t=static_cast<Task *>(data);t->timeout=0;g_subprocess_force_exit(t->process);return G_SOURCE_REMOVE; }, task);
		g_subprocess_communicate_utf8_async(p, input.c_str(), nullptr, [](GObject * source, GAsyncResult * result, gpointer data) {auto task=static_cast<Task *>(data);char *out=nullptr,*err=nullptr;GError *error=nullptr;bool ok=g_subprocess_communicate_utf8_finish(G_SUBPROCESS(source),result,&out,&err,&error);
      if(task->timeout)g_source_remove(task->timeout);
      if(task->ui){auto &list=task->ui->tasks;list.erase(std::remove(list.begin(),list.end(),task),list.end());try{if(!ok||!g_subprocess_get_successful(G_SUBPROCESS(source)))throw std::runtime_error(error?error->message:err&&*err?err:"Helper failed");task->done(out?out:"");}catch(const std::exception &e){task->ui->profileBusy=false;task->ui->notice(e.what());}}
      g_free(out);g_free(err);g_clear_error(&error);g_object_unref(task->process);delete task; }, task);
	}
	void profileRequest(Json j, bool reload)
	{
		if (profileBusy)
			return;
		profileBusy = true;
		j["path"] = profilePath;
		auto helper = std::filesystem::exists(WIVRN_DASHBOARD_PROFILE_HELPER) ? WIVRN_DASHBOARD_PROFILE_HELPER : WIVRN_DASHBOARD_SOURCE_HELPER;
		process({WIVRN_DASHBOARD_PYTHON, helper}, j.dump(), [this, reload](const std::string & out) {profileBusy=false;auto result=Json::parse(out);if(!result.value("ok",false))throw std::runtime_error(result.value("error",std::string("Profile helper failed")));profileText=result["text"];if(reload){devices->load(result["profile"]);deviceForm->load(result["profile"]);}notice("Startup profile loaded. Saved changes apply after restarting WiVRn."); });
	}
	void saveProfile()
	{
		if (profileBusy)
			return;
		auto j = deviceForm->apply(devices->json());
		profileRequest({{"action", "save"}, {"expected", profileText}, {"profile", j}}, true);
	}
	void exportTuning()
	{
		if (!trackingAvailable)
			throw std::runtime_error("Connect tracking before exporting active settings");
		Fl_Native_File_Chooser chooser;
		chooser.title("Export active tuning TOML");
		chooser.type(Fl_Native_File_Chooser::BROWSE_SAVE_FILE);
		chooser.preset_file("shora-tracking.toml");
		chooser.options(Fl_Native_File_Chooser::SAVEAS_CONFIRM);
		if (chooser.show())
			return;
		std::string path = chooser.filename();
		auto helper = std::filesystem::exists(WIVRN_DASHBOARD_PROFILE_HELPER) ? WIVRN_DASHBOARD_PROFILE_HELPER : WIVRN_DASHBOARD_SOURCE_HELPER;
		Json old = nullptr;
		if (std::filesystem::exists(path))
		{
			std::ifstream in(path);
			std::ostringstream text;
			text << in.rdbuf();
			old = text.str();
		}
		process({WIVRN_DASHBOARD_PYTHON, helper}, Json({{"action", "save"}, {"path", path}, {"expected", old}, {"profile", tracking.at("settings")}}).dump(), [this](const std::string & out) {auto j=Json::parse(out);if(!j.value("ok",false))throw std::runtime_error(j.value("error",std::string("Export failed")));notice("Active tracking settings exported."); });
	}
	void hardwareDialog()
	{
		std::vector<Json> reports;
		for (auto & port: tracking.value("ports", Json::array()))
			if (port.value("connected", false))
				for (auto report: port.value("trackers", Json::array()))
					if (report.contains("settings") && !report["settings"].is_null() && report.contains("settings_age_ms") && report["settings_age_ms"].is_number() && report["settings_age_ms"].get<double>() < 5000)
						reports.push_back(report);
		if (reports.empty())
			throw std::runtime_error("No connected HaritoraX tracker with fresh settings readback.");
		std::ostringstream names;
		for (size_t i = 0; i < reports.size(); ++i)
			names << i + 1 << ": " << reports[i].value("name", std::string("Tracker")) << "\n";
		auto index = fl_input("%s", "1", ("Select tracker number:\n" + names.str()).c_str());
		if (!index)
			return;
		int n = std::stoi(index) - 1;
		if (n < 0 || size_t(n) >= reports.size())
			throw std::runtime_error("Invalid tracker selection");
		auto settings = fl_input("Edit sensor settings JSON (mode, FPS and correction flags). Apply affects the physical tracker:", reports[n]["settings"].dump().c_str());
		if (!settings)
			return;
		auto hw = Json::parse(settings);
		// Revalidate freshness after the user has spent time in the dialog.
		bool fresh = false;
		for (auto & port: tracking.value("ports", Json::array()))
			if (port.value("connected", false))
				for (auto & r: port.value("trackers", Json::array()))
					if (r.value("mac", Json()) == reports[n]["mac"] && r.contains("settings_age_ms") && r["settings_age_ms"].is_number() && r["settings_age_ms"].get<double>() < 5000)
						fresh = true;
		if (!fresh)
			throw std::runtime_error("Tracker readback is stale. Retry after reconnecting.");
		send({{"command", "set_haritora_settings"}, {"tracker_mac", reports[n]["mac"]}, {"hardware", hw}});
	}
};
void pump(void * data)
{
	auto ui = static_cast<Dashboard *>(data);
	for (int i = 0; i < 32 && g_main_context_pending(nullptr); ++i)
		g_main_context_iteration(nullptr, false);
	ui->tick();
	Fl::repeat_timeout(.01, pump, data);
}
} // namespace
int main(int argc, char ** argv)
try
{
	bool forceShow = false, forceTray = false;
	double exitAfter = 0;
	std::string snapshotFile, screenshotFile, page;
	std::string socketPath = std::string(g_get_user_runtime_dir()) + "/wivrn-pro/ShoraControl";
	std::string profilePath = std::string(g_get_user_config_dir()) + "/wivrn-pro/shora.toml";
	for (int i = 1; i < argc; ++i)
	{
		std::string a = argv[i];
		if (a == "--help")
		{
			puts("wivrn-pro-dashboard [--show|--tray] [--socket PATH] [--profile PATH]\nDomeD starts hidden in its tray manager. --show explicitly opens the dashboard.\nDiagnostics: --snapshot JSON --screenshot FILE.ppm --exit-after SECONDS");
			return 0;
		}
		else if (a == "--show")
			forceShow = true;
		else if (a == "--tray")
			forceTray = true;
		else if ((a == "--socket" || a == "--profile" || a == "--snapshot" || a == "--screenshot" || a == "--page" || a == "--exit-after") && i + 1 < argc)
		{
			std::string v = argv[++i];
			if (a == "--socket")
				socketPath = v;
			else if (a == "--profile")
				profilePath = v;
			else if (a == "--snapshot")
				snapshotFile = v;
			else if (a == "--screenshot")
				screenshotFile = v;
			else if (a == "--page")
				page = v;
			else
				exitAfter = std::stod(v);
		}
		else
			throw std::runtime_error("Unknown argument: " + a);
	}
	GError * error = nullptr;
	auto bus = g_bus_get_sync(G_BUS_TYPE_SESSION, nullptr, &error);
	if (!bus)
	{
		std::string text = error->message;
		g_error_free(error);
		throw std::runtime_error(text);
	}
	auto backend = std::make_shared<Backend>(bus, socketPath);
	// Reserve the shared Qt/FLTK instance name before creating a window or loading fonts.
	Dashboard * active = nullptr;
	bool earlyShow = false;
	auto surface = getenv("WAYVR_DISPLAY_AUTH") || (getenv("WIVRN_DASHBOARD_SURFACE") && std::string(getenv("WIVRN_DASHBOARD_SURFACE")) == "vr") ? "vr" : "desktop";
	Tray tray(bus, surface, [&](int id) {if(!active){earlyShow=true;return;}if(id==1)active->present();else if(id==5)active->requestQuit();else backend->serviceAction(id==2?"start":id==3?"stop":"restart"); });
	g_object_unref(bus);
	if (!tray.owner)
		return 0;
	dx::init();
	Dashboard window(backend, profilePath);
	active = &window;
	window.tray = &tray;
	if (page == "skeleton" || !screenshotFile.empty())
		window.tabs->value(window.trackingPage);
	bool hidden = (dx::dome() || forceTray) && !forceShow && screenshotFile.empty() && std::string(surface) != "vr";
	if (!hidden || earlyShow)
		window.present();
	Json fixture;
	if (!snapshotFile.empty())
	{
		std::ifstream in(snapshotFile);
		in >> fixture;
		window.skeleton->update(fixture);
	}
	signal(SIGINT, [](int) { interrupted = 1; });
	signal(SIGTERM, [](int) { interrupted = 1; });
	Fl::add_timeout(.01, pump, &window);
	auto start = g_get_monotonic_time();
	bool captured = false, fallbackShown = false;
	while (!window.quit && !interrupted)
	{
		Fl::wait(.02);
		auto elapsed = (g_get_monotonic_time() - start) / 1e6;
		// Retain access when no tray host is running or it disappears after startup.
		if (hidden && !tray.available && elapsed > 2 && !fallbackShown)
		{
			window.present();
			fallbackShown = true;
		}
		if (!snapshotFile.empty())
			window.skeleton->update(fixture);
		if (!screenshotFile.empty() && !captured && elapsed > .6)
		{
			if (!dx::screenshot(window, screenshotFile.c_str()))
				throw std::runtime_error("Screenshot failed");
			captured = true;
		}
		if (exitAfter > 0 && elapsed >= exitAfter)
			break;
	}
	Fl::remove_timeout(pump, &window);
	for (auto task: window.tasks)
	{
		task->ui = nullptr;
		g_subprocess_force_exit(task->process);
	}
	backend->shutdown();
	active = nullptr;
	return 0;
}
catch (const std::exception & e)
{
	fprintf(stderr, "WiVRn Pro dashboard: %s\n", e.what());
	return 1;
}
