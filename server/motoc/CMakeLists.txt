# Motoc stays in a separate Rust workspace/OpenXR loader ABI.
find_program(CARGO_EXECUTABLE cargo REQUIRED)
option(WIVRN_MOTOC_OFFLINE "Build bundled Motoc using cached crates only" OFF)
set(motoc_args --locked --release -p motoc)
if(WIVRN_MOTOC_OFFLINE)
    list(APPEND motoc_args --offline)
endif()
file(GLOB_RECURSE motoc_sources CONFIGURE_DEPENDS "upstream/*.rs" "upstream/Cargo*" "upstream/*/Cargo.toml")
set(motoc_binary "${CMAKE_CURRENT_BINARY_DIR}/cargo/release/motoc")
add_custom_command(OUTPUT "${motoc_binary}"
    COMMAND ${CMAKE_COMMAND} -E env "CARGO_TARGET_DIR=${CMAKE_CURRENT_BINARY_DIR}/cargo" "CARGO_BUILD_JOBS=4"
        ${CARGO_EXECUTABLE} build ${motoc_args} --manifest-path "${CMAKE_CURRENT_SOURCE_DIR}/upstream/Cargo.toml"
    DEPENDS ${motoc_sources}
    VERBATIM)
add_custom_target(wivrn-motoc ALL DEPENDS "${motoc_binary}")
set(WIVRN_MOTOC_BUILD_PATH "${motoc_binary}" PARENT_SCOPE)
install(PROGRAMS "${motoc_binary}" DESTINATION ${CMAKE_INSTALL_LIBEXECDIR}/wivrn-pro RENAME motoc)
install(FILES upstream/LICENSE DESTINATION ${CMAKE_INSTALL_DATADIR}/licenses/wivrn-pro/motoc)
