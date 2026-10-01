include(CheckCXXSourceRuns)
set(CMAKE_REQUIRED_LIBRARIES PkgConfig::LIBAV)
check_cxx_source_runs([=[
extern "C" {
#include <libavcodec/avcodec.h>
#include <libavutil/hwcontext.h>
}
int main() {
    return !(avcodec_find_encoder_by_name("h264_qsv") &&
             avcodec_find_encoder_by_name("hevc_qsv") &&
             av_hwdevice_find_type_by_name("qsv") == AV_HWDEVICE_TYPE_QSV);
}
]=] WIVRN_FFMPEG_HAS_QSV)
unset(CMAKE_REQUIRED_LIBRARIES)
if(NOT WIVRN_FFMPEG_HAS_QSV)
    message(FATAL_ERROR "QSV requires FFmpeg with h264_qsv, hevc_qsv and QSV hardware context support")
endif()
