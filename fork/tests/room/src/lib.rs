// SPDX-License-Identifier: GPL-3.0-only
extern crate self as wlx_common;
#[path = "../../../../server/wayvr/room_binding.rs"]
mod binding;
pub mod windowing {
    pub use crate::binding::RoomBinding;
}
#[path = "../../../../server/wayvr/room.rs"]
pub mod room;
#[path = "../../../../server/wayvr/watch_menu.rs"]
pub mod watch_menu;
