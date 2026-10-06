// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include "subtitle_protocol.h"
#include "ocr_exchange.h"
#include <optional>
#include <string>
#include <sys/types.h>

namespace wivrn
{
// Private child IPC, polled by the session network thread. No audio callbacks,
// API calls or blocking inference on WiVRn threads.
class subtitle_bridge
{
    int fd = -1;
    pid_t child = -1;
    bool live = true;
public:
    explicit subtitle_bridge(std::string worker = {});
    ~subtitle_bridge();
    subtitle_bridge(const subtitle_bridge &) = delete;
    subtitle_bridge & operator=(const subtitle_bridge &) = delete;
    std::optional<subtitles::packet> poll(ocr_exchange * images = nullptr);
    void send_image(ocr_frame frame);
    bool running() const { return live; }
};
}
