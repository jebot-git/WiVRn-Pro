use super::*;
use rosc::{OscMessage, OscType};
use std::collections::HashMap;

async fn configure(relay: &FaceRelay, cfg: FaceConfig) -> Result<(), String> {
    relay.configure(cfg, || Ok(())).unwrap().await.unwrap()
}

#[tokio::test]
async fn live_modes_preserve_acquisition_and_disabled_relay_releases_ports() {
    use super::super::config::OutputMode;
    let (mut cfg, sink) = test_config();
    cfg.enabled = false;
    cfg.output = OutputMode::Native;
    let relay = FaceRelay::start(cfg.clone()).unwrap();
    assert_eq!(relay.snapshot().state, "disabled");
    cfg.enabled = true;
    configure(&relay, cfg.clone()).await.unwrap();
    let sender = UdpSocket::bind("127.0.0.1:0").unwrap();
    sender.send_to(&rosc::encoder::encode(&message("/jawOpen", OscType::Float(0.5))).unwrap(),
        ("127.0.0.1", cfg.babble_port)).unwrap();
    let mut bytes = vec![0; 102]; bytes[36] = 128;
    wait_values(&sink, &[("/tracking/face/v1", OscType::Blob(bytes.clone()))]);
    cfg.output = OutputMode::Unift;
    configure(&relay, cfg.clone()).await.unwrap();
    // No new input: replacing routing must retain the existing source and data.
    wait_values(&sink, &[("/avatar/parameters/FT/v2/JawOpen", OscType::Float(0.5))]);
    cfg.output = OutputMode::Native;
    configure(&relay, cfg.clone()).await.unwrap();
    wait_values(&sink, &[("/tracking/face/v1", OscType::Blob(bytes))]);
    cfg.enabled = false;
    configure(&relay, cfg.clone()).await.unwrap();
    wait_values(&sink, &[("/tracking/face/v1", OscType::Blob(vec![0; 102]))]);
    let feedback = UdpSocket::bind(("127.0.0.1", cfg.osc_port)).unwrap();
    let babble = UdpSocket::bind(("127.0.0.1", cfg.babble_port)).unwrap();
    cfg.enabled = true;
    assert!(configure(&relay, cfg.clone()).await.is_err());
    assert!(!relay.snapshot().settings.enabled);
    drop(feedback); drop(babble);
    configure(&relay, cfg).await.unwrap();
}

#[tokio::test]
async fn live_rejected_mapping_ports_and_persistence_keep_previous_output() {
    use super::super::config::OutputMode;
    use std::sync::atomic::AtomicUsize;
    let (mut cfg, sink) = test_config(); cfg.output = OutputMode::Native;
    let relay = FaceRelay::start(cfg.clone()).unwrap();
    let count = Arc::new(AtomicUsize::new(0));
    let mut bad = cfg.clone(); bad.output = OutputMode::Json;
    bad.translation_sheet = Some("/missing-live-face-test.json".into());
    let seen = count.clone();
    assert!(relay.configure(bad, move || { seen.fetch_add(1, Ordering::SeqCst); Ok(()) }).unwrap().await.unwrap().is_err());
    assert_eq!(count.load(Ordering::SeqCst), 0);
    let busy = UdpSocket::bind("127.0.0.1:0").unwrap();
    let mut bad = cfg.clone(); bad.osc_port = busy.local_addr().unwrap().port();
    assert!(configure(&relay, bad).await.is_err());
    let mut bad = cfg.clone(); bad.output = OutputMode::Unift;
    assert!(relay.configure(bad, || anyhow::bail!("simulated profile write failure")).unwrap().await.unwrap().is_err());
    assert_eq!(relay.snapshot().settings, cfg);
    let sender = UdpSocket::bind("127.0.0.1:0").unwrap();
    sender.send_to(&rosc::encoder::encode(&message("/jawOpen", OscType::Float(0.75))).unwrap(),
        ("127.0.0.1", cfg.babble_port)).unwrap();
    let mut bytes = vec![0; 102]; bytes[36] = 191;
    wait_values(&sink, &[("/tracking/face/v1", OscType::Blob(bytes))]);
}

