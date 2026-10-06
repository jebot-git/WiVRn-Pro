// SPDX-License-Identifier: GPL-3.0-or-later
#include "subtitle_overlay.h"
#include "ocr_overlay.h"
#include "wivrn_serialization.h"
#include "wivrn_packets.h"
#include <cassert>
#include <iostream>
#include <limits>

int main()
{
    using namespace wivrn::subtitles;
    packet p{{{"Hello", 1, -.5f, 1000, true}, {"Good day", 7, .5f, 1000, true}}, 5, 1};
    wivrn::serialization_packet encoded;
    encoded.serialize(wivrn::to_headset::packets(p));
    std::vector<uint8_t> bytes;
    for (auto span : static_cast<std::vector<std::span<uint8_t>> &>(encoded)) bytes.insert(bytes.end(),span.begin(),span.end());
    wivrn::deserialization_packet reader({},bytes);
    auto result = std::get<packet>(reader.deserialize<wivrn::to_headset::packets>());
    assert(reader.empty() && result.lines.size() == 2 && result.lines[1].speaker == 7);
    assert(valid(result));
    result.scale = std::numeric_limits<float>::quiet_NaN(); assert(!valid(result));
    result = p; result.lines[0].bearing = std::numeric_limits<float>::infinity(); assert(!valid(result));
    result = p; result.lines[0].age_ms = 12001; assert(!valid(result));
    result = p; result.lines[0].text = std::string("a\0b", 3); assert(!valid(result));
    result = p; result.lines.resize(17); assert(!valid(result));
    overlay o;
    for (int64_t at = 1'000'000'000; at <= 3'000'000'000; at += 50'000'000)
        o.pose_at(at, {at < 2'500'000'000 ? 0.f : 1.f,1.6f,0}, {1,0,0,0});
    o.receive(p, 3'000'000'000, 3'000'000'000);
    assert(o.visible(3'000'000'000));
    assert(o.captions[0].spatial && o.captions[0].position.x < 0);
    assert(o.captions[1].position.x > 0);
    assert(o.captions[0].colour != o.captions[1].colour);
    const auto position = o.captions[0].position;
    o.pose_at(3'100'000'000, {10,1.6f,0}, glm::angleAxis(1.f,glm::vec3(0,1,0)));
    assert(o.captions[0].position == position); // source anchored to capture time, not inference time
    assert(!o.visible(9'000'000'000));
    o.receive({},3'100'000'000,3'100'000'000); assert(!o.visible(3'100'000'000));
    p.lines[0].spatial = false;
    o.receive(p,3'100'000'000,3'100'000'000);
    assert(!o.captions[0].spatial);
    o.clear(); assert(!o.visible(3'100'000'000));
    o.receive(p,3'100'000'000,3'100'000'000); assert(!o.visible(3'100'000'000)); // no old-frame reappearance
    o.pose_at(4'000'000'000,{0,1.6,0},{1,0,0,0});
    o.enabled = false; o.receive(p,4'000'000'000,4'000'000'000); assert(!o.visible(4'000'000'000));
    packet visual;
    visual.visual = true; visual.capture_pose = {0,1.6f,0,0,0,0,1}; visual.capture_fov = {-.8f,.8f,.8f,-.8f};
    line sign; sign.text = "Exit"; sign.box = {.6f,.1f,.2f,.1f}; sign.foreground={1,.8f,.2f}; sign.background={.1f,.2f,.3f};
    visual.lines = {sign}; assert(valid(visual));
    scene_overlay scene;
    scene.pose_at({0,1.6f,0},{1,0,0,0}); scene.receive(visual,5'000'000'000,5'000'000'000);
    assert(scene.visible(5'000'000'000));
    assert(scene.regions[0].position.x > 0 && scene.regions[0].position.y > 1.6f);
    assert(scene.regions[0].foreground == sign.foreground && scene.regions[0].background == sign.background);
    scene.pose_at({0,1.6f,0},glm::angleAxis(.5f,glm::vec3(0,1,0))); assert(!scene.visible(5'100'000'000));
    scene.pose_at({0,1.6f,0},{1,0,0,0}); assert(!scene.visible(5'100'000'000));
    scene.receive(visual,5'000'000'000,5'000'000'000); assert(!scene.visible(11'000'000'000));
    scene.enabled=false; scene.receive(visual,5'000'000'000,5'000'000'000); assert(!scene.visible(5'000'000'000));
    auto bad = visual; bad.lines[0].box[0]=.99f; assert(!valid(bad));
    bad=visual; bad.lines[0].background[0]=std::numeric_limits<float>::quiet_NaN(); assert(!valid(bad));
    bad=visual; bad.capture_pose[6]=0; assert(!valid(bad));
    bad=visual; bad.capture_fov[0]=2; assert(!valid(bad));
    bad=visual; bad.lines.resize(5); assert(!valid(bad));
    wivrn::serialization_packet visual_wire; visual_wire.serialize(wivrn::to_headset::packets(visual));
    bytes.clear();
    for (auto span : static_cast<std::vector<std::span<uint8_t>> &>(visual_wire)) bytes.insert(bytes.end(),span.begin(),span.end());
    wivrn::deserialization_packet visual_reader({},bytes);
    auto roundtrip=std::get<packet>(visual_reader.deserialize<wivrn::to_headset::packets>());
    assert(valid(roundtrip) && roundtrip.lines[0].foreground == sign.foreground && roundtrip.visual);
    std::cout << "Subtitle wire, validation, capture-time placement, colours, expiry and disable passed\n";
}
