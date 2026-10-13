//! Motoc belongs to the tracking worker, so headset reconnects do not destroy
//! calibration. Never replay an initial calibration after a worker failure.
use std::{path::PathBuf, time::{Duration, Instant}};
use anyhow::Context;
use serde::{Deserialize, Serialize};
use tokio::process::{Child, Command};
use tokio::io::{AsyncBufReadExt, AsyncReadExt};

#[derive(Debug, Clone, Copy, Default, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum Mode { #[default] Once, Rigid, Anchors }
#[derive(Debug, Clone, Deserialize, Serialize, PartialEq)]
#[serde(default, deny_unknown_fields)]
pub struct Settings {
    pub enabled: bool,
    pub source: String,
    pub target: String,
    pub mode: Mode,
    pub keep_alive: bool,
}
impl Default for Settings {
    fn default() -> Self { Self { enabled: false, source: "WiVRn HMD".into(), target: String::new(), mode: Mode::Once, keep_alive: true } }
}
impl Settings {
    pub fn validate(&self) -> anyhow::Result<()> {
        for value in [&self.source, &self.target] {
            anyhow::ensure!(value.len() <= 128 && !value.chars().any(char::is_control), "Invalid Motoc device serial");
        }
        anyhow::ensure!(!self.enabled || (!self.source.trim().is_empty() && !self.target.trim().is_empty() && self.source != self.target),
            "Choose distinct Quest and Lighthouse device serials");
        Ok(())
    }
    fn profile(&self) -> &'static str {
        match self.mode { Mode::Once => "wivrn-pro-once", Mode::Rigid => "wivrn-pro-rigid", Mode::Anchors => "wivrn-pro-anchors" }
    }
    fn args(&self, calibrate: bool) -> Vec<String> {
        let mut args = if calibrate {
            vec!["calibrate".into(), "--src".into(), self.source.clone(), "--dst".into(), self.target.clone()]
        } else { vec![if self.mode == Mode::Anchors {"anchor"} else {"continue"}.into()] };
        args.extend(["--profile".into(), self.profile().into()]);
        if calibrate {
            match self.mode { Mode::Rigid => args.push("--continue".into()), Mode::Anchors => args.push("--anchors".into()), Mode::Once => {} }
        }
        args
    }
}
#[derive(Debug, Clone, Default, Serialize)]
pub struct Status { pub settings: Settings, pub running: bool, pub message: String }
pub struct Manager {
    #[cfg(test)]
    binary: Option<PathBuf>,
    settings: Settings,
    active: bool,
    ready: Option<tokio::sync::oneshot::Receiver<()>>,
    child: Option<Child>,
    desired: bool,
    retry: Instant,
    failures: u32,
    message: String,
}
impl Manager {
    pub fn new(settings: Settings, active: bool) -> Self {
        Self { #[cfg(test)] binary: None, desired: active && settings.enabled && settings.keep_alive && settings.mode != Mode::Once, active, ready: None, settings, child: None, retry: Instant::now(), failures: 0, message: "Stopped; calibrate or resume a saved profile".into() }
    }
    fn executable(&self) -> anyhow::Result<PathBuf> {
        #[cfg(test)]
        if let Some(path) = &self.binary { return Ok(path.clone()); }
        // The C++ worker supplies the installed/build-tree binary; never search PATH.
        std::env::var_os("WIVRN_MOTOC_EXECUTABLE").map(PathBuf::from)
            .filter(|p| p.is_absolute()).context("Motoc is not available in this server build")
    }
    pub async fn configure(&mut self, settings: Settings) {
        self.stop().await;
        self.settings = settings;
    }
    pub async fn start(&mut self, calibrate: bool) -> anyhow::Result<()> {
        self.settings.validate()?;
        anyhow::ensure!(self.active, "Motoc bridge mode is not active; restart WiVRn after switching modes");
        anyhow::ensure!(self.settings.enabled, "Enable Motoc first");
        let executable = self.executable()?;
        self.stop().await;
        let mut command = Command::new(executable);
        command.args(self.settings.args(calibrate)).kill_on_drop(true);
        command.stdin(std::process::Stdio::null()).stdout(std::process::Stdio::piped());
        command.env("WIVRN_MOTOC_SOURCE", &self.settings.source).env("WIVRN_MOTOC_TARGET", &self.settings.target);
        // The matching XR manifest and libmonado path are inherited from WiVRn.
        // Kill descendants even if the worker itself crashes, including outside systemd.
        let parent = unsafe { libc::getpid() };
        unsafe { command.pre_exec(move || {
            if libc::prctl(libc::PR_SET_PDEATHSIG, libc::SIGKILL) != 0 || libc::getppid() != parent {
                return Err(std::io::Error::last_os_error());
            }
            Ok(())
        }); }
        let mut child = command.spawn().context("Cannot start bundled Motoc")?;
        let stdout = child.stdout.take().unwrap();
        let (tx, rx) = tokio::sync::oneshot::channel();
        self.ready = Some(rx);
        tokio::spawn(async move {
            let mut line = Vec::new();
            if tokio::io::BufReader::new(stdout).take(64).read_until(b'\n', &mut line).await.is_ok() && line == b"WIVRN_MOTOC_READY\n" { let _ = tx.send(()); }
        });
        self.child = Some(child);
        self.desired = !calibrate && self.settings.keep_alive && self.settings.mode != Mode::Once;
        // Do not replay sampling after failure. Resume requires a saved profile.
        self.message = if calibrate {"Motoc calibration started; see service log for progress"} else {"Motoc saved calibration resumed"}.into();
        self.failures = 0;
        Ok(())
    }
    pub async fn stop(&mut self) {
        self.desired = false;
        self.ready = None;
        if let Some(mut child) = self.child.take() { let _ = child.kill().await; }
        self.message = "Stopped".into();
    }
    pub async fn tick(&mut self) {
        if self.ready.as_mut().is_some_and(|rx| rx.try_recv().is_ok()) {
            self.ready = None;
            self.desired = self.settings.keep_alive && self.settings.mode != Mode::Once;
            self.message = "Calibration active; saved alignment available for reconnect".into();
        }
        if let Some(child) = self.child.as_mut() {
            match child.try_wait() {
                Ok(Some(exit)) => {
                    self.child = None;
                    self.message = format!("Motoc exited: {exit}; inspect service log");
                    self.retry = Instant::now() + Duration::from_secs(2u64.pow(self.failures.min(4)));
                    self.failures += 1;
                }
                Err(e) => { self.message = e.to_string(); self.desired = false; }
                _ => {}
            }
        } else if self.desired && Instant::now() >= self.retry {
            let failures = self.failures;
            if let Err(e) = self.start(false).await {
                self.message = format!("{e:#}");
                self.desired = true;
                self.retry = Instant::now() + Duration::from_secs(30);
            }
            self.failures = failures;
        }
    }
    pub fn status(&self) -> Status { Status { settings: self.settings.clone(), running: self.child.is_some(), message: self.message.clone() } }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[tokio::test]
    async fn readiness_enables_resume_and_stop_reaps_the_owned_process() {
        use std::os::unix::fs::PermissionsExt;
        let directory=tempfile::tempdir().unwrap(); let executable=directory.path().join("motoc");
        std::fs::write(&executable,"#!/bin/sh\nprintf 'WIVRN_MOTOC_READY\\n'\nexec sleep 30\n").unwrap();
        std::fs::set_permissions(&executable,std::fs::Permissions::from_mode(0o700)).unwrap();
        let mut manager=Manager::new(Settings {enabled:true,target:"LHR-test".into(),mode:Mode::Rigid,..Default::default()},true);
        manager.binary=Some(executable);
        manager.start(true).await.unwrap(); assert!(!manager.desired);
        let pid=manager.child.as_ref().unwrap().id().unwrap();
        for _ in 0..100 { manager.tick().await; if manager.desired {break;} tokio::time::sleep(Duration::from_millis(5)).await; }
        assert!(manager.desired, "calibration readiness was not received");
        manager.stop().await; assert!(!manager.desired && manager.child.is_none());
        assert_eq!(unsafe {libc::waitpid(pid as i32,std::ptr::null_mut(),libc::WNOHANG)},-1);
    }
    #[test] fn modes_are_explicit_and_serials_are_arguments() {
        let mut s = Settings { enabled: true, target: "LHR-123; touch /tmp/no".into(), ..Default::default() };
        assert!(s.validate().is_ok());
        assert!(!s.args(true).contains(&"--continue".into()));
        s.mode = Mode::Rigid; assert!(s.args(true).contains(&"--continue".into()));
        s.mode = Mode::Anchors; assert!(s.args(true).contains(&"--anchors".into()));
        assert_eq!(s.args(false)[0], "anchor");
        s.target = s.source.clone(); assert!(s.validate().is_err());
    }
    #[tokio::test] async fn inactive_bridge_rejects_motoc_and_never_resumes() {
        let mut settings=Settings { enabled:true, mode:Mode::Rigid, target:"LHR-123".into(), ..Default::default() };
        let mut manager=Manager::new(settings.clone(),false);
        assert!(!manager.desired);
        assert!(manager.start(false).await.unwrap_err().to_string().contains("bridge mode"));
        manager=Manager::new(settings.clone(),true); assert!(manager.desired);
        manager.stop().await; assert!(!manager.desired);
        settings.keep_alive=false;
        assert!(!Manager::new(settings,true).desired);
    }
    #[test] fn default_does_not_start_or_change_shora() {
        let manager = Manager::new(Settings::default(), false);
        assert!(!manager.desired && !manager.status().running && !manager.status().settings.enabled);
    }
}
