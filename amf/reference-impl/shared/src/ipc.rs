use anyhow::Result;
use serde_json;
use std::io::{Read, Write};
use std::os::unix::net::{UnixListener, UnixStream};
use std::path::Path;
use std::time::Duration;

/// IPC message (admission request/response, DH exchange)
use crate::data::Message;

#[derive(serde::Serialize, serde::Deserialize, Debug, Clone)]
pub struct IPCMessage {
    pub msg_type: String, // "admit_request", "admit_response", "dh_exchange", "message"
    pub payload: serde_json::Value,
}

/// Unix socket server for ring agents
pub struct RingSocketServer {
    listener: UnixListener,
    path: std::path::PathBuf,
}

impl RingSocketServer {
    pub fn bind(socket_path: &Path) -> Result<Self> {
        // Remove existing socket if present
        let _ = std::fs::remove_file(socket_path);

        let listener = UnixListener::bind(socket_path)?;
        listener.set_nonblocking(true)?;

        Ok(RingSocketServer {
            listener,
            path: socket_path.to_path_buf(),
        })
    }

    pub fn accept(&self) -> Result<Option<RingSocketClient>> {
        match self.listener.accept() {
            Ok((stream, _)) => {
                stream.set_read_timeout(Some(Duration::from_secs(30)))?;
                stream.set_write_timeout(Some(Duration::from_secs(30)))?;
                Ok(Some(RingSocketClient { stream }))
            }
            Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => Ok(None),
            Err(e) => Err(anyhow::anyhow!("Accept error: {}", e)),
        }
    }
}

impl Drop for RingSocketServer {
    fn drop(&mut self) {
        let _ = std::fs::remove_file(&self.path);
    }
}

/// Unix socket client connection
pub struct RingSocketClient {
    stream: UnixStream,
}

impl RingSocketClient {
    pub fn connect(socket_path: &Path) -> Result<Self> {
        let stream = UnixStream::connect(socket_path)?;
        stream.set_read_timeout(Some(Duration::from_secs(30)))?;
        stream.set_write_timeout(Some(Duration::from_secs(30)))?;
        Ok(RingSocketClient { stream })
    }

    pub fn send(&mut self, msg: &IPCMessage) -> Result<()> {
        let json = serde_json::to_string(msg)?;
        self.stream.write_all(json.as_bytes())?;
        self.stream.write_all(b"\n")?;
        Ok(())
    }

    pub fn recv(&mut self) -> Result<Option<IPCMessage>> {
        let mut buffer = vec![0; 4096];
        match self.stream.read(&mut buffer) {
            Ok(0) => Ok(None),
            Ok(n) => {
                let json_str = std::str::from_utf8(&buffer[..n])?;
                let msg = serde_json::from_str(json_str)?;
                Ok(Some(msg))
            }
            Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => Ok(None),
            Err(e) => Err(anyhow::anyhow!("Recv error: {}", e)),
        }
    }
}
