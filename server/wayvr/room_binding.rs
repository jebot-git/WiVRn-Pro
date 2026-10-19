// SPDX-License-Identifier: GPL-3.0-only
use glam::Affine3A;
use serde::{Deserialize, Serialize};

/// Persistent physical-room placement; never a saved absolute STAGE pose.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct RoomBinding {
    pub uuid: String,
    pub relative: Affine3A,
}
