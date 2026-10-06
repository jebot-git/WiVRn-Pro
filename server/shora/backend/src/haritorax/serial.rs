//! Managed serial workers: reconnect, bounded queues, and joined shutdown.
use std::collections::BTreeMap;
use std::sync::{Arc, RwLock, atomic::{AtomicBool, Ordering}};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use serde::Serialize;
use tokio::sync::{mpsc, oneshot};
use super::{config::{HaritoraXConfig, Model}, gx6::{Decoder, discover_dongle_ports}, settings::{HardwareSettings, HardwareTracker}, HaritoraXEvent};

#[derive(Debug)]
pub struct InputEvent { pub port: String, pub event: HaritoraXEvent }
#[derive(Debug, Clone, Default, Serialize)]
pub struct PortStatus { pub path: String, pub connected: bool, pub error: Option<String>, pub trackers: Vec<HardwareTracker> }
type PortStates = Arc<RwLock<BTreeMap<String, PortStatus>>>;
type OperationReply = oneshot::Sender<Result<usize, String>>;
#[derive(Clone, Copy)]
enum Operation { PowerOff, Settings { mac:[u8;6], settings:HardwareSettings } }
type Request = (Operation, OperationReply);

pub struct Acquisition {
    pub events: mpsc::Receiver<InputEvent>,
    tx: mpsc::Sender<InputEvent>,
    states: PortStates,
    stop: Arc<AtomicBool>,
    workers: Vec<JoinHandle<()>>,
    controls: BTreeMap<String, mpsc::Sender<Request>>,
    cfg: HaritoraXConfig,
}

impl Acquisition {
    pub fn start(cfg: HaritoraXConfig) -> anyhow::Result<Self> {
        cfg.validate()?;
        let (tx, events) = mpsc::channel(1024);
        let mut me = Self { events, tx, states: Arc::default(), stop: Arc::new(AtomicBool::new(false)), workers: vec![], controls: BTreeMap::new(), cfg };
        me.spawn()?;
        Ok(me)
    }
    fn spawn(&mut self) -> anyhow::Result<()> {
        if !self.cfg.enabled { return Ok(()); }
        let mut ports = if self.cfg.ports.is_empty() { discover_dongle_ports() } else { self.cfg.ports.clone() };
        ports.sort(); ports.dedup();
        for path in ports {
            self.states.write().unwrap().insert(path.clone(), PortStatus { path: path.clone(), ..Default::default() });
            let (tx, states, stop, cfg) = (self.tx.clone(), self.states.clone(), self.stop.clone(), self.cfg.clone());
            let (control, requests) = mpsc::channel(1);
            self.controls.insert(path.clone(), control);
            self.workers.push(std::thread::Builder::new().name("haritorax-serial".into())
                .spawn(move || worker(path, cfg, tx, states, stop, requests))?);
        }
        Ok(())
    }
    pub fn restart(&mut self) -> anyhow::Result<()> {
        anyhow::ensure!(self.cfg.enabled, "HaritoraX input is disabled");
        self.stop_workers();
        while self.events.try_recv().is_ok() {}
        self.states.write().unwrap().clear();
        self.stop = Arc::new(AtomicBool::new(false));
        self.spawn()
    }
    pub fn ports(&self) -> Vec<PortStatus> { self.states.read().unwrap().values().cloned().collect() }
    pub async fn shutdown_trackers(&self) -> anyhow::Result<()> { self.operation(Operation::PowerOff).await }
    pub fn set_settings(&self, mac:[u8;6], settings:HardwareSettings) -> impl std::future::Future<Output=anyhow::Result<()>> + Send + 'static {
        let enabled = self.cfg.enabled;
        let ports = self.ports();
        let controls = self.controls.clone();
        async move {
            settings.validate()?;
            Self::operate(enabled, ports, controls, Operation::Settings {mac,settings}).await
        }
    }
    async fn operation(&self, operation:Operation) -> anyhow::Result<()> {
        Self::operate(self.cfg.enabled, self.ports(), self.controls.clone(), operation).await
    }
    async fn operate(enabled:bool, ports:Vec<PortStatus>, controls:BTreeMap<String,mpsc::Sender<Request>>, operation:Operation) -> anyhow::Result<()> {
        anyhow::ensure!(enabled, "HaritoraX input is disabled");
        let mut replies = Vec::new();
        let mut errors = Vec::new();
        for port in ports.into_iter().filter(|p| p.connected) {
            if let Operation::Settings {mac,..}=operation {
                if !port.trackers.iter().any(|t|t.mac==mac) {continue;}
            }
            let (reply, receiver) = oneshot::channel();
            match controls[&port.path].try_send((operation,reply)) {
                Ok(()) => replies.push((port.path, receiver)),
                Err(error) => errors.push(format!("{}: {error}", port.path)),
            }
        }
        let deadline = tokio::time::Instant::now() + Duration::from_secs(2);
        let mut count = 0;
        for (path, reply) in replies {
            match tokio::time::timeout_at(deadline, reply).await {
                Ok(Ok(Ok(sent))) => count += sent,
                Ok(Ok(Err(error))) => errors.push(format!("{path}: {error}")),
                _ => errors.push(format!("{path}: hardware request did not complete")),
            }
        }
        anyhow::ensure!(errors.is_empty(), "Hardware request failed (a write may have reached the tracker): {}", errors.join("; "));
        anyhow::ensure!(count > 0, "No matching connected physical HaritoraX tracker");
        Ok(())
    }
    fn stop_workers(&mut self) {
        self.stop.store(true, Ordering::Relaxed);
        for worker in &self.workers { worker.thread().unpark(); }
        for worker in self.workers.drain(..) { let _ = worker.join(); }
        self.controls.clear();
    }
}
impl Drop for Acquisition { fn drop(&mut self) { self.stop_workers(); } }

