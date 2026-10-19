//! Validated body-tracking settings shared by startup config and live controls.
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct TrackingSettings {
    #[serde(default)]
    pub walking: crate::walking::Settings,
    pub height_m: f32,
    /// Slerp blend toward the newest sample: 0 disables, smaller nonzero = smoother.
    pub smoothing: f32,
    /// Prediction horizon in seconds.
    pub prediction: f32,
    pub drift_correction: bool,
    pub drift_amount: f32,
    #[serde(default)]
    pub stay_aligned: crate::stay_aligned::Settings,
}
impl Default for TrackingSettings {
    fn default() -> Self {
        Self { walking: Default::default(), height_m: 1.8, smoothing: 0.3, prediction: 0.0,
            drift_correction: false, drift_amount: 0.5, stay_aligned: Default::default() }
    }
}
impl TrackingSettings {
    pub fn validate(&self) -> anyhow::Result<()> {
        anyhow::ensure!(self.height_m.is_finite() && (0.5..=3.0).contains(&self.height_m),
            "height_m must be between 0.5 and 3.0 metres");
        anyhow::ensure!(self.smoothing.is_finite() && (0.0..=1.0).contains(&self.smoothing),
            "smoothing must be between 0 and 1 (0 disables smoothing)");
        anyhow::ensure!(self.prediction.is_finite() && (0.0..=0.2).contains(&self.prediction),
            "prediction must be between 0 and 0.2 seconds");
        anyhow::ensure!(self.drift_amount.is_finite() && (0.0..=1.0).contains(&self.drift_amount),
            "drift_amount must be between 0 and 1");
        self.stay_aligned.validate()?;
        self.walking.validate()?;
        anyhow::ensure!(!(self.drift_correction && self.stay_aligned.enabled),
            "Stay Aligned and reset-based drift correction cannot be enabled together");
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn rejects_invalid_settings_before_application() {
        for settings in [
            TrackingSettings { smoothing: f32::NAN, ..Default::default() },
            TrackingSettings { smoothing: -0.1, ..Default::default() },
            TrackingSettings { prediction: 0.21, ..Default::default() },
            TrackingSettings { height_m: 0.0, ..Default::default() },
            TrackingSettings { drift_amount: 1.1, ..Default::default() },
        ] { assert!(settings.validate().is_err()); }
        TrackingSettings::default().validate().unwrap();
    }

    #[test]
    fn stay_aligned_requires_setup_and_excludes_drift_compensation() {
        let mut settings = TrackingSettings::default();
        settings.stay_aligned.enabled = true;
        assert!(settings.validate().is_err());
        settings.stay_aligned.standing = Some(crate::stay_aligned::RelaxedPose { upper_leg_deg: 10.0, lower_leg_deg: 8.0, foot_deg: 0.0 });
        settings.validate().unwrap();
        settings.drift_correction = true;
        assert!(settings.validate().is_err());
        settings.drift_correction = false;
        settings.stay_aligned.rate_deg_per_sec = f32::NAN;
        assert!(settings.validate().is_err());
        // Old clients/configs that omit Stay Aligned remain valid.
        let old = r#"{"height_m":1.8,"smoothing":0.3,"prediction":0.0,"drift_correction":false,"drift_amount":0.5}"#;
        let old: TrackingSettings = serde_json::from_str(old).unwrap();
        assert!(!old.stay_aligned.enabled);
    }
}
