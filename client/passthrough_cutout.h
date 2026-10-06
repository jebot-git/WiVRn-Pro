// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include "room_calibration.h"
#include <limits>

namespace wivrn::cutout {
using namespace room_calibration;
struct rectangle {
    XrPosef pose{{0,0,0,1},{}};
    float width=0, height=0;
    bool valid() const { return room_calibration::valid(pose) && std::isfinite(width) && std::isfinite(height) && width>=.1f && height>=.1f && width<=3 && height<=3; }
};
struct hand { XrVector3f position{}; float press=0; int source=0; bool valid=false; };
inline XrVector3f cross(XrVector3f a, XrVector3f b) { return {a.y*b.z-a.z*b.y,a.z*b.x-a.x*b.z,a.x*b.y-a.y*b.x}; }
inline float dot(XrVector3f a, XrVector3f b) { return a.x*b.x+a.y*b.y+a.z*b.z; }
// Homogeneous ray-to-rectangle transform. 48 bytes, within Vulkan's minimum
// 128-byte push constant budget together with the existing color parameters.
using projection = std::array<std::array<float,4>,3>;
inline projection project(const rectangle & rect, XrPosef eye, XrFovf fov, bool preview) {
    projection result{};
    if (!rect.valid() || !room_calibration::valid(eye)) return result;
    auto p=compose(inverse(eye),rect.pose);
    auto a=rotate(p.orientation,{rect.width/2,0,0}), b=rotate(p.orientation,{0,rect.height/2,0}), c=p.position;
    auto normal=rotate(p.orientation,{0,0,1});
    if (std::abs(dot(normal,c))<.03f) return result; // eye on/too close to plane
    float determinant=dot(a,cross(b,c));
    if (!std::isfinite(determinant) || std::abs(determinant)<1e-6f) return result;
    std::array rows{cross(b,c),cross(c,a),cross(a,b)};
    float l=std::tan(fov.angleLeft),r=std::tan(fov.angleRight),u=std::tan(fov.angleUp),d=std::tan(fov.angleDown);
    if (!std::isfinite(l+r+u+d) || l>=r || d>=u) return result;
    for (size_t i=0;i<3;++i) {
        // Vulkan NDC y=-1 is the top of the projection image.
        result[i]={rows[i].x*(r-l)/2/determinant,rows[i].y*(d-u)/2/determinant,
            (rows[i].x*(r+l)/2+rows[i].y*(d+u)/2-rows[i].z)/determinant,0};
    }
    result[0][3]=1; result[1][3]=preview?1:0;
    return result;
}

// Reuse the UUID/relative-pose representation, without standing calibration's
// HMD height requirement: a cutout can be designed while seated or kneeling.
inline std::optional<spot> capture_anchor(const spatial::snapshot & s) {
    if (!s.active || !tracked(s.head)) return {};
    const spatial::marker * best=nullptr;
    float distance=std::numeric_limits<float>::max();
    for (const auto & marker:s.markers) {
        if (!marker.persisted || !tracked(marker.pose) ||
            std::none_of(marker.uuid.begin(),marker.uuid.end(),[](auto b){return b!=0;})) continue;
        auto a=marker.pose.pose.position,b=s.head.pose.position;
        float d=(a.x-b.x)*(a.x-b.x)+(a.y-b.y)*(a.y-b.y)+(a.z-b.z)*(a.z-b.z);
        if (d<distance) {best=&marker; distance=d;}
    }
    if (!best) return {};
    spot result; result.uuid=best->uuid; return result;
}

class editor {
    std::filesystem::path file;
    std::optional<spot> anchor;
    std::optional<rectangle> saved, draft;
    std::optional<XrPosef> plane;
    bool armed=false;
    XrTime pressed_since=0, last_time=0;
    uint64_t generation=0;
    std::array<int,2> sources{};
    void reset_gesture() { armed=false; pressed_since=0; plane.reset(); draft.reset(); sources={}; }
public:
    bool designing=false, enabled=true;
    std::string message;
    explicit editor(std::filesystem::path path = {}) : file(std::move(path)) { load(); }
    bool has_saved() const { return saved.has_value(); }
    bool available(const spatial::snapshot & s) const { return capture_anchor(s).has_value(); }
    void begin(const spatial::snapshot & s) {
        if (!available(s)) { message="Localize a saved room marker before designing a cutout."; return; }
        designing=true; generation=s.generation; reset_gesture();
        message="Release both triggers/pinches. Pull your hands to opposite diagonal corners, then hold both triggers or pinches to confirm.";
    }
    void cancel() { designing=false; reset_gesture(); }
    std::optional<rectangle> locate(const spatial::snapshot & s) const {
        if (!s.active || !tracked(s.head)) return {};
        if (designing) return draft;
        if (!enabled || !saved || !anchor) return {};
        auto pose=anchor->locate(s);
        if (!pose) return {};
        return rectangle{*pose,saved->width,saved->height};
    }
    void update(const spatial::snapshot & s, std::array<hand,2> hands, XrTime now) {
        if (!designing) return;
        if (!s.active || !tracked(s.head) || s.generation!=generation) {
            cancel(); message="Design cancelled: room tracking changed."; return;
        }
        if (now<=last_time || (last_time && now-last_time>250'000'000)) reset_gesture();
        last_time=now;
        for (const auto & h:hands) if (!h.valid || !room_calibration::valid({{0,0,0,1},h.position}) || !std::isfinite(h.press)) {
            reset_gesture(); message="Both controllers or both hands must be tracked. Release to resume."; return;
        }
        std::array current_sources{hands[0].source,hands[1].source};
        if (sources!=current_sources) { reset_gesture(); sources=current_sources; }
        if (!plane) {
            auto forward=rotate(s.head.pose.orientation,{0,0,-1});
            if (std::hypot(forward.x,forward.z)<.2f) return;
            float yaw=std::atan2(-forward.x,-forward.z);
            auto a=hands[0].position,b=hands[1].position;
            plane=XrPosef{{0,std::sin(yaw/2),0,std::cos(yaw/2)},{(a.x+b.x)/2,(a.y+b.y)/2,(a.z+b.z)/2}};
        }
        auto a=compose(inverse(*plane),{{0,0,0,1},hands[0].position}).position;
        auto b=compose(inverse(*plane),{{0,0,0,1},hands[1].position}).position;
        rectangle next{compose(*plane,{{0,0,0,1},{(a.x+b.x)/2,(a.y+b.y)/2,(a.z+b.z)/2}}),std::abs(a.x-b.x),std::abs(a.y-b.y)};
        draft=next.valid()?std::optional(next):std::nullopt;
        if (hands[0].press<.25f && hands[1].press<.25f) armed=true;
        if (!draft) { pressed_since=0; message="Separate diagonal corners: width and height must each be 10 cm to 3 m."; return; }
        message="Preview: hold BOTH triggers or BOTH pinches to save. Cancel keeps the previous cutout.";
        if (!armed || hands[0].press<.75f || hands[1].press<.75f) { pressed_since=0; return; }
        if (!pressed_since) pressed_since=now;
        if (now-pressed_since<200'000'000) return;
        auto binding=capture_anchor(s);
        if (!binding) { reset_gesture(); message="No saved room marker is tracked."; return; }
        // Bind the rectangle to the selected persisted UUID.
        for (const auto & marker:s.markers) if (marker.uuid==binding->uuid) {
            binding->relative=compose(inverse(marker.pose.pose),draft->pose); break;
        }
        auto old_anchor=anchor; auto old_saved=saved; bool old_enabled=enabled;
        anchor=binding; saved=draft; enabled=true;
        if (save()) { cancel(); message="Cutout saved and anchored to the room."; }
        else { anchor=old_anchor; saved=old_saved; enabled=old_enabled; armed=false; pressed_since=0; message="Could not save cutout. Release both inputs before retrying."; }
    }
    bool save() const {
        if (!saved || !anchor || !saved->valid() || !anchor->valid() || file.empty()) return false;
        auto tmp=file; tmp+=".tmp";
        std::ofstream f(tmp,std::ios::trunc);
        f << "wivrn-cutout-v1\n";
        for (auto b:anchor->uuid) f << unsigned(b) << ' ';
        auto q=anchor->relative.orientation; auto p=anchor->relative.position;
        f << std::setprecision(9) << '\n' << q.x << ' ' << q.y << ' ' << q.z << ' ' << q.w << ' ' << p.x << ' ' << p.y << ' ' << p.z << ' ' << saved->width << ' ' << saved->height << ' ' << enabled << '\n';
        f.close(); if (!f) return false;
        std::error_code ec; std::filesystem::rename(tmp,file,ec); return !ec;
    }
    void erase() {
        std::error_code ec; std::filesystem::remove(file,ec);
        if (ec) { message="Could not delete the saved cutout."; return; }
        cancel(); saved.reset(); anchor.reset(); message="Cutout deleted.";
    }
private:
    void load() {
        std::error_code ec;
        if (file.empty() || std::filesystem::file_size(file,ec)>1024 || ec) return;
        std::ifstream f(file); std::string version; f>>version;
        if (version!="wivrn-cutout-v1") return;
        spot binding; rectangle rect;
        for (auto & b:binding.uuid) { unsigned n; if (!(f>>n) || n>255) return; b=n; }
        auto & q=binding.relative.orientation; auto & p=binding.relative.position;
        bool active;
        if (!(f>>q.x>>q.y>>q.z>>q.w>>p.x>>p.y>>p.z>>rect.width>>rect.height>>active)) return;
        f>>std::ws;
        if (f.eof() && binding.valid() && rect.valid()) { anchor=binding; saved=rect; enabled=active; }
    }
};
} // namespace wivrn::cutout
