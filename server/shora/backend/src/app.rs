//! Unified native runtime and shared Shora UI command handling.
use std::net::SocketAddr;
use std::sync::{Arc, RwLock};
use std::time::{Duration, Instant};
use firmware_protocol::ActionType;
use tokio::sync::mpsc;
use crate::{autobone, calibration::Calibration, config::Config, control::{Command, Envelope, ControlServer},
    face, feeder, haritorax::{self, bridge::Bridge, serial::Acquisition}, reset, skeleton, smoothing,
    solarxr::{self, Pose}, status::{StatusHandle, StatusSnapshot}, tracker::{self, TrackerRegistry}, ui::{UiMode, TuiHandle}};

pub async fn run(config: Config) -> anyhow::Result<()> {
    let started = Instant::now();
    let registry = Arc::new(RwLock::new(TrackerRegistry::default()));
    let pose = Arc::new(RwLock::new(Pose::default()));
    let calib = Arc::new(RwLock::new(Calibration::new()));
    let spatial_reference = std::env::var_os("WIVRN_SPATIAL_SOCKET").map(|path| {
        let shared = Arc::new(RwLock::new(crate::spatial_reference::Reference::default()));
        calib.write().unwrap().attach_reference(shared.clone());
        (std::path::PathBuf::from(path), shared)
    });
    let mut settings = config.tracking_settings();
    calib.write().unwrap().configure_drift(settings.drift_correction, settings.drift_amount);
    calib.write().unwrap().stay_aligned.configure(settings.stay_aligned);
    let target_height = Arc::new(RwLock::new(settings.height_m));
    let assignments = Arc::new(config.tracker_assignments.clone());
    let hmd = Arc::new(RwLock::new(None));
    let fresh_hmd: feeder::FreshHmdHandle = Arc::new(RwLock::new(None));
    let lengths = Arc::new(RwLock::new(skeleton::bone_lengths_from_height(config.height_m)));
    let autobone = Arc::new(RwLock::new(autobone::AutoboneState::default()));
    let status: StatusHandle = Arc::new(RwLock::new(StatusSnapshot {
        version: env!("CARGO_PKG_VERSION").into(), pid: std::process::id(), tracker_port: config.tracker_port,
        solarxr_socket: config.solarxr_socket.clone(), feeder_socket: config.feeder_socket.clone(),
        control_socket: config.control_socket.as_ref().map(|p| p.display().to_string()),
        config_path: config.config_path.as_ref().map(|p| p.display().to_string()),
        log_file: config.log_file.as_ref().map(|p| p.display().to_string()),
        face_source: if config.face.enabled { format!("{:?}", config.face.source).to_lowercase() } else { "disabled".into() },
        face_output: if config.face.translation_sheet.is_some() && config.face.output == face::config::OutputMode::Auto {
            "json".into()
        } else { format!("{:?}", config.face.output).to_lowercase() },
        haritorax_enabled: config.haritorax.enabled,
        settings,
        ..Default::default()
    }));
    let (commands, mut command_rx) = mpsc::channel::<Envelope>(32);
    // Claim the control endpoint before opening hardware or starting services.
    let control = if let Some(path) = &config.control_socket { Some(ControlServer::bind(path.clone()).await?) } else { None };
    let mut face_relay = if config.face.enabled { Some(face::FaceRelay::start(config.face.clone())?) } else { None };
    let mut acquisition = Acquisition::start(config.haritorax.clone())?;
    let mut bridge = Bridge::default();
    let mut services = tokio::task::JoinSet::new();
    if let Some((path, shared)) = spatial_reference { services.spawn(crate::spatial_reference::run(path, shared)); }
    if let Some(control) = control { services.spawn(control.run(status.clone(), commands.clone())); }
    services.spawn(tracker::udp::run(SocketAddr::from(([0,0,0,0],config.tracker_port)),
        registry.clone(), calib.clone(), hmd.clone(), assignments.clone(), config.ping_interval_secs, config.tracker_timeout_secs));
    services.spawn(solarxr::run(config.solarxr_socket.clone(), pose.clone(), registry.clone(),
        calib.clone(), hmd.clone(), lengths.clone(), autobone.clone(), target_height.clone()));
    services.spawn(feeder::run(config.feeder_socket.clone(), hmd.clone(), fresh_hmd.clone()));
    let mut tui = if config.ui == UiMode::Tui { Some(TuiHandle::start(status.clone(), commands.clone())?) } else { None };
    let mut filter = smoothing::RotationFilter::new(config.smoothing, config.prediction);
    let mut tick = tokio::time::interval(Duration::from_millis(33));
    tick.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
    let shutdown = async {
        let mut term = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())?;
        tokio::select! { r = tokio::signal::ctrl_c() => r, _ = term.recv() => Ok(()) }
    }; tokio::pin!(shutdown);
    let mut paused = false;
    let mut hardware_ops = tokio::task::JoinSet::new();
    loop {
        let mut pending = None;
        let mut hardware_done = None;
        let frame_due = tokio::select! {
            result = &mut shutdown => { result?; break; }
            result = services.join_next() => {
                match result {
                    Some(Ok(Err(error))) => return Err(error.context("server service failed")),
                    Some(Err(error)) => return Err(error.into()),
                    _ => anyhow::bail!("server service stopped unexpectedly"),
                }
            }
            Some(event) = acquisition.events.recv() => {
                bridge.handle(event, &mut registry.write().unwrap(), &mut calib.write().unwrap(), &assignments);
                false
            }
            Some(result) = hardware_ops.join_next(), if !hardware_ops.is_empty() => {
                hardware_done = Some(result?); false
            }
            Some(command) = command_rx.recv() => { pending = Some(command); false }
            _ = tick.tick() => true,
        };
        {
            let fresh = fresh_hmd.read().unwrap();
            *hmd.write().unwrap() = fresh.as_ref()
                .filter(|h| h.at.elapsed().as_secs_f32() <= crate::stay_aligned::FRESH_SECS)
                .map(|h| h.pose);
        }
        for port in acquisition.ports() {
            for tracker in port.trackers {
                if let Some(hardware) = tracker.settings.filter(|_| tracker.settings_age_ms.is_some_and(|age| age < 5000)) {
                    let mut calibration = calib.write().unwrap();
                    calibration.observe_hardware(tracker::TrackerId::new(tracker.mac, 0), hardware);
                    if let Some(thigh) = haritorax::thigh_name_for_ankle(&tracker.name) {
                        calibration.observe_hardware(tracker::TrackerId::new(haritorax::identity::mac_from_name(thigh), 0), hardware);
                    }
                }
            }
        }
        calib.write().unwrap().check_hmd_reference(hmd.read().unwrap().is_some());
        if let Some(relay) = face_relay.as_mut() { relay.check_health()?; }
        if let Some(tui) = tui.as_mut() { tui.check_health()?; }
        if frame_due {
            for command in bridge.take_actions() { let _ = commands.try_send(command.into()); }
        }
        let mut stop = false;
        let mut reply = None;
        if let Some((envelope, result, ids)) = hardware_done {
            let envelope: Envelope = envelope;
            let result: anyhow::Result<()> = result;
            let ids: Vec<tracker::TrackerId> = ids;
            for id in ids { calib.write().unwrap().clear_tracker_drift(id); }
            status.write().unwrap().last_action = match &result {
                Ok(()) => "Tracker confirmed sensor settings; repeat standing and mounting calibration".into(),
                Err(e) => format!("{e:#}"),
            };
            reply = envelope.reply.map(|tx| (tx, result.map_err(|e| format!("{e:#}"))));
        }
        if let Some(envelope) = pending {
            if let Command::SetHaritoraSettings {tracker_mac, hardware} = envelope.command {
                if hardware.validate().is_ok() && hardware_ops.is_empty() {
                    let mut ids = vec![tracker::TrackerId::new(tracker_mac, 0)];
                    for port in acquisition.ports() {
                        for tracker in port.trackers.iter().filter(|t| t.mac == tracker_mac) {
                            if let Some(thigh) = haritorax::thigh_name_for_ankle(&tracker.name) {
                                ids.push(tracker::TrackerId::new(haritorax::identity::mac_from_name(thigh), 0));
                            }
                        }
                    }
                    for id in &ids { calib.write().unwrap().clear_tracker_drift(*id); }
                    let operation = acquisition.set_settings(tracker_mac, hardware);
                    hardware_ops.spawn(async move { (envelope, operation.await, ids) });
                    continue;
                }
            }
            let result = match envelope.command {
                Command::Status => Ok(()),
                Command::Shutdown => { stop = true; Ok(()) }
                Command::ShutdownTrackers => acquisition.shutdown_trackers().await,
                Command::SetHaritoraSettings { hardware, .. } => hardware.validate().and_then(|_| Err(anyhow::anyhow!("A sensor settings request is already pending"))),
                Command::PauseTracking => { paused = !paused; Ok(()) }
                Command::SetSettings { settings: next } => {
                    match config.save_tracking_settings(next) {
                        Err(error) => Err(error),
                        Ok(()) => {
                            // Validate and persist before changing any live state.
                            if next.smoothing != settings.smoothing || next.prediction != settings.prediction {
                                filter = smoothing::RotationFilter::new(next.smoothing, next.prediction);
                            }
                            if next.height_m != settings.height_m {
                                *lengths.write().unwrap() = skeleton::bone_lengths_from_height(next.height_m);
                                *target_height.write().unwrap() = next.height_m;
                            }
                            calib.write().unwrap().configure_drift(next.drift_correction, next.drift_amount);
                            calib.write().unwrap().stay_aligned.configure(next.stay_aligned);
                            settings = next;
                            Ok(())
                        }
                    }
                }
                Command::ClearDrift => { calib.write().unwrap().clear_drift(); Ok(()) }
                Command::ClearStayAligned => { calib.write().unwrap().stay_aligned.clear(); Ok(()) }
                Command::SaveRelaxedPose { pose: kind } => {
                    let trackers: Vec<_> = registry.read().unwrap().iter().cloned().collect();
                    let mut calibration = calib.write().unwrap();
                    if paused { Err(anyhow::anyhow!("Resume tracking before saving a relaxed pose")) }
                    else {
                        let now = Instant::now();
                        match calibration.capture_relaxed_pose(&trackers, kind, now) {
                        Ok(pose) => {
                            let mut next = settings;
                            next.stay_aligned.save(kind, pose);
                            config.save_tracking_settings(next).map(|()| {
                                settings = next;
                                calibration.stay_aligned.configure(settings.stay_aligned);
                                tracing::info!(?kind, ?pose, "Stay Aligned relaxed pose saved");
                            })
                        }
                        Err(error) => Err(error),
                    } }
                }
                Command::Restart => {
                    let result = acquisition.restart();
                    bridge.clear(&mut registry.write().unwrap());
                    result
                }
                command => {
                    let action = match command { Command::YawReset => ActionType::ResetYaw,
                        Command::FullReset => ActionType::Reset, Command::MountingReset => ActionType::ResetMounting, _ => unreachable!() };
                    let reg = registry.read().unwrap();
                    if reg.iter().all(|t| t.rotation.is_none()) { Err(anyhow::anyhow!("no tracker samples available for calibration")) }
                    else { reset::handle_user_action(&reg, &mut calib.write().unwrap(), hmd.read().unwrap().as_ref(), &action) }
                }
            };
            if let (Command::SaveRelaxedPose { pose }, Err(error)) = (envelope.command, &result) {
                tracing::warn!(?pose, error = %error, "Relaxed pose was not saved");
            }
            status.write().unwrap().last_action = match &result {
                Ok(()) => match envelope.command {
                    Command::SetSettings { .. } if config.config_path.is_some() => "Tracking settings applied and saved".into(),
                    Command::SetSettings { .. } => "Tracking settings applied (no startup profile)".into(),
                    Command::ShutdownTrackers => "Tracker shutdown commands sent".into(),
                    other => format!("{other:?}") },
                Err(e) => format!("{e:#}")
            };
            reply = envelope.reply.map(|tx| (tx, result.map_err(|e| format!("{e:#}"))));
        }
        if frame_due && !paused {
            let mut trackers: Vec<_> = registry.read().unwrap().iter().cloned().collect();
            let now = Instant::now();
            let alignment_hmd = fresh_hmd.read().unwrap().as_ref()
                .filter(|h| now.saturating_duration_since(h.at).as_secs_f32() <= crate::stay_aligned::FRESH_SECS)
                .map(|h| nalgebra::UnitQuaternion::from_quaternion(nalgebra::Quaternion::new(h.pose[6], h.pose[3], h.pose[4], h.pose[5])));
            calib.write().unwrap().update_alignment(&trackers, alignment_hmd, now);
            for t in &mut trackers { if let Some(rot) = t.rotation { t.rotation = Some(filter.apply(t.id(), rot, now)); } }
            let new_pose = skeleton::solve_pose_with_lengths(trackers.clone().into_iter(),
                &calib.read().unwrap(), *lengths.read().unwrap(), hmd.read().unwrap().as_ref());
            *pose.write().unwrap() = new_pose;
            let mut ab = autobone.write().unwrap();
            if ab.recording { ab.frames.push(autobone::Frame { trackers }); }
        }
        if frame_due && paused { calib.write().unwrap().stay_aligned.suspend(Instant::now()); }
        if frame_due || reply.is_some() {
            let mut snapshot = status.write().unwrap();
            snapshot.uptime_secs = started.elapsed().as_secs(); snapshot.paused = paused;
            snapshot.ports = acquisition.ports();
            snapshot.settings = settings;
            let reg = registry.read().unwrap();
            snapshot.refresh_pose(&pose.read().unwrap(), &reg);
            let calibration = calib.read().unwrap();
            snapshot.refresh_trackers(&reg, &bridge, &calibration);
            snapshot.calibrated_trackers = reg.iter().filter(|t| calibration.is_calibrated(t.id())).count();
            snapshot.spatial_reference = calibration.reference_health();
            snapshot.reference_needs_reset = calibration.reference_needs_reset();
            snapshot.drift_samples = reg.iter().filter(|t| calibration.has_drift_sample(t.id())).count();
            snapshot.hmd_pose_received = hmd.read().unwrap().is_some();
            snapshot.face_active = face_relay.as_ref().is_some_and(|relay| relay.active());
        }
        if let Some((tx, result)) = reply { let _ = tx.send(result); }
        if stop {
            // Allow the control reply to flush before cancellation closes clients.
            tokio::time::sleep(Duration::from_millis(30)).await;
            break;
        }
    }
    drop(tui);
    services.abort_all();
    while services.join_next().await.is_some() {}
    tracing::info!("native tracking hub stopped");
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use clap::Parser;
    use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
    use tokio::net::UnixStream;

    async fn request(client: &mut BufReader<UnixStream>, value: serde_json::Value) -> serde_json::Value {
        let mut bytes = serde_json::to_vec(&value).unwrap();
        bytes.push(b'\n');
        client.get_mut().write_all(&bytes).await.unwrap();
        let mut line = String::new();
        client.read_line(&mut line).await.unwrap();
        serde_json::from_str(&line).unwrap()
    }

    #[tokio::test]
    async fn apply_settings_saves_before_reply_and_survives_worker_restart() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("shora.toml");
        let control = directory.path().join("control");
        std::fs::write(&path, "height_m = 1.8\n").unwrap();
        let cli = crate::config::Cli::parse_from(["worker", "--config", path.to_str().unwrap(),
            "--tracker-port", "0", "--control-socket", control.to_str().unwrap(),
            "--solarxr-socket", directory.path().join("rpc").to_str().unwrap(),
            "--feeder-socket", directory.path().join("input").to_str().unwrap()]);
        let mut next = Config::load(&cli).unwrap().tracking_settings();
        next.height_m = 1.67;
        next.smoothing = 0.6;
        next.stay_aligned.standing = Some(crate::stay_aligned::RelaxedPose {
            upper_leg_deg: 12.0, lower_leg_deg: 4.0, foot_deg: 1.0,
        });
        next.stay_aligned.enabled = true;
        for restarted in [false, true] {
            let worker = run(Config::load(&cli).unwrap());
            let client = async {
                let stream = loop {
                    if let Ok(stream) = UnixStream::connect(&control).await { break stream; }
                    tokio::time::sleep(Duration::from_millis(10)).await;
                };
                let mut client = BufReader::new(stream);
                let response = if restarted {
                    request(&mut client, serde_json::json!({"command": "status"})).await
                } else {
                    request(&mut client, serde_json::json!({"command": "set_settings", "settings": next})).await
                };
                assert_eq!(response["ok"], true);
                assert_eq!(serde_json::from_value::<crate::settings::TrackingSettings>(response["status"]["settings"].clone()).unwrap(), next);
                assert_eq!(response["status"]["config_path"], path.to_str().unwrap());
                assert_eq!(Config::load(&cli).unwrap().tracking_settings(), next);
                let saved = std::fs::read_to_string(&path).unwrap();
                std::fs::remove_file(&path).unwrap();
                let mut rejected = next;
                rejected.height_m = 1.95;
                let response = request(&mut client, serde_json::json!({"command": "set_settings", "settings": rejected})).await;
                assert_eq!(response["ok"], false);
                assert!(response["error"].as_str().unwrap().contains(path.to_str().unwrap()));
                assert_eq!(serde_json::from_value::<crate::settings::TrackingSettings>(response["status"]["settings"].clone()).unwrap(), next);
                assert!(!path.exists());
                std::fs::write(&path, saved).unwrap();
                assert_eq!(request(&mut client, serde_json::json!({"command": "shutdown"})).await["ok"], true);
            };
            tokio::time::timeout(Duration::from_secs(5), async {
                let (result, ()) = tokio::join!(worker, client);
                result.unwrap();
            }).await.unwrap();
        }
    }
}