#[tokio::test]
async fn independent_live_eye_expression_masks_do_not_lose_raw_values() {
    use super::super::config::OutputMode;
    let (mut cfg, sink) = test_config(); cfg.output = OutputMode::Native;
    let relay = FaceRelay::start(cfg.clone()).unwrap();
    let sender = UdpSocket::bind("127.0.0.1:0").unwrap();
    for (addr, value) in [("/jawOpen", 0.5), ("/LeftEyeX", 0.5)] {
        sender.send_to(&rosc::encoder::encode(&message(addr, OscType::Float(value))).unwrap(),
            ("127.0.0.1", cfg.babble_port)).unwrap();
    }
    let mut both = vec![0; 102]; both[5] = 128; both[36] = 128;
    wait_values(&sink, &[("/tracking/face/v1", OscType::Blob(both.clone()))]);
    cfg.eye_enabled = false;
    configure(&relay, cfg.clone()).await.unwrap();
    let mut face = vec![0; 102]; face[36] = 128;
    wait_values(&sink, &[("/tracking/face/v1", OscType::Blob(face))]);
    cfg.eye_enabled = true; cfg.expression_enabled = false;
    configure(&relay, cfg.clone()).await.unwrap();
    let mut eyes = vec![0; 102]; eyes[5] = 128;
    wait_values(&sink, &[("/tracking/face/v1", OscType::Blob(eyes)),
        ("/avatar/parameters/ExpressionTrackingActive", OscType::Bool(false))]);
    cfg.expression_enabled = true;
    configure(&relay, cfg).await.unwrap();
    wait_values(&sink, &[("/tracking/face/v1", OscType::Blob(both))]);
}

fn test_config() -> (FaceConfig, UdpSocket) {
    let sink = UdpSocket::bind("127.0.0.1:0").unwrap();
    sink.set_read_timeout(Some(Duration::from_millis(50))).unwrap();
    let feedback = UdpSocket::bind("127.0.0.1:0").unwrap();
    let babble = UdpSocket::bind("127.0.0.1:0").unwrap();
    let cfg = FaceConfig {
        enabled: true,
        osc_query: false,
        destination: sink.local_addr().unwrap(),
        osc_port: feedback.local_addr().unwrap().port(),
        babble_port: babble.local_addr().unwrap().port(),
        ..FaceConfig::default()
    };
    (cfg, sink)
}

fn message(addr: &str, value: OscType) -> OscPacket {
    OscPacket::Message(OscMessage { addr: addr.into(), args: vec![value] })
}

fn avatar_packet(uid: &str, native: bool) -> OscPacket {
    OscPacket::Bundle(OscBundle { timetag: (0, 1).into(), content: vec![
        message("/avatar/change", OscType::String(uid.into())),
        message("/avatar/name", OscType::String("Test avatar".into())),
        message("/avatar/avatarfullfacetracking", OscType::Bool(native)),
    ] })
}

#[test]
fn avatar_handshake_accepts_all_orders_and_resets_interrupted_changes() {
    let OscPacket::Bundle(packet) = avatar_packet("avtr_native", true) else { unreachable!() };
    for order in [[0,1,2], [0,2,1], [1,0,2], [1,2,0], [2,0,1], [2,1,0]] {
        let mut avatar = PendingAvatarChange::default();
        for (index, item) in order.into_iter().enumerate() {
            let OscPacket::Message(message) = &packet.content[item] else { unreachable!() };
            assert_eq!(avatar.receive(message), index == 2);
        }
        assert_eq!(avatar.native, Some(true));
    }
    let mut avatar = PendingAvatarChange::default();
    let mut cache = FaceParams::new();
    let mut vsync = false;
    assert!(handle_feedback(&avatar_packet("avtr_a", true), &mut cache, &mut vsync, &mut None, &mut avatar));
    assert_eq!(avatar.native, Some(true));
    handle_feedback(&message("/avatar/change", OscType::String("avtr_old_client".into())),
        &mut cache, &mut vsync, &mut None, &mut avatar);
    assert_eq!(avatar.native, None);
    // A repeated uid starts a new change instead of joining stale metadata.
    handle_feedback(&avatar_packet("avtr_b", false), &mut cache, &mut vsync, &mut None, &mut avatar);
    assert_eq!(avatar.native, Some(false));
}

