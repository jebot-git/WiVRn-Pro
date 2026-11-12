//! Managed face/eye OSC relay. All source handles stay on its own worker.
use std::net::{SocketAddr, UdpSocket};
use std::sync::{Arc, RwLock, mpsc, atomic::{AtomicBool, Ordering}};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};
use anyhow::Context;
use rosc::{OscBundle, OscPacket, OscType};
use serde::Serialize;
use tokio::sync::oneshot;
use super::bundle::{AvatarBundle, PARAM_PREFIX};
use super::config::{FaceConfig, Source};
use super::output::FaceOutput;
use super::unified::{FaceParams, UnifiedTrackingData};
use super::FaceSource;

const FRAME_INTERVAL: Duration = Duration::from_millis(11);

#[derive(Debug, Clone, Serialize)]
pub struct FaceStatus {
    pub settings: FaceConfig,
    pub state: String,
    pub active: bool,
    pub eye_active: bool,
    pub expression_active: bool,
    pub error: Option<String>,
    pub source_error: Option<String>,
}
impl Default for FaceStatus {
    fn default() -> Self { Self { settings: FaceConfig::default(), state: "disabled".into(),
        active: false, eye_active: false, expression_active: false, error: None, source_error: None } }
}
type Persist = Box<dyn FnOnce() -> anyhow::Result<()> + Send>;
struct Update { config: FaceConfig, persist: Persist, reply: oneshot::Sender<Result<(), String>>, expires: Instant }

/// A persistent controller, including when tracking is disabled. Live updates
/// never join this thread from the body-tracking or control-socket event loop.
pub struct FaceRelay {
    stop: Arc<AtomicBool>,
    worker: Option<JoinHandle<anyhow::Result<()>>>,
    updates: mpsc::SyncSender<Update>,
    status: Arc<RwLock<FaceStatus>>,
}
impl FaceRelay {
    pub fn start(cfg: FaceConfig) -> anyhow::Result<Self> {
        cfg.validate()?;
        let stop = Arc::new(AtomicBool::new(false));
        let status = Arc::new(RwLock::new(FaceStatus::default()));
        let (updates, receiver) = mpsc::sync_channel(4);
        let (ready, initialized) = mpsc::sync_channel(1);
        let worker_stop = stop.clone(); let worker_status = status.clone();
        let worker = std::thread::Builder::new().name("face-relay".into()).spawn(move || {
            let mut current = None;
            if let Err(e) = apply_config(&mut current, cfg, Box::new(|| Ok(())), &worker_status) {
                let text = format!("{e:#}"); let _ = ready.send(Err(text.clone()));
                anyhow::bail!(text);
            }
            let _ = ready.send(Ok(()));
            while !worker_stop.load(Ordering::Relaxed) {
                let began = Instant::now();
                if let Ok(update) = receiver.try_recv() {
                    let Update { config, persist, reply, expires } = update;
                    if !reply.is_closed() {
                        let guarded = Box::new(move || {
                            anyhow::ensure!(Instant::now() < expires, "Face/eye update expired before applying; retry the change");
                            persist()
                        });
                        let result = apply_config(&mut current, config, guarded, &worker_status)
                            .map_err(|e| format!("{e:#}"));
                        if let Err(error) = &result { worker_status.write().unwrap().error = Some(error.clone()); }
                        let _ = reply.send(result);
                    }
                }
                if let Some(state) = current.as_mut() {
                    match state.frame() {
                        Ok(()) => {
                            let mut status = worker_status.write().unwrap();
                            status.active = state.last_active;
                            status.eye_active = state.last_eye;
                            status.expression_active = state.last_expression;
                            status.source_error = state.source.as_ref().and_then(|source| source.error()).map(str::to_string);
                            status.state = if state.last_active { "active" } else { "waiting" }.into();
                        }
                        Err(error) => {
                            state.clear_output();
                            let mut status = worker_status.write().unwrap();
                            status.active = false; status.eye_active = false; status.expression_active = false;
                            status.state = "error".into(); status.error = Some(format!("{error:#}"));
                            drop(status);
                            // A broken face source must not terminate FBT or the controller.
                            current = None;
                        }
                    }
                }
                std::thread::park_timeout(FRAME_INTERVAL.saturating_sub(began.elapsed()));
            }
            if let Some(state) = current.as_mut() { state.clear_output(); }
            Ok(())
        }).context("spawn face relay")?;
        let initialized = initialized.recv().context("face relay initialization stopped")?;
        if let Err(error) = initialized { let _ = worker.join(); anyhow::bail!(error); }
        Ok(Self { stop, worker: Some(worker), updates, status })
    }
    pub fn configure(&self, config: FaceConfig, persist: impl FnOnce() -> anyhow::Result<()> + Send + 'static)
        -> anyhow::Result<oneshot::Receiver<Result<(), String>>> {
        let (reply, receiver) = oneshot::channel();
        self.updates.try_send(Update { config, persist: Box::new(persist), reply, expires: Instant::now() + Duration::from_secs(4) })
            .map_err(|_| anyhow::anyhow!("Face/eye controller unavailable or another update is pending"))?;
        if let Some(worker) = &self.worker { worker.thread().unpark(); }
        Ok(receiver)
    }
    pub fn snapshot(&self) -> FaceStatus { self.status.read().unwrap().clone() }
    pub fn active(&self) -> bool { self.status.read().unwrap().active }
    pub fn report_error(&self, error: String) {
        let mut status = self.status.write().unwrap(); status.state = "error".into(); status.error = Some(error);
    }
    pub fn check_health(&mut self) -> anyhow::Result<()> {
        if self.worker.as_ref().is_some_and(|w| w.is_finished()) {
            match self.worker.take().unwrap().join() {
                Ok(Err(error)) => return Err(error).context("face relay stopped"),
                Ok(Ok(())) => anyhow::bail!("face relay stopped unexpectedly"),
                Err(_) => anyhow::bail!("face relay worker panicked"),
            }
        }
        Ok(())
    }
}
impl Drop for FaceRelay {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Relaxed);
        if let Some(worker) = self.worker.take() { worker.thread().unpark(); let _ = worker.join(); }
    }
}

