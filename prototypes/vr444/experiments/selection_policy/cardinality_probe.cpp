// SPDX-License-Identifier: MIT
#include "cardinality_oracle.h"
#include <charconv>
#include <fstream>
#include <iostream>
#include <string>
using namespace cardinality_oracle;
int64_t number(std::istream& stream) {
    std::string token;need(bool(stream>>token)&&token.size()<=20,"missing/oversize integer");
    int64_t value{};auto parsed=std::from_chars(token.data(),token.data()+token.size(),value);
    need(parsed.ec==std::errc{}&&parsed.ptr==token.data()+token.size(),"invalid exact integer");return value;
}
int main(int argc,char** argv) {try {
    need(argc==2,"usage: cardinality-probe INPUT");std::ifstream input(argv[1]);need(bool(input),"input file");
    auto n=number(input),d=number(input),b=number(input),threshold=number(input);
    need(n>=0&&n<=10000&&d>0&&d<=10000&&b>=0&&b<=64,"input population bounds");
    std::vector<Frame> frames;std::vector<Deadline> deadlines;
    for(int64_t i=0;i<n;++i) frames.push_back({number(input),number(input),number(input),number(input)});
    for(int64_t i=0;i<d;++i) deadlines.push_back({number(input),number(input),number(input)});
    std::string trailing;need(!(input>>trailing),"trailing input");
    auto result=solve(frames,deadlines,size_t(b),threshold);
    std::cout<<"{\"schema\":1,\"rank_band_width\":"<<result.rank_band_width<<",\"parent_slots\":"<<result.parent_slots
             <<",\"stored_states\":"<<result.stored_states<<",\"frontier\":[";
    bool first=true;
    for(const auto& r:result.results) {
        std::cout<<(first?"":",")<<"{\"skips\":"<<r.skips<<",\"required_count\":"<<r.required_count
                 <<",\"feasible\":"<<(r.feasible?"true":"false")<<",\"maximum_good\":"<<r.maximum_good<<",\"certificate\":[";
        bool first_choice=true;for(auto c:r.path) {
            std::cout<<(first_choice?"":",")<<'['<<deadlines[c.deadline].sequence<<','<<frames[c.frame].id<<']';first_choice=false;
        }
        std::cout<<"]}";first=false;
    }
    std::cout<<"]}\n";return 0;
}catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 2;}}
