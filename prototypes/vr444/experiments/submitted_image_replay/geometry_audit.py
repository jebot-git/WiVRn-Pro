#!/usr/bin/env python3
"""Compare replay vertex bytes to the actual source-extracted native geometry loop."""
import argparse,hashlib,json,subprocess,tempfile
from pathlib import Path
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[3]
SOURCE=ROOT/'validation/wivrn-codecs/integration/source-overlay/client/scenes/stream_defoveator.cpp'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def block(text,start):
    brace=text.index('{',start);depth=0
    for i in range(brace,len(text)):
        depth+=(text[i]=='{')-(text[i]=='}')
        if not depth:return text[start:i+1]
    raise ValueError('unclosed native block')
def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--glm-root',type=Path,required=True);a=p.parse_args()
    raw=SOURCE.read_bytes();text=raw.decode();start=text.index('for (size_t view = 0;',text.index('void stream_defoveator::defoveate'));loop=block(text,start)
    required=block(text,text.index('static size_t required_vertices'))
    count=block(text,text.index('static uint16_t count_pixels'))
    harness=r'''
#ifdef NATIVE_GEOMETRY
#include <vector>
#include <array>
#include <cstring>
#include <cstdint>
#define VULKAN_HPP_NO_STRUCT_CONSTRUCTORS
#include <vulkan/vulkan.hpp>
#include <glm/glm.hpp>
#include <cassert>
namespace wivrn::to_headset { struct foveation_parameter {std::vector<uint16_t> x,y;}; }
namespace utils {template<class T> auto enumerate_range(const T& values){std::vector<std::pair<size_t,typename T::value_type>> out;for(size_t i=0;i<values.size();++i)out.emplace_back(i,values[i]);return out;} }
REQUIRED
COUNT
struct Native {
 struct vertex {alignas(8)glm::vec2 position;alignas(8)glm::uvec2 uv;};
 static_assert(sizeof(vertex)==16);
 std::array<std::vector<vertex>,2> data;
 static constexpr size_t view_count=2;
 struct Extent {int32_t width,height;};
 static Extent defoveated_size(const wivrn::to_headset::foveation_parameter& f){return {count_pixels(f.x),count_pixels(f.y)};}
 vertex* get_vertices(size_t view){return data[view].data();}
 struct Command {void setScissor(unsigned,vk::Rect2D){}void setViewport(unsigned,vk::Viewport){}} command_buffer;
 void generate(const std::array<wivrn::to_headset::foveation_parameter,2>& foveation){
  for(size_t i=0;i<2;++i)data[i].resize(required_vertices(foveation[i]));
NATIVE_LOOP
 }
};
extern "C" void reference_geometry(const std::vector<uint32_t>& x,const std::vector<uint32_t>& y,void* output) {
 std::array<wivrn::to_headset::foveation_parameter,2> f;
 for(auto& eye:f){eye.x={x.begin(),x.end()};eye.y={y.begin(),y.end()};}
 Native native;native.generate(f);std::memcpy(output,native.data[0].data(),native.data[0].size()*16);
}
#else
#define main replay_entry_point
#include "REPLAY_SOURCE"
#undef main
#include <cassert>
extern "C" void reference_geometry(const std::vector<uint32_t>&,const std::vector<uint32_t>&,void*);
namespace wivrn::to_headset {struct foveation_parameter {std::vector<uint16_t>x,y;};}
int main(){unsigned cases=0;uint64_t state=0x79257aUL;
 for(unsigned i=0;i<1000;++i){
  std::array<wivrn::to_headset::foveation_parameter,2> f;
  for(auto& e:f)for(auto* a:{&e.x,&e.y}){
   state=state*6364136223846793005ULL+1;unsigned count=1+2*((state>>32)%7);
   for(unsigned n=0;n<count;++n){state=state*6364136223846793005ULL+1;a->push_back((state>>32)%16);}
   if(std::all_of(a->begin(),a->end(),[](auto x){return x==0;}))(*a)[a->size()/2]=1;
  }
  for(unsigned eye=0;eye<2;++eye){Eye e{};e.x={f[eye].x.begin(),f[eye].x.end()};e.y={f[eye].y.begin(),f[eye].y.end()};geometry(e,axis(e.x),axis(e.y));
   std::vector<Vertex> native(e.vertices.size());reference_geometry(e.x,e.y,native.data());assert(std::memcmp(e.vertices.data(),native.data(),e.vertices.size()*sizeof(Vertex))==0);++cases;}
 }
 std::cout<<"native_geometry_exact: "<<cases<<" eyes\n";
}
#endif
'''.replace('REPLAY_SOURCE',str(HERE/'replay.cpp')).replace('REQUIRED',required).replace('COUNT',count).replace('NATIVE_LOOP',loop)
    hashes={str(p.relative_to(ROOT)):sha(p) for p in (SOURCE,HERE/'replay.cpp',Path(__file__))}
    with tempfile.TemporaryDirectory(prefix='sub15-native-geometry-') as tmp:
        tmp=Path(tmp);src=tmp/'geometry.cpp';src.write_text(harness);binary=tmp/'geometry'
        flags=['clang++','-std=c++20','-O2','-g','-fsanitize=address,undefined']
        native_argv=flags+['-ffast-math','-DNATIVE_GEOMETRY','-I',str(a.glm_root),'-c',str(src),'-o',str(tmp/'native.o')]
        argv=flags+[str(src),str(tmp/'native.o'),'-lvulkan','-o',str(binary)]
        for command in (native_argv,argv):
            build=subprocess.run(command,capture_output=True,text=True,timeout=40)
            if build.returncode:raise RuntimeError(build.stderr)
        run=subprocess.run([str(binary)],capture_output=True,text=True,timeout=20)
        assert run.returncode==0 and run.stdout=='native_geometry_exact: 2000 eyes\n' and not run.stderr,(run.returncode,run.stderr)
        result=dict(status='pass',source_sha256=hashes,extracted_loop_sha256=hashlib.sha256(loop.encode()).hexdigest(),harness_sha256=sha(src),binary_sha256=sha(binary),argv=argv,native_argv=native_argv,glm_headers_sha256={str(p.relative_to(a.glm_root)):sha(p) for p in a.glm_root.rglob('*') if p.is_file()},stdout=run.stdout,stderr=run.stderr,scope='Actual source-extracted native CPU vertex loop compiled with -ffast-math vs replay compiled without it, 2000 eyes including zero-length segments; no GPU/native runtime use')
    assert hashes=={str(p.relative_to(ROOT)):sha(p) for p in (SOURCE,HERE/'replay.cpp',Path(__file__))}
    a.output.write_text(json.dumps(result,indent=2)+'\n');print(run.stdout,end='')
if __name__=='__main__':main()
