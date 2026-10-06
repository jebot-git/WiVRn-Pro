// SPDX-License-Identifier: GPL-3.0-or-later
#include "subtitle_bridge.h"
#include <array>
#include <cerrno>
#include <chrono>
#include <csignal>
#include <filesystem>
#include <nlohmann/json.hpp>
#include <spawn.h>
#include <stdexcept>
#include <sys/socket.h>
#include <sys/wait.h>
#include <thread>
#include <unistd.h>
#include <sys/mman.h>
#include <fcntl.h>

extern char ** environ;
namespace wivrn
{
subtitle_bridge::subtitle_bridge(std::string worker)
{
    if (worker.empty())
    {
        auto prefix = std::filesystem::read_symlink("/proc/self/exe").parent_path().parent_path();
        auto installed = prefix / "share/wivrn-pro/subtitles/subtitle_backend.py";
        worker = std::filesystem::exists(installed) ? installed.string() : WIVRN_SUBTITLE_SOURCE;
    }
    if (!std::filesystem::is_regular_file(worker)) throw std::runtime_error("Subtitle worker is not installed");
    int pair[2];
    if (socketpair(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC | SOCK_NONBLOCK, 0, pair))
        throw std::runtime_error("Cannot create subtitle IPC");
    posix_spawn_file_actions_t actions;
    posix_spawn_file_actions_init(&actions);
    posix_spawn_file_actions_adddup2(&actions, pair[1], STDIN_FILENO);
    if (pair[0] != STDIN_FILENO) posix_spawn_file_actions_addclose(&actions, pair[0]);
    if (pair[1] != STDIN_FILENO) posix_spawn_file_actions_addclose(&actions, pair[1]);
    posix_spawnattr_t attr;
    posix_spawnattr_init(&attr);
    posix_spawnattr_setflags(&attr, POSIX_SPAWN_SETPGROUP);
    posix_spawnattr_setpgroup(&attr, 0);
    char python[] = "python3";
    char * argv[] = {python, worker.data(), nullptr};
    auto error = posix_spawnp(&child, python, &actions, &attr, argv, environ);
    posix_spawn_file_actions_destroy(&actions);
    posix_spawnattr_destroy(&attr);
    close(pair[1]);
    if (error)
    {
        close(pair[0]); child = -1;
        throw std::runtime_error("Cannot start subtitle worker");
    }
    fd = pair[0];
}
subtitle_bridge::~subtitle_bridge()
{
    if (fd >= 0) close(fd);
    if (child <= 0) return;
    kill(-child, SIGTERM);
    for (int i = 0; i < 10; ++i)
    {
        auto result = waitpid(child, nullptr, WNOHANG);
        if (result == child || (result < 0 && errno == ECHILD)) return;
        std::this_thread::sleep_for(std::chrono::milliseconds(20));
    }
    kill(-child, SIGKILL);
    while (waitpid(child, nullptr, 0) < 0 && errno == EINTR) {}
}
std::optional<subtitles::packet> subtitle_bridge::poll(ocr_exchange * images)
{
    std::array<char, 32768> data;
    auto count = recv(fd, data.data(), data.size(), MSG_DONTWAIT | MSG_TRUNC);
    if (count == 0 || (count < 0 && errno != EAGAIN && errno != EWOULDBLOCK && errno != EINTR)) live = false;
    if (count <= 0 || size_t(count) >= data.size()) return {};
    try
    {
        auto json = nlohmann::json::parse(data.data(), data.data() + count);
        if (json.contains("ocr_request"))
        {
            if (images) images->ask(json.at("ocr_request").get<uint64_t>());
            return {};
        }
        subtitles::packet result;
        result.visual = json.value("visual", false);
        if (json.value("text", std::string("not-clear")).empty()) return result;
        if (result.visual)
        {
            result.capture_pose = json.at("capture_pose").get<std::array<float, 7>>();
            result.capture_fov = json.at("capture_fov").get<std::array<float, 4>>();
            result.distance = json.at("distance").get<float>();
        }
        result.duration = json.at("duration").get<float>();
        result.scale = json.at("scale").get<float>();
        result.use_jupiteroid = json.value("use_jupiteroid", true);
        auto now = std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count();
        const auto & lines = json.at("lines");
        if (!lines.is_array() || lines.size() > 16) return {};
        for (const auto & source : lines)
        {
            double age = now - source.at("captured").get<double>();
            if (!std::isfinite(age) || age < 0 || age > 12) continue;
            result.lines.push_back({source.at("text").get<std::string>(), source.at("speaker").get<uint32_t>(),
                source.at("bearing").get<float>(), uint32_t(age * 1000), source.at("spatial").get<bool>()});
            if (result.visual)
            {
                result.lines.back().box = source.at("box").get<std::array<float,4>>();
                result.lines.back().foreground = source.at("foreground").get<std::array<float,3>>();
                result.lines.back().background = source.at("background").get<std::array<float,3>>();
            }
        }
        if (subtitles::valid(result)) return result;
    }
    catch (const std::exception &) {} // Malformed optional subtitles never interrupt VR.
    return {};
}
void subtitle_bridge::send_image(ocr_frame frame)
{
    if (frame.rgba.size() != 1024*1024*4 || frame.metadata.size() > 4096) return;
    int image = memfd_create("wivrn-ocr", MFD_CLOEXEC | MFD_ALLOW_SEALING);
    if (image < 0) return;
    auto count = write(image, frame.rgba.data(), frame.rgba.size());
    if (count == ssize_t(frame.rgba.size()) &&
        fcntl(image, F_ADD_SEALS, F_SEAL_WRITE | F_SEAL_GROW | F_SEAL_SHRINK | F_SEAL_SEAL) == 0)
    {
        iovec io{frame.metadata.data(), frame.metadata.size()};
        alignas(cmsghdr) char control[CMSG_SPACE(sizeof(int))]{};
        msghdr message{};
        message.msg_iov = &io; message.msg_iovlen = 1;
        message.msg_control = control; message.msg_controllen = sizeof(control);
        auto cmsg = CMSG_FIRSTHDR(&message);
        cmsg->cmsg_level = SOL_SOCKET; cmsg->cmsg_type = SCM_RIGHTS; cmsg->cmsg_len = CMSG_LEN(sizeof(int));
        memcpy(CMSG_DATA(cmsg), &image, sizeof(image));
        sendmsg(fd, &message, MSG_DONTWAIT | MSG_NOSIGNAL);
    }
    close(image);
}
}
