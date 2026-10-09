// SPDX-License-Identifier: GPL-3.0-or-later
#include "staged_rowblocks_image.h"
#include "../direct_image_gpu/gpu_oracle.hpp"
#include <functional>
#include <optional>
using namespace direct_image;
namespace staged = wivrn::staged_rowblocks;
unsigned checks = 0;
void expect(bool value, const char* message) { require(value, message); ++checks; }
void rejects(const std::function<void()>& call) {
    bool caught = false; try { call(); } catch (const std::exception&) { caught = true; }
    expect(caught, "negative control accepted");
}
struct Fixture {
    unsigned width, height;
    std::array<std::vector<uint32_t>, 2> rgba;
    std::array<std::vector<uint8_t>, 2> residual;
    std::array<std::vector<staged::Block>, 2> blocks;
    Fixture(unsigned w, unsigned h, unsigned seed): width(w), height(h) {
        for (unsigned eye = 0; eye < 2; ++eye) {
            auto& pixels = rgba[eye]; pixels.resize(size_t(w) * h);
            for (unsigned y = 0; y < h; ++y) for (unsigned x = 0; x < w; ++x) {
                // Independent full-resolution RGB spatial/channel/eye oracle;
                // no VR444 quantization or codec implementation in this source.
                unsigned r=(x*37+y*17+eye*53+seed*19)&255;
                unsigned g=(x*3+y*251+eye*139+seed*41)&255;
                unsigned b=(x*131+y*13+eye*97+seed*7)&255;
                pixels[size_t(y)*w+x]=r|(g<<8)|(b<<16)|0xff000000u;
            }
            auto& rgb = residual[eye]; rgb.resize(size_t(w)*h*3);
            for (unsigned row=0; row<h;) {
                auto rows=std::min(h-row,eye?16u:32u);
                unsigned mode=(unsigned(blocks[eye].size())+seed+eye)%3;
                blocks[eye].push_back({row,rows,mode,row*w*3});
                for(unsigned y=row;y<row+rows;++y)for(unsigned x=0;x<w;++x)for(unsigned c=0;c<3;++c) {
                    unsigned now=(pixels[size_t(y)*w+x]>>(8*c))&255;
                    unsigned prev=mode==2&&y>row?((pixels[size_t(y-1)*w+x]>>(8*c))&255):0;
                    rgb[(size_t(y)*w+x)*3+c]=uint8_t(now-prev);
                }
                row+=rows;
            }
        }
    }
    staged::Input input(unsigned eye) const { return {width,height,residual[eye],blocks[eye]}; }
};
void cpu_tests() {
    for (auto [w,h] : std::array<std::pair<unsigned,unsigned>,7>{{{1,1},{1,129},{17,33},{129,65},{4096,1},{1,4096},{576,640}}}) {
        Fixture f(w,h,2); staged::validate(f.input(0)); staged::validate(f.input(1)); checks+=2;
        auto in=f.input(0); in.width=0; rejects([&]{staged::validate(in);});
        in=f.input(0); in.rgb=in.rgb.first(in.rgb.size()-1); rejects([&]{staged::validate(in);});
        auto bad=f.blocks[0]; bad.back().mode=3; in=f.input(0); in.blocks=bad; rejects([&]{staged::validate(in);});
        bad=f.blocks[0]; bad.back().raw_offset+=1; in.blocks=bad; rejects([&]{staged::validate(in);});
        bad=f.blocks[0]; bad.back().height+=1; in.blocks=bad; rejects([&]{staged::validate(in);});
        bad=f.blocks[0]; bad.front().row=1; in.blocks=bad; rejects([&]{staged::validate(in);});
        in=f.input(0); in.blocks={}; rejects([&]{staged::validate(in);});
    }
    staged::Lease empty; rejects([&]{empty.image();}); rejects([&]{empty.view();}); rejects([&]{empty.identity();});
}
std::vector<uint32_t> spirv(const std::filesystem::path& path) {
    auto n=std::filesystem::file_size(path); require(n&&n%4==0&&n<=1024*1024,"SPIR-V length");
    std::vector<uint32_t> data(n/4); std::ifstream file(path,std::ios::binary);
    file.read(reinterpret_cast<char*>(data.data()),n); require(bool(file),"SPIR-V read"); return data;
}
using Pair=std::array<staged::Lease,2>;
using Samples=std::array<std::vector<float>,2>;
struct Oracle {
    Session s;
    Oracle(unsigned w,unsigned h,const std::filesystem::path& shaders,unsigned device):s(w,h,shaders,device,false,false) {
        require(s.properties.deviceType==VK_PHYSICAL_DEVICE_TYPE_INTEGRATED_GPU||s.properties.deviceType==VK_PHYSICAL_DEVICE_TYPE_DISCRETE_GPU,"hardware GPU required");
        vkDestroySampler(s.o.device,s.o.sampler,nullptr);s.o.sampler=VK_NULL_HANDLE;
        VkSamplerCreateInfo si{VK_STRUCTURE_TYPE_SAMPLER_CREATE_INFO};si.magFilter=si.minFilter=VK_FILTER_LINEAR;
        si.mipmapMode=VK_SAMPLER_MIPMAP_MODE_NEAREST;si.addressModeU=si.addressModeV=si.addressModeW=VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE;
        check(vkCreateSampler(s.o.device,&si,nullptr,&s.o.sampler));
    }
    void baseline(const Fixture& f) {
        for(unsigned eye=0;eye<2;++eye) {
            require(f.rgba[eye].size()*4<=s.o.inputs[eye][0].bytes,"reference staging capacity");
            std::memcpy(s.o.inputs[eye][0].mapped,f.rgba[eye].data(),f.rgba[eye].size()*4);
        }
        s.poison(0);s.begin();
        s.memory_barrier(VK_ACCESS_HOST_WRITE_BIT,VK_ACCESS_TRANSFER_READ_BIT,VK_PIPELINE_STAGE_HOST_BIT,VK_PIPELINE_STAGE_TRANSFER_BIT);
        for(unsigned eye=0;eye<2;++eye) {
            auto& image=s.o.images[0][eye];auto region=s.region();
            vkCmdCopyBufferToImage(s.command,s.o.inputs[eye][0].handle,image.handle,image.layout,1,&region);
            s.barrier(image,staged::Lease::layout,VK_ACCESS_TRANSFER_WRITE_BIT,VK_ACCESS_SHADER_READ_BIT,VK_PIPELINE_STAGE_TRANSFER_BIT,VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT);
        }
        s.submit();
    }
    Samples consume(const Fixture& f,const Pair* pair,unsigned mode,bool omit_right=false) {
        for(unsigned eye=0;eye<2;++eye) {
            auto view=pair?(*pair)[eye].view():s.o.images[0][eye].sample;
            s.write_image(s.sample_sets[0][eye],0,view,VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER,staged::Lease::layout);
            std::memset(s.o.raw[eye].mapped,0x5a,s.o.raw[eye].bytes);
        }
        s.begin();
        for(unsigned eye=0;eye<2;++eye)vkCmdFillBuffer(s.command,s.o.linear[eye].handle,0,VK_WHOLE_SIZE,0x7fc00000);
        s.memory_barrier(VK_ACCESS_TRANSFER_WRITE_BIT|VK_ACCESS_HOST_WRITE_BIT,VK_ACCESS_SHADER_WRITE_BIT|VK_ACCESS_TRANSFER_WRITE_BIT,
                         VK_PIPELINE_STAGE_TRANSFER_BIT|VK_PIPELINE_STAGE_HOST_BIT,VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT|VK_PIPELINE_STAGE_TRANSFER_BIT);
        for(unsigned eye=0;eye<2;++eye) {
            s.bind(modes,s.sample_sets[0][eye]);uint32_t params[]{s.width,s.height,mode,0};
            vkCmdPushConstants(s.command,s.o.pipelines[modes].layout,VK_SHADER_STAGE_COMPUTE_BIT,0,sizeof(params),params);
            if(!omit_right||eye==0)vkCmdDispatch(s.command,(s.width*s.height+63)/64,1,1);
            Image borrowed{};borrowed.handle=pair?(*pair)[eye].image():s.o.images[0][eye].handle;borrowed.layout=staged::Lease::layout;
            s.barrier(borrowed,VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL,VK_ACCESS_SHADER_READ_BIT,VK_ACCESS_TRANSFER_READ_BIT,VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,VK_PIPELINE_STAGE_TRANSFER_BIT);
            auto region=s.region();vkCmdCopyImageToBuffer(s.command,borrowed.handle,borrowed.layout,s.o.raw[eye].handle,1,&region);
            s.barrier(borrowed,staged::Lease::layout,VK_ACCESS_TRANSFER_READ_BIT,VK_ACCESS_SHADER_READ_BIT,VK_PIPELINE_STAGE_TRANSFER_BIT,VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT|VK_PIPELINE_STAGE_FRAGMENT_SHADER_BIT);
        }
        s.memory_barrier(VK_ACCESS_TRANSFER_WRITE_BIT|VK_ACCESS_SHADER_WRITE_BIT,VK_ACCESS_HOST_READ_BIT,VK_PIPELINE_STAGE_TRANSFER_BIT|VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,VK_PIPELINE_STAGE_HOST_BIT);
        s.submit(); // Consumer lease remains held through actual fence observation.
        Samples samples;
        for(unsigned eye=0;eye<2;++eye) {
            expect(std::memcmp(s.o.raw[eye].mapped,f.rgba[eye].data(),s.o.raw[eye].bytes)==0,"actual image byte mismatch");
            auto begin=static_cast<float*>(s.o.linear[eye].mapped);samples[eye].assign(begin,begin+s.width*s.height*4);
            if(omit_right&&eye==1)continue;
            for(size_t p=0;p<f.rgba[eye].size();++p)for(unsigned c=0;c<4;++c) {
                float value=samples[eye][p*4+c];require(std::isfinite(value)&&value>=0&&value<=1,"sample range");
                if(c==3)require(value==1,"opaque alpha changed");
                if(mode==0&&c<3) {
                    double encoded=value<=.0031308?value*12.92:1.055*std::pow(value,1/2.4)-.055;
                    require(std::abs(encoded*255-double((f.rgba[eye][p]>>(8*c))&255))<.5,"sample EOTF roundtrip");
                }
            }
        }
        return samples;
    }
};
void equal(const Samples& a,const Samples& b) {
    for(unsigned eye=0;eye<2;++eye)expect(a[eye].size()==b[eye].size()&&!std::memcmp(a[eye].data(),b[eye].data(),a[eye].size()*4),"first consumer differs from CPU reference");
}
void gpu_tests(const std::filesystem::path& shaders,unsigned device,bool full) {
    auto code=spirv(shaders/"staged.spv");std::vector<std::pair<unsigned,unsigned>> shapes{{1,1},{1,129},{17,33},{129,65},{4095,3},{4096,1},{1,4096}};
    if(full)shapes.emplace_back(576,640);
    for(auto [w,h]:shapes) {
        Oracle oracle(w,h,shaders,device);auto& s=oracle.s;
        std::cout<<"{\"device\":"<<quote(s.properties.deviceName)<<",\"device_type\":"<<s.properties.deviceType<<",\"vendor_id\":"<<s.properties.vendorID<<",\"device_id\":"<<s.properties.deviceID<<",\"driver\":"<<s.properties.driverVersion<<",\"api_version\":"<<s.properties.apiVersion<<",\"queue_family\":"<<s.family<<",\"width\":"<<w<<",\"height\":"<<h<<",\"unorm_storage_srgb_linear_supported\":true}\n";
        std::array<std::unique_ptr<staged::Pool>,2> pools;
        for(unsigned eye=0;eye<2;++eye)pools[eye]=std::make_unique<staged::Pool>(s.physical,s.o.device,s.family,w,h,11,eye,code,2);
        auto produce=[&](uint64_t frame,const Fixture& f) {
            // Independent complete stereo preflight before either prepare.
            staged::validate(f.input(0));staged::validate(f.input(1));
            std::array<std::optional<staged::Pending>,2> pending{pools[0]->prepare(frame,f.input(0)),pools[1]->prepare(frame,f.input(1))};
            expect(bool(pending[0])&&bool(pending[1]),"unexpected pool pressure");
            rejects([&]{pending[0]->publish(0);}); // Cannot expose unsubmitted image.
            for(auto& p:pending)p->submit(s.queue);
            rejects([&]{pending[0]->submit(s.queue);});
            Pair pair{pending[0]->publish(10'000'000'000),pending[1]->publish(10'000'000'000)};
            for(unsigned eye=0;eye<2;++eye)expect(pair[eye].identity()==staged::Identity{11,frame,eye},"generation/frame/eye identity");
            rejects([&]{pending[0]->publish(0);});
            return pair;
        };
        Fixture first(w,h,1),second(w,h,2),third(w,h,3);
        auto a=produce(1,first);auto image=a[0].image();Pair retained=a;
        auto b=produce(2,second);expect(image!=b[0].image(),"live lease overwritten");
        expect(!pools[0]->prepare(3,third.input(0)),"third frame overwrote retained lease");
        a={};expect(!pools[0]->prepare(3,third.input(0)),"consumer copy not retained");
        for(unsigned mode=0;mode<3;++mode) {
            oracle.baseline(first);auto baseline=oracle.consume(first,nullptr,mode);
            equal(oracle.consume(first,&retained,mode),baseline);
            rejects([&]{equal(oracle.consume(first,&retained,mode,true),baseline);});
            oracle.baseline(second);baseline=oracle.consume(second,nullptr,mode);
            equal(oracle.consume(second,&b,mode),baseline);
        }
        rejects([&]{pools[0]->prepare(2,second.input(0));});
        auto malformed=third.blocks[1];malformed.back().mode=3;auto bad=third.input(1);bad.blocks=malformed;
        rejects([&]{staged::validate(third.input(0));staged::validate(bad);});
        retained={};auto c=produce(3,third);expect(c[0].image()==image,"retired slot not reused");
        b={};
        {
            auto abandoned=pools[0]->prepare(4,first.input(0));expect(bool(abandoned),"unsubmitted reservation");
            expect(!pools[0]->prepare(5,first.input(0)),"recorded job not retained");
        }
        // Abandoning an unsubmitted command releases its slot; it never exposes
        // a view. The reserved identity remains consumed, so stale retry fails.
        rejects([&]{pools[0]->prepare(4,first.input(0));});
        auto abandoned=pools[0]->prepare(5,first.input(0));expect(bool(abandoned),"cancelled private slot unavailable");abandoned.reset();
        // Decoder replacement may drop pool ownership while renderer handles
        // still own the image+pipeline; test actual sampling after that point.
        pools={};
        for(unsigned mode=0;mode<3;++mode) {oracle.baseline(third);equal(oracle.consume(third,&c,mode),oracle.consume(third,nullptr,mode));}
        auto wrong=c;std::swap(wrong[0],wrong[1]);rejects([&]{oracle.consume(third,&wrong,0);});
    }
}
int main(int argc,char** argv) {try {
    cpu_tests();bool cpu=false,full=false;unsigned device=0;std::filesystem::path shaders;
    for(int i=1;i<argc;++i) {std::string arg=argv[i];if(arg=="--cpu-only")cpu=true;else if(arg=="--full")full=true;
        else if(arg=="--device"){require(++i<argc,"device value");size_t end=0;auto value=std::stoul(argv[i],&end);require(end==std::strlen(argv[i])&&value<16,"device bound");device=value;}
        else {require(shaders.empty(),"extra shader path");shaders=arg;}}
    if(!cpu){require(!shaders.empty(),"shader path required");gpu_tests(shaders,device,full);}
    std::cout<<"{\"pass\":true,\"checks\":"<<checks<<",\"gpu_used\":"<<(cpu?"false":"true")<<",\"headset_used\":false,\"performance_claim\":false,\"native_renderer_executed\":false}\n";
    return 0;
}catch(const std::exception& e){std::cerr<<"staged-image-test: "<<e.what()<<'\n';return 1;}}
