// SPDX-License-Identifier: MIT OR Apache-2.0
//! Shora's reconnect bridge embedded in WiVRn's runtime process, after fork.
mod proxy;
use std::{ffi::{c_char, CStr}, path::PathBuf, sync::mpsc, thread, time::Duration};

pub struct Bridge {
    stop: Option<tokio::sync::oneshot::Sender<()>>,
    thread: Option<thread::JoinHandle<()>>,
}
impl Drop for Bridge {
    fn drop(&mut self) {
        if let Some(stop) = self.stop.take() { let _ = stop.send(()); }
        if let Some(thread) = self.thread.take() { let _ = thread.join(); }
    }
}
fn start(directory: PathBuf) -> anyhow::Result<Box<Bridge>> {
    use std::os::unix::fs::{MetadataExt, PermissionsExt};
    let metadata = std::fs::symlink_metadata(&directory)?;
    anyhow::ensure!(metadata.is_dir() && metadata.uid() == unsafe { libc::geteuid() } &&
        metadata.permissions().mode() & 0o077 == 0, "Shora bridge directory must be private and user-owned");
    let (stop_tx, stop_rx) = tokio::sync::oneshot::channel();
    let (ready_tx, ready_rx) = mpsc::sync_channel(1);
    let thread = thread::Builder::new().name("wivrn-shora".into()).spawn(move || {
        let result = (|| -> anyhow::Result<()> {
            let runtime = tokio::runtime::Builder::new_current_thread().enable_all().build()?;
            let _ = tracing_subscriber::fmt().with_env_filter(
                tracing_subscriber::EnvFilter::try_from_default_env().unwrap_or_else(|_| "warn".into())
            ).try_init();
            runtime.block_on(proxy::run(proxy::Paths {
                solarxr_listen: directory.join("SlimeVRRpc"),
                feeder_listen: directory.join("SlimeVRInput"),
                solarxr_backend: directory.join("ShoraRpc.backend"),
                feeder_backend: directory.join("ShoraInput.backend"),
            }, stop_rx, ready_tx.clone()))
        })();
        if let Err(error) = result {
            let _ = ready_tx.try_send(Err(error.to_string()));
            eprintln!("WiVRn Shora bridge: {error:#}");
        }
    })?;
    let handle = Box::new(Bridge { stop: Some(stop_tx), thread: Some(thread) });
    match ready_rx.recv_timeout(Duration::from_secs(5)) {
        Ok(Ok(())) => Ok(handle),
        Ok(Err(error)) => anyhow::bail!(error),
        Err(error) => anyhow::bail!("Shora bridge startup failed: {error}"),
    }
}
/// directory must point to a NUL-terminated UTF-8 path, valid for this call.
#[no_mangle]
pub unsafe extern "C" fn wivrn_shora_start(directory: *const c_char) -> *mut Bridge {
    std::panic::catch_unwind(|| {
        if directory.is_null() { return std::ptr::null_mut(); }
        let Ok(directory) = CStr::from_ptr(directory).to_str() else { return std::ptr::null_mut() };
        match start(PathBuf::from(directory)) {
            Ok(bridge) => Box::into_raw(bridge),
            Err(error) => { eprintln!("WiVRn Shora bridge: {error:#}"); std::ptr::null_mut() }
        }
    }).unwrap_or(std::ptr::null_mut())
}
/// Consume a handle returned by wivrn_shora_start, once. Null is allowed.
#[no_mangle]
pub unsafe extern "C" fn wivrn_shora_stop(bridge: *mut Bridge) {
    let _ = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| { if !bridge.is_null() { drop(Box::from_raw(bridge)); } }));
}

#[cfg(test)]
mod lifecycle_tests {
    use super::*;
    use std::os::unix::{fs::PermissionsExt, net::UnixStream};
    use std::io::{Read, Write};
    fn accept(listener: &std::os::unix::net::UnixListener) -> UnixStream {
        listener.set_nonblocking(true).unwrap();
        let deadline = std::time::Instant::now() + Duration::from_secs(5);
        loop {
            match listener.accept() {
                Ok((stream, _)) => return stream,
                Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                    assert!(std::time::Instant::now() < deadline, "backend connection timed out");
                    std::thread::sleep(Duration::from_millis(10));
                }
                Err(e) => panic!("{e}"),
            }
        }
    }
    #[test]
    fn embedded_service_starts_relays_and_cleans_its_sockets() {
        let dir = std::env::temp_dir().join(format!("wivrn-shora-ffi-{}", std::process::id()));
        std::fs::create_dir(&dir).unwrap();
        std::fs::set_permissions(&dir, std::fs::Permissions::from_mode(0o700)).unwrap();
        let bridge = start(dir.clone()).unwrap();
        assert!(start(dir.clone()).is_err()); // Must preserve the live first publisher.
        let listener = std::os::unix::net::UnixListener::bind(dir.join("ShoraInput.backend")).unwrap();
        let mut client = UnixStream::connect(dir.join("SlimeVRInput")).unwrap();
        // A valid feeder tracker identity, including four-byte framing.
        let frame = [10,0,0,0,26,4,8,4,32,19];
        client.write_all(&frame).unwrap();
        let mut backend = accept(&listener);
        backend.set_read_timeout(Some(Duration::from_secs(2))).unwrap();
        let mut received = [0;10]; backend.read_exact(&mut received).unwrap();
        assert_eq!(received,frame);
        drop(backend); drop(listener);
        std::fs::remove_file(dir.join("ShoraInput.backend")).unwrap();
        let listener = std::os::unix::net::UnixListener::bind(dir.join("ShoraInput.backend")).unwrap();
        let mut backend = accept(&listener);
        backend.set_read_timeout(Some(Duration::from_secs(2))).unwrap();
        backend.read_exact(&mut received).unwrap(); // identity replay without client reconnect
        assert_eq!(received,frame);
        drop(bridge);
        assert!(!dir.join("SlimeVRRpc").exists() && !dir.join("SlimeVRInput").exists());
        drop(client); drop(backend); drop(listener);
        std::fs::remove_dir_all(dir).unwrap();
    }
}
