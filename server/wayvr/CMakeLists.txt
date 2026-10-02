# Pinned source in its own Cargo workspace: avoids Shora/OpenXR loader ABI conflicts.
find_program(CARGO_EXECUTABLE cargo REQUIRED)
option(WIVRN_WAYVR_OFFLINE "Build bundled WayVR using cached dependencies only" OFF)
set(wayvr_args --locked --release -p wayvr --no-default-features --features openxr,wayland,x11)
set(wayvrctl_args --locked --release -p wayvrctl)
if(WIVRN_WAYVR_OFFLINE)
    list(APPEND wayvr_args --offline)
    list(APPEND wayvrctl_args --offline)
endif()
file(GLOB_RECURSE wayvr_sources CONFIGURE_DEPENDS "upstream/*")
set(WIVRN_WAYVR_TARGET_DIR "${CMAKE_CURRENT_BINARY_DIR}/cargo" CACHE PATH "WayVR Cargo build cache")
set(wayvr_binary "${WIVRN_WAYVR_TARGET_DIR}/release/wayvr")
set(wayvrctl_binary "${WIVRN_WAYVR_TARGET_DIR}/release/wayvrctl")
add_custom_command(OUTPUT "${wayvr_binary}" "${wayvrctl_binary}"
    COMMAND ${CMAKE_COMMAND} -E env "CARGO_TARGET_DIR=${WIVRN_WAYVR_TARGET_DIR}" "CARGO_BUILD_JOBS=4"
        ${CARGO_EXECUTABLE} build ${wayvr_args} --manifest-path "${CMAKE_CURRENT_SOURCE_DIR}/upstream/Cargo.toml"
    COMMAND ${CMAKE_COMMAND} -E env "CARGO_TARGET_DIR=${WIVRN_WAYVR_TARGET_DIR}" "CARGO_BUILD_JOBS=4"
        ${CARGO_EXECUTABLE} build ${wayvrctl_args} --manifest-path "${CMAKE_CURRENT_SOURCE_DIR}/upstream/Cargo.toml"
    DEPENDS ${wayvr_sources} wivrn_tests.rs shora_battery.rs hand_input.rs hand_touch.rs hand_joystick.rs hand_double_tap.rs mouse_priority.rs hand_pad.rs hand_pad.frag
    WORKING_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}/upstream"
    VERBATIM)
add_custom_target(wivrn-wayvr ALL DEPENDS "${wayvr_binary}" "${wayvrctl_binary}")
install(PROGRAMS "${wayvr_binary}" DESTINATION ${CMAKE_INSTALL_LIBEXECDIR}/wivrn-pro RENAME wayvr)
install(PROGRAMS "${wayvrctl_binary}" DESTINATION ${CMAKE_INSTALL_BINDIR} RENAME wivrn-pro-wayvrctl)
install(FILES upstream/LICENSE DESTINATION ${CMAKE_INSTALL_DATADIR}/licenses/wivrn-pro/wayvr)

foreach(component wayvr wayvrctl wayvr-ipc dash-frontend wgui wlx-common wlx-capture)
    install(DIRECTORY "upstream/${component}/" DESTINATION "${CMAKE_INSTALL_DATADIR}/licenses/wivrn-pro/wayvr/${component}"
        FILES_MATCHING PATTERN "LICENSE*" PATTERN "src" EXCLUDE PATTERN "res" EXCLUDE)
endforeach()