#[test]
fn explicit_native_udp_output_works_without_discovery_and_ignores_legacy_avatar_feedback() {
    let (mut cfg, sink) = test_config();
    cfg.output = super::super::config::OutputMode::Native;
    let relay = FaceRelay::start(cfg.clone()).unwrap();
    let sender = UdpSocket::bind("127.0.0.1:0").unwrap();
    let send = |packet: OscPacket, port| {
        sender.send_to(&rosc::encoder::encode(&packet).unwrap(), ("127.0.0.1", port)).unwrap();
    };
    send(message("/jawOpen", OscType::Float(0.5)), cfg.babble_port);
    let mut bytes = vec![0; 102];
    bytes[36] = 128;
    let values = wait_values(&sink, &[("/tracking/face/v1", OscType::Blob(bytes.clone()))]);
    assert!(!values.contains_key("/avatar/parameters/FT/v2/JawOpen"));
    send(avatar_packet("avtr_legacy", false), cfg.osc_port);
    send(message("/jawOpen", OscType::Float(0.5)), cfg.babble_port);
    wait_values(&sink, &[("/tracking/face/v1", OscType::Blob(bytes.clone()))]);
    wait_values(&sink, &[("/tracking/face/v1", OscType::Blob(vec![0; 102]))]);
    send(message("/jawOpen", OscType::Float(0.5)), cfg.babble_port);
    wait_values(&sink, &[("/tracking/face/v1", OscType::Blob(bytes))]);
    drop(relay);
    wait_values(&sink, &[("/tracking/face/v1", OscType::Blob(vec![0; 102]))]);
}

#[test]
fn native_udp_output_switches_avatars_and_clears_expired_and_stopped_sources() {
    let (cfg, sink) = test_config();
    let relay = FaceRelay::start(cfg.clone()).unwrap();
    let sender = UdpSocket::bind("127.0.0.1:0").unwrap();
    let send = |packet: OscPacket, port| {
        sender.send_to(&rosc::encoder::encode(&packet).unwrap(), ("127.0.0.1", port)).unwrap();
    };
    send(avatar_packet("avtr_native", true), cfg.osc_port);
    send(message("/jawOpen", OscType::Float(0.5)), cfg.babble_port);
    let mut bytes = vec![0; 102];
    bytes[36] = 128;
    wait_values(&sink, &[("/tracking/face/v1", OscType::Blob(bytes.clone()))]);
    wait_values(&sink, &[("/tracking/face/v1", OscType::Blob(vec![0; 102])),
        ("/avatar/parameters/ExpressionTrackingActive", OscType::Bool(false))]);
    send(message("/jawOpen", OscType::Float(0.5)), cfg.babble_port);
    wait_values(&sink, &[("/tracking/face/v1", OscType::Blob(bytes.clone()))]);
    send(avatar_packet("avtr_legacy", false), cfg.osc_port);
    wait_values(&sink, &[("/avatar/parameters/FT/v2/JawOpen", OscType::Float(0.5))]);
    send(avatar_packet("avtr_native_again", true), cfg.osc_port);
    wait_values(&sink, &[("/tracking/face/v1", OscType::Blob(bytes))]);
    drop(relay);
    wait_values(&sink, &[("/tracking/face/v1", OscType::Blob(vec![0; 102]))]);
}

fn collect(packet: OscPacket, values: &mut HashMap<String, OscType>) {
    match packet {
        OscPacket::Message(message) => {
            if let Some(value) = message.args.into_iter().next() { values.insert(message.addr, value); }
        }
        OscPacket::Bundle(bundle) => {
            for packet in bundle.content { collect(packet, values); }
        }
    }
}

fn wait_values(sink: &UdpSocket, expected: &[(&str, OscType)]) -> HashMap<String, OscType> {
    let deadline = Instant::now() + Duration::from_secs(3);
    let mut values = HashMap::new();
    let mut buf = [0; 65535];
    while Instant::now() < deadline {
        match sink.recv_from(&mut buf) {
            Ok((size, _)) => collect(rosc::decoder::decode_udp(&buf[..size]).unwrap().1, &mut values),
            Err(e) if matches!(e.kind(), std::io::ErrorKind::WouldBlock | std::io::ErrorKind::TimedOut) => {}
            Err(e) => panic!("receive output: {e}"),
        }
        if expected.iter().all(|(key, value)| values.get(*key).is_some_and(|found| {
            match (found, value) {
                (OscType::Float(a), OscType::Float(b)) => (a - b).abs() < 1e-5,
                _ => found == value,
            }
        })) { return values; }
    }
    panic!("missing expected OSC values {expected:?}; received {values:?}");
}

