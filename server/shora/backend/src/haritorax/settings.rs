//! GX6/GX2 settings layout from haritorax-interpreter (see THIRD_PARTY.md).
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct HardwareSettings {
    pub sensor_mode: u8,
    pub fps: u16,
    pub accelerometer: bool,
    pub gyroscope: bool,
    pub magnetometer: bool,
}
impl HardwareSettings {
    pub fn validate(&self) -> anyhow::Result<()> {
        anyhow::ensure!(matches!(self.sensor_mode, 1 | 2), "Sensor mode must be 1 (magnetometer on) or 2 (off)");
        anyhow::ensure!(matches!(self.fps, 50 | 100), "HaritoraX FPS must be 50 or 100");
        Ok(())
    }
    pub fn decode(hex: &str) -> Option<Self> {
        if hex.len()!=14 || !hex.bytes().all(|c|c.is_ascii_hexdigit()) {return None;}
        let bytes=hex.as_bytes();
        if !matches!(bytes[5], b'0' | b'1') || !matches!(bytes[6], b'0' | b'1') {return None;}
        let bits=(bytes[10] as char).to_digit(16)?;
        if bits>7 {return None;}
        Some(Self {sensor_mode:if bytes[6]==b'1' {1} else {2}, fps:if bytes[5]==b'1' {100} else {50},
            accelerometer:bits&1!=0, gyroscope:bits&2!=0, magnetometer:bits&4!=0})
    }
    pub fn patch(&self, hex: &str) -> anyhow::Result<String> {
        self.validate()?;
        anyhow::ensure!(Self::decode(hex).is_some(), "Unknown tracker settings layout; refresh settings first");
        // Do not accidentally repeat a power-off/communication transition pulse.
        anyhow::ensure!(hex.as_bytes()[12]==b'0', "Tracker is in a hardware transition; retry after it settles");
        let mut bytes=hex.to_ascii_lowercase().into_bytes();
        bytes[5]=if self.fps==100 {b'1'} else {b'0'};
        bytes[6]=if self.sensor_mode==1 {b'1'} else {b'0'};
        bytes[10]=b'0'+self.accelerometer as u8+2*self.gyroscope as u8+4*self.magnetometer as u8;
        // Preserve ankle detection, device-specific fields and communication bits.
        Ok(String::from_utf8(bytes)?)
    }
}

#[derive(Debug, Clone, Serialize)]
pub struct HardwareTracker {
    pub name: String,
    pub mac: [u8; 6],
    pub channel: u8,
    pub settings: Option<HardwareSettings>,
    pub settings_age_ms: Option<u64>,
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn matches_upstream_layout_preserves_unrelated_digits_and_rejects_unknowns() {
        for mode in [1,2] { for fps in [50,100] { for bits in 0..8 {
            let settings=HardwareSettings {sensor_mode:mode,fps,accelerometer:bits&1!=0,gyroscope:bits&2!=0,magnetometer:bits&4!=0};
            let original="abcd0000100001";
            let patched=settings.patch(original).unwrap();
            assert_eq!(HardwareSettings::decode(&patched),Some(settings));
            for i in 0..14 {if ![5,6,10].contains(&i) {assert_eq!(patched.as_bytes()[i],original.as_bytes()[i]);}}
        }}}
        let mut settings=HardwareSettings::decode("00000110103000").unwrap();
        assert_eq!(settings.sensor_mode,1); assert_eq!(settings.fps,100);
        assert!(settings.accelerometer && settings.gyroscope && !settings.magnetometer);
        for hex in ["", "00000900103000", "0000000010f000", "00000000103020"] {assert!(settings.patch(hex).is_err());}
        settings.fps=90; assert!(settings.validate().is_err());
        settings.fps=50; settings.sensor_mode=3; assert!(settings.validate().is_err());
    }
}
