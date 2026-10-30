//! Private process fixture checkpoint. No module or configuration enters the release binary.
use crate::common::{Result, ensure};
use std::{
    io::{Read, Write},
    path::PathBuf,
    sync::{
        OnceLock,
        atomic::{AtomicBool, Ordering},
    },
    time::Duration,
};

pub(crate) static PROCESS_BACKUP_SOCKET: OnceLock<PathBuf> = OnceLock::new();
pub(crate) static PROCESS_POINT: OnceLock<String> = OnceLock::new();
static ENTERED: AtomicBool = AtomicBool::new(false);
pub(crate) fn checkpoint() -> Result<()> {
    checkpoint_at("backup-first-chunk")
}
pub(crate) fn checkpoint_at(point: &str) -> Result<()> {
    if PROCESS_POINT
        .get()
        .is_none_or(|configured| configured != point)
    {
        return Ok(());
    }
    let Some(path) = PROCESS_BACKUP_SOCKET.get() else {
        return Ok(());
    };
    if ENTERED.swap(true, Ordering::SeqCst) {
        return Ok(());
    }
    let mut socket = std::os::unix::net::UnixStream::connect(path)?;
    socket.set_read_timeout(Some(Duration::from_secs(15)))?;
    socket.set_write_timeout(Some(Duration::from_secs(3)))?;
    socket.write_all(format!("{point}\n").as_bytes())?;
    let mut response = [0];
    socket.read_exact(&mut response)?;
    ensure(response == *b"1", "进程测试检查点回执无效")
}
