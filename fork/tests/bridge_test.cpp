// SPDX-License-Identifier: GPL-3.0-or-later
#include "spatial_bridge.h"
#include <nlohmann/json.hpp>
#include <cassert>
#include <chrono>
#include <cmath>
#include <cstring>
#include <filesystem>
#include <iostream>
#include <limits>
#include <poll.h>
#include <source_location>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <thread>
#include <unistd.h>
using namespace wivrn::spatial;
using json = nlohmann::json;
using namespace std::chrono_literals;
int checks = 0;
void check(bool value, std::source_location at = std::source_location::current()) {
    if (!value) { std::cerr << "FAILED at " << at.line() << '\n'; std::abort(); }
    ++checks;
}
int64_t now() { return std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now().time_since_epoch()).count(); }
int connect_reader(const std::filesystem::path & directory) {
    int fd = socket(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC, 0);
    check(fd >= 0);
    sockaddr_un address{}; address.sun_family = AF_UNIX;
    auto path = (directory / "bridge.sock").string();
    std::copy(path.begin(), path.end(), address.sun_path);
    check(connect(fd, reinterpret_cast<sockaddr *>(&address), sizeof(address)) == 0);
    return fd;
}
json receive(int fd) {
    pollfd p{fd, POLLIN, 0};
    check(poll(&p, 1, 2000) == 1);
    std::array<char, 128*1024> buffer{};
    auto n = recv(fd, buffer.data(), buffer.size(), MSG_TRUNC);
    check(n > 0 && size_t(n) < buffer.size());
    return json::parse(buffer.data(), buffer.data() + n);
}
json sequence_at_least(int fd, uint64_t sequence) {
    for (int i = 0; i < 30; ++i) { auto data = receive(fd); if (data.value("sequence", uint64_t(0)) >= sequence) return data; }
    check(false); return {};
}
int main() {
    XrPosef identity{{0,0,0,1},{0,0,0}};
    XrPosef rotation{{0,float(std::sqrt(.5)),0,float(std::sqrt(.5))},{10,2,3}};
    XrPosef point{{0,0,0,1},{1,0,0}};
    auto placed = transform(rotation, point);
    check(std::abs(placed.position.x-10)<1e-5 && std::abs(placed.position.y-2)<1e-5 && std::abs(placed.position.z-2)<1e-5);
    auto composed = transform(rotation, rotation);
    check(std::abs(composed.orientation.y-1)<1e-5 && std::abs(composed.orientation.w)<1e-5);
    auto recovered = transform(XrPosef{{0,-rotation.orientation.y,0,rotation.orientation.w},{3,-2,-10}}, placed);
    check(std::abs(recovered.position.x-1)<1e-5 && std::abs(recovered.position.z)<1e-5);
    char temporary[] = "/tmp/wivrn-spatial-test-XXXXXX";
    auto name = mkdtemp(temporary); check(name);
    std::filesystem::path directory(name);
    {
        bridge server(directory); check(server.enabled());
        bridge duplicate(directory); check(!duplicate.enabled());
        struct stat info{}; check(stat((directory/"bridge.sock").c_str(), &info) == 0 && (info.st_mode & 0777) == 0600);
        int a = connect_reader(directory), b = connect_reader(directory);
        check(!receive(a)["live"].get<bool>()); check(!receive(b)["live"].get<bool>());
        snapshot packet;
        packet.stream=1; packet.sequence=1; packet.generation=1; packet.active=true;
        packet.head={identity,15}; packet.markers[0].pose={point,3}; packet.markers[0].persisted=true;
        packet.entity_count=1; packet.entities[0].pose={point,3}; packet.entities[0].boundary_count=4;
        server.publish(packet, rotation, now());
        auto first=sequence_at_least(a,1), second=sequence_at_least(b,1);
        check(first["live"] && second["live"]);
        check(first["markers"][0]["pose"]["quest_stage"]["position"][0] == 1);
        check(std::abs(first["markers"][0]["pose"]["pc_stage"]["position"][2].get<double>()-2)<1e-5);
        check(!first["markers"][1]["pose"]["valid"].get<bool>());
        int late=connect_reader(directory); check(receive(late)["live"]);
        // Replay and regressed generation cannot overwrite the current publication.
        server.publish(packet, identity, now());
        auto repeated=receive(a); check(repeated["alignment"]["position"][0] == 10);
        packet.sequence=2; packet.generation=0; server.publish(packet, identity, now());
        ++packet.generation;
        packet.sequence=3; server.publish(packet, {}, now());
        auto unmapped=sequence_at_least(a,3); check(!unmapped["live"].get<bool>() && unmapped["markers"][0]["pose"]["pc_stage"].is_null());
        packet.sequence=4; server.publish(packet, identity, now()-2'000'000'000);
        check(!sequence_at_least(a,4)["live"].get<bool>());
        packet.sequence=5; server.publish(packet, identity, now()+2'000'000'000);
        check(!sequence_at_least(a,5)["live"].get<bool>());
        packet.sequence=6; packet.head.pose.orientation.w=std::numeric_limits<float>::quiet_NaN();
        server.publish(packet, identity, now()); check(!sequence_at_least(a,6)["live"].get<bool>());
        packet.head.pose=identity;
        packet.sequence=7; packet.head.flags=3; server.publish(packet, rotation, now());
        check(!sequence_at_least(a,7)["live"].get<bool>());
        packet.sequence=8; packet.head.flags=15; packet.generation=2; server.publish(packet, rotation, now());
        check(sequence_at_least(a,8)["live"]);
        server.invalidate("disconnected");
        auto lost=receive(a); check(lost["status"] == "disconnected" && lost["markers"][0]["pose"]["quest_stage"].is_null());
        packet.sequence=9; server.publish(packet, rotation, now());
        check(sequence_at_least(a,9)["live"]);
        auto expired=receive(a); check(expired["status"] == "expired" && expired["alignment"].is_null());
        int stale=connect_reader(directory); check(!receive(stale)["live"].get<bool>()); close(stale);
        // New client scene/session epoch, max geometry payload, and valid tracking loss.
        packet.stream=2; packet.sequence=1; packet.generation=1;
        packet.entity_count=entity_limit;
        for (auto & e : packet.entities) {
            e.pose={point,3}; e.boundary_count=boundary_limit;
            std::fill(e.labels.begin(), e.labels.end()-1, 'X');
            for (auto & vertex : e.boundary) vertex={.123456789f,-.987654321f};
        }
        server.publish(packet, identity, now());
        auto full=receive(a); while (full.value("source_session",std::string{})!="2") full=receive(a);
        check(full["live"] && full["scene"]["entities"].size()==entity_limit);
        // b and late deliberately do not read; their full queues cannot block a.
        for (uint64_t i=2;i<18;++i) {
            packet.sequence=i; server.publish(packet, identity, now());
            check(sequence_at_least(a,i)["live"]);
        }
        packet.sequence=18; packet.markers[0].pose.flags=0;
        server.publish(packet, identity, now());
        check(sequence_at_least(a,18)["markers"][0]["pose"]["pc_stage"].is_null());
        packet.sequence=19; packet.active=false;
        server.publish(packet, identity, now()); check(!sequence_at_least(a,19)["live"].get<bool>());
        close(a); close(b); close(late);
    }
    check(!std::filesystem::exists(directory/"bridge.sock"));
    std::filesystem::permissions(directory,std::filesystem::perms::others_read,std::filesystem::perm_options::add);
    { bridge unsafe(directory); check(!unsafe.enabled()); }
    std::filesystem::remove_all(directory);
    std::cout << checks << " native transform/IPC checks passed\n";
}
