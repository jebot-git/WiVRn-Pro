// SPDX-License-Identifier: GPL-3.0-or-later
#include "guided_capture.h"
#include "capture_hud.h"
#include <cassert>
#include <iostream>
#include <fstream>
#include <source_location>
using namespace room_capture;
using wivrn::anchor::state;
static int checks=0;
static void check(bool ok, std::source_location at=std::source_location::current()){if(!ok)std::cerr<<at.line()<<" check failed\n";assert(ok);++checks;}
struct simulator {
    guide capture;
    XrTime now=1;
    XrSpaceLocation head{XR_TYPE_SPACE_LOCATION};
    std::array<observation,marker_count> samples{};
    int creates=0,loads=0;
    simulator(){head.locationFlags=tracked;head.pose={{0,0,0,1},{0,1.6f,0}};for(auto &s:samples)s.anchor.status=state::empty;}
    std::optional<request> tick(bool active=true,bool retry=false,bool skip=false) {
        now+=100'000'000;
        return capture.tick(now,head,active,samples,retry,skip);
    }
    void aim(int sector){head.pose.orientation=rotation(-sector*pi/2);}
    void scan() {
        for(int i=0;i<100&&capture.state==guide::phase::scan;++i){aim(capture.scan_stage);check(!tick());}
        check(capture.state==guide::phase::capture);
        check(std::count(capture.coverage.begin(),capture.coverage.end(),true)==4);
    }
    void execute(const request &r) {
        auto &s=samples[r.slot];
        if(r.op==operation::create){++creates;s.anchor.pose=r.pose;s.saved_file=true;}
        if(r.op==operation::load)++loads;
        s.anchor.status=state::localized;s.anchor.flags=tracked;s.anchor.persisted=true;s.busy=false;
    }
    void finish() {
        for(int i=0;i<1000&&capture.state!=guide::phase::done;++i){
            aim(std::min(capture.slot/3,3));
            if(auto r=tick())execute(*r);
        }
        check(capture.state==guide::phase::done);
    }
};
int main(){
    {
        simulator s;s.scan();s.finish();check(s.creates==12&&s.loads==12);
        check(s.capture.complete()&&s.capture.verified_count()==12);
        for(int i=0;i<12;++i)for(int j=i+1;j<12;++j)
            check(distance(s.samples[i].anchor.pose.position,s.samples[j].anchor.pose.position)>0.3f);
        for(int i=0;i<10;++i)check(!s.tick()); // No automatic replacement/recreation.
        if(auto r=s.tick(true,true))s.execute(*r);
        s.finish();check(s.creates==12&&s.loads==24);
    }
    {
        simulator s;s.scan();s.aim(0);
        for(int i=0;i<10;++i)check(!s.tick());
        s.head.locationFlags=3;for(int i=0;i<20;++i)check(!s.tick());
        s.head.locationFlags=tracked;
        for(int i=0;i<10;++i)check(!s.tick()); // Invalid tracking reset the dwell.
        for(int i=0;i<20;++i)check(!s.tick(false));
        for(int i=0;i<10;++i)check(!s.tick()); // Pausing cannot finish a dwell.
        s.aim(1);for(int i=0;i<20;++i)check(!s.tick()); // Wrong direction.
        s.aim(0);for(int i=0;i<40;++i){s.head.pose.position.x=(i%2)*0.1f;check(!s.tick());}
    }
    {
        simulator s;s.scan();s.aim(0);std::optional<request> r;
        for(int i=0;i<30&&!r;++i)r=s.tick();
        check(r&&r->op==operation::create);
        s.samples[0].anchor.status=state::error;s.samples[0].anchor.result=XR_ERROR_RUNTIME_FAILURE;
        s.tick();check(s.capture.state==guide::phase::blocked);
        s.samples[0].busy=true;check(!s.tick(true,true)); // Pending operation stays owned.
        s.samples[0].busy=false;r=s.tick(true,true);check(r&&r->op==operation::retry);
        s.samples[0].anchor.status=state::empty;s.tick();
        for(int i=0;i<30;++i)if(auto created=s.tick()){s.execute(*created);break;}
        s.finish();check(s.capture.complete());
    }
    {
        simulator s;s.scan();s.tick(true,false,true); // Skip one without marking it verified.
        s.finish();check(!s.capture.complete());check(s.capture.verified_count()==11);
        check(s.capture.skipped[0]&&s.creates==11&&s.loads==11);
    }
    {
        simulator s;s.scan();s.aim(0);std::optional<request> r;
        for(int i=0;i<30&&!r;++i)r=s.tick();
        check(bool(r));
        s.samples[0].busy=true;s.samples[0].anchor.status=state::saving;
        for(int i=0;i<310;++i)s.tick();
        check(s.capture.state==guide::phase::blocked);
        check(!s.tick(true,true));s.tick(true,false,true);check(s.capture.slot==1);
        // The late completion is retained, but isn't silently accepted after skip.
        s.execute(*r);check(!s.capture.accepted[0]);
    }
    {
        simulator s;s.scan();bool shifted=false;
        for(int i=0;i<1000&&s.capture.state!=guide::phase::blocked;++i){
            s.aim(std::min(s.capture.slot/3,3));
            if(auto r=s.tick()){
                s.execute(*r);
                if(r->op==operation::load&&s.capture.verifying){s.samples[r->slot].anchor.pose.position.x+=0.1f;shifted=true;}
            }
        }
        check(shifted&&s.capture.state==guide::phase::blocked);check(s.capture.verified_count()==0);
        s.capture.reference_changed();check(s.capture.state==guide::phase::scan);
        check(s.capture.accepted_count()==0&&s.capture.verified_count()==0);
    }
    {
        simulator first;first.scan();first.finish();
        simulator restarted;restarted.samples=first.samples;restarted.scan();restarted.finish();
        check(restarted.creates==0&&restarted.loads==24&&restarted.capture.complete());
    }
    {
        simulator first;first.scan();first.finish();
        simulator resumed;
        for(int i=0;i<5;++i)resumed.samples[i]=first.samples[i];
        for(int i=0;i<100&&resumed.capture.state==guide::phase::scan;++i){
            resumed.head.pose.orientation=rotation(pi/2-resumed.capture.scan_stage*pi/2);
            check(!resumed.tick());
        }
        resumed.finish();check(resumed.creates==7&&resumed.capture.complete());
        for(int i=0;i<12;++i)check(distance(resumed.samples[i].anchor.pose.position,first.samples[i].anchor.pose.position)<0.001f);
    }
    auto pixels=hud_pixels({"ROOM CAPTURE - FOUR STAGES","TURN RIGHT 90 DEG","CAPTURE 2/4 - MARKER 1/3",
        "SAVED 3/12  VERIFIED 0/12","X RETRY OR RECHECK - A SKIP","KEEP THE AREA CLEAR - TURN IN PLACE"},0.65f);
    check(pixels.size()==hud_width*hud_height*4);
    std::ofstream image("/tmp/spatial-capture-hud.ppm",std::ios::binary);
    image<<"P6\n"<<hud_width<<" "<<hud_height<<"\n255\n";
    for(int y=0;y<hud_height;++y)for(int x=0;x<hud_width;++x)
        image.write(reinterpret_cast<const char *>(&pixels[((hud_height-1-y)*hud_width+x)*4]),3);
    std::cout<<"GUIDED_CAPTURE "<<checks<<" checks passed\n";
}
