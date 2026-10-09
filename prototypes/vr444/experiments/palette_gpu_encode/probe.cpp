// SPDX-License-Identifier: MIT
#include "encoder.hpp"
using namespace palette_encode;
struct Fixture { std::string name;unsigned width,height;std::array<std::vector<uint32_t>,2> pixels;std::array<rgb_palette::Bytes,2> reference; };
void add_generated(std::vector<Fixture>& fixtures,unsigned w,unsigned h,unsigned kind,unsigned q) {
    Fixture f;f.name=std::to_string(w)+"x"+std::to_string(h)+"-pattern"+std::to_string(kind)+"-q"+std::to_string(q);f.width=w;f.height=h;
    for(unsigned eye=0;eye<2;++eye) {
        auto& p=f.pixels[eye];p.resize(size_t(w)*h);uint32_t seed=0x712ab89+eye;
        for(unsigned y=0;y<h;++y)for(unsigned x=0;x<w;++x) {
            seed=seed*1664525u+1013904223u;unsigned value=0;
            if(kind==0)value=eye*0x25678+0x194866;
            if(kind==1)value=seed>>8;
            if(kind>=2&&kind<=4) { auto n=kind==2?2u:kind==3?4u:16u;value=((x+3*y+eye)%n)*0x0f1331; }
            if(kind==5)value=((x%16+(y%16)*16)/7+eye)*0x052913;
            if(kind==6)value=(x*0x293567+y*0x2589+eye*0x76543)&0xffffff;
            if(kind==7)value=((x/16+y/16)%3==0)?0x29588a:(seed&0xffffff);
            p[size_t(y)*w+x]=0xff000000|(value&0xffffff);
        }
        p=vr444::decode(vr444::encode(p,w,h,q));f.reference[eye]=rgb_palette::encode(p,w,h);
    }
    fixtures.push_back(std::move(f));
}
std::vector<uint8_t> read(const std::filesystem::path& path) {
    auto bytes=std::filesystem::file_size(path);require(bytes&&bytes<=16*1024*1024,"fixture size");std::vector<uint8_t> out(bytes);std::ifstream in(path,std::ios::binary);in.read(reinterpret_cast<char*>(out.data()),bytes);require(bool(in),"fixture read");return out;
}
void corpus(std::vector<Fixture>& cases,const std::filesystem::path& path,const std::string& label) {
    std::vector<std::filesystem::path> paths;for(auto& item:std::filesystem::recursive_directory_iterator(path))if(item.is_regular_file()&&item.path().extension()==".rpt")paths.push_back(item.path());
    std::sort(paths.begin(),paths.end());require(paths.size()<=128,"corpus count");size_t total=0;
    for(auto& item:paths) {
        auto wire=read(item);auto decoded=rgb_palette::decode(wire);auto name=item;name.replace_extension(".rgba");auto bytes=read(name);total+=bytes.size()+wire.size();require(total<=128*1024*1024,"corpus bytes");
        require(bytes.size()==decoded.rgba.size()*4&&!std::memcmp(bytes.data(),decoded.rgba.data(),bytes.size()),"independent RGBA mismatch");
        auto canonical=rgb_palette::encode(decoded.rgba,decoded.width,decoded.height);
        cases.push_back({label+"/"+std::filesystem::relative(item,path).string(),decoded.width,decoded.height,{decoded.rgba,decoded.rgba},{canonical,canonical}});
    }
}
void validate_pair(const Fixture& f) { for(auto& eye:f.pixels)validate(eye,f.width,f.height); }
int main(int argc,char** argv) { try {
    require(argc>=3,"usage: palette-encode SHADERS GOLDEN_DIR [--corpus DIR] [--extra-goldens DIR] [--full] [--max-size] [--cpu-only] [--device N] [--reps N]");
    bool full=false,max_size=false,cpu=false;unsigned device=0,reps=1;std::filesystem::path capture,extra;
    for(int i=3;i<argc;++i) {
        std::string arg=argv[i];if(arg=="--full")full=true;else if(arg=="--max-size")max_size=true;else if(arg=="--cpu-only")cpu=true;
        else { require(i+1<argc,"missing argument");std::string value=argv[++i];if(arg=="--corpus")capture=value;else if(arg=="--extra-goldens")extra=value;
            else if(arg=="--device"||arg=="--reps") { size_t end=0;auto n=std::stoul(value,&end);require(end==value.size()&&n<=30,"numeric bound");if(arg=="--device")device=n;else reps=n; }else throw std::runtime_error("unknown option"); }
    }
    require(reps>=1,"repetitions");std::vector<Fixture> cases;
    std::vector<std::pair<unsigned,unsigned>> shapes{{1,1},{1,17},{17,1},{17,19},{31,33},{65,47},{256,2}};if(full)shapes.emplace_back(576,640);
    for(auto [w,h]:shapes)for(unsigned kind=0;kind<8;++kind)for(unsigned q:{1u,8u})add_generated(cases,w,h,kind,q);
    for(auto [w,h]:std::array<std::pair<unsigned,unsigned>,4>{{{4080,16},{4096,16},{17,2049},{4096,17}}})for(unsigned kind:{1u,7u})add_generated(cases,w,h,kind,1);
    if(max_size)for(unsigned kind:{0u,1u})add_generated(cases,2048,2048,kind,1);
    corpus(cases,argv[2],"golden");if(!extra.empty())corpus(cases,extra,"extra-golden");if(!capture.empty())corpus(cases,capture,"captured");
    std::array<size_t,6> modes{};
    for(auto& f:cases) {
        validate_pair(f);for(unsigned eye=0;eye<2;++eye) { require(rgb_palette::decode(f.reference[eye]).rgba==f.pixels[eye],"CPU reference changed pixels");for(auto t:rgb_palette::inspect(f.reference[eye]).tiles)++modes[t.mode]; }
        auto bad=f;bad.pixels[1].back()&=0xffffff;bool rejected=false;try { validate_pair(bad); }catch(const std::exception&) { rejected=true; }require(rejected,"invalid right alpha accepted");
    }
    for(auto n:modes)require(n,"encoder mode coverage");
    if(cpu) { std::cout<<"{\"cpu_fixture_pass\":true,\"fixture_count\":"<<cases.size()<<",\"canonical_modes\":6,\"vulkan_initialized\":false,\"headset_used\":false}\n";return 0; }
    std::map<std::pair<unsigned,unsigned>,std::unique_ptr<Encoder>> sessions;
    std::cout<<"{\"schema\":1,\"experiment\":\"palette-gpu-encode\",\"headset_used\":false,\"performance_claim\":false,\"promotion_blocked\":true,\"warmups\":1,\"repetitions\":"<<reps<<",\"canonical_modes\":6,\"cases\":["<<std::flush;
    unsigned index=0;
    for(const auto& f:cases) {
        auto key=std::make_pair(f.width,f.height);if(!sessions[key])sessions[key]=std::make_unique<Encoder>(f.width,f.height,argv[1],device);auto& e=*sessions[key];
        auto bad=f;bad.pixels[1].back()&=0xffffff;auto before=e.submissions;bool rejected=false;try { validate_pair(bad); }catch(const std::exception&) { rejected=true; }require(rejected&&e.submissions==before,"malformed pair admitted");
        std::cout<<(index?",":"")<<"{\"name\":"<<quote(f.name)<<",\"device\":"<<quote(e.properties.deviceName)<<",\"driver\":"<<e.properties.driverVersion<<",\"device_type\":"<<e.properties.deviceType<<",\"width\":"<<f.width<<",\"height\":"<<f.height<<",\"tiles\":"<<e.tiles<<",\"scan_blocks\":"<<e.blocks<<",\"input_memory_flags\":"<<e.buffer(0).flags<<",\"output_memory_flags\":"<<e.buffer(5).flags<<",\"readback_memory_flags\":"<<e.o.raw[0].flags<<",\"stereo_uploaded_bytes\":"<<size_t(f.width)*f.height*8<<",\"stereo_readback_capacity_bytes\":"<<e.o.raw[0].bytes*2<<",\"stereo_wire_bytes\":"<<f.reference[0].size()+f.reference[1].size()<<",\"iterations\":[";
        for(unsigned rep=0;rep<=reps;++rep) {
            auto start=Clock::now();validate_pair(f);double preflight=elapsed(start);std::array<Result,2> result{e.run(f.pixels[0]),e.run(f.pixels[1])};
            for(unsigned eye=0;eye<2;++eye) { require(result[eye].wire==f.reference[eye],"GPU RPT bytes differ from canonical CPU encoder: "+f.name);require(rgb_palette::decode(result[eye].wire).rgba==f.pixels[eye],"GPU RPT decode changed pixels"); }
            if(!rep)continue;
            std::cout<<(rep==1?"":",")<<"{\"rep\":"<<rep-1<<",\"stereo_preflight_ms\":"<<preflight+result[0].validation_ms+result[1].validation_ms<<",\"stereo_upload_ms\":"<<result[0].upload_ms+result[1].upload_ms<<",\"stereo_gpu_and_full_readback_ms\":"<<result[0].gpu_copy_ms+result[1].gpu_copy_ms<<",\"stereo_submit_wait_ms\":"<<result[0].submit_wait_ms+result[1].submit_wait_ms<<",\"stereo_assembly_crc_ms\":"<<result[0].assembly_crc_ms+result[1].assembly_crc_ms<<",\"canonical_bytes_exact\":true,\"decoded_pixels_exact\":true,\"malformed_pair_rejected_before_submit\":true}";
        }
        std::cout<<"]}"<<std::flush;++index;
    }
    std::cout<<"],\"case_count\":"<<index<<",\"pass\":true,\"scope\":\"Offline canonical RPT1 GPU encoder; host source upload and full-capacity readback; no integration, network, XR or performance promotion\"}\n";return 0;
}catch(const std::exception& e) { std::cerr<<"palette-encode: "<<e.what()<<'\n';return 1; } }
