// SPDX-License-Identifier: GPL-3.0-or-later
#include "shora_bridge.h"
#include <string_view>
#include <cstdlib>
#include <filesystem>
#include <stdexcept>
#include <sys/stat.h>
#include <unistd.h>
extern "C" void * wivrn_shora_start(const char * directory);
extern "C" void wivrn_shora_stop(void * handle);
namespace wivrn
{
shora_bridge::shora_bridge()
{
    if (const auto * mode = std::getenv("WIVRN_TRACKING_MODE"); mode && std::string_view(mode) == "motoc") return;
	const auto * runtime = std::getenv("XDG_RUNTIME_DIR");
	const auto directory = (runtime ? std::filesystem::path(runtime) : std::filesystem::path("/run/user") / std::to_string(getuid())) / "wivrn-pro";
	if (mkdir(directory.c_str(), 0700) != 0 && errno != EEXIST)
		throw std::runtime_error("Cannot create WiVRn Pro runtime directory");
	handle = wivrn_shora_start(directory.c_str());
	if (!handle)
		throw std::runtime_error("Cannot start integral Shora bridge; inspect socket ownership and server log");
}
shora_bridge::~shora_bridge()
{
	wivrn_shora_stop(handle);
}
} // namespace wivrn
