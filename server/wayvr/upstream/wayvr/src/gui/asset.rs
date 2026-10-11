#[derive(rust_embed::Embed)]
#[folder = "src/assets/"]
pub struct GuiAsset;

impl wgui::assets::AssetProvider for GuiAsset {
    fn load_from_path(&mut self, path: &str) -> anyhow::Result<Vec<u8>> {
        if path == "gui/watch-motoc.xml" {
            return Ok(include_str!("../assets/gui/watch.xml")
                .replace("Full reset: stand upright and face forward. Three-second countdown; press again to cancel.", "Motoc calibrate: hold the selected tracker against the headset. Three-second countdown; press again to cancel.")
                .replace("Mounting reset: after a full reset, bend knees and lean forward in a ski pose. Three-second countdown; press again to cancel.", "Motoc resume: use the saved calibration in the selected mode. Three-second countdown; press again to cancel.")
                .replace("<sprite width=\"40\" height=\"40\" src_builtin=\"@/watch/full-reset.svg\" />", "<label text=\"Calib\" size=\"15\" />")
                .replace("<sprite width=\"40\" height=\"40\" src_builtin=\"@/watch/mounting-reset.svg\" />", "<label text=\"Resume\" size=\"13\" />")
                .into_bytes());
        }
        if path == "watch/full-reset.svg" {
            return Ok(include_bytes!("../../../../icons/full-reset.svg").to_vec());
        }
        if path == "watch/mounting-reset.svg" {
            return Ok(include_bytes!("../../../../icons/mounting-reset.svg").to_vec());
        }
        if path == "gui/performance.xml" {
            return Ok(include_bytes!("../../../../performance.xml").to_vec());
        }
        if path == "watch/sticks.svg" {
            return Ok(include_bytes!("../../../../icons/sticks.svg").to_vec());
        }
        if path == "watch/performance.svg" {
            return Ok(include_bytes!("../../../../icons/performance.svg").to_vec());
        }
        match Self::get(path) {
            Some(data) => Ok(data.data.to_vec()),
            None => anyhow::bail!("embedded file {path} not found"),
        }
    }
}