fn state(states: &PortStates, path: &str, connected: bool, error: Option<String>) {
    states.write().unwrap().insert(path.into(), PortStatus { path: path.into(), connected, error, trackers:vec![] });
}
fn emit(tx: &mpsc::Sender<InputEvent>, path: &str, events: Vec<HaritoraXEvent>) {
    for event in events {
        // Dropping an overloaded sample is preferable to unbounded latency.
        let _ = tx.try_send(InputEvent { port: path.into(), event });
    }
}
fn worker(path: String, cfg: HaritoraXConfig, tx: mpsc::Sender<InputEvent>, states: PortStates, stop: Arc<AtomicBool>,
    mut requests: mpsc::Receiver<Request>) {
    while !stop.load(Ordering::Relaxed) {
        match serialport::new(&path, cfg.baud_rate).timeout(Duration::from_millis(100)).open() {
            Ok(mut port) => {
                state(&states, &path, true, None);
                let mut decoder = Decoder::default();
                let result = read_port(port.as_mut(), cfg.model == Model::X2, &mut decoder, &tx, &path, &stop, &mut requests, &states);
                emit(&tx, &path, decoder.disconnect());
                let error = result.err().map(|e| e.to_string());
                state(&states, &path, false, error);
            }
            Err(error) => state(&states, &path, false, Some(error.to_string())),
        }
        while let Ok((_,reply)) = requests.try_recv() { let _ = reply.send(Err("Serial port disconnected".into())); }
        if !stop.load(Ordering::Relaxed) { std::thread::park_timeout(Duration::from_secs(2)); }
    }
    state(&states, &path, false, None);
}

