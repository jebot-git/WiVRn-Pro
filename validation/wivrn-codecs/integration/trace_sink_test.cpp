#include "trace_sink.hpp"
#include <cassert>
#include <fstream>
#include <iostream>
using namespace wivrn_codec_test;
int main(int argc,char** argv) {
    if(argc!=2) return 2;
    Ring<3> ring; Event e; e.frame=41;
    assert(ring.push(e)); assert(ring.push(e)); assert(!ring.push(e));
    Event out; assert(ring.pop(out)); assert(out.frame==41);
    assert(ring.pop(out)); assert(!ring.pop(out));
    Sink sink(argv[1],"native-selftest","host");
    for(uint64_t i=0;i<100;i++) { e.frame=i; e.ts_ns=monotonic_ns(); assert(sink.emit(e)); }
    assert(sink.close()==0);
    std::ifstream file(argv[1]); std::string line; size_t count=0;
    while(std::getline(file,line)) ++count;
    assert(count==100); std::cout << "100 events flushed; bounded ring overflow verified\n";
}
