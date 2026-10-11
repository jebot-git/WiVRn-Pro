// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once

#include <charconv>
#include <map>
#include <optional>
#include <stdexcept>
#include <string>

namespace wivrn
{
// Host-owned options, removed before forwarding the remaining FFmpeg options.
// Hardware AVC and HEVC use QP 1..51; other codecs/backends need separate validation.
inline std::optional<int> cqp_quantizer(const std::map<std::string, std::string> & options, bool supported)
{
	const auto mode = options.find("rate_control");
	const auto qp = options.find("qp");
	if (mode == options.end() || mode->second == "cbr")
	{
		if (qp != options.end())
			throw std::runtime_error("qp requires rate_control=cqp");
		return {};
	}
	if (mode->second != "cqp")
		throw std::runtime_error("rate_control must be cbr or cqp");
	if (!supported)
		throw std::runtime_error("CQP requires NVENC, VAAPI or QSV H.264/HEVC");
	if (qp == options.end())
		throw std::runtime_error("rate_control=cqp requires qp (1..51)");
	int value = 0;
	const auto & text = qp->second;
	const auto [end, error] = std::from_chars(text.data(), text.data() + text.size(), value);
	if (error != std::errc{} || end != text.data() + text.size() || value < 1 || value > 51)
		throw std::runtime_error("H.264/HEVC qp must be an integer from 1 to 51");
	// These can override AVCodecContext fields in avcodec_open2, or enable BRC.
	for (const auto * key : {"b", "ab", "bit_rate", "maxrate", "minrate", "bufsize", "rc_init_occupancy", "global_quality", "flags", "rc_mode", "qscale", "q", "qmin", "qmax", "i_qfactor", "i_qoffset", "b_qfactor", "b_qoffset", "look_ahead", "look_ahead_depth", "extbrc", "low_delay_brc"})
	{
		if (const auto it = options.find(key); it != options.end())
		{
			const std::string name(key);
			const bool disabled_brc = (name == "look_ahead" || name == "look_ahead_depth" || name == "extbrc" || name == "low_delay_brc") && it->second == "0";
			if (!disabled_brc)
				throw std::runtime_error("CQP conflicts with encoder option: " + name);
		}
	}
	return value;
}
} // namespace wivrn
