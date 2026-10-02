#[derive(rust_embed::Embed)]
#[folder = "src/assets/"]
pub struct GuiAsset;

impl wgui::assets::AssetProvider for GuiAsset {
    fn load_from_path(&mut self, path: &str) -> anyhow::Result<Vec<u8>> {
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
