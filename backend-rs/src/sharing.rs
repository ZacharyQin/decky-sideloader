use crate::{
    common::*,
    diagnostics::{Diagnostics, PACKAGE_BYTES, ZIP_MARKER},
    engine::Engine,
};
use serde_json::{Value, json};
use std::{
    collections::{BTreeMap, BTreeSet},
    io::{Read, Seek, SeekFrom},
    net::Ipv4Addr,
    os::unix::fs::MetadataExt,
    sync::Arc,
    time::{Duration, Instant},
};
use subtle::ConstantTimeEq;
#[cfg(test)]
use tokio::sync::Notify;
use tokio::{
    io::{AsyncReadExt, AsyncWriteExt},
    net::{TcpListener, TcpStream},
    sync::Mutex,
    task::JoinSet,
};
use tokio_util::sync::CancellationToken;
#[cfg(test)]
pub(crate) static PROCESS_TEST_ADDRESSES: std::sync::OnceLock<Vec<String>> =
    std::sync::OnceLock::new();
#[cfg(test)]
type FixtureGate = (Arc<Notify>, CancellationToken);
#[cfg(test)]
#[derive(Default)]
struct ShareFixture {
    headers: Arc<Notify>,
    transfer: Option<FixtureGate>,
    preparing: Option<FixtureGate>,
}
#[derive(Clone)]
pub struct Sharing {
    state: Arc<Mutex<ShareState>>,
    #[cfg(test)]
    fixture: Arc<Mutex<ShareFixture>>,
}
struct ShareState {
    phase: String,
    session: Option<String>,
    package: Option<Value>,
    address: Option<Ipv4Addr>,
    port: u16,
    token: String,
    deadline: Instant,
    successes: u64,
    reserved: u64,
    expected: Option<FileIdentity>,
    cancel: CancellationToken,
    done: Option<CancellationToken>,
    prepared: Option<CancellationToken>,
    reason: String,
}
#[derive(Clone, PartialEq, Eq)]
struct FileIdentity {
    dev: u64,
    ino: u64,
    size: u64,
    mtime: i64,
    mtime_ns: i64,
    ctime: i64,
    ctime_ns: i64,
    nlink: u64,
    uid: u32,
    hash: String,
}
impl FileIdentity {
    fn from(info: &std::fs::Metadata, hash: String) -> Self {
        Self {
            dev: info.dev(),
            ino: info.ino(),
            size: info.len(),
            mtime: info.mtime(),
            mtime_ns: info.mtime_nsec(),
            ctime: info.ctime(),
            ctime_ns: info.ctime_nsec(),
            nlink: info.nlink(),
            uid: info.uid(),
            hash,
        }
    }
    fn matches(&self, info: &std::fs::Metadata) -> bool {
        let mut current = Self::from(info, String::new());
        current.hash = self.hash.clone();
        *self == current
    }
}
pub fn addresses() -> Vec<String> {
    #[cfg(test)]
    if let Some(addresses) = PROCESS_TEST_ADDRESSES.get() {
        return addresses.clone();
    }
    let mut head = std::ptr::null_mut::<libc::ifaddrs>();
    if unsafe { libc::getifaddrs(&mut head) } != 0 {
        return vec![];
    }
    struct Guard(*mut libc::ifaddrs);
    impl Drop for Guard {
        fn drop(&mut self) {
            unsafe { libc::freeifaddrs(self.0) }
        }
    }
    let _guard = Guard(head);
    let mut current = head;
    let mut out = BTreeSet::new();
    let mut count = 0;
    while !current.is_null() && count < 128 {
        let row = unsafe { &*current };
        if !row.ifa_addr.is_null()
            && unsafe { (*row.ifa_addr).sa_family } as i32 == libc::AF_INET
            && row.ifa_flags & ((libc::IFF_UP | libc::IFF_RUNNING) as u32)
                == (libc::IFF_UP | libc::IFF_RUNNING) as u32
            && row.ifa_flags & (libc::IFF_LOOPBACK as u32) == 0
        {
            let addr = unsafe { &*(row.ifa_addr as *const libc::sockaddr_in) };
            let ip = Ipv4Addr::from(addr.sin_addr.s_addr.to_ne_bytes());
            if ip.is_private() {
                out.insert(ip.to_string());
            }
        }
        current = row.ifa_next;
        count += 1;
    }
    out.into_iter().collect()
}
impl Default for Sharing {
    fn default() -> Self {
        Self::new()
    }
}
impl Sharing {
    #[cfg(test)]
    pub(crate) async fn fixture_headers(&self) -> Arc<Notify> {
        self.fixture.lock().await.headers.clone()
    }
    #[cfg(test)]
    pub(crate) async fn fixture_hold_transfers(&self) -> FixtureGate {
        let gate = (Arc::new(Notify::new()), CancellationToken::new());
        self.fixture.lock().await.transfer = Some(gate.clone());
        gate
    }
    #[cfg(test)]
    pub(crate) async fn fixture_hold_preparation(&self) -> FixtureGate {
        let gate = (Arc::new(Notify::new()), CancellationToken::new());
        self.fixture.lock().await.preparing = Some(gate.clone());
        gate
    }
    #[cfg(test)]
    pub(crate) async fn fixture_expire(&self) {
        self.state.lock().await.deadline = Instant::now() - Duration::from_secs(1);
    }
    #[cfg(test)]
    pub(crate) async fn fixture_address(&self, value: Ipv4Addr) {
        self.state.lock().await.address = Some(value);
    }
    #[cfg(test)]
    pub(crate) async fn fixture_counts(&self) -> (u64, u64) {
        let state = self.state.lock().await;
        (state.successes, state.reserved)
    }
    pub fn new() -> Self {
        Self {
            state: Arc::new(Mutex::new(ShareState {
                phase: "idle".into(),
                session: None,
                package: None,
                address: None,
                port: 0,
                token: String::new(),
                deadline: Instant::now(),
                successes: 0,
                reserved: 0,
                expected: None,
                cancel: CancellationToken::new(),
                done: None,
                prepared: None,
                reason: String::new(),
            })),
            #[cfg(test)]
            fixture: Arc::new(Mutex::new(ShareFixture::default())),
        }
    }
    pub async fn status(&self) -> Value {
        let s = self.state.lock().await;
        let mut out = json!({"state":s.phase});
        if let Some(session) = &s.session {
            out["session"] = json!(session);
        }
        if let Some(package) = &s.package {
            out["package"] = package.clone();
        }
        if s.phase == "active" {
            out["url"] = json!(format!(
                "http://{}:{}/pickup/{}",
                s.address.unwrap(),
                s.port,
                s.token
            ));
            out["remaining"] = json!(
                s.deadline
                    .saturating_duration_since(Instant::now())
                    .as_secs_f64()
                    .ceil() as u64
            );
            out["downloads"] = json!(s.successes);
        }
        if s.phase == "error" {
            out["error"] = json!("领取未启动或已失效，请检查所选局域网地址和诊断文件后重试");
        }
        out
    }
    pub async fn active(&self) -> bool {
        matches!(
            self.state.lock().await.phase.as_str(),
            "preparing" | "active" | "draining"
        )
    }
    pub async fn stop(&self, session: Option<&str>) -> Value {
        let done = {
            let mut s = self.state.lock().await;
            if session.is_some_and(|id| s.session.as_deref() != Some(id)) {
                drop(s);
                return self.status().await;
            }
            if matches!(s.phase.as_str(), "preparing" | "active" | "draining") {
                s.phase = "draining".into();
                s.reason = "stopped".into();
                s.token.clear();
                s.cancel.cancel();
                s.done.clone()
            } else {
                None
            }
        };
        if let Some(done) = done {
            done.cancelled().await;
        }
        self.status().await
    }
    async fn fail(&self, reason: &str) {
        let mut s = self.state.lock().await;
        if s.phase == "active" {
            s.phase = "draining".into();
            s.reason = reason.into();
            s.token.clear();
            s.cancel.cancel();
        }
    }
    fn open_package(
        diagnostics: &Diagnostics,
        package: &Value,
        expected: Option<&FileIdentity>,
    ) -> Result<(std::fs::File, FileIdentity)> {
        let name = text(&package["file"]);
        ensure(
            regex::Regex::new(r"^diagnostics-[0-9]{19}-[a-f0-9]{32}\.zip$")
                .unwrap()
                .is_match(name),
            "诊断文件不可用",
        )?;
        let root = diagnostics.export_directory()?;
        let dir = crate::diagnostics::secure_directory(&root, false)?;
        let mut file = dir.file(name)?;
        let info = file.metadata()?;
        ensure(
            info.nlink() == 1
                && info.uid() == unsafe { libc::getuid() }
                && info.mode() & 0o777 == 0o600
                && info.len() > 0
                && info.len() <= PACKAGE_BYTES
                && package["bytes"] == info.len(),
            "诊断文件不可用",
        )?;
        use sha2::{Digest, Sha256};
        let mut marker = vec![0; ZIP_MARKER.len()];
        file.read_exact(&mut marker)?;
        ensure(marker == ZIP_MARKER, "诊断文件标记无效")?;
        let mut digest = Sha256::new();
        digest.update(&marker);
        let mut remaining = info
            .len()
            .checked_sub(marker.len() as u64)
            .ok_or_else(|| Error::invalid("诊断文件不完整"))?;
        let mut buffer = vec![0; 65536];
        while remaining > 0 {
            let count =
                std::io::Read::read(&mut file, &mut buffer[..remaining.min(65536) as usize])?;
            ensure(count > 0, "诊断文件不完整")?;
            digest.update(&buffer[..count]);
            remaining -= count as u64;
        }
        let identity = FileIdentity::from(&info, hex::encode(digest.finalize()));
        ensure(
            expected.is_none_or(|v| v == &identity)
                && identity.matches(&file.metadata()?)
                && identity.matches(&dir.file(name)?.metadata()?),
            "诊断文件已变化",
        )?;
        file.seek(SeekFrom::Start(0))?;
        Ok((file, identity))
    }
    fn unchanged(
        diagnostics: &Diagnostics,
        package: &Value,
        file: &std::fs::File,
        expected: &FileIdentity,
    ) -> bool {
        let check = (|| -> Result<bool> {
            let dir =
                crate::diagnostics::secure_directory(&diagnostics.export_directory()?, false)?;
            Ok(expected.matches(&file.metadata()?)
                && expected.matches(&dir.file(text(&package["file"]))?.metadata()?))
        })();
        check.unwrap_or(false)
    }
    fn path_current(diagnostics: &Diagnostics, package: &Value, expected: &FileIdentity) -> bool {
        let result = (|| -> Result<bool> {
            let dir =
                crate::diagnostics::secure_directory(&diagnostics.export_directory()?, false)?;
            Ok(expected.matches(&dir.file(text(&package["file"]))?.metadata()?))
        })();
        result.unwrap_or(false)
    }
    async fn serve(
        self,
        listener: TcpListener,
        diagnostics: Diagnostics,
        cancel: CancellationToken,
        done: CancellationToken,
        #[cfg(test)] fixture_loopback: bool,
    ) {
        #[cfg(not(test))]
        let fixture_loopback = false;
        let mut clients = JoinSet::new();
        let mut tick = tokio::time::interval(Duration::from_secs(1));
        loop {
            tokio::select! {_=cancel.cancelled()=>break,_=tick.tick()=>{let(package,expected,address,deadline)={let s=self.state.lock().await;(s.package.clone(),s.expected.clone(),s.address,s.deadline)};if Instant::now()>=deadline{self.fail("expired").await;}else if !(fixture_loopback && address == Some(Ipv4Addr::LOCALHOST)) && !addresses().iter().any(|a|Some(a.as_str())==address.as_ref().map(|v|v.to_string()).as_deref())||package.as_ref().zip(expected.as_ref()).is_none_or(|(p,e)|!Self::path_current(&diagnostics,p,e)){self.fail("error").await;}},result=listener.accept()=>match result{Ok((stream,peer))=>{if clients.len()>=4||!peer.ip().is_ipv4(){drop(stream);continue;}let this=self.clone();let diagnostics=diagnostics.clone();let cancellation=cancel.clone();clients.spawn(async move{tokio::select!{_=cancellation.cancelled()=>{},_=this.client(stream,diagnostics)=>{}}});},Err(_)=>{self.fail("error").await;}},Some(_)=clients.join_next(),if !clients.is_empty()=>{}}
        }
        drop(listener);
        clients.shutdown().await;
        let mut s = self.state.lock().await;
        s.phase = if s.reason.is_empty() {
            "stopped"
        } else {
            &s.reason
        }
        .to_owned();
        s.token.clear();
        s.reserved = 0;
        s.done = None;
        done.cancel();
    }
    async fn headers(&self, stream: &mut TcpStream) -> Result<bool> {
        let mut data = Vec::new();
        let mut buffer = [0; 2048];
        let end = loop {
            ensure(data.len() <= 18432, "请求超过上限")?;
            let n = stream.read(&mut buffer).await?;
            ensure(n > 0, "请求不完整")?;
            data.extend_from_slice(&buffer[..n]);
            if let Some(index) = data.windows(4).position(|v| v == b"\r\n\r\n") {
                break index;
            }
            ensure(
                data.windows(2)
                    .position(|v| v == b"\r\n")
                    .is_some_and(|i| i <= 2048)
                    || data.len() <= 2048,
                "请求行超过上限",
            )?;
        };
        ensure(data.len() == end + 4, "请求包含正文")?;
        let raw = std::str::from_utf8(&data[..end]).map_err(|_| Error::invalid("请求无效"))?;
        let mut lines = raw.split("\r\n");
        let first = lines.next().unwrap_or("");
        ensure(first.len() <= 2048, "请求行超过上限")?;
        let parts: Vec<_> = first.split(' ').collect();
        ensure(
            parts.len() == 3 && ["GET", "HEAD"].contains(&parts[0]) && parts[2] == "HTTP/1.1",
            "请求无效",
        )?;
        let (address, port, token) = {
            let s = self.state.lock().await;
            (s.address, s.port, s.token.clone())
        };
        let route = format!("/pickup/{token}");
        ensure(
            !token.is_empty() && bool::from(parts[1].as_bytes().ct_eq(route.as_bytes())),
            "请求无效",
        )?;
        let mut headers = BTreeMap::new();
        let name_regex = regex::Regex::new(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$").unwrap();
        let mut count = 0;
        for line in lines {
            count += 1;
            ensure(count <= 32 && end - first.len() <= 16384, "请求头超过上限")?;
            let (name, value) = line
                .split_once(':')
                .ok_or_else(|| Error::invalid("请求头无效"))?;
            ensure(
                name_regex.is_match(name)
                    && value.bytes().all(|c| c == 9 || (32..=126).contains(&c)),
                "请求头无效",
            )?;
            ensure(
                headers.insert(name.to_lowercase(), value.trim()).is_none(),
                "重复请求头",
            )?;
        }
        let host = format!(
            "{}:{port}",
            address.ok_or_else(|| Error::invalid("下载已停止"))?
        );
        ensure(
            headers.get("host") == Some(&host.as_str())
                && !headers.keys().any(|k| {
                    ["range", "transfer-encoding", "expect", "upgrade"].contains(&k.as_str())
                })
                && headers.get("content-length").is_none_or(|v| *v == "0"),
            "请求头无效",
        )?;
        for name in ["purpose", "sec-purpose"] {
            ensure(
                !headers.get(name).is_some_and(|v| {
                    v.split([',', ';', '\t', ' '])
                        .any(|s| s.eq_ignore_ascii_case("prefetch"))
                }),
                "预取不计为下载",
            )?;
        }
        Ok(parts[0] == "GET")
    }
    async fn client(&self, mut stream: TcpStream, diagnostics: Diagnostics) {
        #[cfg(test)]
        self.fixture.lock().await.headers.notify_one();
        let method = tokio::time::timeout(Duration::from_secs(5), self.headers(&mut stream)).await;
        let get = match method {
            Ok(Ok(v)) => v,
            _ => {
                reject(&mut stream, "400 Bad Request").await;
                return;
            }
        };
        let mut reserved = false;
        let (package, expected, deadline) = {
            let mut s = self.state.lock().await;
            if s.phase != "active" || Instant::now() >= s.deadline {
                return;
            }
            if get {
                if s.reserved >= 2 || s.successes + s.reserved >= 3 {
                    drop(s);
                    reject(&mut stream, "429 Too Many Requests").await;
                    return;
                }
                s.reserved += 1;
                reserved = true;
            }
            (
                s.package.clone().unwrap(),
                s.expected.clone().unwrap(),
                s.deadline,
            )
        };
        let result=tokio::time::timeout(Duration::from_secs(30),async{let(mut file,_)=Self::open_package(&diagnostics,&package,Some(&expected))?;ensure(Instant::now()<deadline,"下载已到期")?;let header=format!("HTTP/1.1 200 OK\r\nContent-Type: application/zip\r\nContent-Length: {}\r\nContent-Disposition: attachment; filename=\"{}\"\r\nCache-Control: no-store\r\nPragma: no-cache\r\nX-Content-Type-Options: nosniff\r\nReferrer-Policy: no-referrer\r\nConnection: close\r\n\r\n",package["bytes"],text(&package["file"]));stream.write_all(header.as_bytes()).await?;
            #[cfg(test)]
            if get {
                let gate = self.fixture.lock().await.transfer.clone();
                if let Some((entered, release)) = gate { entered.notify_one(); release.cancelled().await; }
            }
            if get{let mut remaining=expected.size;let mut buffer=vec![0;65536];while remaining>0{ensure(Instant::now()<deadline&&Self::unchanged(&diagnostics,&package,&file,&expected),"诊断文件已变化或下载到期")?;let n=std::io::Read::read(&mut file,&mut buffer[..remaining.min(65536)as usize])?;ensure(n>0,"诊断文件不完整")?;stream.write_all(&buffer[..n]).await?;remaining-=n as u64;}ensure(Instant::now()<deadline&&Self::unchanged(&diagnostics,&package,&file,&expected),"诊断文件已变化或下载到期")?;}stream.shutdown().await?;Ok::<_,Error>(())}).await;
        let mut s = self.state.lock().await;
        if reserved {
            s.reserved = s.reserved.saturating_sub(1);
        }
        if get && matches!(result, Ok(Ok(()))) && s.phase == "active" {
            s.successes += 1;
            if s.successes >= 3 {
                s.phase = "draining".into();
                s.reason = "exhausted".into();
                s.token.clear();
                s.cancel.cancel();
            }
        }
    }
}
async fn reject(stream: &mut TcpStream, status: &str) {
    let _=tokio::time::timeout(Duration::from_millis(200),stream.write_all(format!("HTTP/1.1 {status}\r\nContent-Length: 0\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n").as_bytes())).await;
}
impl Engine {
    pub async fn start_share(
        self: &Arc<Self>,
        address: &str,
        session: &str,
        previous: Option<&str>,
        steam: &Value,
    ) -> Result<Value> {
        self.start_share_inner(
            address,
            session,
            previous,
            steam,
            #[cfg(test)]
            false,
        )
        .await
    }
    #[cfg(test)]
    pub(crate) async fn start_share_loopback_fixture(
        self: &Arc<Self>,
        session: &str,
        previous: Option<&str>,
        steam: &Value,
    ) -> Result<Value> {
        self.start_share_inner("127.0.0.1", session, previous, steam, true)
            .await
    }
    async fn start_share_inner(
        self: &Arc<Self>,
        address: &str,
        session: &str,
        previous: Option<&str>,
        steam: &Value,
        #[cfg(test)] fixture_loopback: bool,
    ) -> Result<Value> {
        #[cfg(not(test))]
        let fixture_loopback = false;
        ensure(
            valid_id(session) && previous.is_none_or(valid_id),
            "领取请求无效",
        )?;
        let addr: Ipv4Addr = address
            .parse()
            .map_err(|_| Error::invalid("所选地址无效"))?;
        ensure(
            (addr.is_private() || (fixture_loopback && addr == Ipv4Addr::LOCALHOST))
                && addr.to_string() == address,
            "所选地址无效",
        )?;
        self.require_running().await?;
        let done = {
            let mut s = self.sharing.state.lock().await;
            if s.session.as_deref() == Some(session) {
                ensure(s.address == Some(addr), "领取意图地址不匹配")?;
                let pending = if s.phase == "preparing" {
                    s.prepared.clone()
                } else {
                    None
                };
                drop(s);
                if let Some(done) = pending {
                    done.cancelled().await;
                }
                return Ok(self.sharing.status().await);
            }
            ensure(
                previous == s.session.as_deref(),
                "领取意图已变化，请刷新状态后重试",
            )?;
            ensure(
                !matches!(s.phase.as_str(), "preparing" | "active" | "draining")
                    && self.diagnostics.status()["state"] != "exporting",
                "诊断导出或领取正在进行，请先停止领取",
            )?;
            ensure(
                (fixture_loopback && addr == Ipv4Addr::LOCALHOST)
                    || addresses().iter().any(|s| s == address),
                "所选地址不在可用局域网中，请刷新后重试",
            )?;
            let done = CancellationToken::new();
            s.phase = "preparing".into();
            s.session = Some(session.into());
            s.address = Some(addr);
            s.package = None;
            s.expected = None;
            s.successes = 0;
            s.reserved = 0;
            s.reason.clear();
            s.cancel = self.stop.child_token();
            s.done = Some(done.clone());
            s.prepared = Some(CancellationToken::new());
            done
        };
        let result = async {
            #[cfg(test)]
            {
                let gate = self.sharing.fixture.lock().await.preparing.clone();
                if let Some((entered, release)) = gate {
                    entered.notify_one();
                    release.cancelled().await;
                }
            }
            let summary = self.diagnostic_summary(steam).await;
            let package = self.diagnostics.export(summary, self.stop.clone()).await?;
            ensure(
                package["state"] == "success"
                    && ((fixture_loopback && addr == Ipv4Addr::LOCALHOST)
                        || addresses().iter().any(|s| s == address)),
                "诊断下载不可用",
            )?;
            let (_, identity) = Sharing::open_package(&self.diagnostics, &package, None)?;
            let listener = TcpListener::bind((addr, 0)).await?;
            let port = listener.local_addr()?.port();
            let cancel = {
                let mut s = self.sharing.state.lock().await;
                ensure(
                    s.phase == "preparing" && !s.cancel.is_cancelled(),
                    "诊断下载已取消",
                )?;
                s.phase = "active".into();
                s.package = Some(package);
                s.expected = Some(identity);
                s.port = port;
                s.token = format!("{}{}", id(), id());
                s.deadline = Instant::now() + Duration::from_secs(300);
                if let Some(prepared) = s.prepared.take() {
                    prepared.cancel();
                }
                s.cancel.clone()
            };
            let sharing = self.sharing.clone();
            let diagnostics = self.diagnostics.clone();
            let d = done.clone();
            self.tasks.spawn(async move {
                sharing
                    .serve(
                        listener,
                        diagnostics,
                        cancel,
                        d,
                        #[cfg(test)]
                        fixture_loopback,
                    )
                    .await;
            });
            Ok::<_, Error>(())
        }
        .await;
        if result.is_err() {
            let mut s = self.sharing.state.lock().await;
            s.phase = if s.phase == "draining" {
                "stopped"
            } else {
                "error"
            }
            .into();
            s.token.clear();
            s.done = None;
            if let Some(prepared) = s.prepared.take() {
                prepared.cancel();
            }
            done.cancel();
        }
        Ok(self.sharing.status().await)
    }
    pub async fn export_diagnostics(self: &Arc<Self>, steam: &Value) -> Result<Value> {
        ensure(
            !self.sharing.active().await,
            "诊断下载正在进行，请先停止下载服务",
        )?;
        self.diagnostics
            .export(self.diagnostic_summary(steam).await, self.stop.clone())
            .await
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn address_policy() {
        for s in ["10.0.0.1", "172.16.0.1", "192.168.0.1"] {
            assert!(s.parse::<Ipv4Addr>().unwrap().is_private());
        }
        for s in ["127.0.0.1", "8.8.8.8"] {
            assert!(!s.parse::<Ipv4Addr>().unwrap().is_private());
        }
    }
}
