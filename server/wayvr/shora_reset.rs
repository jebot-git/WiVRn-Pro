// SPDX-License-Identifier: GPL-3.0-only
//! Countdown and control I/O run off the render thread. A reset is never replayed
//! against a replacement worker, and backend validation errors remain visible.
use std::{path::{Path, PathBuf}, sync::{Arc, Mutex, mpsc}, time::{Duration, Instant}};

#[derive(Clone, Copy, Debug)]
pub enum ResetKind { Walking, Full, Mounting, MotocCalibrate, MotocContinue }
impl ResetKind {
    fn validate(self, status: &super::Status) -> anyhow::Result<()> {
        let motoc = matches!(self, Self::MotocCalibrate | Self::MotocContinue);
        anyhow::ensure!(motoc == (status.tracking_mode == "motoc"), "Bridge mode changed; retry using the current calibration controls");
        anyhow::ensure!(motoc || (!status.paused && status.trackers.iter().any(|t| t.active)), "Connect trackers and resume tracking first");
        Ok(())
    }
    fn command(self) -> &'static str {
        match self { Self::Walking => "calibrate_walking", Self::Full => "full_reset", Self::Mounting => "mounting_reset", Self::MotocCalibrate => "motoc_calibrate", Self::MotocContinue => "motoc_continue" }
    }
    fn label(self) -> &'static str {
        match self { Self::Walking => "Walking setup", Self::Full => "Full reset", Self::Mounting => "Mounting reset", Self::MotocCalibrate => "Motoc calibrate", Self::MotocContinue => "Motoc resume" }
    }
    fn instructions(self) -> &'static str {
        match self {
            Self::Walking => "Stand still for two seconds, then march on the spot for eight seconds",
            Self::MotocCalibrate => "Hold the selected tracker firmly against the headset, then move slowly together",
            Self::MotocContinue => "Resume the saved Motoc alignment for the configured mode",
            Self::Full => "Stand upright and face forward",
            Self::Mounting => "After a full reset, bend knees and lean forward in a ski pose",
        }
    }
}
enum Message { Reset(ResetKind), Cancel, Stop }
#[derive(Default)]
struct State {
    busy: bool,
    countdown: Option<(ResetKind, Instant)>,
    message: String,
}
pub struct ResetControl {
    sender: Option<mpsc::SyncSender<Message>>,
    state: Arc<Mutex<State>>,
}
impl ResetControl {
    pub fn new(path: Option<PathBuf>) -> Self {
        Self::with_delay(path, Duration::from_secs(3))
    }
    fn with_delay(path: Option<PathBuf>, delay: Duration) -> Self {
        let state = Arc::new(Mutex::new(State::default()));
        let Some(path) = path else { return Self { sender: None, state }; };
        let (sender, receiver) = mpsc::sync_channel(1);
        let shared = state.clone();
        let worker = std::thread::Builder::new().name("shora-resets".into()).spawn(move || {
            while let Ok(message) = receiver.recv() {
                let kind = match message { Message::Reset(kind) => kind, Message::Cancel => continue, Message::Stop => return };
                let result = (|| -> anyhow::Result<()> {
                    let status = super::read_status(&path)?;
                    kind.validate(&status)?;
                    shared.lock().unwrap().countdown = Some((kind, Instant::now() + delay));
                    match receiver.recv_timeout(delay) {
                        Err(mpsc::RecvTimeoutError::Timeout) => {}
                        _ => anyhow::bail!("Reset cancelled"),
                    }
                    shared.lock().unwrap().countdown = None;
                    send_reset(&path, kind, status.pid)
                })();
                let message = match result {
                    Ok(()) => if matches!(kind, ResetKind::Walking | ResetKind::MotocCalibrate | ResetKind::MotocContinue) {
                        format!("{} command accepted", kind.label())
                    } else { format!("{} complete", kind.label()) },
                    Err(error) => format!("{}: {error}", kind.label()),
                };
                log::info!("{message}");
                *shared.lock().unwrap() = State { message, ..Default::default() };
            }
        });
        if let Err(error) = worker {
            state.lock().unwrap().message = format!("Tracking controls unavailable: {error}");
            return Self { sender: None, state };
        }
        Self { sender: Some(sender), state }
    }
    pub fn request(&self, kind: ResetKind) {
        let Some(sender) = &self.sender else { return; };
        let mut state = self.state.lock().unwrap();
        if state.busy {
            let _ = sender.try_send(Message::Cancel);
        } else if sender.try_send(Message::Reset(kind)).is_ok() {
            state.busy = true;
            state.message = format!("{}: checking trackers…", kind.label());
        }
    }
    pub fn busy(&self) -> bool { self.state.lock().unwrap().busy }
    pub fn text(&self) -> String {
        let state = self.state.lock().unwrap();
        if let Some((kind, deadline)) = state.countdown {
            format!("{} in {}…\n{}\nPress either reset button to cancel", kind.label(),
                deadline.saturating_duration_since(Instant::now()).as_secs_f32().ceil() as u32,
                kind.instructions())
        } else if !state.message.is_empty() {
            state.message.clone()
        } else if self.sender.is_none() {
            "Tracking controls unavailable".into()
        } else {
            String::new()
        }
    }
}
impl Drop for ResetControl {
    fn drop(&mut self) {
        if let Some(sender) = self.sender.take() { let _ = sender.try_send(Message::Stop); }
    }
}

