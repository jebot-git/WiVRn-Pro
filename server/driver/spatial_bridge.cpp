// SPDX-License-Identifier: GPL-3.0-or-later
#include "spatial_bridge.h"
#include <algorithm>
#include <atomic>
#include <cerrno>
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <iostream>
#include <mutex>
#include <nlohmann/json.hpp>
#include <poll.h>
#include <stdexcept>
#include <sys/file.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <thread>
#include <unistd.h>
#include <vector>

namespace wivrn::spatial
{
namespace
{
using json = nlohmann::json;
constexpr int64_t ttl_ns = 750'000'000;
constexpr size_t record_limit = 128 * 1024, client_limit = 32;
int64_t now_ns()
{
	return std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now().time_since_epoch()).count();
}
bool finite(const XrVector3f & v)
{
	return std::isfinite(v.x) && std::isfinite(v.y) && std::isfinite(v.z);
}
bool valid(const XrPosef & pose)
{
	const auto & q = pose.orientation;
	double norm = double(q.x) * q.x + double(q.y) * q.y + double(q.z) * q.z + double(q.w) * q.w;
	return finite(pose.position) && std::isfinite(norm) && std::abs(norm - 1) < .02;
}
json pose_json(const XrPosef & pose)
{
	const auto & p = pose.position;
	const auto & q = pose.orientation;
	return {{"position", {p.x, p.y, p.z}}, {"rotation", {q.x, q.y, q.z, q.w}}};
}
std::string uuid_text(const std::array<uint8_t, 16> & uuid)
{
	const char * digits = "0123456789abcdef";
	std::string result;
	for (auto value: uuid)
	{
		result += digits[value >> 4];
		result += digits[value & 15];
	}
	return result;
}
json location_json(const location & location, bool live, const std::optional<XrPosef> & mapping)
{
	bool located = live && (location.flags & 3) == 3 && valid(location.pose);
	return {{"valid", located}, {"flags", located ? location.flags : 0}, {"quest_stage", located ? pose_json(location.pose) : json(nullptr)}, {"pc_stage", located && mapping ? pose_json(transform(*mapping, location.pose)) : json(nullptr)}};
}
void invalidate_json(json & data, std::string_view reason)
{
	data["live"] = false;
	data["status"] = reason;
	data["alignment"] = nullptr;
	const json empty = {{"valid", false}, {"flags", 0}, {"quest_stage", nullptr}, {"pc_stage", nullptr}};
	data["head"] = empty;
	if (data.contains("markers"))
		for (auto & marker: data["markers"])
			marker["pose"] = empty;
	if (data.contains("scene"))
		for (auto & entity: data["scene"]["entities"])
			entity["pose"] = empty;
}
} // namespace
XrPosef transform(const XrPosef & parent, const XrPosef & child)
{
	const auto & a = parent.orientation;
	const auto & b = child.orientation;
	const auto & v = child.position;
	XrVector3f cross{2 * (a.y * v.z - a.z * v.y), 2 * (a.z * v.x - a.x * v.z), 2 * (a.x * v.y - a.y * v.x)};
	return {{a.w * b.x + a.x * b.w + a.y * b.z - a.z * b.y,
	         a.w * b.y - a.x * b.z + a.y * b.w + a.z * b.x,
	         a.w * b.z + a.x * b.y - a.y * b.x + a.z * b.w,
	         a.w * b.w - a.x * b.x - a.y * b.y - a.z * b.z},
	        {parent.position.x + v.x + a.w * cross.x + a.y * cross.z - a.z * cross.y,
	         parent.position.y + v.y + a.w * cross.y + a.z * cross.x - a.x * cross.z,
	         parent.position.z + v.z + a.w * cross.z + a.x * cross.y - a.y * cross.x}};
}
struct bridge::implementation
{
	int directory_fd = -1, lock_fd = -1, listener = -1;
	bool bound = false;
	std::vector<int> clients;
	std::mutex mutex;
	json latest{{"schema", "wivrn.spatial.v1"}, {"live", false}, {"status", "waiting"}, {"markers", json::array()}, {"scene", {{"entities", json::array()}}}};
	uint64_t stream = 0, sequence = 0, generation = 0, revision = 1;
	int64_t sampled_at = 0;
	std::jthread worker; // Destroy/join before all state and file descriptors.
	explicit implementation(const std::filesystem::path & directory)
	{
		try
		{
			if (mkdir(directory.c_str(), 0700) != 0 && errno != EEXIST)
				throw std::runtime_error("cannot create private spatial directory");
			directory_fd = open(directory.c_str(), O_DIRECTORY | O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
			struct stat info{};
			if (directory_fd < 0 || fstat(directory_fd, &info) || info.st_uid != getuid() || (info.st_mode & 077) != 0)
				throw std::runtime_error("spatial directory must be private and owned by this user");
			lock_fd = openat(directory_fd, "server.lock", O_CREAT | O_RDWR | O_CLOEXEC | O_NOFOLLOW, 0600);
			if (lock_fd < 0 || fstat(lock_fd, &info) || !S_ISREG(info.st_mode) || info.st_uid != getuid() ||
			    flock(lock_fd, LOCK_EX | LOCK_NB))
				throw std::runtime_error("spatial socket already owned or lock unsafe");
			auto path = (directory / "bridge.sock").string();
			sockaddr_un address{};
			if (path.size() >= sizeof(address.sun_path))
				throw std::runtime_error("spatial socket path too long");
			address.sun_family = AF_UNIX;
			std::copy(path.begin(), path.end(), address.sun_path);
			listener = socket(AF_UNIX, SOCK_SEQPACKET | SOCK_NONBLOCK | SOCK_CLOEXEC, 0);
			if (listener < 0)
				throw std::runtime_error("spatial socket creation failed");
			if (fstatat(directory_fd, "bridge.sock", &info, AT_SYMLINK_NOFOLLOW) == 0)
			{
				if (!S_ISSOCK(info.st_mode) || info.st_uid != getuid())
					throw std::runtime_error("refusing to replace non-socket spatial path");
				if (unlinkat(directory_fd, "bridge.sock", 0))
					throw std::runtime_error("cannot remove stale socket");
			}
			else if (errno != ENOENT)
				throw std::runtime_error("cannot inspect spatial socket");
			if (bind(listener, reinterpret_cast<sockaddr *>(&address), sizeof(address)))
				throw std::runtime_error("spatial bind failed");
			bound = true;
			if (fchmodat(directory_fd, "bridge.sock", 0600, 0) || listen(listener, client_limit))
				throw std::runtime_error("spatial listen failed");
			worker = std::jthread([this](std::stop_token stop) { run(stop); });
		}
		catch (...)
		{
			cleanup();
			throw;
		}
	}
	void cleanup()
	{
		for (int fd: clients)
			close(fd);
		if (listener >= 0)
			close(listener);
		if (bound)
			unlinkat(directory_fd, "bridge.sock", 0);
		if (lock_fd >= 0)
			close(lock_fd);
		if (directory_fd >= 0)
			close(directory_fd);
	}
	~implementation()
	{
		worker.request_stop();
		if (worker.joinable())
			worker.join();
		cleanup();
	}
	void run(std::stop_token stop)
	{
		uint64_t sent_revision = 0;
		int64_t last_sent = 0;
		while (!stop.stop_requested())
		{
			pollfd poll_fd{listener, POLLIN, 0};
			poll(&poll_fd, 1, 25);
			bool joined = false;
			// Bound work per tick even under a connection flood.
			for (size_t n = 0; n < client_limit; ++n)
			{
				int fd = accept4(listener, nullptr, nullptr, SOCK_NONBLOCK | SOCK_CLOEXEC);
				if (fd < 0)
					break;
				ucred peer{};
				socklen_t length = sizeof(peer);
				if (clients.size() >= client_limit || getsockopt(fd, SOL_SOCKET, SO_PEERCRED, &peer, &length) || peer.uid != getuid())
				{
					close(fd);
					continue;
				}
				int size = record_limit * 2;
				setsockopt(fd, SOL_SOCKET, SO_SNDBUF, &size, sizeof(size));
				clients.push_back(fd);
				joined = true;
			}
			std::string message;
			auto now = now_ns();
			{
				std::lock_guard lock(mutex);
				if (latest.value("live", false) && now - sampled_at > ttl_ns)
				{
					invalidate_json(latest, "expired");
					++revision;
				}
				if (joined || sent_revision != revision || now - last_sent >= 1'000'000'000)
				{
					latest["emitted_monotonic_ns"] = now;
					latest["age_ns"] = sampled_at ? json(now - sampled_at) : json(nullptr);
					message = latest.dump(-1, ' ', false, json::error_handler_t::replace);
					sent_revision = revision;
					last_sent = now;
				}
			}
			if (message.empty())
				continue;
			if (message.size() > record_limit)
				message = R"({"schema":"wivrn.spatial.v1","live":false,"status":"record_limit"})";
			std::erase_if(clients, [&](int fd) {
				// There is no command API. Never let an idle reader delay XR or other readers.
				char input;
				auto received = recv(fd, &input, 1, MSG_DONTWAIT | MSG_PEEK);
				if (received >= 0 || (errno != EAGAIN && errno != EWOULDBLOCK) ||
				    send(fd, message.data(), message.size(), MSG_DONTWAIT | MSG_NOSIGNAL) != ssize_t(message.size()))
				{
					close(fd);
					return true;
				}
				return false;
			});
		}
	}
};
bridge::bridge(std::filesystem::path directory)
{
	if (directory.empty())
	{
		auto runtime = std::getenv("XDG_RUNTIME_DIR");
		directory = (runtime ? std::filesystem::path(runtime) : std::filesystem::path("/run/user") / std::to_string(getuid())) / "wivrn-pro";
	}
	try
	{
		impl = std::make_unique<implementation>(directory);
	}
	catch (const std::exception & error)
	{
		std::cerr << "WiVRn spatial bridge disabled: " << error.what() << '\n';
	}
}
bridge::~bridge() = default;
void bridge::invalidate(std::string_view reason)
{
	if (!impl)
		return;
	std::lock_guard lock(impl->mutex);
	invalidate_json(impl->latest, reason);
	++impl->revision;
}
void bridge::publish(const snapshot & packet, std::optional<XrPosef> mapping, int64_t sample_ns)
{
	if (!impl)
		return;
	std::lock_guard lock(impl->mutex);
	if (!packet.stream || !packet.sequence || !packet.generation || packet.entity_count > entity_limit ||
	    (packet.stream == impl->stream && (packet.sequence <= impl->sequence || packet.generation < impl->generation)))
		return;
	impl->stream = packet.stream;
	impl->sequence = packet.sequence;
	impl->generation = packet.generation;
	impl->sampled_at = sample_ns;
	auto age = now_ns() - sample_ns;
	bool live = packet.active && mapping && valid(*mapping) && age >= -100'000'000 && age <= ttl_ns &&
	            (packet.head.flags & 15) == 15 && valid(packet.head.pose);
	json data{{"schema", "wivrn.spatial.v1"}, {"source_session", std::to_string(packet.stream)}, {"sequence", packet.sequence}, {"generation", packet.generation}, {"sample_monotonic_ns", sample_ns}, {"ttl_ns", ttl_ns}, {"live", live}, {"status", live ? "live" : "unavailable"}, {"reference_space", "pc_stage"}, {"alignment_method", "wivrn_runtime"}, {"alignment", live ? pose_json(*mapping) : json(nullptr)}, {"head", location_json(packet.head, live, mapping)}, {"capture", {{"supported", packet.supported}, {"active", packet.capturing}, {"accepted", packet.accepted}, {"verified", packet.verified}}}, {"markers", json::array()}, {"boundary", {{"kind", "quest_stage_rectangle"}, {"available", packet.bounds_available && std::isfinite(packet.bounds.width) && std::isfinite(packet.bounds.height)}, {"width", packet.bounds.width}, {"depth", packet.bounds.height}, {"guardian_polygon", false}}}, {"scene", {{"complete", packet.scene_complete}, {"result", packet.scene_result}, {"entity_limit", entity_limit}, {"entities", json::array()}}}};
	for (size_t i = 0; i < marker_count; ++i)
	{
		const auto & marker = packet.markers[i];
		data["markers"].push_back({{"slot", i}, {"uuid", uuid_text(marker.uuid)}, {"persisted", marker.persisted}, {"status", marker.status}, {"result", marker.result}, {"pose", location_json(marker.pose, live, mapping)}});
	}
	for (size_t i = 0; i < packet.entity_count; ++i)
	{
		const auto & entity = packet.entities[i];
		auto end = std::find(entity.labels.begin(), entity.labels.end(), '\0');
		json item{{"uuid", uuid_text(entity.uuid)}, {"labels", std::string(entity.labels.begin(), end)}, {"pose", location_json(entity.pose, live, mapping)}, {"boundary_2d", json::array()}, {"boundary_omitted", entity.boundary_omitted || entity.boundary_count > boundary_limit}};
		if (entity.boundary_count <= boundary_limit)
			for (size_t j = 0; j < entity.boundary_count; ++j)
			{
				auto vertex = entity.boundary[j];
				if (!std::isfinite(vertex.x) || !std::isfinite(vertex.y))
				{
					item["boundary_2d"] = json::array();
					item["boundary_omitted"] = true;
					break;
				}
				item["boundary_2d"].push_back({vertex.x, vertex.y});
			}
		if (entity.has_box && finite(entity.box_offset) && finite(entity.box_extent))
			item["box"] = {{"offset", {entity.box_offset.x, entity.box_offset.y, entity.box_offset.z}},
			               {"extent", {entity.box_extent.x, entity.box_extent.y, entity.box_extent.z}}};
		data["scene"]["entities"].push_back(std::move(item));
	}
	impl->latest = std::move(data);
	++impl->revision;
}
} // namespace wivrn::spatial