fn command(port: &mut dyn serialport::SerialPort, text: &str) -> std::io::Result<()> {
    port.write_all(format!("\n{text}\n").as_bytes())
}
fn power_off(port: &mut dyn serialport::SerialPort, decoder: &Decoder) -> anyhow::Result<usize> {
    let commands = decoder.power_off_commands()?;
    for (off, restore) in &commands {
        command(port, off)?;
        std::thread::sleep(Duration::from_millis(25));
        command(port, restore)?;
    }
    Ok(commands.len())
}
#[allow(clippy::too_many_arguments)]
fn read_port(port: &mut dyn serialport::SerialPort, is_x2: bool, decoder: &mut Decoder,
    tx: &mpsc::Sender<InputEvent>, path: &str, stop: &AtomicBool,
    requests: &mut mpsc::Receiver<Request>, states: &PortStates) -> std::io::Result<()> {
    for cmd in ["r0:", "r1:", "r:", "o:"] { command(port, cmd)?; }
    let opened = Instant::now();
    let mut heartbeat = Instant::now();
    let mut delayed = false;
    let mut line = Vec::new();
    let mut discard = false;
    let mut buffer = [0; 1024];
    let mut pending: Option<([u8;6],char,String,Instant,OperationReply)> = None;
    while !stop.load(Ordering::Relaxed) {
        if let Some(status)=states.write().unwrap().get_mut(path) {status.trackers=decoder.hardware_trackers();}
        if let Some((mac,id,hex,started,reply))=pending.take() {
            if decoder.confirms(mac,id,&hex,started) {let _=reply.send(Ok(1));}
            else if started.elapsed()>Duration::from_millis(1200) {let _=reply.send(Err("Tracker did not confirm settings; refresh before retrying".into()));}
            else if !reply.is_closed() {pending=Some((mac,id,hex,started,reply));}
        }
        while let Ok((operation,reply)) = requests.try_recv() {
            // Never execute a request that timed out while the port was unavailable.
            if !reply.is_closed() {
                if pending.is_some() {let _=reply.send(Err("A hardware request is already pending".into()));continue;}
                match operation {
                    Operation::PowerOff => {let result=power_off(port,decoder).map_err(|e|e.to_string());let _=reply.send(result);}
                    Operation::Settings {mac,settings} => match decoder.settings_command(mac,settings) {
                        Ok((id,hex)) => {
                            let started=Instant::now();
                            command(port,&format!("o{id}:{hex}"))?;
                            command(port,&format!("o{id}:"))?;
                            pending=Some((mac,id,hex,started,reply));
                        }
                        Err(error)=>{let _=reply.send(Err(error.to_string()));}
                    }
                }
            }
        }
        if !delayed && opened.elapsed() >= Duration::from_millis(1500) {
            for cmd in ["i:", "i0:", "i1:", "o0:", "o1:", "v0:", "v1:"] { command(port, cmd)?; }
            // A waking tracker can miss the first identity request. Match the
            // upstream interpreter's delayed retry of the initial batch.
            for cmd in ["r0:", "r1:", "r:", "o:"] { command(port, cmd)?; }
            delayed = true;
        }
        if heartbeat.elapsed() >= Duration::from_secs(2) {
            command(port, "i:")?;
            for id in decoder.missing_assignment_ports() {
                command(port, &format!("r{id}:"))?;
            }
            for tracker in decoder.hardware_trackers() {
                command(port, &format!("o{}:",tracker.channel))?;
            }
            heartbeat = Instant::now();
        }
        match port.read(&mut buffer) {
            Ok(0) => return Err(std::io::Error::new(std::io::ErrorKind::UnexpectedEof, "serial port closed")),
            Ok(len) => for byte in &buffer[..len] {
                if *byte == b'\n' {
                    if !discard {
                        if let Ok(text) = std::str::from_utf8(&line) { emit(tx, path, decoder.line(text, is_x2)); }
                    }
                    line.clear(); discard = false;
                } else if !discard {
                    if line.len() < 8192 { line.push(*byte); } else { line.clear(); discard = true; }
                }
            },
            Err(error) if matches!(error.kind(), std::io::ErrorKind::TimedOut | std::io::ErrorKind::WouldBlock) => {},
            Err(error) => return Err(error),
        }
    }
    Ok(())
}
