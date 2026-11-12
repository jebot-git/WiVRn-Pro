// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include "choice.h"
#include <FL/Fl_Check_Button.H>
#include <FL/Fl_Choice.H>
#include <FL/Fl_Input.H>
#include <FL/Fl_Value_Input.H>
#include <algorithm>
#include <deque>
#include <nlohmann/json.hpp>
#include <vector>
namespace wivrn::dashboard
{
using Json = nlohmann::json;
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
		auto p = place<LiteralChoice>(label);
		std::vector<Json> values;
		std::vector<std::string> labels;
		for (auto & v: entries)
		{
			labels.emplace_back(v.first);
			values.push_back(v.second);
		}
		p->setLabels(labels);
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
} // namespace wivrn::dashboard
