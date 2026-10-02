// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include "spatial_protocol.h"
#include <algorithm>
#include <cmath>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <optional>

namespace wivrn::room_calibration
{
inline XrQuaternionf multiply(XrQuaternionf a, XrQuaternionf b)
{
	return {a.w * b.x + a.x * b.w + a.y * b.z - a.z * b.y, a.w * b.y - a.x * b.z + a.y * b.w + a.z * b.x, a.w * b.z + a.x * b.y - a.y * b.x + a.z * b.w, a.w * b.w - a.x * b.x - a.y * b.y - a.z * b.z};
}
inline XrVector3f rotate(XrQuaternionf q, XrVector3f v)
{
	auto r = multiply(multiply(q, {v.x, v.y, v.z, 0}), {-q.x, -q.y, -q.z, q.w});
	return {r.x, r.y, r.z};
}
inline XrPosef compose(XrPosef a, XrPosef b)
{
	auto v = rotate(a.orientation, b.position);
	return {multiply(a.orientation, b.orientation), {a.position.x + v.x, a.position.y + v.y, a.position.z + v.z}};
}
inline XrPosef inverse(XrPosef a)
{
	auto q = a.orientation;
	q = {-q.x, -q.y, -q.z, q.w};
	return {q, rotate(q, {-a.position.x, -a.position.y, -a.position.z})};
}
inline bool valid(XrPosef p)
{
	const auto & q = p.orientation;
	const auto & v = p.position;
	return std::isfinite(v.x) && std::isfinite(v.y) && std::isfinite(v.z) &&
	       std::abs(v.x) < 100 && std::abs(v.y) < 100 && std::abs(v.z) < 100 &&
	       std::isfinite(q.x) && std::isfinite(q.y) && std::isfinite(q.z) && std::isfinite(q.w) &&
	       std::abs(q.x * q.x + q.y * q.y + q.z * q.z + q.w * q.w - 1) < .001f;
}
inline bool tracked(const spatial::location & p)
{
	return (p.flags & 15) == 15 && valid(p.pose);
}
// Anchor-relative standing spot, not a saved STAGE pose or a tracker correction.
struct spot
{
	std::array<uint8_t, 16> uuid{};
	XrPosef relative{{0, 0, 0, 1}, {}};
	float height = 1.6f;
	bool valid() const
	{
		return std::any_of(uuid.begin(), uuid.end(), [](auto b) { return b != 0; }) &&
		       room_calibration::valid(relative) && std::isfinite(height) && height >= .5f && height <= 2.5f;
	}
	static std::optional<spot> capture(const spatial::snapshot & s)
	{
		if (!s.active || !tracked(s.head))
			return {};
		auto forward = rotate(s.head.pose.orientation, {0, 0, -1});
		if (std::hypot(forward.x, forward.z) < .2f)
			return {};
		float yaw = std::atan2(-forward.x, -forward.z);
		XrPosef floor{{0, std::sin(yaw / 2), 0, std::cos(yaw / 2)}, s.head.pose.position};
		floor.position.y = 0;
		const spatial::marker * nearest = nullptr;
		float best = 1e9f;
		for (const auto & m: s.markers)
			if (m.persisted && tracked(m.pose))
			{
				auto a = m.pose.pose.position, b = s.head.pose.position;
				float d = (a.x - b.x) * (a.x - b.x) + (a.y - b.y) * (a.y - b.y) + (a.z - b.z) * (a.z - b.z);
				if (d < best && std::any_of(m.uuid.begin(), m.uuid.end(), [](auto b) { return b != 0; }))
				{
					best = d;
					nearest = &m;
				}
			}
		if (!nearest)
			return {};
		spot result{nearest->uuid, compose(inverse(nearest->pose.pose), floor), s.head.pose.position.y};
		return result.valid() ? std::optional(result) : std::nullopt;
	}
	std::optional<XrPosef> locate(const spatial::snapshot & s) const
	{
		if (!valid() || !s.active || !tracked(s.head))
			return {};
		for (const auto & m: s.markers)
			if (m.uuid == uuid && m.persisted && tracked(m.pose))
			{
				auto result = compose(m.pose.pose, relative);
				if (room_calibration::valid(result))
					return result;
			}
		return {};
	}
	bool save(const std::filesystem::path & path) const
	{
		if (!valid())
			return false;
		auto tmp = path;
		tmp += ".tmp";
		std::ofstream f(tmp, std::ios::trunc);
		f << "wivrn-calibration-spot-v1\n";
		for (auto b: uuid)
			f << unsigned(b) << ' ';
		const auto & q = relative.orientation;
		const auto & v = relative.position;
		f << std::setprecision(9) << '\n'
		  << q.x << ' ' << q.y << ' ' << q.z << ' ' << q.w << '\n'
		  << v.x << ' ' << v.y << ' ' << v.z << '\n'
		  << height << '\n';
		f.close();
		if (!f)
			return false;
		std::error_code ec;
		std::filesystem::rename(tmp, path, ec);
		return !ec;
	}
	static std::optional<spot> load(const std::filesystem::path & path)
	{
		std::error_code ec;
		if (std::filesystem::file_size(path, ec) > 1024 || ec)
			return {};
		std::ifstream f(path);
		std::string version;
		f >> version;
		if (version != "wivrn-calibration-spot-v1")
			return {};
		spot result;
		for (auto & b: result.uuid)
		{
			unsigned n;
			if (!(f >> n) || n > 255)
				return {};
			b = n;
		}
		auto & q = result.relative.orientation;
		auto & v = result.relative.position;
		if (!(f >> q.x >> q.y >> q.z >> q.w >> v.x >> v.y >> v.z >> result.height))
			return {};
		f >> std::ws;
		return f.eof() && result.valid() ? std::optional(result) : std::nullopt;
	}
};
} // namespace wivrn::room_calibration
