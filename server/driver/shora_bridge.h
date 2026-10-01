// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
namespace wivrn
{
// Constructed only inside the WiVRn runtime child, never before fork().
class shora_bridge
{
	void * handle = nullptr;

public:
	shora_bridge();
	~shora_bridge();
	shora_bridge(const shora_bridge &) = delete;
	shora_bridge & operator=(const shora_bridge &) = delete;
};
} // namespace wivrn
