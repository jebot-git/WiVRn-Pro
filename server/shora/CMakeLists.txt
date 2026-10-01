find_program(CARGO_EXECUTABLE cargo REQUIRED)
option(WIVRN_SHORA_OFFLINE "Use only cached Rust crates" OFF)
set(shora_args --locked --release)
if(WIVRN_SHORA_OFFLINE)
    list(APPEND shora_args --offline)
endif()
set(shora_archive "${CMAKE_CURRENT_BINARY_DIR}/cargo/release/libwivrn_shora_bridge.a")
file(GLOB_RECURSE shora_sources CONFIGURE_DEPENDS "src/*.rs" "vendor/*.rs" "proto/*.proto")
add_custom_command(OUTPUT "${shora_archive}"
    COMMAND ${CMAKE_COMMAND} -E env "CARGO_TARGET_DIR=${CMAKE_CURRENT_BINARY_DIR}/cargo"
        ${CARGO_EXECUTABLE} build ${shora_args} --manifest-path "${CMAKE_CURRENT_SOURCE_DIR}/Cargo.toml"
    DEPENDS ${shora_sources} Cargo.toml Cargo.lock build.rs
    WORKING_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}"
    VERBATIM)
add_custom_target(wivrn-shora-build DEPENDS "${shora_archive}")
add_library(wivrn-shora STATIC IMPORTED GLOBAL)
set_target_properties(wivrn-shora PROPERTIES IMPORTED_LOCATION "${shora_archive}")
add_dependencies(wivrn-shora wivrn-shora-build)
