// SPDX-License-Identifier: MIT
#include "../rowblocks_workers/workers.hpp"
#include "codec.hpp"
#include <fstream>
#include <iostream>

static rowblocks::Bytes read(const char*path,size_t limit){
    std::ifstream f(path,std::ios::binary|std::ios::ate);rowblocks::need(bool(f));
    auto end=f.tellg();rowblocks::need(end>0&&uint64_t(end)<=limit);
    rowblocks::Bytes b(static_cast<size_t>(end));f.seekg(0);
    f.read(reinterpret_cast<char*>(b.data()),std::streamsize(b.size()));rowblocks::need(bool(f));return b;
}
int main(int argc,char**argv){
    try{
        rowblocks::need(argc==7);
        auto left=read(argv[3],262144),right=read(argv[4],262144);
        rowblocks::need(left.size()+right.size()<=262144);
        std::array<std::vector<uint32_t>,2>reference;
        std::array<vr444::Frame,2>frames;
        for(unsigned eye=0;eye<2;++eye){
            auto raw=read(argv[1+eye],16*1024*1024);
            rowblocks::need(raw.size()>=24);
            auto width=wpc::u32(raw,8),height=wpc::u32(raw,12);
            rowfilter::pixels(width,height); // Bound the independent reference before allocation.
            frames[eye]=vr444::unpack(raw);reference[eye]=vr444::decode(frames[eye]);
            auto oracle=read(argv[5+eye],16*1024*1024);
            rowblocks::need(oracle.size()==reference[eye].size()*4);
            for(size_t i=0;i<reference[eye].size();++i)
                rowblocks::need(wpc::u32(oracle,i*4)==reference[eye][i]);
        }
        rowblocks::need(frames[0].width==frames[1].width&&frames[0].height==frames[1].height);
        rowworkers::Workspace work;rowworkers::Pool pool(4);
        auto result=rowworkers::decode_pair(left,right,work,&pool);
        rowblocks::need(result.width==frames[0].width&&result.height==frames[0].height);
        rowblocks::need(std::equal(result.left.begin(),result.left.end(),reference[0].begin(),reference[0].end()));
        rowblocks::need(std::equal(result.right.begin(),result.right.end(),reference[1].begin(),reference[1].end()));
        std::cout<<"{\"status\":\"pass\",\"both_eyes_exact\":true,\"pixels_per_eye\":"<<reference[0].size()<<"}\n";
        return 0;
    }catch(const std::exception&e){std::cerr<<e.what()<<'\n';return 1;}
}
