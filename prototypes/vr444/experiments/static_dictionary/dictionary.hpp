// SPDX-License-Identifier: MIT
#pragma once
#include "wpc.hpp"
#include <openssl/sha.h>
#include <memory>
#include <string>

// Experimental host-only wrapper. Each frame references one immutable dictionary,
// never another frame. SHA-256 identifies dictionary bytes, not peer authenticity.
namespace dictionary {
using Bytes=std::vector<uint8_t>;
using View=std::span<const uint8_t>;
constexpr size_t max_dictionary=65536,header_bytes=64;
constexpr size_t max_payload=16u*1024*1024;
constexpr size_t max_raw=max_payload;
inline void need(bool v){if(!v)throw std::runtime_error("invalid DPC1 packet");}
struct StreamDeleter {void operator()(LZ4_stream_t*p)const{LZ4_freeStream(p);}};
using Stream=std::unique_ptr<LZ4_stream_t,StreamDeleter>;

class Dictionary {
    static Bytes checked_copy(View bytes){need(bytes.size()>=8&&bytes.size()<=max_dictionary);return {bytes.begin(),bytes.end()};}
    Bytes bytes_;
    std::array<uint8_t,SHA256_DIGEST_LENGTH> hash_{};
    Stream context_{LZ4_createStream()};
public:
    explicit Dictionary(View bytes,bool slow=false):bytes_(checked_copy(bytes)){
        need(bytes.size()>=8&&bytes.size()<=max_dictionary&&context_);
        need(SHA256(bytes_.data(),bytes_.size(),hash_.data())!=nullptr);
        int loaded=slow?LZ4_loadDictSlow(context_.get(),reinterpret_cast<const char*>(bytes_.data()),int(bytes_.size())):
                        LZ4_loadDict(context_.get(),reinterpret_cast<const char*>(bytes_.data()),int(bytes_.size()));
        need(loaded==int(bytes_.size()));
    }
    Dictionary(const Dictionary&)=delete;Dictionary&operator=(const Dictionary&)=delete;
    Dictionary(Dictionary&&)=delete;Dictionary&operator=(Dictionary&&)=delete;
    View bytes()const{return bytes_;}
    View hash()const{return hash_;}
    const LZ4_stream_t* context()const{return context_.get();}
    std::string hex()const{constexpr char h[]="0123456789abcdef";std::string s;for(auto b:hash_){s+=h[b>>4];s+=h[b&15];}return s;}
};
struct Workspace {
    Bytes wire,scratch,decoded;
    Stream stream{LZ4_createStream()};
    Workspace(){need(bool(stream));}
    Workspace(const Workspace&)=delete;Workspace&operator=(const Workspace&)=delete;
};
inline bool alias(View input,const Bytes& buffer){
    if(input.empty()||!buffer.capacity())return false;
    auto a=reinterpret_cast<uintptr_t>(input.data()),b=reinterpret_cast<uintptr_t>(buffer.data());
    return a<=b?b-a<input.size():a-b<buffer.capacity();
}
inline bool alias(View input,const Workspace& w){return alias(input,w.wire)||alias(input,w.scratch)||alias(input,w.decoded);}
inline void set32(Bytes&b,size_t at,uint32_t v){for(unsigned i=0;i<4;++i)b[at+i]=uint8_t(v>>(8*i));}
struct Selection {View bytes;bool compressed;};

inline Selection encode(View raw,const Dictionary& dict,Workspace&w,size_t minimum_saving=64){
    need(!raw.empty()&&raw.size()<=max_raw&&!alias(raw,w)&&!wpc::overlaps(raw,dict.bytes()));
    w.wire.clear();
    if(raw.size()<=header_bytes||minimum_saving>=raw.size()||raw.size()-minimum_saving<=header_bytes)return {raw,false};
    w.scratch.resize(LZ4_compressBound(int(raw.size())));
    // Stable LZ4 v1.10.0 API; reset and reattach for EACH frame, forbidding
    // history from previously encoded frames even when reusing the workspace.
    LZ4_resetStream_fast(w.stream.get());
    LZ4_attach_dictionary(w.stream.get(),dict.context());
    int packed=LZ4_compress_fast_continue(w.stream.get(),reinterpret_cast<const char*>(raw.data()),
                                        reinterpret_cast<char*>(w.scratch.data()),int(raw.size()),int(w.scratch.size()),1);
    if(packed<=0){LZ4_initStream(w.stream.get(),size_t(LZ4_sizeofState()));throw std::runtime_error("dictionary compression failed");}
    if(header_bytes+size_t(packed)>=raw.size()||raw.size()-(header_bytes+size_t(packed))<minimum_saving)return {raw,false};
    w.wire.assign(header_bytes,0);w.wire[0]='D';w.wire[1]='P';w.wire[2]='C';w.wire[3]='1';w.wire[4]=1;w.wire[5]=1;
    set32(w.wire,8,uint32_t(raw.size()));set32(w.wire,12,uint32_t(packed));set32(w.wire,16,uint32_t(dict.bytes().size()));
    set32(w.wire,20,wpc::crc(raw));std::copy(dict.hash().begin(),dict.hash().end(),w.wire.begin()+24);
    // Bytes56..59 remain reserved zero; header CRC occupies60..63.
    set32(w.wire,60,wpc::crc(View(w.wire).first(60)));
    w.wire.insert(w.wire.end(),w.scratch.begin(),w.scratch.begin()+packed);
    return {w.wire,true};
}

inline View decode(View wire,const Dictionary&dict,Workspace&w){
    need(!alias(wire,w));w.decoded.clear();
    try{
        need(wire.size()>header_bytes&&wire.size()<=max_payload);
        need(wire[0]=='D'&&wire[1]=='P'&&wire[2]=='C'&&wire[3]=='1'&&wire[4]==1&&wire[5]==1&&wire[6]==0&&wire[7]==0);
        uint32_t raw=wpc::u32(wire,8),stored=wpc::u32(wire,12),dict_size=wpc::u32(wire,16),checksum=wpc::u32(wire,20);
        need(raw>0&&raw<=max_raw&&stored>0&&stored==wire.size()-header_bytes&&wire.size()<raw);
        need(dict_size==dict.bytes().size()&&std::equal(dict.hash().begin(),dict.hash().end(),wire.begin()+24));
        need(wpc::u32(wire,56)==0&&wpc::u32(wire,60)==wpc::crc(wire.first(60)));
        w.decoded.resize(raw);
        int got=LZ4_decompress_safe_usingDict(reinterpret_cast<const char*>(wire.data()+header_bytes),
                                            reinterpret_cast<char*>(w.decoded.data()),int(stored),int(raw),
                                            reinterpret_cast<const char*>(dict.bytes().data()),int(dict.bytes().size()));
        need(got==int(raw)&&wpc::crc(w.decoded)==checksum);
        return w.decoded;
    }catch(...){w.decoded.clear();throw;}
}
}
