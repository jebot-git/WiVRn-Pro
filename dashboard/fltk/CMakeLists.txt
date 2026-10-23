find_package(FLTK 1.4.5 EXACT CONFIG REQUIRED)
find_package(PkgConfig REQUIRED)
pkg_check_modules(DASHBOARD_GIO REQUIRED IMPORTED_TARGET gio-2.0 gio-unix-2.0)
pkg_check_modules(DASHBOARD_FONT REQUIRED IMPORTED_TARGET fontconfig zlib)
find_package(nlohmann_json REQUIRED)
find_package(Python3 3.11 REQUIRED COMPONENTS Interpreter)
set(WIVRN_DOME_SOURCE "" CACHE PATH "DomeD source tree providing native/gpu-window.h and appearance helpers")
if(NOT WIVRN_DOME_SOURCE)
    find_path(WIVRN_DOME_NATIVE_DIR gpu-window.h
        HINTS "$ENV{DOME_SOURCE}/native" "$ENV{HOME}/de-xr/dome-compositor/native"
        PATHS /opt/fltk-vr/include/dome /usr/local/include/dome)
else()
    set(WIVRN_DOME_NATIVE_DIR "${WIVRN_DOME_SOURCE}/native")
endif()
if(NOT EXISTS "${WIVRN_DOME_NATIVE_DIR}/gpu-window.h")
    message(FATAL_ERROR "FLTK dashboard requires DomeD appearance helpers: set WIVRN_DOME_SOURCE to its source tree")
endif()
add_executable(wivrn-dashboard main.cpp backend.cpp tray.cpp)
set_target_properties(wivrn-dashboard PROPERTIES OUTPUT_NAME wivrn-pro-dashboard INSTALL_RPATH "$<TARGET_FILE_DIR:fltk::fltk-shared>")
target_compile_features(wivrn-dashboard PRIVATE cxx_std_17)
target_compile_options(wivrn-dashboard PRIVATE -Wall -Wextra)
target_include_directories(wivrn-dashboard PRIVATE "${WIVRN_DOME_NATIVE_DIR}")
target_link_libraries(wivrn-dashboard PRIVATE fltk::fltk-shared fltk::gl-shared fltk::images-shared
    PkgConfig::DASHBOARD_GIO PkgConfig::DASHBOARD_FONT nlohmann_json::nlohmann_json)
target_compile_definitions(wivrn-dashboard PRIVATE
    WIVRN_DASHBOARD_PYTHON="${Python3_EXECUTABLE}"
    WIVRN_DASHBOARD_PROFILE_HELPER="${CMAKE_INSTALL_FULL_DATADIR}/wivrn-pro/dashboard/profile.py"
    WIVRN_DASHBOARD_SOURCE_HELPER="${CMAKE_CURRENT_SOURCE_DIR}/profile.py"
    WIVRN_DASHBOARD_PRESETS="${CMAKE_INSTALL_FULL_DATADIR}/wivrn-pro/encoder-presets/profiles"
    WIVRN_DASHBOARD_SOURCE_PRESETS="${CMAKE_CURRENT_SOURCE_DIR}/../../fork/encoder-presets/profiles")
install(TARGETS wivrn-dashboard RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR} COMPONENT Dashboard)
install(FILES profile.py DESTINATION ${CMAKE_INSTALL_DATADIR}/wivrn-pro/dashboard COMPONENT Dashboard)
option(WIVRN_DASHBOARD_FLTK_TESTS "Build native dashboard integration driver" OFF)
if(WIVRN_DASHBOARD_FLTK_TESTS)
    add_executable(wivrn-dashboard-backend-test backend-test.cpp backend.cpp)
    target_compile_features(wivrn-dashboard-backend-test PRIVATE cxx_std_17)
    target_link_libraries(wivrn-dashboard-backend-test PRIVATE PkgConfig::DASHBOARD_GIO nlohmann_json::nlohmann_json)
endif()