struct RelayState {
    cfg: FaceConfig,
    listener: Arc<UdpSocket>,
    upstream: UdpSocket,
    source: Option<Box<dyn FaceSource>>,
    output: Option<FaceOutput>,
    advert: Option<super::oscquery::OscQueryAdvert>,
    browser: Option<super::oscjson::OscJsonBrowser>,
    data: UnifiedTrackingData,
    cache: FaceParams,
    last_frame: Instant,
    last_vsync: Option<Instant>,
    last_active: bool,
    last_eye: bool,
    last_expression: bool,
    avatar: PendingAvatarChange,
}
impl RelayState {
    fn prepare(cfg: FaceConfig, old: Option<&Self>) -> anyhow::Result<Self> {
        let same_output = old.is_some_and(|s| s.cfg.output == cfg.output && s.cfg.avatar == cfg.avatar
            && s.cfg.translation_sheet == cfg.translation_sheet && s.cfg.destination == cfg.destination);
        // Reload explicit mapping files even when the selected path is unchanged.
        let reuse_output = same_output && cfg.avatar.is_none() && cfg.translation_sheet.is_none();
        let output = if reuse_output { None } else { Some(FaceOutput::load(&cfg)?) };
        let addr = cfg.listen_addr(cfg.osc_port);
        let listener = if let Some(old) = old.filter(|s| s.cfg.listen_addr(s.cfg.osc_port) == addr) {
            old.listener.clone()
        } else {
            let socket = UdpSocket::bind(addr).context("bind face OSC feedback listener")?;
            socket.set_nonblocking(true)?; Arc::new(socket)
        };
        let upstream = UdpSocket::bind(if cfg.destination.is_ipv4() { "0.0.0.0:0" } else { "[::]:0" })?;
        upstream.set_write_timeout(Some(Duration::from_millis(100)))?;
        let reuse_source = old.is_some_and(|s| s.cfg.source == cfg.source && (cfg.source != Source::Babble
            || s.cfg.listen_addr(s.cfg.babble_port) == cfg.listen_addr(cfg.babble_port)));
        let source: Option<Box<dyn FaceSource>> = if reuse_source { None } else { Some(match cfg.source {
            Source::Babble => Box::new(super::babble::BabbleSource::bind(cfg.listen_addr(cfg.babble_port))?),
            Source::Openxr => {
                #[cfg(feature = "face-xr")]
                { Box::new(super::openxr::OpenXrSource::new()) }
                #[cfg(not(feature = "face-xr"))]
                { anyhow::bail!("openxr requires --features face-xr") }
            }
        }) };
        Ok(Self { cfg, listener, upstream, source, output, advert: None, browser: None,
            data: UnifiedTrackingData::default(), cache: FaceParams::new(), last_frame: Instant::now(),
            last_vsync: None, last_active: false, last_eye: false, last_expression: false,
            avatar: PendingAvatarChange::default() })
    }
    fn frame(&mut self) -> anyhow::Result<()> {
        if let Some(node) = self.browser.as_mut().and_then(|browser| browser.poll_avatar()) {
            self.output = Some(if node.native_face_tracking().unwrap_or(self.avatar.native == Some(true)) {
                FaceOutput::VrcFaceV1
            } else { FaceOutput::from_oscquery(&node) });
            self.output.as_mut().unwrap().invalidate(&mut self.data);
        }
        let mut vsync = false; let mut buf = [0; 65535];
        for _ in 0..128 {
            match self.listener.recv_from(&mut buf) {
                Ok((len, _)) => if let Ok((_, packet)) = rosc::decoder::decode_udp(&buf[..len]) {
                    if handle_feedback(&packet, &mut self.cache, &mut vsync, &mut self.browser, &mut self.avatar) {
                        if FaceOutput::automatic(&self.cfg) {
                            self.output = Some(if self.avatar.native == Some(true) { FaceOutput::VrcFaceV1 }
                                else { FaceOutput::OscQuery(Box::new(super::mystery::default_params())) });
                        }
                        self.output.as_mut().unwrap().invalidate(&mut self.data);
                    }
                },
                Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => break,
                Err(e) => return Err(e).context("receive face OSC feedback"),
            }
        }
        if vsync { self.last_vsync = Some(Instant::now()); }
        if vsync || self.last_vsync.is_none_or(|at| at.elapsed() > Duration::from_millis(500)) {
            let available = self.source.as_mut().unwrap().receive(&mut self.data)?;
            let eye = available && self.cfg.eye_enabled && self.data.eye_active;
            let expression = available && self.cfg.expression_enabled && self.data.expression_active.unwrap_or(true);
            let active = eye || expression;
            if !active && self.last_active { self.clear_output(); }
            if active {
                let original = self.data.clone();
                if !matches!(self.output, Some(FaceOutput::VrcFaceV1)) {
                    self.data.calc_combined(self.last_frame.elapsed().as_secs_f32(), &self.cache);
                }
                self.data.mask_channels(self.cfg.eye_enabled, self.cfg.expression_enabled);
                let mut bundle = OscBundle::new_bundle();
                self.output.as_mut().unwrap().apply(&mut self.data, &mut bundle, true);
                send_bundle(&self.upstream, self.cfg.destination, bundle);
                // Keep raw acquisition values across output-only changes and masks.
                let rendered = std::mem::replace(&mut self.data, original);
                self.data.copy_output_cache(&rendered);
            }
            self.last_active = active; self.last_eye = eye; self.last_expression = expression;
            self.last_frame = Instant::now();
        }
        Ok(())
    }
    fn clear_output(&mut self) {
        if !self.last_active { return; }
        let mut neutral = UnifiedTrackingData::default();
        neutral.expression_active = Some(false);
        neutral.calc_combined(0.0, &FaceParams::new());
        let output = self.output.as_mut().unwrap(); output.invalidate(&mut neutral);
        let mut bundle = OscBundle::new_bundle(); output.apply(&mut neutral, &mut bundle, false);
        if self.last_eye {
            bundle.send_tracking("/tracking/eye/LeftRightPitchYaw", vec![OscType::Float(0.0); 4]);
            bundle.send_tracking("/tracking/eye/EyesClosedAmount", vec![OscType::Float(0.0)]);
        }
        send_bundle(&self.upstream, self.cfg.destination, bundle);
        output.invalidate(&mut self.data);
        self.last_active = false; self.last_eye = false; self.last_expression = false;
    }
}

