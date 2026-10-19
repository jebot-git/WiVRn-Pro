use super::*;
fn s(time: f64, left: f32, right: f32, yaw: f32) -> Sample {
    Sample {
        time,
        revision: 1,
        pitch: [left, right],
        shortening: [
            0.4 * (1. - left.to_radians().cos()),
            0.4 * (1. - right.to_radians().cos()),
        ],
        head: [0., 1.7, 0.],
        torso: [Some(yaw), Some(yaw)],
    }
}
fn advance(g: &mut Detector, time: &mut f64, left: f32, right: f32, yaw: f32) -> Output {
    *time += 0.033;
    g.update(Some(s(*time, left, right, yaw)), Settings::default())
}
fn baseline(g: &mut Detector, time: &mut f64) {
    for _ in 0..65 {
        advance(g, time, 0., 0., 0.);
    }
    assert!(g.baseline.is_some());
    // Test recognition independently from the setup verification UI.
    g.calibrating = None;
}
fn step(g: &mut Detector, time: &mut f64, hand: usize, yaw: f32) -> Output {
    let mut out = Output::default();
    for angle in [0., 5., 10., 15., 20., 25., 20., 15., 10., 5., 0., 0.] {
        out = advance(
            g,
            time,
            if hand == 0 { angle } else { 0. },
            if hand == 1 { angle } else { 0. },
            yaw,
        );
    }
    out
}
#[test]
fn alternating_steps_start_bounded_forward_and_torso_turn() {
    let mut g = Detector::default();
    let mut time = 0.;
    baseline(&mut g, &mut time);
    assert_eq!(step(&mut g, &mut time, 0, 0.).forward, 0.);
    let out = step(&mut g, &mut time, 1, -0.6);
    assert!(out.moving && out.forward > 0. && out.forward <= 0.8);
    // A subsequent leg movement keeps turning active.
    let out = step(&mut g, &mut time, 0, -0.6);
    assert!(out.turn > 0. && out.turn <= 0.65);
    for _ in 0..6 {
        advance(&mut g, &mut time, 0., 0., 0.7);
    }
    let out = advance(&mut g, &mut time, 0., 0., 1.);
    assert!(!out.moving && out.turn == 0.);
    for _ in 0..20 {
        advance(&mut g, &mut time, 0., 0., 1.);
    }
    let out = advance(&mut g, &mut time, 0., 0., 1.);
    assert_eq!(out.forward, 0.);
}
#[test]
fn drift_head_bobbing_crouching_and_same_leg_do_not_walk() {
    let mut g = Detector::default();
    let mut time = 0.;
    baseline(&mut g, &mut time);
    for i in 0..100 {
        time += 0.033;
        let mut sample = s(time, 0., 0., i as f32 * 0.003);
        sample.head[1] += 0.1 * (i as f32).sin();
        let out = g.update(Some(sample), Settings::default());
        assert_eq!(out.forward, 0.);
        assert_eq!(out.turn, 0.);
    }
    for _ in 0..4 {
        assert_eq!(step(&mut g, &mut time, 0, 0.).forward, 0.);
    }
    for angle in [0., 10., 20., 25., 20., 10., 0.] {
        assert_eq!(advance(&mut g, &mut time, angle, angle, 0.).forward, 0.);
    }
}
#[test]
fn held_knee_and_torso_rotation_cannot_sustain_turning() {
    let mut g = Detector::default();
    let mut time = 0.;
    baseline(&mut g, &mut time);
    step(&mut g, &mut time, 0, 0.);
    step(&mut g, &mut time, 1, 0.);
    for a in [5., 10., 15., 20., 25.] {
        advance(&mut g, &mut time, a, 0., 0.8);
    }
    for i in 0..12 {
        let noise = if i % 2 == 0 { 0.5 } else { -0.5 };
        advance(&mut g, &mut time, 25. + noise, 0., 1.);
    }
    let out = advance(&mut g, &mut time, 25., 0., 1.2);
    assert!(!out.moving && out.turn == 0.);
}
#[test]
fn missing_samples_resets_and_new_revision_cannot_replay_gait() {
    let mut g = Detector::default();
    let mut time = 0.;
    baseline(&mut g, &mut time);
    step(&mut g, &mut time, 0, 0.);
    assert!(step(&mut g, &mut time, 1, 0.).forward > 0.);
    assert_eq!(g.update(None, Settings::default()).forward, 0.);
    assert!(g.baseline.is_none());
    baseline(&mut g, &mut time);
    let mut sample = s(time + 0.033, 0., 0., 0.);
    sample.revision = 2;
    assert!(!g.update(Some(sample), Settings::default()).valid);
}
#[test]
fn real_translation_rejects_gait_and_missing_torso_only_disables_turning() {
    let mut g = Detector::default();
    let mut time = 0.;
    baseline(&mut g, &mut time);
    step(&mut g, &mut time, 0, 0.);
    step(&mut g, &mut time, 1, 0.);
    time += 0.033;
    let mut sample = s(time, 20., 0., 0.8);
    sample.head[0] = 0.1;
    assert_eq!(g.update(Some(sample), Settings::default()).forward, 0.);
    g.reset();
    baseline(&mut g, &mut time);
    for h in [0, 1, 0] {
        for a in [0., 5., 10., 15., 20., 25., 20., 15., 10., 5., 0., 0.] {
            time += 0.033;
            let mut sample = s(
                time,
                if h == 0 { a } else { 0. },
                if h == 1 { a } else { 0. },
                0.,
            );
            sample.torso = [None, None];
            let out = g.update(Some(sample), Settings::default());
            assert_eq!(out.turn, 0.);
        }
    }
    assert!(g.speed > 0.);
}
#[test]
fn settings_reject_nan_ranges_and_reversed_deadzone() {
    assert!(Settings {
        sensitivity: f32::NAN,
        ..Default::default()
    }
    .validate()
    .is_err());
    assert!(Settings {
        stop_ms: 0,
        ..Default::default()
    }
    .validate()
    .is_err());
    assert!(Settings {
        turn_deadzone_deg: 25.,
        turn_range_deg: 20.,
        ..Default::default()
    }
    .validate()
    .is_err());
    Settings::default().validate().unwrap();
}
#[test]
fn setup_suppresses_output_and_validates_a_marching_sample() {
    let mut g = Detector::default();
    let mut time = 0.;
    for _ in 0..65 {
        assert_eq!(advance(&mut g, &mut time, 0., 0., 0.).forward, 0.);
    }
    for i in 0..40 {
        let out = step(&mut g, &mut time, i % 2, 0.);
        if g.calibrating.is_none() {
            break;
        }
        assert_eq!(out.forward, 0.);
    }
    assert!(g.calibrating.is_none());
    assert!(g.baseline.is_some());
}