#[test]
fn babble_bundles_reach_output_expire_and_release_ports() {
    let (mut cfg, sink) = test_config();
    cfg.expose = true; // Input exposure must never turn output into 0.0.0.0.
    let mut relay = FaceRelay::start(cfg.clone()).unwrap();
    let sender = UdpSocket::bind("127.0.0.1:0").unwrap();
    let packet = OscPacket::Bundle(OscBundle {
        timetag: (0, 1).into(),
        content: vec![
            message("/jawOpen", OscType::Float(0.65)),
            OscPacket::Bundle(OscBundle {
                timetag: (0, 1).into(),
                content: vec![message("/LeftEyeLid", OscType::Float(0.25)),
                    message("/avatar/parameters/RightEyeLid", OscType::Float(0.2)),
                    message("/avatar/parameters/LeftEyeX", OscType::Float(-0.3))],
            }),
        ],
    });
    sender.send_to(b"not OSC", ("127.0.0.1", cfg.babble_port)).unwrap();
    sender.send_to(&rosc::encoder::encode(&packet).unwrap(), ("127.0.0.1", cfg.babble_port)).unwrap();
    wait_values(&sink, &[
        ("/avatar/parameters/FT/v2/JawOpen", OscType::Float(0.65)),
        ("/avatar/parameters/FT/v2/EyeLeftX", OscType::Float(-0.3)),
        ("/avatar/parameters/FT/v2/EyeLidLeft", OscType::Float(0.0)),
        ("/avatar/parameters/FT/v2/EyeLidRight", OscType::Float(0.525)),
        ("/avatar/parameters/ExpressionTrackingActive", OscType::Bool(true)),
    ]);
    wait_values(&sink, &[
        ("/avatar/parameters/ExpressionTrackingActive", OscType::Bool(false)),
        ("/avatar/parameters/FT/v2/JawOpen", OscType::Float(0.0)),
    ]);
    relay.check_health().unwrap();
    drop(relay);
    let _feedback = UdpSocket::bind(("0.0.0.0", cfg.osc_port)).unwrap();
    let _babble = UdpSocket::bind(("0.0.0.0", cfg.babble_port)).unwrap();
}

#[test]
fn occupied_source_port_fails_startup_and_releases_feedback() {
    let (cfg, _sink) = test_config();
    let _occupied = UdpSocket::bind(("127.0.0.1", cfg.babble_port)).unwrap();
    let error = match FaceRelay::start(cfg.clone()) {
        Ok(_) => panic!("startup should fail"),
        Err(error) => error,
    };
    assert!(format!("{error:#}").contains("Babble/EyeTrackVR"));
    let _feedback = UdpSocket::bind(("127.0.0.1", cfg.osc_port)).unwrap();
}

#[test]
fn avatar_mapping_errors_are_reported_before_startup() {
    let cfg = FaceConfig {
        avatar: Some(std::path::PathBuf::from("/nonexistent/slimevr-avatar.json")),
        ..FaceConfig::default()
    };
    assert!(FaceOutput::load(&cfg).is_err());
}

#[test]
fn bundled_feedback_updates_cache_and_vsync() {
    let mut cache = FaceParams::new();
    let mut vsync = false;
    let packet = OscPacket::Bundle(OscBundle {
        timetag: (0, 1).into(),
        content: vec![message("/avatar/parameters/VSync", OscType::Bool(true)),
            message("/avatar/parameters/Test", OscType::Float(0.5))],
    });
    handle_feedback(&packet, &mut cache, &mut vsync, &mut None, &mut PendingAvatarChange::default());
    assert!(vsync);
    assert_eq!(cache["Test"], OscType::Float(0.5));
    handle_feedback(&message("/avatar/change", OscType::String("avtr_test".into())),
        &mut cache, &mut vsync, &mut None, &mut PendingAvatarChange::default());
    assert!(cache.is_empty());
}

