use shaderc::{Compiler, EnvVersion, ShaderKind, TargetEnv};
use std::path::PathBuf;

const SHADERS: &[(&str, ShaderKind)] = &[
    ("quad.vert", ShaderKind::Vertex),
    ("color.frag", ShaderKind::Fragment),
    ("hand_pad.frag", ShaderKind::Fragment),
    ("grid.frag", ShaderKind::Fragment),
    ("screen.frag", ShaderKind::Fragment),
    ("simple.frag", ShaderKind::Fragment),
    ("wayland.frag", ShaderKind::Fragment),
    ("srgb.frag", ShaderKind::Fragment),
    ("sky.frag", ShaderKind::Fragment),
];

fn main() {
    compile_shaders();

    let mut wlx_build = String::from("26.8.0-4590371-wivrn-pro");

    match std::env::var("GITHUB_JOB").as_deref() {
        Ok("make_release") => {
            wlx_build = format!("{} (Release)", wlx_build);
        }
        Ok("build_appimage") => {
            wlx_build = format!("{} (AppImage)", wlx_build);
        }
        _ => {}
    }
    println!("cargo:rustc-env=WLX_BUILD={wlx_build}");
}

fn compile_shaders() {
    let shader_dir = PathBuf::from("src/shaders");
    let out_dir = PathBuf::from(std::env::var_os("OUT_DIR").expect("OUT_DIR is set by Cargo"));
    let compiler = Compiler::new().expect("shaderc compiler is available");

    let mut options = shaderc::CompileOptions::new().expect("shaderc options are available");
    options.set_target_env(TargetEnv::Vulkan, EnvVersion::Vulkan1_3 as u32);

    for (name, kind) in SHADERS {
        let path = if *name == "hand_pad.frag" {
            PathBuf::from("../../hand_pad.frag")
        } else {
            shader_dir.join(name)
        };
        println!("cargo:rerun-if-changed={}", path.display());

        let source = std::fs::read_to_string(&path)
            .unwrap_or_else(|e| panic!("failed to read {}: {e}", path.display()));
        let artifact = compiler
            .compile_into_spirv(&source, *kind, name, "main", Some(&options))
            .unwrap_or_else(|e| panic!("failed to compile {}: {e}", path.display()));
        std::fs::write(out_dir.join(format!("{name}.spv")), artifact.as_binary_u8())
            .unwrap_or_else(|e| panic!("failed to write compiled shader {name}: {e}"));
    }
}