fn apply_config(current: &mut Option<RelayState>, cfg: FaceConfig, persist: Persist,
    status: &Arc<RwLock<FaceStatus>>) -> anyhow::Result<()> {
    cfg.validate()?;
    let mut next = if cfg.enabled && (cfg.eye_enabled || cfg.expression_enabled) {
        Some(RelayState::prepare(cfg.clone(), current.as_ref())?)
    } else { None };
    // All fallible resource preparation precedes persistence and the live swap.
    persist()?;
    if let Some(old) = current.as_mut() { old.clear_output(); }
    let mut old = current.take();
    if let Some(next) = next.as_mut() {
        if next.source.is_none() {
            let previous = old.as_mut().unwrap();
            next.source = previous.source.take(); next.data = std::mem::take(&mut previous.data);
        }
        if next.output.is_none() {
            let previous = old.as_mut().unwrap();
            next.output = previous.output.take(); next.cache = std::mem::take(&mut previous.cache);
            next.avatar = std::mem::take(&mut previous.avatar);
        }
        next.output.as_mut().unwrap().invalidate(&mut next.data);
        tracing::info!(source = next.source.as_ref().unwrap().name(), output = ?next.cfg.output,
            eyes = next.cfg.eye_enabled, expressions = next.cfg.expression_enabled, "face/eye settings applied");
        if next.cfg.osc_query {
            if let Some(previous) = old.as_mut().filter(|s| s.cfg.osc_query && s.cfg.osc_port == next.cfg.osc_port) {
                next.advert = previous.advert.take();
            } else {
                next.advert = super::oscquery::OscQueryAdvert::new(next.cfg.osc_port)
                    .map_err(|e| tracing::warn!(%e, "OSCQuery advertisement unavailable")).ok();
            }
        }
        if FaceOutput::discovers_avatar(&next.cfg) {
            next.browser = old.as_mut().and_then(|s| s.browser.take()).or_else(super::oscjson::OscJsonBrowser::new);
            if let Some(browser) = next.browser.as_mut() { browser.refresh(); }
        }
    }
    let running = next.is_some(); *current = next;
    *status.write().unwrap() = FaceStatus { settings: cfg, state: if running {"waiting"} else {"disabled"}.into(),
        ..FaceStatus::default() };
    Ok(())
}

