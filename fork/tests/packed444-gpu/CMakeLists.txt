cmake_minimum_required(VERSION 3.25)
project(packed444_foveation_gpu LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 23)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
get_filename_component(WIVRN_SOURCE "${CMAKE_CURRENT_LIST_DIR}/../../.." ABSOLUTE)
add_executable(packed444-foveation-gpu-test
 ${WIVRN_SOURCE}/fork/tests/packed444_foveation_gpu_test.cpp
 ${WIVRN_SOURCE}/common/vk/allocation.cpp
 ${WIVRN_SOURCE}/common/vk/error_category.cpp
 ${WIVRN_SOURCE}/common/vk/vk_allocator.cpp
 ${WIVRN_SOURCE}/common/vk/vk_mem_alloc.cpp)
target_compile_definitions(packed444-foveation-gpu-test PRIVATE VULKAN_HPP_NO_STRUCT_CONSTRUCTORS)
target_include_directories(packed444-foveation-gpu-test PRIVATE ${WIVRN_SOURCE}/common ${WIVRN_SOURCE}/server ${WIVRN_SOURCE}/external)
find_library(VULKAN_LIB vulkan REQUIRED)
target_link_libraries(packed444-foveation-gpu-test PRIVATE ${VULKAN_LIB} ${CMAKE_DL_LIBS})
