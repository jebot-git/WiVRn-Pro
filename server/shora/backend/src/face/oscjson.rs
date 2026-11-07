//! VRChat OSCQuery avatar discovery, adapted from Shora's oscavmgr port.

use std::io::Read;
use std::net::SocketAddr;
use std::time::{Duration, Instant};

use mdns_sd::{ServiceDaemon, ServiceEvent};
use super::mystery::OscJsonNode;

const RESCAN_INTERVAL: Duration = Duration::from_secs(15);

pub struct OscJsonBrowser {
    mdns: ServiceDaemon,
    events: mdns_sd::Receiver<ServiceEvent>,
    addr: Option<SocketAddr>,
    last_scan: Instant,
}

impl OscJsonBrowser {
    pub fn new() -> Option<Self> {
        let mdns = ServiceDaemon::new().ok()?;
        let _ = mdns.enable_interface(mdns_sd::IfKind::LoopbackV4);
        let events = match mdns.browse("_oscjson._tcp.local.") {
            Ok(events) => events,
            Err(_) => { let _ = mdns.shutdown(); return None; }
        };
        Some(Self { mdns, events, addr: None, last_scan: Instant::now() - RESCAN_INTERVAL })
    }

    pub fn refresh(&mut self) {
        self.last_scan = Instant::now() - RESCAN_INTERVAL;
    }

    pub fn poll_avatar(&mut self) -> Option<OscJsonNode> {
        if self.last_scan.elapsed() < RESCAN_INTERVAL { return None; }
        self.last_scan = Instant::now();
        for event in self.events.try_iter() {
            if let ServiceEvent::ServiceResolved(info) = event {
                if !info.get_fullname().starts_with("VRChat-Client-") { continue; }
                if let Some(ip) = info.get_addresses().iter().find(|ip| ip.is_ipv4()) {
                    self.addr = Some(SocketAddr::new(ip.to_ip_addr(), info.get_port()));
                }
            }
        }
        // Re-fetch periodically and after avatar changes, retrying failed fetches.
        // Static face.avatar files bypass this browser entirely.
        fetch_avatar(self.addr?)
    }
}

impl Drop for OscJsonBrowser {
    fn drop(&mut self) { let _ = self.mdns.shutdown(); }
}

fn fetch_avatar(addr: SocketAddr) -> Option<OscJsonNode> {
    // Current VRChat OSCQuery can compress and chunk its replies. Let the HTTP
    // client decode both, while retaining the existing deadline and size cap.
    let client = reqwest::blocking::Client::builder().no_proxy()
        .redirect(reqwest::redirect::Policy::none())
        .connect_timeout(Duration::from_millis(200))
        .timeout(Duration::from_millis(500)).build().ok()?;
    let response = client.get(format!("http://{addr}/avatar"))
        .header(reqwest::header::ACCEPT, "application/json")
        .send().ok()?.error_for_status().ok()?;
    let mut bytes = Vec::new();
    response.take(1024 * 1024 + 1).read_to_end(&mut bytes).ok()?;
    if bytes.len() > 1024 * 1024 { return None; }
    let node: OscJsonNode = serde_json::from_slice(&bytes).ok()?;
    if node.get("parameters").is_none() && node.native_face_tracking().is_none() { return None; }
    Some(node)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;
    use std::net::TcpListener;

    #[test]
    fn compressed_chunked_oscquery_reply_selects_native_without_legacy_parameters() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let worker = std::thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            stream.set_read_timeout(Some(Duration::from_secs(1))).unwrap();
            let mut request = [0; 4096];
            let _ = stream.read(&mut request).unwrap();
            let bytes: &[u8] = &[31, 139, 8, 0, 0, 0, 0, 0, 2, 255, 171, 86, 114, 11, 245, 241, 137, 15, 112, 12, 241, 80, 178, 82, 210, 79, 44, 75, 44, 73, 44, 82, 210, 81, 114, 246, 247, 11, 113, 245, 11, 9, 86, 178, 170, 86, 130, 8, 166, 149, 230, 228, 164, 37, 38, 167, 150, 20, 37, 38, 103, 103, 230, 165, 131, 100, 176, 232, 213, 199, 161, 90, 71, 41, 204, 209, 39, 212, 85, 201, 42, 186, 164, 168, 52, 53, 182, 182, 182, 22, 0, 114, 35, 164, 112, 123, 0, 0, 0];
            write!(stream, "HTTP/1.1 200 OK\r\nContent-Encoding: gzip\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n").unwrap();
            for chunk in bytes.chunks(17) {
                write!(stream, "{:x}\r\n", chunk.len()).unwrap();
                stream.write_all(chunk).unwrap();
                stream.write_all(b"\r\n").unwrap();
            }
            stream.write_all(b"0\r\n\r\n").unwrap();
        });
        assert_eq!(fetch_avatar(address).unwrap().native_face_tracking(), Some(true));
        worker.join().unwrap();
    }
}
