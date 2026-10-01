// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include <array>
#include <cstdint>
#include <string_view>
namespace wivrn::fork_policy
{
// PCI IDs explicitly identified as Arc; exclude Xe MAX and Data Center Flex.
// Keep this small table reviewable when updating upstream or adding Arc models.
inline constexpr std::array arc_ids{
        0x5690u,
        0x5691u,
        0x5692u,
        0x5693u,
        0x5694u,
        0x5696u,
        0x5697u,
        0x5698u,
        0x56a0u,
        0x56a1u,
        0x56a2u,
        0x56a3u,
        0x56a4u,
        0x56a5u,
        0x56a6u,
        0x56a7u,
        0x56a8u,
        0x56a9u,
        0x56b0u,
        0x56b1u,
        0x56b2u,
        0x56b3u,
        0x56bau,
        0x56bbu,
        0x56bcu,
        0x56bdu,
        0x56beu,
        0x56bfu,
        0xe20bu,
        0xe20cu,
        0xe211u,
        0xe212u,
        0xe222u,
        0xe223u};
constexpr bool intel_arc(uint32_t vendor, uint32_t device)
{
	if (vendor != 0x8086)
		return false;
	for (auto id: arc_ids)
		if (id == device)
			return true;
	return false;
}
constexpr bool wifi_interface(std::string_view name)
{
	return name.starts_with("wlan");
}
constexpr bool quest_pro(std::string_view device, std::string_view manufacturer)
{
	return device == "seacliff" && (manufacturer == "Oculus" || manufacturer == "Meta");
}
constexpr bool encoder(std::string_view name)
{
	return name.empty() || name == "vaapi" || name == "vulkan" || name == "qsv";
}
constexpr bool codec(std::string_view name)
{
	return name == "h264" || name == "h265";
}
} // namespace wivrn::fork_policy
