// SPDX-License-Identifier: GPL-3.0-or-later
#include "config_migration.h"
#include <cassert>
#include <iostream>
#include <sys/stat.h>
using namespace wivrn::migration;
int main(int argc, char ** argv)
{
	if (argc == 2)
	{
		for (auto & change: carry_over(argv[1]))
			std::cout << change << '\n';
		return 0;
	}
	char name[] = "/tmp/wivrn-config-test-XXXXXX";
	auto home = std::filesystem::path(mkdtemp(name));
	auto legacy = home / "wivrn";
	auto fork = home / "wivrn-pro";
	auto original = R"({"encoder":[{"encoder":"vaapi","options":{"quality":"balanced"},"codec":"h265"}],"application":["app","--flag"],"unknown":{"keep":true},"tcp-only":true})";
	create_once(legacy / "config.json", original);
	create_once(legacy / "known_keys.json", R"([{"name":"Quest Pro","key":"test-key"}])");
	create_once(legacy / "cookie", "test-cookie");
	std::string tracking = "# keep comments\nheight_m=1.91\n[face]\nenabled=true\nsource='openxr'\n[haritorax]\nenabled=true\nports=['/fake/serial']\n";
	create_once(home / "shora-rust/server.toml", tracking);
	assert(carry_over(home).size() == 4);
	assert(nlohmann::json::parse(read(fork / "config.json")) == nlohmann::json::parse(original));
	assert(read(fork / "known_keys.json") == read(legacy / "known_keys.json"));
	assert(read(fork / "cookie") == "test-cookie");
	assert(read(fork / "shora.toml") == tracking);
	assert(read(legacy / "config.json") == original);
	struct stat st{};
	stat((fork / "shora.toml").c_str(), &st);
	assert((st.st_mode & 0777) == 0600);
	std::ofstream(fork / "config.json") << "{\"hostname\":\"custom-fork\"}";
	std::ofstream(fork / "shora.toml") << "height_m=1.65\n";
	assert(carry_over(home).empty());
	assert(read(fork / "config.json") == "{\"hostname\":\"custom-fork\"}");
	assert(read(fork / "shora.toml") == "height_m=1.65\n");
	std::filesystem::remove(fork / "config.json");
	std::ofstream(legacy / "config.json") << R"({"encoder":[{"encoder":"nvenc","codec":"av1","options":{"keep":1}}],"use-steamvr-lh":true})";
	assert(carry_over(home).size() == 4);
	auto normalized = nlohmann::json::parse(read(fork / "config.json"));
	assert(normalized["encoder"][0]["encoder"] == "vaapi");
	assert(normalized["encoder"][0]["codec"] == "h265");
	assert(normalized["encoder"][0]["options"]["keep"] == 1);
	assert(normalized["use-steamvr-lh"] == false);
	std::filesystem::remove(fork / "config.json");
	std::ofstream(legacy / "config.json") << "invalid";
	bool threw = false;
	try
	{
		carry_over(home);
	}
	catch (...)
	{
		threw = true;
	}
	assert(threw && !std::filesystem::exists(fork / "config.json"));
	assert(carry_over(home, false).empty()); // -f bypasses a malformed legacy streaming config.
	std::filesystem::remove_all(home);
	std::cout << "Configuration migration: import, precedence, preservation, restrictions, malformed source passed\n";
}
