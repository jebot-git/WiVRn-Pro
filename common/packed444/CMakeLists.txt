include(CompileGLSL)
add_library(wivrn-packed444 STATIC unpacker.cpp)
target_link_libraries(wivrn-packed444 PUBLIC wivrn-common)
target_compile_definitions(wivrn-packed444 PUBLIC VULKAN_HPP_NO_STRUCT_CONSTRUCTORS)
target_include_directories(wivrn-packed444 PUBLIC ${CMAKE_CURRENT_SOURCE_DIR})
wivrn_compile_glsl(wivrn-packed444 TARGET_ENV vulkan1.1 NAMESPACE packed444
    SHADERS ${CMAKE_CURRENT_SOURCE_DIR}/shaders/unpack.comp)

if(ANDROID)
    # Opt-in, real MediaCodec PRIVATE AHB -> Vulkan -> GPU unpack validation.
    add_executable(packed444-android-import-test EXCLUDE_FROM_ALL tests/android_import_test.cpp)
    target_compile_definitions(packed444-android-import-test PRIVATE VK_USE_PLATFORM_ANDROID_KHR)
    target_link_libraries(packed444-android-import-test PRIVATE wivrn-packed444 Vulkan::Vulkan android mediandk)
endif()
