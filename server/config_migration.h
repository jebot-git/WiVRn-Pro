// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include <fcntl.h>
#include <filesystem>
#include <fstream>
#include <nlohmann/json.hpp>
#include <stdexcept>
#include <unistd.h>
#include <vector>

namespace wivrn::migration
{
// Publish complete files without replacing a profile created by another process.
inline bool create_once(const std::filesystem::path & path, const std::string & text)
{
	if (std::filesystem::exists(path))
		return false;
	std::filesystem::create_directories(path.parent_path());
	auto temp = path.string() + ".import-XXXXXX";
	int fd = mkstemp(temp.data());
	if (fd < 0)
		throw std::runtime_error("Cannot create profile temporary file: " + path.string());
	try
	{
		size_t at = 0;
		while (at < text.size())
		{
			auto count = write(fd, text.data() + at, text.size() - at);
			if (count < 0 && errno == EINTR)
				continue;
			if (count <= 0)
				throw std::runtime_error("Cannot write profile: " + path.string());
			at += count;
		}
		if (fsync(fd))
			throw std::runtime_error("Cannot sync profile: " + path.string());
		close(fd);
		fd = -1;
		if (link(temp.c_str(), path.c_str()) != 0)
		{
			if (errno != EEXIST)
				throw std::runtime_error("Cannot publish profile: " + path.string());
			unlink(temp.c_str());
			return false;
		}
		unlink(temp.c_str());
		return true;
	}
	catch (...)
	{
		if (fd >= 0)
			close(fd);
		unlink(temp.c_str());
		throw;
	}
}
inline std::string read(const std::filesystem::path & path)
{
	std::ifstream file(path);
	if (!file)
		throw std::runtime_error("Cannot read profile: " + path.string());
	return {std::istreambuf_iterator<char>(file), {}};
}
inline void normalize_encoder(nlohmann::json & item, std::vector<std::string> & changes)
{
	if (item.is_array())
	{
		for (auto & part: item)
			normalize_encoder(part, changes);
		return;
	}
	auto normalize_name = [&](nlohmann::json & name) {
		if (name == "x264" || name == "raw")
		{
			changes.push_back("Unsupported encoder " + name.dump() + " replaced with vaapi");
			name = "vaapi";
		}
	};
	if (item.is_string())
		normalize_name(item);
	if (item.is_object())
	{
		if (item.contains("encoder"))
			normalize_name(item["encoder"]);
		if (item.contains("codec") && (item["codec"] == "av1" || item["codec"] == "AV1" || item["codec"] == "raw"))
		{
			item["codec"] = "h265";
			changes.push_back("Unsupported Quest Pro codec replaced with h265");
		}
	}
}
// Explicit fork profiles always win. Sources remain byte-for-byte unchanged.
inline std::vector<std::string> carry_over(const std::filesystem::path & config_home, bool import_streaming = true)
{
	std::vector<std::string> changes;
	auto dest = config_home / "wivrn-pro";
	auto source = config_home / "wivrn";
	if (import_streaming && !std::filesystem::exists(dest / "config.json") && std::filesystem::exists(source / "config.json"))
	{
		auto json = nlohmann::json::parse(read(source / "config.json"));
		if (!json.is_object())
			throw std::runtime_error("Original WiVRn config must be an object");
		if (json.contains("encoder"))
			normalize_encoder(json["encoder"], changes);
		if (json.value("use-steamvr-lh", false))
		{
			json["use-steamvr-lh"] = false;
			changes.push_back("Select Motoc tracking-mode explicitly to enable Lighthouse");
		}
		if (create_once(dest / "config.json", json.dump(2) + "\n"))
			changes.push_back("Imported wivrn/config.json");
	}
	for (const auto * name: {"known_keys.json", "cookie"})
	{
		if (!std::filesystem::exists(dest / name) && std::filesystem::exists(source / name))
		{
			auto text = read(source / name);
			if (std::string_view(name) == "known_keys.json")
			{
				auto keys = nlohmann::json::parse(text);
				if (!keys.is_array() && !keys.is_null())
					throw std::runtime_error("Original headset keys must be an array");
			}
			if (create_once(dest / name, text))
				changes.push_back(std::string("Imported wivrn/") + name);
		}
	}
	auto old = config_home / "shora-rust" / "server.toml";
	if (!std::filesystem::exists(dest / "shora.toml") && std::filesystem::exists(old))
	{
		if (create_once(dest / "shora.toml", read(old)))
			changes.push_back("Imported shora-rust/server.toml (worker overrides private socket paths and UI mode)");
	}
	return changes;
}
} // namespace wivrn::migration
