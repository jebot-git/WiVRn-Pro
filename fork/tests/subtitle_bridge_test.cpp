// SPDX-License-Identifier: GPL-3.0-or-later
#include "driver/subtitle_bridge.h"
#include <cassert>
#include <chrono>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <thread>
#include <unistd.h>

int main()
{
    auto path = std::filesystem::temp_directory_path() / ("wivrn-subtitle-test-" + std::to_string(getpid()) + ".py");
    {
        std::ofstream f(path);
        f << R"(import json, socket, time
s=socket.socket(fileno=0)
s.send(b'invalid-json')
s.send(json.dumps({'lines':[{'text':'Hello','speaker':2,'captured':time.monotonic(), 'bearing':.4,'spatial':True}], 'duration':5,'scale':1}).encode())
s.send(json.dumps({'lines':[{'text':'stale','speaker':2,'captured':time.monotonic()-20,'bearing':.4,'spatial':True}], 'duration':5,'scale':1}).encode())
time.sleep(60)
)";
    }
    auto start = std::chrono::steady_clock::now();
    {
        wivrn::subtitle_bridge bridge(path.string());
        bool found = false;
        for (int i=0;i<100;++i)
        {
            if (auto p=bridge.poll(); p && !p->lines.empty())
            {
                assert(p->lines[0].text == "Hello" && p->lines[0].speaker == 2);
                found = true; break;
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(10));
        }
        assert(found);
        auto stale=bridge.poll(); assert(stale && stale->lines.empty());
    }
    {
        std::ofstream f(path);
        f << R"(import array, fcntl, json, os, socket, time
s=socket.socket(fileno=0)
s.settimeout(2)
s.send(json.dumps({'ocr_request':time.monotonic_ns()}).encode())
data,ancillary,flags,addr=s.recvmsg(4096,socket.CMSG_SPACE(4))
metadata=json.loads(data)
fds=array.array('i'); fds.frombytes(ancillary[0][2]); fd=fds[0]
assert os.fstat(fd).st_size==1024*1024*4
assert fcntl.fcntl(fd,fcntl.F_GET_SEALS)&fcntl.F_SEAL_WRITE
assert os.pread(fd,4,0)==bytes([42])*4
os.close(fd)
s.send(json.dumps({'visual':True,'capture_pose':[0,1.6,0,0,0,0,1],'capture_fov':[-.8,.8,.8,-.8],
'distance':2,'duration':5,'scale':1,'lines':[{'text':'Exit','speaker':0,'captured':time.monotonic(),'bearing':0,
'spatial':True,'box':[.2,.3,.4,.1],'foreground':[1,.8,.2],'background':[.1,.2,.3]}]}).encode())
time.sleep(60)
)";
    }
    {
        wivrn::subtitle_bridge bridge(path.string());
        wivrn::ocr_exchange exchange;
        bool sent=false, found=false;
        for (int i=0;i<100;++i)
        {
            if (auto packet=bridge.poll(&exchange); packet && packet->visual)
            {
                assert(packet->lines.size()==1 && packet->lines[0].text=="Exit");
                assert(packet->lines[0].foreground[1]==.8f && packet->lines[0].background[2]==.3f);
                found=true; break;
            }
            if (exchange.take_request())
            {
                bridge.send_image({"{\"ocr_frame\":1}",std::vector<uint8_t>(1024*1024*4,42)}); sent=true;
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(10));
        }
        assert(sent && found);
    }
    assert(std::chrono::steady_clock::now()-start < std::chrono::seconds(3));
    std::filesystem::remove(path);
    std::cout << "Subtitle IPC, stale filtering and bounded process shutdown passed\n";
}
