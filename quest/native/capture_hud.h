// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include <algorithm>
#include <array>
#include <cstdint>
#include <string>
#include <vector>

namespace room_capture {
constexpr int hud_width=1024, hud_height=384;
// Small original 5x7 bitmap alphabet. No engine, font file or JNI render dependency.
inline std::array<uint8_t,7> glyph(char c) {
    switch(c) {
    case 'A':return {14,17,17,31,17,17,17}; case 'B':return {30,17,17,30,17,17,30};
    case 'C':return {14,17,16,16,16,17,14}; case 'D':return {30,17,17,17,17,17,30};
    case 'E':return {31,16,16,30,16,16,31}; case 'F':return {31,16,16,30,16,16,16};
    case 'G':return {14,17,16,23,17,17,14}; case 'H':return {17,17,17,31,17,17,17};
    case 'I':return {14,4,4,4,4,4,14}; case 'J':return {7,2,2,2,18,18,12};
    case 'K':return {17,18,20,24,20,18,17}; case 'L':return {16,16,16,16,16,16,31};
    case 'M':return {17,27,21,21,17,17,17}; case 'N':return {17,25,25,21,19,19,17};
    case 'O':return {14,17,17,17,17,17,14}; case 'P':return {30,17,17,30,16,16,16};
    case 'Q':return {14,17,17,17,21,18,13}; case 'R':return {30,17,17,30,20,18,17};
    case 'S':return {15,16,16,14,1,1,30}; case 'T':return {31,4,4,4,4,4,4};
    case 'U':return {17,17,17,17,17,17,14}; case 'V':return {17,17,17,17,17,10,4};
    case 'W':return {17,17,17,21,21,21,10}; case 'X':return {17,17,10,4,10,17,17};
    case 'Y':return {17,17,10,4,4,4,4}; case 'Z':return {31,1,2,4,8,16,31};
    case '0':return {14,17,19,21,25,17,14}; case '1':return {4,12,4,4,4,4,14};
    case '2':return {14,17,1,2,4,8,31}; case '3':return {30,1,1,14,1,1,30};
    case '4':return {2,6,10,18,31,2,2}; case '5':return {31,16,16,30,1,1,30};
    case '6':return {14,16,16,30,17,17,14}; case '7':return {31,1,2,4,8,8,8};
    case '8':return {14,17,17,14,17,17,14}; case '9':return {14,17,17,15,1,1,14};
    case '-':return {0,0,0,31,0,0,0}; case '/':return {1,1,2,4,8,16,16};
    case ':':return {0,4,4,0,4,4,0}; case '.':return {0,0,0,0,0,4,4};
    default:return {};
    }
}
inline std::vector<uint8_t> hud_pixels(const std::vector<std::string> &lines, float progress) {
    std::vector<uint8_t> pixels(hud_width*hud_height*4);
    auto pixel=[&](int x,int y,bool lit) {
        if(x<0||x>=hud_width||y<0||y>=hud_height)return;
        int i=((hud_height-1-y)*hud_width+x)*4;
        pixels[i]=lit?170:8; pixels[i+1]=lit?245:18; pixels[i+2]=lit?255:25; pixels[i+3]=255;
    };
    for(int y=0;y<hud_height;++y)for(int x=0;x<hud_width;++x)pixel(x,y,false);
    for(size_t line=0;line<lines.size()&&line<6;++line) {
        int x=24,y=24+int(line)*48;
        for(char c:lines[line]) {
            if(x+15>=hud_width-24)break;
            auto rows=glyph(c);
            for(int row=0;row<7;++row)for(int col=0;col<5;++col)if(rows[row]&(1<<(4-col)))
                for(int dy=0;dy<3;++dy)for(int dx=0;dx<3;++dx)pixel(x+col*3+dx,y+row*3+dy,true);
            x+=18;
        }
    }
    int width=int(std::clamp(progress,0.f,1.f)*(hud_width-48));
    for(int y=330;y<346;++y)for(int x=24;x<24+width;++x)pixel(x,y,true);
    return pixels;
}
} // namespace room_capture