fn send_reset(path: &Path, kind: ResetKind, expected_pid: u32) -> anyhow::Result<()> {
    // Check and dispatch on the same connection; a restarted worker cannot
    // accidentally receive a reset queued for an earlier tracking session.
    let mut stream = super::connect(path)?;
    let status = super::exchange(&mut stream, "status")?;
    anyhow::ensure!(status.pid == expected_pid, "Tracking worker restarted; retry the reset");
    kind.validate(&status)?;
    super::exchange(&mut stream, kind.command())?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{io::{BufRead, BufReader, Write}, os::unix::net::UnixListener, sync::atomic::{AtomicUsize, Ordering}};

    fn listener() -> (PathBuf, UnixListener) {
        static NEXT: AtomicUsize = AtomicUsize::new(0);
        let path = std::env::temp_dir().join(format!("wayvr-reset-{}-{}.sock", std::process::id(), NEXT.fetch_add(1, Ordering::Relaxed)));
        let listener = UnixListener::bind(&path).unwrap();
        (path, listener)
    }
    fn reply(pid: u32, error: Option<&str>) -> String {
        format!("{}\n", serde_json::json!({"ok":error.is_none(), "error":error, "status":{
            "pid":pid, "paused":false, "trackers":[{"name":"hip","mac":"test","sensor_id":0,"active":true,"battery_percent":80.0}]
        }}))
    }
    fn expect(reader: &mut BufReader<std::os::unix::net::UnixStream>, command: &str) {
        let mut line = String::new();
        reader.read_line(&mut line).unwrap();
        assert_eq!(serde_json::from_str::<serde_json::Value>(&line).unwrap()["command"], command);
    }
    fn wait_until(mut condition: impl FnMut() -> bool) {
        let deadline = Instant::now() + Duration::from_secs(3);
        while !condition() {
            assert!(Instant::now() < deadline, "reset worker did not respond");
            std::thread::sleep(Duration::from_millis(2));
        }
    }

    #[test]
    fn modes_cannot_dispatch_each_others_commands() {
        let mut status: super::super::Status = serde_json::from_str(r#"{"pid":1,"paused":false,"trackers":[],"tracking_mode":"motoc"}"#).unwrap();
        assert!(ResetKind::MotocCalibrate.validate(&status).is_ok());
        assert!(ResetKind::Full.validate(&status).is_err());
        status.tracking_mode="slimevr".into();
        assert!(ResetKind::MotocContinue.validate(&status).is_err());
    }
    #[test]
    fn reset_buttons_send_typed_commands_and_preserve_backend_errors() {
        for (kind, error) in [(ResetKind::Full, None), (ResetKind::Mounting, Some("Reference protection: wait for stable tracking"))] {
            let (path, listener) = listener();
            let worker = std::thread::spawn(move || {
                let mut client = BufReader::new(listener.accept().unwrap().0);
                expect(&mut client, "status");
                client.get_mut().write_all(reply(123, None).as_bytes()).unwrap();
                expect(&mut client, kind.command());
                client.get_mut().write_all(reply(123, error).as_bytes()).unwrap();
            });
            let result = send_reset(&path, kind, 123);
            match error { Some(error) => assert_eq!(result.unwrap_err().to_string(), error), None => result.unwrap() }
            worker.join().unwrap();
            std::fs::remove_file(path).unwrap();
        }
    }

    #[test]
    fn restarted_worker_never_receives_queued_reset() {
        let (path, listener) = listener();
        let worker = std::thread::spawn(move || {
            let mut client = BufReader::new(listener.accept().unwrap().0);
            expect(&mut client, "status");
            client.get_mut().write_all(reply(456, None).as_bytes()).unwrap();
            let mut line = String::new();
            assert_eq!(client.read_line(&mut line).unwrap(), 0);
        });
        assert!(send_reset(&path, ResetKind::Full, 123).unwrap_err().to_string().contains("restarted"));
        worker.join().unwrap();
        std::fs::remove_file(path).unwrap();
    }

    #[test]
    fn countdown_runs_off_thread_and_second_press_cancels_without_sending_reset() {
        for cancel in [false, true] {
            let (path, listener) = listener();
            let worker = std::thread::spawn(move || {
                let mut initial = BufReader::new(listener.accept().unwrap().0);
                expect(&mut initial, "status");
                initial.get_mut().write_all(reply(123, None).as_bytes()).unwrap();
                if !cancel {
                    let mut client = BufReader::new(listener.accept().unwrap().0);
                    expect(&mut client, "status");
                    client.get_mut().write_all(reply(123, None).as_bytes()).unwrap();
                    expect(&mut client, "full_reset");
                    client.get_mut().write_all(reply(123, None).as_bytes()).unwrap();
                }
            });
            let control = ResetControl::with_delay(Some(path.clone()), Duration::from_millis(100));
            control.request(ResetKind::Full);
            wait_until(|| control.text().contains("cancel"));
            if cancel { control.request(ResetKind::Mounting); }
            wait_until(|| !control.state.lock().unwrap().busy);
            assert!(control.text().contains(if cancel { "cancelled" } else { "complete" }), "{}", control.text());
            drop(control);
            worker.join().unwrap();
            std::fs::remove_file(path).unwrap();
        }
    }
}
