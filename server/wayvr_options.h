// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include <algorithm>
#include <cctype>
#include <filesystem>
#include <nlohmann/json.hpp>
#include <stdexcept>
#include <string>
#include <vector>
namespace wivrn
{
inline bool is_wayvr_application(const std::vector<std::string> & args)
{
	if (args.empty())
		return false;
	auto name = std::filesystem::path(args.front()).filename().string();
	std::ranges::transform(name, name.begin(), [](unsigned char c) { return std::tolower(c); });
	return name == "wayvr" || name == "wlx-overlay-s" || name == "wivrn-pro-wayvr" ||
	       ((name.starts_with("wayvr-") || name.starts_with("wlxoverlay-s-")) && name.ends_with(".appimage"));
}
struct wayvr_options
{
	bool enabled = true, dashboard = true;
	std::vector<std::string> flags;
	explicit wayvr_options(const nlohmann::json & json, const std::vector<std::string> & application = {})
	{
		if (auto it = json.find("wayvr"); it != json.end())
		{
			if (!it->is_object())
				throw std::runtime_error("wayvr must be an object");
			enabled = it->value("enabled", true);
			dashboard = it->value("dashboard", true);
			flags = it->value("args", std::vector<std::string>{});
		}
		// Preserve legacy arguments only when no explicit fork args are supplied.
		if ((!json.contains("wayvr") || !json.at("wayvr").contains("args")) && is_wayvr_application(application))
			flags.assign(application.begin() + 1, application.end());
		for (const auto & flag: flags)
		{
			if (flag != "--show" && flag != "--headless" && flag != "--no-autostart" && flag != "--no-audio" && flag != "--openxr" && flag != "--wait")
				throw std::runtime_error("Unsupported managed WayVR argument: " + flag);
		}
		std::erase(flags, "--openxr");
		std::erase(flags, "--wait");
		std::ranges::sort(flags);
		flags.erase(std::unique(flags.begin(), flags.end()), flags.end());
	}
};
} // namespace wivrn
