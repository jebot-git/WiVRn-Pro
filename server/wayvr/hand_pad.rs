// SPDX-License-Identifier: GPL-3.0-only
use super::{
    CompositionLayer, XrState, helpers,
    swapchain::{SwapchainOpts, WlxSwapchain, create_swapchain},
};
use crate::{
    graphics::GpuFutures,
    hand_joystick::{DEADZONE, Pad},
    state::AppState,
};
use openxr as xr;
use smallvec::SmallVec;
use std::sync::Arc;
use wgui::gfx::{
    Buffer, BufferUsage, CommandBufferUsage, Scissor, Vert2Uv, cmd::WGfxClearMode, pass::WGfxPass,
    pipeline::WPipelineCreateInfo,
};

struct Surface {
    swapchain: WlxSwapchain,
    uniform: Arc<Buffer<f32>>,
    pass: WGfxPass<Vert2Uv>,
    pad: Option<Pad>,
}
pub(super) struct HandPads {
    surfaces: Vec<Surface>,
}
impl HandPads {
    pub(super) fn new(xr: &XrState, app: &AppState) -> anyhow::Result<Self> {
        let fragment = crate::shaders::frag_hand_pad::load(&app.gfx)?;
        let pipeline = app.gfx.create_pipeline(
            app.gfx_extras.shaders.get("vert_quad").unwrap(),
            &fragment,
            WPipelineCreateInfo::new(app.gfx.surface_format()),
        )?;
        let mut surfaces = Vec::new();
        for _ in 0..2 {
            let uniform = app
                .gfx
                .empty_buffer(BufferUsage::TRANSFER_DST | BufferUsage::UNIFORM_BUFFER, 8)?;
            let set = pipeline.buffer(0, uniform.clone())?;
            let pass = pipeline.create_pass(
                [256.0, 256.0],
                [0.0, 0.0],
                app.gfx_extras.quad_verts.clone(),
                0..4,
                0..1,
                vec![set],
                Scissor::from_viewport([256.0, 256.0], [0.0, 0.0]),
            )?;
            surfaces.push(Surface {
                swapchain: create_swapchain(
                    xr,
                    app.gfx.clone(),
                    [256, 256],
                    1,
                    SwapchainOpts::new(),
                )?,
                uniform,
                pass,
                pad: None,
            });
        }
        Ok(Self { surfaces })
    }
    pub(super) fn render(
        &mut self,
        app: &AppState,
        futures: &mut GpuFutures,
    ) -> anyhow::Result<()> {
        for (i, surface) in self.surfaces.iter_mut().enumerate() {
            surface.pad = app.input_state.pointers[i].joystick_pad;
            if let Some(pad) = surface.pad {
                let target = surface
                    .swapchain
                    .acquire_wait_image()?
                    .views
                    .into_iter()
                    .next()
                    .unwrap();
                surface.uniform.write()?[..8].copy_from_slice(&[
                    pad.cursor.x,
                    pad.cursor.y,
                    i as f32,
                    f32::from(pad.armed),
                    DEADZONE,
                    0.0,
                    0.0,
                    0.0,
                ]);
                let mut cmd = app
                    .gfx
                    .create_gfx_command_buffer(CommandBufferUsage::OneTimeSubmit)?;
                cmd.begin_rendering(target, WGfxClearMode::DontCare)?;
                cmd.run_ref(&surface.pass)?;
                cmd.end_rendering()?;
                futures.execute(cmd.build()?);
            }
        }
        Ok(())
    }
    pub(super) fn present<'a>(
        &'a mut self,
        xr: &'a XrState,
    ) -> anyhow::Result<SmallVec<[CompositionLayer<'a>; 2]>> {
        let mut layers = SmallVec::new();
        for surface in &mut self.surfaces {
            surface.swapchain.ensure_image_released()?;
            if let Some(pad) = surface.pad.take() {
                layers.push(CompositionLayer::Quad(
                    xr::CompositionLayerQuad::new()
                        .layer_flags(
                            xr::CompositionLayerFlags::BLEND_TEXTURE_SOURCE_ALPHA
                                | xr::CompositionLayerFlags::UNPREMULTIPLIED_ALPHA,
                        )
                        .pose(helpers::transform_to_posef(&pad.frame))
                        .sub_image(surface.swapchain.get_subimage(0))
                        .eye_visibility(xr::EyeVisibility::BOTH)
                        .space(&xr.stage)
                        .size(xr::Extent2Df {
                            width: pad.radius * 2.4,
                            height: pad.radius * 2.4,
                        }),
                ));
            }
        }
        Ok(layers)
    }
}
