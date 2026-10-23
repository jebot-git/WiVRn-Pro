// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include "backend.h"
#include <FL/Fl_Widget.H>
#include <FL/fl_draw.H>
#include <algorithm>
#include <array>
#include <cmath>
#include <vector>
namespace wivrn::dashboard
{
inline bool validPoint(const Json & p)
{
	if (!p.is_array() || p.size() != 3)
		return false;
	return std::all_of(p.begin(), p.end(), [](const Json & v) { return v.is_number() && std::isfinite(v.get<double>()) && std::abs(v.get<double>()) < 100000; });
}
struct Skeleton : Fl_Widget
{
	struct Point
	{
		double x, y, depth;
	};
	Json snapshot = Json::object();
	std::vector<Json> bones;
	double yaw = -25, pitch = 10, zoom = 1;
	bool grid = true;
	int dragX = 0, dragY = 0;
	bool dragging = false;
	Skeleton(int x, int y, int w, int h) : Fl_Widget(x, y, w, h) {}
	void update(const Json & s)
	{
		snapshot = s;
		bones.clear();
		if (s.contains("bones") && s["bones"].is_array())
			for (const auto & b: s["bones"])
				if (b.is_object() && b.contains("head") && b.contains("tail") && validPoint(b["head"]) && validPoint(b["tail"]))
					bones.push_back(b);
		redraw();
	}
	void view(int v)
	{
		yaw = v == 0 ? 0 : v == 1 ? 90
		                          : -25;
		pitch = v == 2 ? 10 : 0;
		zoom = 1;
		redraw();
	}
	Point project(const Json & p, const std::array<double, 3> & center, double scale) const
	{
		auto a = yaw * M_PI / 180, b = pitch * M_PI / 180;
		double xx = p[0].get<double>() - center[0], yy = p[1].get<double>() - center[1], zz = p[2].get<double>() - center[2];
		double rx = cos(a) * xx + sin(a) * zz, rz = -sin(a) * xx + cos(a) * zz;
		return {x() + w() * .5 + rx * scale, y() + h() * .51 - (cos(b) * yy - sin(b) * rz) * scale, sin(b) * yy + cos(b) * rz};
	}
	void draw() override
	{
		fl_push_clip(x(), y(), w(), h());
		fl_color(FL_BACKGROUND2_COLOR);
		fl_rectf(x(), y(), w(), h());
		fl_font(dx::font, dx::size);
		fl_color(FL_FOREGROUND_COLOR);
		if (bones.empty())
		{
			fl_draw("Connect to WiVRn to preview the skeleton", x() + 12, y() + 12, w() - 24, h() - 24, FL_ALIGN_CENTER | FL_ALIGN_WRAP);
			fl_pop_clip();
			return;
		}
		Json neck = bones.front()["head"];
		for (const auto & b: bones)
			if (b.value("body_part", -1) == 2)
			{
				neck = b["head"];
				break;
			}
		auto settings = snapshot.value("settings", Json::object());
		double height = settings.value("height_m", 1.8);
		if (!std::isfinite(height))
			height = 1.8;
		height = std::clamp(height, .5, 3.);
		std::array<double, 3> center = {neck[0], neck[1].get<double>() - height * .45, neck[2]};
		double scale = std::max(1., std::min(w() / (height * 1.5), (h() - 100) / (height * 1.2))) * zoom;
		auto line = [&](const Json & a, const Json & b) {auto p=project(a,center,scale),q=project(b,center,scale);fl_line(int(p.x),int(p.y),int(q.x),int(q.y)); };
		double floor = 0;
		if (!snapshot.value("hmd_pose_received", false))
		{
			floor = 1e6;
			for (auto & b: bones)
				for (auto k: {"head", "tail"})
					floor = std::min(floor, b[k][1].get<double>());
		}
		if (grid)
		{
			fl_color(fl_color_average(FL_FOREGROUND_COLOR, FL_BACKGROUND2_COLOR, .2f));
			fl_line_style(FL_SOLID, 1);
			for (int i = -6; i <= 6; ++i)
			{
				double d = i * .25, nx = neck[0], nz = neck[2];
				line(Json::array({nx + d, floor, nz - 1.5}), Json::array({nx + d, floor, nz + 1.5}));
				line(Json::array({nx - 1.5, floor, nz + d}), Json::array({nx + 1.5, floor, nz + d}));
			}
		}
		auto sorted = bones;
		std::sort(sorted.begin(), sorted.end(), [&](const Json & a, const Json & b) { return project(a["head"], center, scale).depth > project(b["head"], center, scale).depth; });
		bool active = false;
		for (auto & t: snapshot.value("trackers", Json::array()))
			active |= t.value("active", false);
		for (auto & bone: sorted)
		{
			bool tracked = active && bone.value("tracked", false);
			fl_color(tracked ? FL_SELECTION_COLOR : fl_color_average(FL_FOREGROUND_COLOR, FL_BACKGROUND2_COLOR, .45f));
			fl_line_style(FL_SOLID | FL_CAP_ROUND, tracked ? 6 : 4);
			line(bone["head"], bone["tail"]);
			auto p = project(bone["head"], center, scale);
			fl_color(FL_FOREGROUND_COLOR);
			fl_pie(int(p.x) - 4, int(p.y) - 4, 8, 8, 0, 360);
		}
		fl_line_style(FL_SOLID, 2);
		auto p = project(neck, center, scale);
		int r = std::max(8, int(height * .046 * scale));
		fl_color(FL_SELECTION_COLOR);
		fl_arc(int(p.x) - r, int(p.y) - r, 2 * r, 2 * r, 0, 360);
		fl_line_style(0);
		fl_color(FL_FOREGROUND_COLOR);
		fl_draw(snapshot.value("paused", false) ? "PAUSED" : "LIVE SKELETON", x() + 14, y() + 26);
		fl_draw(!active ? "Reference pose - waiting for trackers" : snapshot.value("hmd_pose_received", false) ? "HMD anchored"
		                                                                                                       : "Relative pose - waiting for HMD",
		        x() + 14,
		        y() + 50);
		fl_draw("Drag to orbit / wheel to zoom / double-click to reset", x() + 12, y() + h() - 18);
		fl_pop_clip();
	}
	int handle(int e) override
	{
		switch (e)
		{
			case FL_PUSH:
				if (Fl::event_button() != FL_LEFT_MOUSE)
					return 0;
				if (Fl::event_clicks())
					view(2);
				dragging = true;
				dragX = Fl::event_x();
				dragY = Fl::event_y();
				return 1;
			case FL_DRAG:
				if (!dragging)
					return 0;
				yaw = std::fmod(yaw + (Fl::event_x() - dragX) * .5, 360.);
				pitch = std::clamp(pitch + (Fl::event_y() - dragY) * .35, -75., 75.);
				dragX = Fl::event_x();
				dragY = Fl::event_y();
				redraw();
				return 1;
			case FL_RELEASE:
				dragging = false;
				return 1;
			case FL_MOUSEWHEEL:
				zoom = std::clamp(zoom * std::exp(-Fl::event_dy() * .12), .35, 3.);
				redraw();
				return 1;
		}
		return Fl_Widget::handle(e);
	}
};
} // namespace wivrn::dashboard
