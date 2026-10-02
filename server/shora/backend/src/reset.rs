//! User-action → calibration orchestration.
//!
//! The chest tracker's button sends a [`ActionType`] user action; this module turns
//! it into the corresponding per-tracker calibration update.

use firmware_protocol::ActionType;
use nalgebra::{Quaternion, UnitQuaternion};

use crate::calibration::Calibration;
use crate::feeder::HmdPose;
use crate::tracker::TrackerRegistry;

/// Handle a tracker user action (full / yaw / mounting reset).
pub fn handle_user_action(
    registry: &TrackerRegistry,
    calib: &mut Calibration,
    hmd: Option<&HmdPose>,
    action: &ActionType,
) -> anyhow::Result<()> {
    match action {
        ActionType::Reset | ActionType::ResetYaw | ActionType::ResetMounting => {
            calib.prepare_reference_reset(hmd.is_some())?;
            let reference = reference_rotation(registry, hmd).unwrap_or_else(UnitQuaternion::identity);
            let mut count = 0;
            for t in registry.iter() {
                let Some(raw) = t.rotation else { continue };
                if calib.managed_reference() && !t.rotation_updated_at.is_some_and(|at|
                    at.elapsed().as_secs_f32() <= crate::stay_aligned::FRESH_SECS) {continue;}
                let previous_applied_deg = calib.drift_diagnostics(t.id()).map(|d| d.applied_deg).unwrap_or(0.0);
                let residual_deg = calib.reset_yaw_error(t.id(), raw, reference);
                let aligned_deg = calib.alignment_diagnostics(t.id()).map(|d| d.correction_deg).unwrap_or(0.0);
                match action {
                    ActionType::Reset => calib.tracker_mut(t.id()).full_reset(raw, reference),
                    ActionType::ResetYaw => calib.tracker_mut(t.id()).yaw_reset(raw, reference),
                    ActionType::ResetMounting => {
                        calib.tracker_mut(t.id()).mounting_reset(raw, reference, t.position)
                    }
                    _ => unreachable!(),
                }
                if let Some(drift) = calib.drift_diagnostics(t.id()) {
                    tracing::info!(tracker = ?t.id(), position = t.position, ?action,
                        samples = drift.samples, interval_secs = drift.last_interval_secs,
                        reset_deg = drift.last_reset_deg,
                        correction_deg_per_min = drift.correction_deg_per_min,
                        previous_applied_deg, aligned_deg, residual_deg, reference_hmd = hmd.is_some(),
                        horizon_secs = drift.horizon_secs, result = drift.last_result,
                        "yaw drift observation");
                }
                count += 1;
            }
            anyhow::ensure!(count > 0, "No tracker samples available for calibration");
            if matches!(action, ActionType::Reset | ActionType::ResetYaw) {calib.confirm_reference_reset();}
            tracing::info!(?action, "calibration applied to {count} trackers");
        }
        _ => {}
    }
    Ok(())
}

/// Pick the reference rotation for a reset. The synthetic trackers are emitted
/// HMD-anchored (`hmd_rot * local_rot`), so the local frame must be HMD-relative:
/// the HMD (feeder) pose is the primary yaw reference, matching the Java server.
/// Fall back to head → chest → first tracker when no HMD pose is available.
fn reference_rotation(
    registry: &TrackerRegistry,
    hmd: Option<&HmdPose>,
) -> Option<UnitQuaternion<f32>> {
    if let Some(h) = hmd {
        // HmdPose = [x, y, z, qx, qy, qz, qw] → nalgebra scalar-first (w, i, j, k).
        return Some(UnitQuaternion::from_quaternion(Quaternion::new(
            h[6], h[3], h[4], h[5],
        )));
    }
    for position in [1u8, 4u8] {
        // TrackerPosition HEAD, CHEST
        if let Some(t) = registry.iter().find(|t| t.position == position) {
            if let Some(r) = t.rotation {
                return Some(r);
            }
        }
    }
    registry.iter().find_map(|t| t.rotation)
}
