#[derive(rust_embed::Embed)]
#[folder = "src/assets/"]
pub struct GuiAsset;

impl wgui::assets::AssetProvider for GuiAsset {
    fn load_from_path(&mut self, path: &str) -> anyhow::Result<Vec<u8>> {
        if path == "gui/watch-motoc.xml" {
            // Labels/actions are selected by the grouped watch controller.
            return Ok(include_bytes!("../assets/gui/watch.xml").to_vec());
        }
        if path == "watch/full-reset.svg" {
            return Ok(include_bytes!("../../../../icons/full-reset.svg").to_vec());
        }
        if path == "watch/mounting-reset.svg" {
            return Ok(include_bytes!("../../../../icons/mounting-reset.svg").to_vec());
        }
        if path == "gui/hands-frame-bookmarks.xml" {return Ok(include_bytes!("../../../../hands-frame-bookmarks.xml").to_vec());}
        if path == "gui/hands-frame.xml" {return Ok(include_bytes!("../../../../hands-frame.xml").to_vec());}
        if path == "gui/hands-frame-guide.xml" {return Ok(include_bytes!("../../../../hands-frame-guide.xml").to_vec());}
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
