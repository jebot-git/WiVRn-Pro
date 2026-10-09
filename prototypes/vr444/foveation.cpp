// SPDX-License-Identifier: MIT
#include "foveation.hpp"
#include <algorithm>
#include <cmath>
#include <iostream>
#include <stdexcept>
namespace vr444 {
static void need(bool b,const char* reason){if(!b)throw std::runtime_error(reason);}
static bool finite(double x){return std::isfinite(x);}
static bool valid(Gaze g,double confidence){return g.valid && finite(g.yaw) && finite(g.pitch) && finite(g.confidence) && g.confidence>=confidence && g.confidence<=1 && std::abs(g.yaw)<89 && std::abs(g.pitch)<89;}
static void check(Footprint p){need(finite(p.left)&&finite(p.right)&&finite(p.bottom)&&finite(p.top)&&p.left<=p.right&&p.bottom<=p.top&&p.left>-89&&p.right<89&&p.bottom>-89&&p.top<89,"invalid angular footprint");}
static double radians=3.141592653589793/180;
static double source_coordinate(double output,double a,double b,double density){
    double weighted=output*(1+(density-1)*(b-a));
    return std::clamp(weighted<a?weighted:(weighted<a+density*(b-a)?a+(weighted-a)/density:weighted-(density-1)*(b-a)),0.0,1.0);
}
static void check_warp(const SpatialWarp& p){
    check(p.fov);need(p.fov.left<p.fov.right&&p.fov.bottom<p.fov.top&&finite(p.density)&&p.density>=1&&p.density<=32,"invalid spatial warp");
    need(finite(p.x0)&&finite(p.x1)&&finite(p.y0)&&finite(p.y1)&&p.x0>=0&&p.x1<=1&&p.x0<=p.x1&&p.y0>=0&&p.y1<=1&&p.y0<=p.y1,"invalid warp bounds");
}
SpatialWarp spatial_warp(Footprint fov,double yaw,double pitch,double radius,double density){
    check(fov);need(fov.left<fov.right&&fov.bottom<fov.top&&finite(yaw)&&finite(pitch)&&finite(radius)&&radius>0&&radius<45,"invalid warp gaze");
    auto normalized=[](double angle,double lo,double hi){return std::clamp((std::tan(std::clamp(angle,-88.9,88.9)*radians)-std::tan(lo*radians))/(std::tan(hi*radians)-std::tan(lo*radians)),0.0,1.0);};
    SpatialWarp out{fov,normalized(yaw-radius,fov.left,fov.right),normalized(yaw+radius,fov.left,fov.right),
                    1-normalized(pitch+radius,fov.bottom,fov.top),1-normalized(pitch-radius,fov.bottom,fov.top),density};
    check_warp(out);return out;
}
std::vector<Footprint> warped_footprints(unsigned w,unsigned h,const SpatialWarp& p){
    need(w&&h&&w<=4096&&h<=4096,"invalid footprint size");check_warp(p);
    auto angle=[](double t,double a,double b){return std::atan((1-t)*std::tan(a*radians)+t*std::tan(b*radians))/radians;};
    std::vector<Footprint> out;
    for(unsigned y=0;y<h;y+=16)for(unsigned x=0;x<w;x+=16){
        double l=source_coordinate(double(x)/w,p.x0,p.x1,p.density),r=source_coordinate(double(std::min(x+16,w))/w,p.x0,p.x1,p.density);
        double t=source_coordinate(double(y)/h,p.y0,p.y1,p.density),b=source_coordinate(double(std::min(y+16,h))/h,p.y0,p.y1,p.density);
        out.push_back({angle(l,p.fov.left,p.fov.right),angle(r,p.fov.left,p.fov.right),angle(b,p.fov.top,p.fov.bottom),angle(t,p.fov.top,p.fov.bottom)});
    }return out;
}
std::vector<uint32_t> warp_rgb(const std::vector<uint32_t>& src,unsigned sw,unsigned sh,unsigned w,unsigned h,const SpatialWarp& p){
    check_warp(p);need(sw&&sh&&w&&h&&sw<=4096&&sh<=4096&&w<=4096&&h<=4096&&src.size()==size_t(sw)*sh,"invalid warp image");
    std::vector<uint32_t> out(size_t(w)*h);
    for(unsigned y=0;y<h;y++)for(unsigned x=0;x<w;x++){
        double sx=std::clamp(source_coordinate((x+.5)/w,p.x0,p.x1,p.density)*sw-.5,0.0,double(sw-1));
        double sy=std::clamp(source_coordinate((y+.5)/h,p.y0,p.y1,p.density)*sh-.5,0.0,double(sh-1));
        unsigned ix=unsigned(sx),iy=unsigned(sy);double fx=sx-ix,fy=sy-iy;uint32_t pixel=0xff000000;
        for(unsigned c=0;c<3;c++){
            auto sample=[&](unsigned xx,unsigned yy){return double((src[size_t(yy)*sw+xx]>>(8*c))&255);};
            double a=sample(ix,iy)*(1-fx)+sample(std::min(ix+1,sw-1),iy)*fx;
            double b=sample(ix,std::min(iy+1,sh-1))*(1-fx)+sample(std::min(ix+1,sw-1),std::min(iy+1,sh-1))*fx;
            pixel|=uint32_t(std::lround(a*(1-fy)+b*fy))<<(8*c);
        }out[size_t(y)*w+x]=pixel;
    }return out;
}
std::vector<Footprint> rectilinear_footprints(unsigned w,unsigned h,Footprint fov){
    need(w && h && w<=4096 && h<=4096,"invalid footprint dimensions");check(fov);
    constexpr double radians=3.141592653589793/180;
    auto angle=[&](double a,double b,double t){return std::atan(std::tan(a*radians)*(1-t)+std::tan(b*radians)*t)/radians;};
    std::vector<Footprint> out;
    for(unsigned y=0;y<h;y+=16)for(unsigned x=0;x<w;x+=16)
        out.push_back({angle(fov.left,fov.right,double(x)/w),angle(fov.left,fov.right,double(std::min(x+16,w))/w),
                       angle(fov.top,fov.bottom,double(std::min(y+16,h))/h),angle(fov.top,fov.bottom,double(y)/h)});
    return out;
}
QualityMap gaze_map(const std::vector<Footprint>& footprints,Gaze gaze,Gaze previous,int64_t display_ns,const FoveationConfig& c){
    need(!footprints.empty(),"empty footprints");
    for(auto p:footprints)check(p);
    for(double x:{c.core_deg,c.guard_deg,c.transition_deg,c.confidence_min,c.stale_ms,c.predict_max_ms,c.velocity_max,c.prediction_max_deg})need(finite(x)&&x>=0,"invalid foveation config");
    need(c.core_deg>0&&c.transition_deg>0&&c.confidence_min<=1&&c.stale_ms<=1000&&c.predict_max_ms<=c.stale_ms,"invalid foveation limits");
    for(unsigned q:{c.core_q,c.transition_q,c.peripheral_q,c.fallback_q})need(q>=1&&q<=1024,"invalid foveation q");
    need(c.core_q<=c.transition_q&&c.transition_q<=c.peripheral_q,"nonmonotonic quality config");
    double age_ms=(double(display_ns)-double(gaze.time_ns))/1e6;
    bool fallback=!valid(gaze,c.confidence_min)||age_ms<0||age_ms>c.stale_ms;
    QualityMap out{{},{},fallback,0,0,c.core_deg+c.guard_deg};
    if(fallback){out.q.assign(footprints.size(),c.fallback_q);out.priority.assign(footprints.size(),0);return out;}
    double dx=0,dy=0,dt=(double(gaze.time_ns)-double(previous.time_ns))/1e9;
    if(valid(previous,c.confidence_min)&&dt>=.001&&dt<=.05){
        dx=(gaze.yaw-previous.yaw)/dt;dy=(gaze.pitch-previous.pitch)/dt;
        double speed=std::hypot(dx,dy);if(speed>c.velocity_max){dx*=c.velocity_max/speed;dy*=c.velocity_max/speed;}
        double horizon=std::min(age_ms,c.predict_max_ms)/1000;dx*=horizon;dy*=horizon;
        double distance=std::hypot(dx,dy);if(distance>c.prediction_max_deg){dx*=c.prediction_max_deg/distance;dy*=c.prediction_max_deg/distance;}
    }
    out.predicted_yaw=gaze.yaw+dx;out.predicted_pitch=gaze.pitch+dy;
    // Protect the whole current-to-predicted bounding segment, including tile
    // footprint intersections. This overprotects corners rather than leaving holes.
    double left=std::min(gaze.yaw,out.predicted_yaw),right=std::max(gaze.yaw,out.predicted_yaw);
    double bottom=std::min(gaze.pitch,out.predicted_pitch),top=std::max(gaze.pitch,out.predicted_pitch);
    for(auto p:footprints){
        double ex=std::max({left-p.right,p.left-right,0.0}),ey=std::max({bottom-p.top,p.bottom-top,0.0});
        double d=std::hypot(ex,ey),t=(d-out.protected_radius)/c.transition_deg;
        if(t<=0){out.q.push_back(c.core_q);out.priority.push_back(0);}
        else if(t<1){out.q.push_back(unsigned(std::lround(c.core_q+(c.transition_q-c.core_q)*t)));out.priority.push_back(1);}
        else{out.q.push_back(c.peripheral_q);out.priority.push_back(2);}
    }
    return out;
}
DynamicFrame prepare_foveated(const std::vector<uint32_t>& source,unsigned sw,unsigned sh,unsigned w,unsigned h,Footprint fov,Gaze gaze,Gaze previous,int64_t display_ns,const FoveationConfig& config){
    auto prediction=gaze_map(rectilinear_footprints(w,h,fov),gaze,previous,display_ns,config);
    auto warp=prediction.fallback?spatial_warp(fov,0,0,8,1):spatial_warp(fov,prediction.predicted_yaw,prediction.predicted_pitch);
    auto quality=gaze_map(warped_footprints(w,h,warp),gaze,previous,display_ns,config);
    return {warp_rgb(source,sw,sh,w,h,warp),std::move(quality),warp};
}
size_t modeled_wire_bytes(const Frame& f){size_t n=payload_bytes(f);return n+((n+1167)/1168)*60;}
BudgetResult encode_budgeted(const std::vector<uint32_t>& rgba,unsigned w,unsigned h,const QualityMap& map,size_t budget){
    need(map.q.size()==map.priority.size()&&map.q.size()==size_t((w+15)/16)*((h+15)/16),"invalid map size");
    for(auto p:map.priority)need(p<=2,"invalid priority");
    auto q=map.q;Frame best=encode(rgba,w,h,1,q);size_t best_size=modeled_wire_bytes(best);unsigned passes=1;
    if(best_size<=budget)return {std::move(best),true,passes,best_size};
    for(unsigned region:{2u,1u})for(unsigned step=0;step<10;step++){
        bool changed=false;
        for(size_t i=0;i<q.size();i++)if(map.priority[i]==region&&q[i]<1024){q[i]=std::min(1024u,q[i]*2);changed=true;}
        if(!changed)break;
        Frame trial=encode(rgba,w,h,1,q);passes++;size_t bytes=modeled_wire_bytes(trial);
        if(bytes<best_size){best=std::move(trial);best_size=bytes;}
        if(best_size<=budget)return {std::move(best),true,passes,best_size};
    }
    return {std::move(best),false,passes,best_size};
}
double protected_psnr(const std::vector<uint32_t>& a,const std::vector<uint32_t>& b,unsigned w,const QualityMap& map){
    need(w&&a.size()==b.size(),"invalid quality image");double sum=0,n=0;unsigned nx=(w+15)/16;
    for(size_t i=0;i<a.size();i++)if(map.priority.at((i/w/16)*nx+(i%w/16))==0)
        for(unsigned c=0;c<3;c++){double d=int((a[i]>>(8*c))&255)-int((b[i]>>(8*c))&255);sum+=d*d;n++;}
    return n ? (sum?10*std::log10(65025*n/sum):999) : 0;
}
void foveation_tests(){
    auto identity=spatial_warp({-45,45,-45,45},0,0,8,1);auto test_image=scene(64,64,2);
    need(warp_rgb(test_image,64,64,64,64,identity)==test_image,"identity spatial warp changed pixels");
    auto warped=warped_footprints(128,128,spatial_warp({-45,45,-45,45},20,0));
    need(std::abs(warped.front().left+45)<1e-9&&std::abs(warped.back().right-45)<1e-9,"warp lost FOV");
    for(unsigned i=1;i<8;i++)need(std::abs(warped[i-1].right-warped[i].left)<1e-9,"warp discontinuity");
    auto footprints=rectilinear_footprints(128,128,{-45,45,-45,45});
    Gaze g{0,0,.95,100000000,true},previous{-2,0,.95,90000000,true};
    auto map=gaze_map(footprints,g,previous,120000000);
    auto invalid_frame=prepare_foveated(test_image,64,64,32,32,{-45,45,-45,45},{0,0,0,100000000,false},previous,120000000);
    need(invalid_frame.quality.fallback&&invalid_frame.warp.density==1,"invalid gaze spatial fallback");
    need(!map.fallback&&std::abs(map.predicted_yaw-4)<1e-8,"gaze prediction");
    auto left=gaze_map(footprints,{-30,0,1,100000000,true},previous,100000000);
    auto right=gaze_map(footprints,{30,0,1,100000000,true},previous,100000000);
    need(left.q!=right.q,"gaze map does not move");
    for(auto bad:{Gaze{0,0,.1,100000000,true},Gaze{0,0,1,100000000,false},Gaze{0,0,1,0,true},Gaze{0,0,1,200000000,true}}){
        auto fallback=gaze_map(footprints,bad,previous,120000000);need(fallback.fallback,"invalid gaze accepted");
        need(std::all_of(fallback.q.begin(),fallback.q.end(),[](auto q){return q==16;}),"fallback not uniform");
    }
    auto image=scene(128,128,1);auto f=encode(image,128,128,1,map.q);auto wire=pack(f);auto restored=unpack(wire);
    need(restored.tile_q==map.q && pack(restored)==wire,"v2 map serialization");decode(restored);
    auto bounded=encode_budgeted(image,128,128,map,modeled_wire_bytes(f)*3/4);
    for(size_t i=0;i<map.q.size();i++)if(map.priority[i]==0)need(bounded.frame.tile_q[i]==map.q[i],"protected region degraded");
    if(bounded.fits)need(bounded.wire_bytes<=modeled_wire_bytes(f)*3/4,"budget violated");
    need(!encode_budgeted(image,128,128,map,1).fits,"impossible budget accepted");
    auto lossless=encode(image,128,128,1,std::vector<uint32_t>(64,1));need(decode(unpack(pack(lossless)))==image,"v2 lossless");
    for(size_t n=0;n<wire.size();n++){bool rejected=false;try{decode(unpack({wire.begin(),wire.begin()+n}));}catch(const std::runtime_error&){rejected=true;}need(rejected,"v2 truncation accepted");}
    auto invalid=wire;invalid[26]=invalid[27]=0;bool rejected=false;try{unpack(invalid);}catch(const std::runtime_error&){rejected=true;}need(rejected,"zero tile quantizer accepted");
    std::cout<<"PASS: dynamic gaze, prediction, stale/invalid fallback, v2 framing, protected quality, budget refusal\n";
}
void foveation_bench(){
    unsigned w=576,h=640;auto source=scene(w*4,h*4,1);
    std::cout<<"core_q,gaze_yaw,target_stereo_mbps,candidate_stereo_mbps,protected_tiles,protected_rgb_psnr,whole_rgb_psnr,passes,fits\n";
    for(unsigned core_q:{4u,8u,16u})for(double yaw:{-25.0,0.0,25.0})for(unsigned mbps:{50u,100u,150u}){
        FoveationConfig config;config.core_q=core_q;
        auto prepared=prepare_foveated(source,w*4,h*4,w,h,{-45,45,-45,45},{yaw,0,.95,100000000,true},{yaw-1,0,.95,90000000,true},120000000,config);
        const auto& image=prepared.pixels;const auto& map=prepared.quality;
        size_t budget=size_t(mbps)*1000000/(8*90*2);auto result=encode_budgeted(image,w,h,map,budget);auto decoded=decode(result.frame);
        std::cout<<core_q<<','<<yaw<<','<<mbps<<','<<result.wire_bytes*8*90*2/1e6<<','<<std::count(map.priority.begin(),map.priority.end(),0)<<','
                 <<protected_psnr(image,decoded,w,map)<<','<<psnr(image,decoded)<<','<<result.passes<<','<<result.fits<<'\n';
    }
}
}
