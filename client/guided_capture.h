// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include "anchor_protocol.h"
#include <algorithm>
#include <array>
#include <cmath>
#include <optional>
#include <string>
#include <utility>

namespace room_capture {
constexpr int marker_count = 12, stage_count = 4, per_stage = 3;
constexpr float pi = 3.14159265358979323846f;
constexpr XrSpaceLocationFlags tracked = XR_SPACE_LOCATION_POSITION_VALID_BIT |
    XR_SPACE_LOCATION_ORIENTATION_VALID_BIT | XR_SPACE_LOCATION_POSITION_TRACKED_BIT |
    XR_SPACE_LOCATION_ORIENTATION_TRACKED_BIT;
inline float distance(const XrVector3f &a, const XrVector3f &b) {
    return std::sqrt((a.x-b.x)*(a.x-b.x)+(a.y-b.y)*(a.y-b.y)+(a.z-b.z)*(a.z-b.z));
}
inline float angle(const XrQuaternionf &a, const XrQuaternionf &b) {
    return 2*std::acos(std::clamp(std::abs(a.x*b.x+a.y*b.y+a.z*b.z+a.w*b.w),0.f,1.f));
}
inline float yaw(const XrQuaternionf &q) {
    return std::atan2(2*(q.x*q.z+q.w*q.y), 1-2*(q.x*q.x+q.y*q.y));
}
inline float wrap(float x) { return std::remainder(x,2*pi); }
inline XrQuaternionf rotation(float y) { return {0,std::sin(y/2),0,std::cos(y/2)}; }
struct observation {
    wivrn::anchor::sample anchor;
    bool saved_file = false, busy = false;
};
enum class operation { create, load, retry };
struct request { operation op; int slot; XrPosef pose; };

// Pure workflow: the render thread executes returned requests and reports samples
// on the next tick. No filesystem, OpenXR dispatch, or UI ownership lives here.
class guide {
public:
    enum class phase { scan, capture, verify, blocked, done };
    phase state = phase::scan;
    int slot = 0, scan_stage = 0;
    bool verifying = false;
    std::array<bool,marker_count> accepted{}, verified{}, skipped{};
    std::array<bool,stage_count> coverage{};
    std::string instruction = "LOOK AROUND THE PLAYSPACE";
    std::string detail = "KEEP PASSTHROUGH VISIBLE";
    float progress = 0;
    int accepted_count() const { return std::count(accepted.begin(),accepted.end(),true); }
    int verified_count() const { return std::count(verified.begin(),verified.end(),true); }
    bool complete() const { return state == phase::done && verified_count() == marker_count; }
    const char *phase_name() const {
        switch(state) {
        case phase::scan:return "scan"; case phase::capture:return "capture";
        case phase::verify:return "verify"; case phase::blocked:return "blocked";
        case phase::done:return complete()?"complete":"partial";
        }
        return "unknown";
    }
    void reference_changed() { *this = guide{}; }
    void suspend() { reset_dwell(); deadline = 0; }
    void rejected() { block("OPERATION NOT AVAILABLE"); }
    std::optional<request> tick(XrTime now, const XrSpaceLocation &head, bool active,
            const std::array<observation,marker_count> &observed, bool retry, bool skip) {
        if (!active || (head.locationFlags & tracked) != tracked) {
            reset_dwell(); deadline = now + timeout;
            instruction = "PAUSED - TRACKING REQUIRED";
            detail = "LOOK AT A WELL LIT PART OF THE ROOM";
            return {};
        }
        if (!initialized) {
            initialized = true; base_yaw = yaw(head.pose.orientation);
        }
        if (!deadline) deadline = now + timeout;
        if (state == phase::done) {
            instruction = complete()?"CAPTURE VERIFIED - 12 OF 12":"CAPTURE FINISHED - PARTIAL";
            detail = "SELECT RETRY TO RECHECK SAVED MARKERS";
            if (!retry) return {};
            verifying = true; verified.fill(false); slot = 0; pending = false;
            state = phase::verify; reset_dwell();
        }
        if (state == phase::scan) {
            direction(head, base_yaw-scan_stage*pi/2);
            if (aligned) instruction = "LOOK AROUND THIS PART OF THE ROOM";
            detail = "SCAN " + std::to_string(scan_stage+1) + " OF 4 - HOLD STEADY";
            if (skip || dwell(head.pose,now,aligned,1'000'000'000)) {
                coverage[scan_stage] = !skip;
                ++scan_stage; reset_dwell();
                if (scan_stage == stage_count) { state = phase::capture; deadline = now+timeout; }
            }
            return {};
        }
        if (skip) {
            skipped[slot] = true; verified[slot] = false;
            advance(now);
            return {};
        }
        while (verifying && slot < marker_count && !accepted[slot]) ++slot;
        if (slot == marker_count) { state = phase::done; return {}; }
        const auto &o = observed[slot];
        const auto &s = o.anchor;
        if (state == phase::blocked) {
            detail = "USE RETRY OR SKIP ON THE PANEL";
            if (!retry) return {};
            if (o.busy) { instruction = "WAIT FOR PENDING OPERATION"; return {}; }
            state = verifying?phase::verify:phase::capture;
            pending = true; deadline = now+timeout; reset_dwell();
            return request{operation::retry,slot,{}};
        }
        float target = base_yaw-(slot/per_stage)*pi/2;
        if ((s.flags & 3) == 3)
            target = std::atan2(-(s.pose.position.x-head.pose.position.x),-(s.pose.position.z-head.pose.position.z));
        direction(head,target);
        detail = std::string(verifying?"VERIFY ":"CAPTURE ") + std::to_string(slot/per_stage+1) +
            "/4 - MARKER " + std::to_string(slot%per_stage+1) + "/3";
        if (!pending) {
            if (o.busy) { instruction = "WAIT FOR PENDING OPERATION"; return {}; }
            if (o.saved_file) {
                pending = true; deadline = now+timeout; reset_dwell();
                return request{operation::load,slot,{}};
            }
            if (verifying) { block("SAVED MARKER FILE MISSING"); return {}; }
            if (s.status == wivrn::anchor::state::unsupported) { block("ANCHOR API UNAVAILABLE"); return {}; }
            if (dwell(head.pose,now,aligned,1'500'000'000)) {
                // Freeze this entire three-marker stage from a stable head pose.
                if (!stage_fixed[slot/per_stage]) {
                    centers[slot/per_stage] = head.pose.position;
                    stage_fixed[slot/per_stage] = true;
                }
                const auto center = centers[slot/per_stage];
                const float side = (slot%per_stage-1)*0.35f;
                XrPosef p{rotation(target),{center.x+side*std::cos(target)-1.2f*std::sin(target),
                    center.y+(slot%per_stage==1?-0.25f:0.f),
                    center.z-side*std::sin(target)-1.2f*std::cos(target)}};
                pending = true; deadline = now+timeout; reset_dwell();
                return request{operation::create,slot,p};
            }
            return {};
        }
        if (s.status == wivrn::anchor::state::error || s.status == wivrn::anchor::state::unsupported) {
            block("ANCHOR ERROR " + std::to_string(s.result)); return {};
        }
        if (s.status == wivrn::anchor::state::empty && !o.busy && !o.saved_file) {
            pending = false; reset_dwell(); return {}; // Retry before creation.
        }
        if (now > deadline) { block("LOCALIZATION TIMED OUT"); return {}; }
        const bool localized = s.status == wivrn::anchor::state::localized && s.persisted && (s.flags&3)==3;
        if (!localized) { reset_dwell(); instruction = "SAVING OR LOCALIZING - LOOK AROUND"; return {}; }
        if (verifying && (distance(s.pose.position,baseline[slot].position)>0.03f ||
                          angle(s.pose.orientation,baseline[slot].orientation)>3*pi/180)) {
            block("RELOAD SHIFT ABOVE 3CM OR 3DEG"); return {};
        }
        if (!anchor_since || distance(s.pose.position,stable_anchor.position)>0.015f ||
                angle(s.pose.orientation,stable_anchor.orientation)>2*pi/180) {
            anchor_since = now; stable_anchor = s.pose;
        }
        if (dwell(head.pose,now,aligned,1'000'000'000) && now-anchor_since>=1'000'000'000) {
            if (verifying) { verified[slot] = true; skipped[slot] = false; }
            else {
                accepted[slot] = true; baseline[slot] = s.pose;
                // Recover the layout basis from a localized saved anchor when
                // resuming a partial capture from a different starting heading.
                if (!basis_from_anchor) {
                    base_yaw = wrap(yaw(s.pose.orientation)+(slot/per_stage)*pi/2);
                    basis_from_anchor = true;
                }
                const float y = yaw(s.pose.orientation), side = (slot%per_stage-1)*0.35f;
                centers[slot/per_stage] = {s.pose.position.x-side*std::cos(y)+1.2f*std::sin(y),
                    s.pose.position.y-(slot%per_stage==1?-0.25f:0.f),
                    s.pose.position.z+side*std::sin(y)+1.2f*std::cos(y)};
                stage_fixed[slot/per_stage] = true;
            }
            advance(now);
        }
        return {};
    }
private:
    static constexpr XrTime timeout = 30'000'000'000;
    bool initialized = false, aligned = false, pending = false, basis_from_anchor = false;
    float base_yaw = 0;
    std::array<XrVector3f,stage_count> centers{};
    std::array<bool,stage_count> stage_fixed{};
    std::array<XrPosef,marker_count> baseline{};
    XrTime stable_since = 0, anchor_since = 0, deadline = 0;
    XrPosef stable_head{}, stable_anchor{};
    void reset_dwell() { stable_since = anchor_since = 0; progress = 0; }
    void block(std::string why) { state = phase::blocked; instruction = std::move(why); reset_dwell(); }
    void advance(XrTime now) {
        ++slot; pending = false; reset_dwell(); deadline = now+timeout;
        if (slot == marker_count) {
            if (!verifying) { verifying = true; slot = 0; state = phase::verify; }
            else state = phase::done;
        } else state = verifying?phase::verify:phase::capture;
    }
    void direction(const XrSpaceLocation &head, float target) {
        float delta = wrap(target-yaw(head.pose.orientation));
        const auto &q = head.pose.orientation;
        float vertical = 2*(q.w*q.x-q.y*q.z);
        aligned = std::abs(delta)<20*pi/180 && std::abs(vertical)<0.57f;
        instruction = aligned?"HOLD STILL - LOOK AT THE MARKERS":
            (std::abs(vertical)>=0.57f?"LOOK LEVEL":(delta>0?"TURN LEFT":"TURN RIGHT"));
        if (!aligned && std::abs(vertical)<0.57f)
            instruction += " " + std::to_string(int(std::round(std::abs(delta)*180/pi))) + " DEG";
    }
    bool dwell(const XrPosef &head, XrTime now, bool allowed, XrTime duration) {
        if (!allowed) { stable_since = 0; progress = 0; return false; }
        if (!stable_since || distance(head.position,stable_head.position)>0.06f ||
                angle(head.orientation,stable_head.orientation)>5*pi/180) {
            stable_since = now; stable_head = head;
        }
        progress = std::clamp(float(now-stable_since)/duration,0.f,1.f);
        return now-stable_since>=duration;
    }
};
} // namespace room_capture