#[test]
fn real_sensor_freshness_assignment_and_calibration_gate_the_sample() {
    use crate::feeder::FreshHmdPose;
    use crate::tracker::{TrackerId, TrackerRegistry};
    use std::sync::{Arc, RwLock};
    let now = Instant::now();
    let hmd = Arc::new(RwLock::new(Some(FreshHmdPose {
        at: now,
        pose: [0., 1.7, 0., 0., 0., 0., 1.],
    })));
    let mut registry = TrackerRegistry::default();
    let mut calibration = Calibration::default();
    for role in [7, 8, 9, 10] {
        let id = TrackerId::new([role; 6], 0);
        let t = registry.ensure_local(id, role);
        t.status = SensorStatus::Ok;
        t.rotation = Some(nalgebra::UnitQuaternion::identity());
        t.rotation_updated_at = Some(now);
        calibration.tracker_mut(id).full_reset(
            nalgebra::UnitQuaternion::identity(),
            nalgebra::UnitQuaternion::identity(),
        );
    }
    let mut trackers: Vec<_> = registry.iter().cloned().collect();
    assert!(sample(&trackers, &calibration, &hmd, now, 1., 1).is_some());
    trackers[0].rotation_updated_at = Some(now - Duration::from_millis(151));
    trackers[0].last_seen = now; // Heartbeats cannot refresh orientation.
    assert!(sample(&trackers, &calibration, &hmd, now, 1., 1).is_none());
    trackers[0].rotation_updated_at = Some(now);
    assert!(sample(&trackers[..3], &calibration, &hmd, now, 1., 1).is_none());
    trackers.push(trackers[0].clone());
    assert!(sample(&trackers, &calibration, &hmd, now, 1., 1).is_none());
}

#[test]
fn either_torso_sensor_can_turn_and_hip_yaw_matches_stick_direction() {
    for torso in [0, 1] {
        let mut g = Detector::default();
        let mut time = 0.;
        baseline(&mut g, &mut time);
        let mut last = Output::default();
        for step in 0..4 {
            for a in [0., 5., 10., 15., 20., 25., 20., 15., 10., 5., 0., 0.] {
                time += 0.033;
                let mut sample = s(
                    time,
                    if step % 2 == 0 { a } else { 0. },
                    if step % 2 == 1 { a } else { 0. },
                    0.,
                );
                sample.torso = [None, None];
                sample.torso[torso] = Some(if step < 2 { 0. } else { -0.8 });
                last = g.update(Some(sample), Settings::default());
            }
        }
        assert!(last.turn_available && last.turn > 0.); // Negative XR yaw is a physical right turn.
        for _ in 0..10 {
            time += 0.033;
            let mut sample = s(time, 0., 0., 0.);
            sample.torso = [None, None];
            sample.torso[torso] = Some(-1.);
            last = g.update(Some(sample), Settings::default());
        }
        assert_eq!(last.turn, 0.);
    }
}

#[test]
fn setup_cannot_accept_repeated_movements_of_only_one_leg() {
    let mut g = Detector::default();
    let mut time = 0.;
    for _ in 0..65 {
        advance(&mut g, &mut time, 0., 0., 0.);
    }
    for _ in 0..25 {
        assert_eq!(step(&mut g, &mut time, 0, 0.).forward, 0.);
    }
    assert!(g.baseline.is_none() || g.calibrating.is_some());
}
