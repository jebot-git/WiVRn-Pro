// SPDX-License-Identifier: GPL-3.0-or-later
#include "passthrough_cutout.h"
#include <cassert>
#include <iostream>
#include <unistd.h>
using namespace wivrn;
using namespace room_calibration;

static bool inside(cutout::projection p,float x,float y) {
    float u=p[0][0]*x+p[0][1]*y+p[0][2],v=p[1][0]*x+p[1][1]*y+p[1][2],w=p[2][0]*x+p[2][1]*y+p[2][2];
    return p[0][3]!=0 && w>1e-5f && std::abs(u/w)<1 && std::abs(v/w)<1;
}
static void check_placement_controls(spatial::snapshot s) {
    auto file=std::filesystem::temp_directory_path()/("wivrn-cutout-placement-"+std::to_string(getpid()));
    for (int source : {1,2}) { // Controllers and tracked-hand pinches share geometry.
        std::filesystem::remove(file);
        cutout::editor e(file);
        e.begin(s);
        XrTime now=1'000'000'000;
        std::array<cutout::hand,2> hands{};
        XrPosef pose{{0,0,0,1},{0,1.5f,-1}};
        auto tick=[&] {
            hands[0].position=compose(pose,{{0,0,0,1},{-.4f,-.3f,0}}).position;
            hands[1].position=compose(pose,{{0,0,0,1},{.4f,.3f,0}}).position;
            for (auto & h:hands) { h.valid=true; h.source=source; }
            now+=20'000'000; e.update(s,hands,now);
        };
        auto check=[&](const cutout::rectangle & rect) {
            assert(std::abs(rect.width-.8f)<1e-5 && std::abs(rect.height-.6f)<1e-5);
            auto p=rect.pose.position,q=pose.position;
            assert(std::abs(p.x-q.x)<1e-5 && std::abs(p.y-q.y)<1e-5 && std::abs(p.z-q.z)<1e-5);
            auto right=rotate(rect.pose.orientation,{1,0,0}),expected=rotate(pose.orientation,{1,0,0});
            assert(cutout::dot(right,expected)>.99999f);
            assert(rotate(rect.pose.orientation,{0,1,0}).y>.99999f);
        };
        tick(); assert(e.locate(s)); check(*e.locate(s));
        // Pan left/right, vertically and in depth without changing dimensions.
        pose.position={.7f,.9f,-1.8f}; tick(); check(*e.locate(s));
        // Rotate beyond edge-on to the initial view without width collapsing.
        for (float yaw : {.4f,.8f,1.2f,1.6f,2.f}) {
            pose.orientation={0,std::sin(yaw/2),0,std::cos(yaw/2)};
            tick(); check(*e.locate(s));
        }
        // Looking away must not rotate a design already placed with the hands.
        s.head.pose.orientation={0,1,0,0}; tick(); check(*e.locate(s));
        // Moving diagonally and rotating at the same time preserves the rectangle.
        pose.position={-1.1f,1.8f,-.6f};
        pose.orientation={0,std::sin(1.2f),0,std::cos(1.2f)}; tick(); check(*e.locate(s));
        std::swap(hands[0].position,hands[1].position);
        now+=20'000'000; e.update(s,hands,now); check(*e.locate(s));
        hands[0].press=hands[1].press=1;
        for (int i=0;i<12;++i) tick();
        assert(e.has_saved() && !e.designing); check(*e.locate(s));
        cutout::editor loaded(file); assert(loaded.locate(s)); check(*loaded.locate(s));
        // An almost vertical hand pair has no usable width and cannot confirm.
        e.begin(s); hands[0].press=hands[1].press=0; tick();
        hands[1].position.x=hands[0].position.x;
        hands[1].position.z=hands[0].position.z;
        hands[0].press=hands[1].press=1;
        for (int i=0;i<20;++i) { now+=20'000'000; e.update(s,hands,now); }
        assert(e.designing && !e.locate(s));
        e.cancel(); check(*e.locate(s));
        s.head.pose.orientation={0,0,0,1};
    }
    std::filesystem::remove(file);
}
static void check_tilt_controls(spatial::snapshot s) {
    auto file=std::filesystem::temp_directory_path()/("wivrn-cutout-tilt-"+std::to_string(getpid()));
    auto check=[](const cutout::rectangle & a,const cutout::rectangle & b) {
        assert(std::abs(a.width-b.width)<1e-4 && std::abs(a.height-b.height)<1e-4);
        auto p=a.pose.position,q=b.pose.position;
        assert(std::abs(p.x-q.x)<1e-4 && std::abs(p.y-q.y)<1e-4 && std::abs(p.z-q.z)<1e-4);
        for (auto axis : {XrVector3f{1,0,0},XrVector3f{0,1,0},XrVector3f{0,0,1}})
            assert(cutout::dot(rotate(a.pose.orientation,axis),rotate(b.pose.orientation,axis))>.99999f);
    };
    for (int source : {1,2}) for (size_t holder : {0u,1u}) {
        std::filesystem::remove(file);
        cutout::editor e(file);
        std::array<cutout::hand,2> hands{{{{-.4f,1.2f,-1},0,source,true},{{.4f,1.8f,-1},0,source,true}}};
        XrTime now=1'000'000'000;
        auto tick=[&] { now+=20'000'000; e.update(s,hands,now); };
        e.begin(s); tick(); assert(e.locate(s));
        auto original=*e.locate(s);
        hands[holder].press=1; tick(); assert(e.manipulating()); check(*e.locate(s),original);
        auto pivot=hands[holder].position;
        // Tilt forward, backwards, nearly horizontal, and roll, with both
        // controller grips and tracked wrists, using either hand as the pivot.
        cutout::rectangle expected;
        for (float pitch : {.6f,-.7f,1.5707963f}) {
            auto orientation=multiply(XrQuaternionf{std::sin(pitch/2),0,0,std::cos(pitch/2)},
                XrQuaternionf{0,0,std::sin(.3f),std::cos(.3f)});
            hands[holder].orientation=orientation;
            hands[holder].position={pivot.x+.2f,pivot.y+.1f,pivot.z-.3f};
            auto motion=compose(XrPosef{orientation,hands[holder].position},inverse({{0,0,0,1},pivot}));
            expected={compose(motion,original.pose),original.width,original.height};
            tick(); check(*e.locate(s),expected);
        }
        // Release with the other hand unmoved: no resize or pose jump.
        hands[holder].press=0; tick(); assert(!e.manipulating()); check(*e.locate(s),expected);
        for (int i=0;i<10;++i) tick();
        check(*e.locate(s),expected);
        // Two-hand pan remains available in the tilted frame.
        for (auto & h:hands) { h.position.x+=.1f; h.position.z-=.2f; }
        expected.pose.position.x+=.1f; expected.pose.position.z-=.2f;
        tick(); check(*e.locate(s),expected);
        // Resize along the tilted window's width axis, not world horizontal.
        auto right=rotate(expected.pose.orientation,{1,0,0});
        for (size_t i=0;i<2;++i) {
            float sign=i==0?-.1f:.1f;
            hands[i].position.x+=sign*right.x;
            hands[i].position.y+=sign*right.y;
            hands[i].position.z+=sign*right.z;
        }
        expected.width+=.2f; tick(); check(*e.locate(s),expected);
        // Pick it up again and confirm directly from the tilt gesture.
        hands[holder].press=1; tick(); check(*e.locate(s),expected);
        hands[1-holder].press=1;
        for (int i=0;i<12;++i) tick();
        assert(e.has_saved() && !e.designing); check(*e.locate(s),expected);
        cutout::editor loaded(file); assert(loaded.locate(s)); check(*loaded.locate(s),expected);
        // Bad orientation cannot leave a grabbed preview or confirm a save.
        e.begin(s); hands[0].press=hands[1].press=0; tick();
        hands[holder].press=1; tick(); assert(e.manipulating());
        hands[holder].orientation.x=std::numeric_limits<float>::quiet_NaN(); tick();
        assert(!e.manipulating() && !e.locate(s));
        e.cancel(); check(*e.locate(s),expected);
    }
    std::filesystem::remove(file);
}
int main() {
    spatial::snapshot s;
    s.active=true; s.generation=1;
    s.head={{{0,0,0,1},{0,1.7f,0}},15};
    s.markers[0].uuid[0]=42; s.markers[0].persisted=true;
    s.markers[0].pose={{{0,0,0,1},{2,1,-2}},15};
    check_placement_controls(s);
    check_tilt_controls(s);
    auto file=std::filesystem::temp_directory_path()/("wivrn-cutout-test-"+std::to_string(getpid()));
    std::filesystem::remove(file);
    cutout::editor e(file);
    assert(!e.has_saved());
    s.head.pose.position.y=.3f; assert(e.available(s)); s.head.pose.position.y=1.7f;
    e.begin(s); assert(e.designing);
    std::array<cutout::hand,2> hands{{{{-.4f,1.2f,-1},1,1,true},{{.4f,1.8f,-1},1,1,true}}};
    XrTime time=1'000'000'000;
    auto tick=[&] { time+=20'000'000; e.update(s,hands,time); };
    for (int i=0;i<20;++i) tick();
    assert(!e.has_saved()); // opening-menu press cannot immediately confirm
    hands[0].press=hands[1].press=0; tick();
    assert(e.locate(s));
    hands[0].press=1;
    for (int i=0;i<20;++i) tick();
    assert(!e.has_saved()); // both inputs required
    hands[1].press=1;
    for (int i=0;i<12;++i) tick();
    assert(e.has_saved() && !e.designing);
    auto saved=e.locate(s); assert(saved);
    assert(std::abs(saved->width-.8f)<1e-5 && std::abs(saved->height-.6f)<1e-5);
    cutout::editor loaded(file); assert(loaded.has_saved() && loaded.locate(s));
    // Cancelling a redesign keeps the original and doesn't persist the preview.
    e.begin(s); hands[0].position.x=-.8f; hands[0].press=hands[1].press=0; tick(); e.cancel();
    assert(std::abs(e.locate(s)->width-.8f)<1e-5);
    // Valid-but-untracked anchors and reference transitions cannot leave stale holes.
    s.markers[0].pose.flags=3; assert(!e.locate(s));
    s.markers[0].pose.flags=15;
    s.markers[0].uuid[0]=43; assert(!e.locate(s)); s.markers[0].uuid[0]=42;
    XrPosef transform{{0,std::sin(.4f),0,std::cos(.4f)},{-2,0,5}};
    s.head.pose=compose(transform,s.head.pose); s.markers[0].pose.pose=compose(transform,s.markers[0].pose.pose);
    auto moved=e.locate(s); auto expected=compose(transform,saved->pose);
    assert(moved && std::abs(moved->pose.position.x-expected.position.x)<1e-5);
    e.begin(s); ++s.generation; tick(); assert(!e.designing);
    e.begin(s); hands[0].valid=false; tick(); assert(!e.locate(s)); e.cancel();
    s.active=false; assert(!e.locate(s)); s.active=true;
    e.enabled=false; assert(e.save()); assert(!cutout::editor(file).locate(s));
    e.erase(); assert(!e.has_saved() && !std::filesystem::exists(file));
    // Input-source changes and tracking gaps require a fresh release, including
    // hand pinches. A missing hand cannot complete a queued confirmation.
    e.begin(s); hands[0].valid=true; hands[0].press=hands[1].press=0; tick();
    hands[0].source=hands[1].source=2; hands[0].press=hands[1].press=1;
    for(int i=0;i<20;++i) tick();
    assert(!e.has_saved());
    hands[0].press=hands[1].press=0; tick();
    hands[0].press=hands[1].press=1; tick();
    hands[1].valid=false; tick(); hands[1].valid=true;
    for(int i=0;i<20;++i) tick();
    assert(!e.has_saved());
    e.cancel();
    // Corrupt persisted records are ignored.

    auto corrupt=file; {std::ofstream f(corrupt); f<<"wivrn-cutout-v1\n999\n";}
    assert(!cutout::editor(corrupt).has_saved()); std::filesystem::remove(corrupt);

    // The exact shader transform: stereo parallax, vertical convention, recenter,
    // behind-eye rejection, eye crossing and asymmetric FOVs.
    cutout::rectangle rect{{{0,0,0,1},{0,.25f,-1}},.5f,.5f};
    XrPosef eye{{0,0,0,1},{}}; XrFovf fov{-.78539816f,.78539816f,.78539816f,-.78539816f};
    auto mask=cutout::project(rect,eye,fov,false);
    assert(inside(mask,0,-.25f)); assert(!inside(mask,0,.25f)); assert(!inside(mask,.4f,-.25f));
    eye.position.x=-.032f;
    auto left=cutout::project(rect,eye,fov,true);
    eye.position.x=.032f;
    auto right=cutout::project(rect,eye,fov,true);
    assert(inside(left,.27f,-.25f)); assert(!inside(right,.27f,-.25f));
    assert(inside(right,-.27f,-.25f)); assert(!inside(left,-.27f,-.25f));
    auto before=cutout::project(rect,eye,fov,false);
    rect.pose=compose(transform,rect.pose); eye=compose(transform,eye);
    auto after=cutout::project(rect,eye,fov,false);
    for(size_t i=0;i<3;++i)for(size_t j=0;j<4;++j)assert(std::abs(before[i][j]-after[i][j])<1e-4);
    rect.pose={{0,0,0,1},{0,0,1}}; eye={{0,0,0,1},{}};
    assert(!inside(cutout::project(rect,eye,fov,false),0,0));
    rect.pose.position.z=0; assert(cutout::project(rect,eye,fov,false)[0][3]==0);
    rect.pose.position={0,0,-1}; fov.angleLeft=-.4f;
    auto asymmetric=cutout::project(rect,eye,fov,false);
    float ndc=-(std::tan(fov.angleRight)+std::tan(fov.angleLeft))/(std::tan(fov.angleRight)-std::tan(fov.angleLeft));
    assert(inside(asymmetric,ndc,0));
    // Near-plane crossing: off-axis ray intersection, no projection divide by z.
    rect.pose={{0,std::sin(.65f),0,std::cos(.65f)},{.3f,0,-.05f}};
    assert(cutout::project(rect,eye,fov,false)[0][3]==1);
    // Tilted/rolled masks still intersect the actual rectangle in each eye.
    rect={{multiply(XrQuaternionf{std::sin(.5f),0,0,std::cos(.5f)},
        XrQuaternionf{0,0,std::sin(.35f),std::cos(.35f)}),{0,0,-1}},.8f,.6f};
    fov={-.78539816f,.78539816f,.78539816f,-.78539816f};
    for (float eye_x : {-.032f,.032f}) {
        eye={{0,0,0,1},{eye_x,0,0}};
        auto projection=cutout::project(rect,eye,fov,false);
        for (float x : {0.f,.3f,.5f}) {
            auto point=compose(compose(inverse(eye),rect.pose),{{0,0,0,1},{x,.1f,0}}).position;
            assert(inside(projection,point.x/-point.z,point.y/point.z)==(x<.4f));
        }
    }
    rect.width=std::numeric_limits<float>::quiet_NaN(); assert(cutout::project(rect,eye,fov,false)[0][3]==0);
    std::cout<<"Cutout geometry, stereo masks, gestures, persistence and tracking-loss checks passed\n";
}
