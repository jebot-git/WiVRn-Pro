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
int main() {
    spatial::snapshot s;
    s.active=true; s.generation=1;
    s.head={{{0,0,0,1},{0,1.7f,0}},15};
    s.markers[0].uuid[0]=42; s.markers[0].persisted=true;
    s.markers[0].pose={{{0,0,0,1},{2,1,-2}},15};
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
    rect.width=std::numeric_limits<float>::quiet_NaN(); assert(cutout::project(rect,eye,fov,false)[0][3]==0);
    std::cout<<"Cutout geometry, stereo masks, gestures, persistence and tracking-loss checks passed\n";
}
