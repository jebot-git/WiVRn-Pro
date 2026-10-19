// SPDX-License-Identifier: GPL-3.0-only
//! Shared watch geometry and press/release state. No renderer or XR dependency.
use serde::Serialize;

pub const WIDTH: f32 = 1000.0;
pub const HEIGHT: f32 = 840.0;
pub const BUTTON: f32 = 128.0;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
pub enum Group {
    Calibration,
    Input,
    Utils,
}
pub const GROUPS: [Group; 3] = [Group::Calibration, Group::Input, Group::Utils];
impl Group {
    pub fn name(self) -> &'static str {
        match self {
            Self::Calibration => "Calibration",
            Self::Input => "Input",
            Self::Utils => "Utils",
        }
    }
    pub fn center(self) -> [f32; 2] {
        [
            match self {
                Self::Calibration => 400.0,
                Self::Input => 552.0,
                Self::Utils => 704.0,
            },
            580.0,
        ]
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
pub enum Action {
    Recenter,
    FixFloor,
    RememberFloor,
    ForgetFloor,
    FullReset,
    MountingReset,
    Keyboard,
    Dpad,
    HandControllers,
    Walking,
    BodyTurning,
    WalkingSetup,
    Performance,
    HandsFrame,
    Configuration,
    Edit,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
pub enum ButtonId {
    Parent(Group),
    Option(Action),
}
#[derive(Clone, Debug, Serialize)]
pub struct Button {
    pub id: ButtonId,
    pub widget: &'static str,
    pub group: Group,
    pub title: &'static str,
    pub icon: &'static str,
    pub x: f32,
    pub y: f32,
}

pub fn buttons(motoc: bool) -> Vec<Button> {
    let mut buttons = Vec::new();
    for group in GROUPS {
        let [x, y] = group.center();
        buttons.push(Button {
            id: ButtonId::Parent(group),
            group,
            widget: match group {
                Group::Calibration => "group_calibration",
                Group::Input => "group_input",
                Group::Utils => "group_utils",
            },
            title: group.name(),
            icon: match group {
                Group::Calibration => "watch/recenter.svg",
                Group::Input => "watch/sticks.svg",
                Group::Utils => "watch/settings.svg",
            },
            x: x - BUTTON / 2.0,
            y: y - BUTTON / 2.0,
        });
        let options: &[(Action, &str, &str, &str)] = match group {
            Group::Calibration => &[
                (
                    Action::Recenter,
                    "btn_recenter",
                    "Recenter",
                    "watch/recenter.svg",
                ),
                (
                    Action::FixFloor,
                    "btn_fix_floor",
                    "Fix floor",
                    "watch/fix-floor.svg",
                ),
                (
                    Action::RememberFloor,
                    "btn_remember_floor",
                    "Remember floor",
                    "edit/pin.svg",
                ),
                (
                    Action::ForgetFloor,
                    "btn_forget_floor",
                    "Forget floor",
                    "edit/close.svg",
                ),
                (
                    Action::FullReset,
                    "btn_full_reset",
                    if motoc { "Calibrate" } else { "Full reset" },
                    "watch/full-reset.svg",
                ),
                (
                    Action::MountingReset,
                    "btn_mounting_reset",
                    if motoc { "Resume" } else { "Mounting reset" },
                    "watch/mounting-reset.svg",
                ),
                (
                    Action::WalkingSetup,
                    "btn_walking_setup",
                    "Walking\nsetup",
                    "watch/full-reset.svg",
                ),
            ],
            Group::Input => &[
                (
                    Action::Keyboard,
                    "btn_keyboard",
                    "Keyboard",
                    "watch/keyboard.svg",
                ),
                (
                    Action::Dpad,
                    "btn_hand_joysticks",
                    "Virtual\nD-pad",
                    "watch/sticks.svg",
                ),
                (
                    Action::HandControllers,
                    "btn_hand_controllers",
                    "Hands as\ncontrollers",
                    "watch/sticks.svg",
                ),
                (
                    Action::Walking,
                    "btn_walking",
                    "Walking in\nplace",
                    "watch/full-reset.svg",
                ),
                (
                    Action::BodyTurning,
                    "btn_body_turning",
                    "Body\nturning",
                    "watch/recenter.svg",
                ),
            ],
            Group::Utils => &[
                (
                    Action::HandsFrame,
                    "btn_hands_frame",
                    "Hands\nFrame",
                    "watch/edit.svg",
                ),
                (
                    Action::Performance,
                    "btn_performance",
                    "fpsVR",
                    "watch/performance.svg",
                ),
                (
                    Action::Configuration,
                    "btn_dashboard",
                    "Config\nmenu",
                    "watch/settings.svg",
                ),
                (
                    Action::Edit,
                    "btn_edit_mode",
                    "Edit layout",
                    "watch/edit.svg",
                ),
            ],
        };
        for (i, &(action, widget, title, icon)) in options.iter().enumerate() {
            let (start, step, radius) = match group {
                Group::Calibration => (-165.0, 30.0, 320.0),
                Group::Input => (-165.0, 37.5, 320.0),
                Group::Utils => (-170.0, 45.0, 280.0),
            };
            let angle = (start + step * i as f32).to_radians();
            buttons.push(Button {
                id: ButtonId::Option(action),
                group,
                widget,
                title,
                icon,
                x: if action == Action::WalkingSetup {
                    x - BUTTON / 2.0
                } else {
                    x + radius * angle.cos() - BUTTON / 2.0
                },
                y: if action == Action::WalkingSetup {
                    y - 150.0 - BUTTON / 2.0
                } else {
                    y + radius * angle.sin() - BUTTON / 2.0
                },
            });
        }
    }
    buttons
}

#[derive(Default)]
pub struct Menu {
    open: Option<Group>,
    armed: [Option<ButtonId>; 2],
}
impl Menu {
    pub fn open(&self) -> Option<Group> {
        self.open
    }
    pub fn visible(&self, id: ButtonId, group: Group) -> bool {
        match self.open {
            None => matches!(id, ButtonId::Parent(_)),
            Some(active) => active == group,
        }
    }
    pub fn press(&mut self, pointer: usize, button: Option<ButtonId>) {
        if pointer >= self.armed.len() {
            return;
        }
        self.armed[pointer] = button.filter(|b| self.allowed(*b));
    }
    pub fn release(&mut self, pointer: usize, button: Option<ButtonId>) -> Option<Action> {
        let armed = self.armed.get_mut(pointer)?.take()?;
        if Some(armed) != button || !self.allowed(armed) {
            return None;
        }
        self.armed = [None; 2]; // a second held pinch cannot activate newly exposed buttons
        match armed {
            ButtonId::Parent(group) => {
                self.open = if self.open == Some(group) {
                    None
                } else {
                    Some(group)
                };
                None
            }
            ButtonId::Option(action) => {
                self.open = None;
                Some(action)
            }
        }
    }
    pub fn cancel_pointer(&mut self, pointer: usize) {
        if let Some(armed) = self.armed.get_mut(pointer) {
            *armed = None;
        }
    }
    pub fn close(&mut self) {
        self.open = None;
        self.armed = [None; 2];
    }
    fn allowed(&self, id: ButtonId) -> bool {
        match id {
            ButtonId::Parent(group) => self.open.is_none_or(|active| active == group),
            ButtonId::Option(action) => buttons(false)
                .iter()
                .any(|b| b.id == ButtonId::Option(action) && Some(b.group) == self.open),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn click(menu: &mut Menu, pointer: usize, id: ButtonId) -> Option<Action> {
        menu.press(pointer, Some(id));
        menu.release(pointer, Some(id))
    }
    #[test]
    fn fans_fit_and_equal_sized_buttons_never_overlap() {
        for group in GROUPS {
            let buttons = buttons(false)
                .into_iter()
                .filter(|b| b.group == group)
                .collect::<Vec<_>>();
            for (i, a) in buttons.iter().enumerate() {
                assert!(
                    a.x >= 0.0 && a.y >= 0.0 && a.x + BUTTON <= WIDTH && a.y + BUTTON <= HEIGHT
                );
                for b in &buttons[i + 1..] {
                    assert!(
                        a.x + BUTTON + 12.0 <= b.x
                            || b.x + BUTTON + 12.0 <= a.x
                            || a.y + BUTTON + 12.0 <= b.y
                            || b.y + BUTTON + 12.0 <= a.y,
                        "overlap: {} / {}",
                        a.widget,
                        b.widget
                    );
                }
            }
        }
    }
    #[test]
    fn only_active_group_and_parent_can_activate_and_options_close() {
        let mut m = Menu::default();
        click(&mut m, 0, ButtonId::Parent(Group::Input));
        click(&mut m, 0, ButtonId::Parent(Group::Utils));
        assert_eq!(m.open(), Some(Group::Input));
        assert_eq!(click(&mut m, 0, ButtonId::Option(Action::FixFloor)), None);
        assert_eq!(m.open(), Some(Group::Input));
        assert_eq!(
            click(&mut m, 0, ButtonId::Option(Action::Keyboard)),
            Some(Action::Keyboard)
        );
        assert_eq!(m.open(), None);
    }
    #[test]
    fn matching_release_required_and_held_second_hand_cannot_click_through() {
        let mut m = Menu::default();
        m.press(0, Some(ButtonId::Parent(Group::Input)));
        m.press(1, Some(ButtonId::Parent(Group::Utils)));
        m.release(0, Some(ButtonId::Parent(Group::Input)));
        assert_eq!(m.release(1, Some(ButtonId::Option(Action::Keyboard))), None);
        assert_eq!(m.release(0, Some(ButtonId::Option(Action::Keyboard))), None);
        m.press(0, Some(ButtonId::Option(Action::Keyboard)));
        m.release(0, Some(ButtonId::Option(Action::Dpad)));
        assert_eq!(m.open(), Some(Group::Input));
        click(&mut m, 0, ButtonId::Parent(Group::Input));
        assert_eq!(m.open(), None);
    }
    #[test]
    fn canceled_presses_do_not_select_and_motoc_keeps_calibration_actions() {
        let mut m = Menu::default();
        m.press(0, Some(ButtonId::Parent(Group::Calibration)));
        m.cancel_pointer(0);
        m.release(0, Some(ButtonId::Parent(Group::Calibration)));
        assert_eq!(m.open(), None);
        let buttons = buttons(true);
        assert_eq!(
            buttons
                .iter()
                .find(|b| b.id == ButtonId::Option(Action::FullReset))
                .unwrap()
                .title,
            "Calibrate"
        );
        assert_eq!(
            buttons
                .iter()
                .find(|b| b.id == ButtonId::Option(Action::MountingReset))
                .unwrap()
                .title,
            "Resume"
        );
    }
}
