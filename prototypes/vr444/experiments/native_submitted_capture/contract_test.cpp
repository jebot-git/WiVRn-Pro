// SPDX-License-Identifier: GPL-3.0-or-later
#include "codec_test/submitted_capture.h"
#include <cassert>
#include <climits>
#include <iostream>
#include <limits>
using namespace wivrn::codec_test::submitted_capture;
int main() {
    unsigned rejected=0;
    auto reject=[&](auto&& fn){bool threw=false;try{fn();}catch(const std::exception&){threw=true;}assert(threw);++rejected;};
    auto c=parse(R"({"enabled":true,"max_pairs":6,"frame_stride":90})");assert(c.enabled&&c.max_pairs==6&&c.frame_stride==90);
    assert(!parse(R"({"enabled":false})").enabled);
    for(auto bad: {"", "null", "[]", "true", "{}", R"({"enabled":1})", R"({"enabled":"true"})",
        R"({"enabled":null})", R"({"enabled":true,"enabled":false})", R"({"enabled":true,"en\u0061bled":false})",
        R"({"enabled":true,"max_pairs":0})",R"({"enabled":true,"max_pairs":9})",R"({"enabled":true,"max_pairs":-1})",
        R"({"enabled":true,"max_pairs":1.0})",R"({"enabled":true,"max_pairs":true})",R"({"enabled":true,"max_pairs":"1"})",
        R"({"enabled":true,"frame_stride":0})",R"({"enabled":true,"frame_stride":901})",R"({"enabled":true,"frame_stride":-1})",
        R"({"enabled":true,"frame_stride":1.0})",R"({"enabled":true,"frame_stride":18446744073709551616})",
        R"({"enabled":true,"max_pairs":1,"max_pairs":2})",R"({"enabled":true,"frame_stride":1,"frame_stride":2})",
        R"({"enabled":true,"unknown":false})",R"({"enabled":true}false)",R"({"enabled":false,"max_pairs":0})"})
        reject([&]{(void)parse(bad);});
    reject([&]{(void)parse(std::string(1025,' '));});
    for(uint32_t pairs=1;pairs<=8;++pairs) for(uint32_t stride:{1u,90u,900u}) {
        Budget budget{Config{true,pairs,stride}};const uint64_t base=7*stride;assert(budget.take(base));
        for(uint32_t i=1;i<pairs;++i) {
            assert(!budget.take(base+(i-1)*stride));assert(!budget.take(0));
            if(stride>1)assert(!budget.take(base+i*stride-1));
            assert(budget.take(base+i*stride));
        }
        assert(!budget.take(UINT64_MAX));assert(budget.attempts==pairs);
    }
    Budget aligned{Config{true,2,90}};assert(!aligned.take(1));assert(!aligned.take(89));assert(aligned.attempts==0);assert(aligned.take(90));assert(!aligned.take(179));assert(!aligned.take(181));assert(aligned.attempts==1);assert(aligned.take(270));assert(!aligned.take(360));
    Budget end{Config{true,8,1}};assert(end.take(UINT64_MAX));assert(!end.take(0));assert(!end.take(UINT64_MAX));
    Budget off{Config{false,1,1}};assert(!off.take(1));
    assert(bytes(4096,1024)==16*1024*1024);assert(bytes(2048,2048)==16*1024*1024);assert(bytes(1,1)==4);
    for(auto wh:std::array<std::array<uint32_t,2>,8>{{{0,1},{1,0},{4097,1},{1,4097},{2048,2049},{UINT_MAX,UINT_MAX},{4096,4096},{1,UINT_MAX}}})
        reject([&]{(void)bytes(wh[0],wh[1]);});
    char temp[]="/tmp/sub15-quality-contract-XXXXXX";auto raw=::mkdtemp(temp);assert(raw);std::filesystem::path dir(raw);
    assert(!load(dir/"missing").enabled);
    exclusive_write(dir/"evidence","exact-original");
    reject([&]{exclusive_write(dir/"evidence","replacement");});
    std::ifstream original(dir/"evidence");std::string original_text((std::istreambuf_iterator<char>(original)),{});assert(original_text=="exact-original");
    reject([&]{exclusive_write(dir/"absent/eye.raw","payload");});assert(!std::filesystem::exists(dir/"capture-pair.json"));
    exclusive_write(dir/"config",R"({"enabled":true,"max_pairs":8,"frame_stride":900})");assert(load(dir/"config").max_pairs==8);
    exclusive_write(dir/"too-big",std::string(1025,' '));reject([&]{(void)load(dir/"too-big");});
    std::filesystem::remove_all(dir);
    std::cout<<"PASS submitted capture contract: "<<rejected<<" rejections; 24 budgets; absolute frame alignment; bounds; no-overwrite evidence\n";
}
