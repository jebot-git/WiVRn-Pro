# SpeexDSP's upstream build uses autotools. Build its portable floating-point
# preprocessing/FFT subset here for both Android and the standalone native tests.
include(FetchContent)
include_guard(GLOBAL)
FetchContent_Declare(speexdsp
    URL https://downloads.xiph.org/releases/speex/speexdsp-1.2.1.tar.gz
    URL_HASH SHA256=8c777343e4a6399569c72abc38a95b24db56882c83dbdb6c6424a5f4aeb54d3d
    DOWNLOAD_EXTRACT_TIMESTAMP TRUE)
FetchContent_MakeAvailable(speexdsp)

set(_speex_types "${CMAKE_CURRENT_BINARY_DIR}/speex-types")
file(MAKE_DIRECTORY "${_speex_types}")
file(WRITE "${_speex_types}/speexdsp_config_types.h"
    "#pragma once\n#include <stdint.h>\ntypedef int16_t spx_int16_t;\ntypedef uint16_t spx_uint16_t;\ntypedef int32_t spx_int32_t;\ntypedef uint32_t spx_uint32_t;\n")
add_library(wivrn-speexdsp STATIC
    ${speexdsp_SOURCE_DIR}/libspeexdsp/preprocess.c
    ${speexdsp_SOURCE_DIR}/libspeexdsp/mdf.c
    ${speexdsp_SOURCE_DIR}/libspeexdsp/fftwrap.c
    ${speexdsp_SOURCE_DIR}/libspeexdsp/filterbank.c
    ${speexdsp_SOURCE_DIR}/libspeexdsp/smallft.c)
target_include_directories(wivrn-speexdsp SYSTEM PUBLIC
    ${speexdsp_SOURCE_DIR}/include "${CMAKE_CURRENT_BINARY_DIR}/speex-types"
    PRIVATE "${_speex_types}")
target_compile_definitions(wivrn-speexdsp PRIVATE FLOATING_POINT USE_SMALLFT EXPORT=)
target_link_libraries(wivrn-speexdsp PUBLIC m)
set_target_properties(wivrn-speexdsp PROPERTIES POSITION_INDEPENDENT_CODE ON)

add_library(wivrn-microphone-dsp STATIC
    ${CMAKE_CURRENT_LIST_DIR}/../client/audio/software_microphone.cpp)
target_include_directories(wivrn-microphone-dsp PUBLIC
    ${CMAKE_CURRENT_LIST_DIR}/../client ${CMAKE_CURRENT_LIST_DIR}/../common)
target_compile_features(wivrn-microphone-dsp PUBLIC cxx_std_23)
target_link_libraries(wivrn-microphone-dsp PRIVATE wivrn-speexdsp)
set_target_properties(wivrn-microphone-dsp PROPERTIES POSITION_INDEPENDENT_CODE ON)

# Retain the binary-distribution notice as a packaged Android resource.
if(ANDROID AND RESOURCES_DIRECTORIES)
    file(MAKE_DIRECTORY "${RESOURCES_DIRECTORIES}/licenses")
    configure_file(${speexdsp_SOURCE_DIR}/COPYING
        "${RESOURCES_DIRECTORIES}/licenses/SpeexDSP.txt" COPYONLY)
endif()