fn handle_feedback(
    packet: &OscPacket,
    cache: &mut FaceParams,
    vsync: &mut bool,
    browser: &mut Option<super::oscjson::OscJsonBrowser>,
    avatar: &mut PendingAvatarChange,
) -> bool {
    match packet {
        OscPacket::Bundle(bundle) => {
            let mut changed = false;
            for packet in &bundle.content { changed |= handle_feedback(packet, cache, vsync, browser, avatar); }
            return changed;
        }
        OscPacket::Message(message) => {
            if message.addr == "/avatar/change" {
                if !matches!(message.args.as_slice(), [OscType::String(_)]) { return false; }
                cache.clear();
                if let Some(browser) = browser { browser.refresh(); }
                avatar.receive(message);
                return true;
            } else if matches!(message.addr.as_str(), "/avatar/name" | "/avatar/avatarfullfacetracking") {
                return avatar.receive(message);
            } else if let Some(name) = message.addr.strip_prefix(PARAM_PREFIX) {
                if name == "VSync" {
                    *vsync = true;
                } else if let Some(value) = message.args.first() {
                    if cache.len() < 512 || cache.contains_key(name) {
                        cache.insert(name.into(), value.clone());
                    }
                }
            }
        }
    }
    false
}

/// Upstream's three-part avatar handshake, accepting metadata in any order.
/// A repeated field starts a new change; old clients sending only /change
/// continue immediately through the legacy OSCQuery path.
#[derive(Default)]
struct PendingAvatarChange {
    uid: Option<String>,
    name: Option<String>,
    full_ft: Option<bool>,
    native: Option<bool>,
}

impl PendingAvatarChange {
    fn receive(&mut self, message: &rosc::OscMessage) -> bool {
        match (message.addr.as_str(), message.args.as_slice()) {
            ("/avatar/change", [OscType::String(uid)]) => {
                if self.uid.is_some() { *self = Self::default(); }
                self.native = None;
                self.uid = Some(uid.clone());
            }
            ("/avatar/name", [OscType::String(name)]) => {
                if self.name.is_some() { *self = Self::default(); }
                self.name = Some(name.clone());
            }
            ("/avatar/avatarfullfacetracking", [OscType::Bool(full_ft)]) => {
                if self.full_ft.is_some() { *self = Self::default(); }
                self.full_ft = Some(*full_ft);
            }
            _ => return false,
        }
        if self.uid.is_some() && self.name.is_some() && self.full_ft.is_some() {
            let native = self.full_ft;
            *self = Self { native, ..Self::default() };
            tracing::info!(native_face_tracking = native.unwrap(), "VRChat avatar tracking mode selected");
            true
        } else { false }
    }
}

fn send_bundle(socket: &UdpSocket, destination: SocketAddr, bundle: OscBundle) {
    // Keep oscavmgr's <=30 messages per bundle. An unconnected UDP socket lets
    // VRChat start/restart independently without terminating this relay on ICMP.
    for chunk in bundle.content.chunks(30) {
        let packet = OscPacket::Bundle(OscBundle {
            timetag: bundle.timetag,
            content: chunk.to_vec(),
        });
        if let Ok(bytes) = rosc::encoder::encode(&packet) {
            if let Err(error) = socket.send_to(&bytes, destination) {
                tracing::debug!(%error, "face OSC send failed");
            }
        }
    }
}

#[cfg(test)]
mod tests;