#[test]
fn unift_and_vrchat_json_outputs_reach_udp_and_clear_on_expiry_and_shutdown() {
    use super::super::config::OutputMode;
    for mode in [OutputMode::Unift, OutputMode::Json, OutputMode::Both] {
        let (mut cfg, sink) = test_config();
        cfg.output = mode;
        if mode != OutputMode::Unift {
            cfg.translation_sheet = Some(concat!(env!("CARGO_MANIFEST_DIR"), "/examples/face/avatar-osc.json").into());
        }
        let relay = FaceRelay::start(cfg.clone()).unwrap();
        let sender = UdpSocket::bind("127.0.0.1:0").unwrap();
        let bytes = rosc::encoder::encode(&OscPacket::Bundle(OscBundle {
            timetag: (0, 1).into(), content: vec![
                message("/jawOpen", OscType::Float(0.65)),
                message("/jawLeft", OscType::Float(1.0)),
                message("/tongueOut", OscType::Float(0.8)),
                message("/LeftEyeX", OscType::Float(-0.3)),
            ],
        })).unwrap();
        sender.send_to(&bytes, ("127.0.0.1", cfg.babble_port)).unwrap();
        let mut expected = vec![];
        if mode != OutputMode::Json {
            expected.extend([
                ("/avatar/parameters/FT/v2/JawOpen", OscType::Float(0.65)),
                ("/avatar/parameters/FT/v2/TongueOut", OscType::Float(0.8)),
                ("/avatar/parameters/FT/v2/JawX", OscType::Float(-1.0)),
                ("/avatar/parameters/ExpressionTrackingActive", OscType::Bool(true)),
                ("/avatar/parameters/EyeTrackingActive", OscType::Bool(true)),
            ]);
        }
        if mode != OutputMode::Unift {
            expected.extend([
                ("/avatar/parameters/CustomMouthOpen", OscType::Float(0.65)),
                ("/avatar/parameters/JawSign", OscType::Bool(true)),
                ("/avatar/parameters/JawBit0", OscType::Bool(true)),
                ("/avatar/parameters/JawBit1", OscType::Bool(true)),
                ("/avatar/parameters/CustomTrackingActive", OscType::Bool(true)),
                ("/avatar/parameters/CustomEyeTrackingActive", OscType::Bool(true)),
            ]);
        }
        let received = wait_values(&sink, &expected);
        assert!(!received.contains_key("/feedback/mouth"));
        if mode == OutputMode::Json {
            assert!(!received.keys().any(|key| key.starts_with("/avatar/parameters/FT/v2/")));
        }
        // Static sheets must remain in use, and unchanged values must be sent to
        // the new avatar even though change suppression already cached them.
        sender.send_to(&rosc::encoder::encode(&message("/avatar/change", OscType::String("avtr_next".into()))).unwrap(),
            ("127.0.0.1", cfg.osc_port)).unwrap();
        wait_values(&sink, &expected);
        let (jaw, tracking) = if mode == OutputMode::Unift {
            ("/avatar/parameters/FT/v2/JawOpen", "/avatar/parameters/ExpressionTrackingActive")
        } else {
            ("/avatar/parameters/CustomMouthOpen", "/avatar/parameters/CustomTrackingActive")
        };
        wait_values(&sink, &[(jaw, OscType::Float(0.0)), (tracking, OscType::Bool(false))]);
        sender.send_to(&bytes, ("127.0.0.1", cfg.babble_port)).unwrap();
        wait_values(&sink, &[(jaw, OscType::Float(0.65)), (tracking, OscType::Bool(true))]);
        drop(relay);
        wait_values(&sink, &[(jaw, OscType::Float(0.0)), (tracking, OscType::Bool(false))]);
    }
}

#[test]
fn invalid_json_mapping_fails_before_binding_ports() {
    let (mut cfg, _sink) = test_config();
    cfg.output = super::super::config::OutputMode::Json;
    cfg.translation_sheet = Some("/nonexistent/avatar-translation-sheet.json".into());
    assert!(FaceRelay::start(cfg.clone()).is_err());
    let _feedback = UdpSocket::bind(("127.0.0.1", cfg.osc_port)).unwrap();
    let _babble = UdpSocket::bind(("127.0.0.1", cfg.babble_port)).unwrap();
}
