// SPDX-License-Identifier: MIT
#include "wire.hpp"
#include <iostream>
using namespace wpp;
static size_t cases=0,rejects=0;
template<class F>void rejected(F f){bool bad=false;try{f();}catch(const std::runtime_error&){bad=true;}need(bad,"negative test accepted");++rejects;}
int main(){try{for(unsigned n:{2u,3u,1159u,1160u,1161u,32768u,262144u}){Config c{token("0123456789abcdeffedcba9876543210"),3,n,5};validate(c);auto source=pair(c,1);Assembly a(c,1);for(unsigned i=count(c);i>0;--i){auto b=data(c,1,i-1,source);auto r=parse(c,b);bool complete=a.accept(r);need(complete==(i==1),"partial publication");a.accept(r);++cases;}need(a.received==source&&a.duplicates==count(c),"duplicate or exactness");auto response=ack(c,1,1000);need(check_ack(c,parse(c,response),1)==1000,"ACK");rejected([&]{check_ack(c,parse(c,response),2);});
 auto b=data(c,1,0,source);for(size_t k=0;k<b.size();++k)rejected([&]{parse(c,View(b).first(k));});b.back()^=1;rejected([&]{a.accept(parse(c,b));});
 for(unsigned at:{0u,4u,5u,6u,8u,24u,28u,34u,36u,38u}){b=data(c,1,0,source);b[at]^=0x80;rejected([&]{auto r=parse(c,b);a.accept(r);});}}
 Config golden{token("0123456789abcdeffedcba9876543210"),1,31,5};auto gb=pair(golden,1);need(hash(View(gb).first(15))==0x65132a848e524242ULL&&hash(View(gb).subspan(15))==0x942d0dab6f2f44c2ULL,"independent Python golden eyes");
 Config missing{token("0123456789abcdeffedcba9876543210"),1,32768,20};Assembly private_pair(missing,1);auto source=pair(missing,1);
 for(unsigned i=0;i+1<count(missing);++i)need(!private_pair.accept(parse(missing,data(missing,1,i,source))),"right tail withheld");
 auto final=data(missing,1,count(missing)-1,source);final.back()^=1;rejected([&]{private_pair.accept(parse(missing,final));});need(private_pair.unique+1==count(missing),"corrupt right tail advanced completion");final.back()^=1;need(private_pair.accept(parse(missing,final)),"exact final tail completion");
 auto late=ack(missing,1,100000001);rejected([&]{check_ack(missing,parse(missing,late),1);});
 Config c{token("0123456789abcdeffedcba9876543210"),1,262144,5};for(unsigned i=0;i<6;++i){auto bad=c;if(i==0)bad.pair_bytes=1;if(i==1)bad.pair_bytes=262145;if(i==2)bad.frames=181;if(i==3)bad.hz=91;if(i==4)bad.hz=90;if(i==5)bad.token={};rejected([&]{validate(bad);});}
 for(auto s:{"", "0000000000000000000000000000000g", "0000"})rejected([&]{token(s);});
 std::cout<<"{\"pass\":true,\"exact_records\":"<<cases<<",\"rejections\":"<<rejects<<",\"sockets_opened\":false}\n";
 }catch(const std::exception&e){std::cerr<<e.what()<<'\n';return 1;}}
