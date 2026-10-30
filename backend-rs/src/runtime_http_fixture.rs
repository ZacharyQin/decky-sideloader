use std::{
    collections::{HashMap, VecDeque},
    sync::{Arc, Mutex},
    time::Duration,
};
use tokio::{
    io::{AsyncReadExt, AsyncWriteExt},
    net::TcpListener,
    sync::Notify,
};
use tokio_util::{sync::CancellationToken, task::TaskTracker};

#[derive(Clone)]
pub(super) struct Reply {
    pub body: Vec<u8>,
    pub status: u16,
    pub headers: Vec<(String, String)>,
    pub ignore_range: bool,
    pub block: Option<(Arc<Notify>, CancellationToken)>,
    pub chunks: Option<(usize, Duration)>,
    pub declared: Option<usize>,
    pub disconnect_after: Option<usize>,
    pub body_prefix: usize,
}
impl Reply {
    pub fn bytes(body: Vec<u8>) -> Self {
        Self {
            body,
            status: 200,
            headers: vec![],
            ignore_range: false,
            block: None,
            chunks: None,
            declared: None,
            disconnect_after: None,
            body_prefix: 0,
        }
    }
    pub fn json(value: serde_json::Value) -> Self {
        Self::bytes(serde_json::to_vec(&value).unwrap())
    }
    pub fn status(status: u16) -> Self {
        Self {
            status,
            ..Self::bytes(Vec::new())
        }
    }
}
type Rules = Arc<Mutex<HashMap<String, (usize, usize)>>>;
type Scripts = Arc<Mutex<HashMap<String, VecDeque<Reply>>>>;
pub(super) struct Server {
    pub origin: String,
    pub routes: Arc<Mutex<HashMap<String, Reply>>>,
    pub seen: Arc<Mutex<Vec<String>>>,
    rules: Rules,
    scripts: Scripts,
    errors: Arc<Mutex<Vec<String>>>,
    stop: CancellationToken,
    tasks: TaskTracker,
}
impl Server {
    pub async fn new() -> Self {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let authority = listener.local_addr().unwrap().to_string();
        let origin = format!("http://{authority}");
        let routes = Arc::new(Mutex::new(HashMap::<String, Reply>::new()));
        let seen = Arc::new(Mutex::new(Vec::<String>::new()));
        let rules: Rules = Arc::new(Mutex::new(HashMap::new()));
        let scripts: Scripts = Arc::new(Mutex::new(HashMap::new()));
        let errors = Arc::new(Mutex::new(Vec::<String>::new()));
        let stop = CancellationToken::new();
        let tasks = TaskTracker::new();
        let (table, requests, contracts, sequences, violations, cancel, clients) = (
            routes.clone(),
            seen.clone(),
            rules.clone(),
            scripts.clone(),
            errors.clone(),
            stop.clone(),
            tasks.clone(),
        );
        tasks.spawn(async move {
            loop {
                let (mut stream,_)=tokio::select!{_=cancel.cancelled()=>break,result=listener.accept()=>match result{Ok(v)=>v,Err(_)=>break}};
                let (table,requests,contracts,sequences,violations,stop,authority)=(table.clone(),requests.clone(),contracts.clone(),sequences.clone(),violations.clone(),cancel.clone(),authority.clone());
                clients.spawn(async move {
                    let work=async {
                        let mut raw=Vec::new();let mut buffer=[0;1024];
                        while !raw.windows(4).any(|w|w==b"\r\n\r\n") {
                            let n=tokio::time::timeout(Duration::from_secs(3),stream.read(&mut buffer)).await.unwrap_or(Ok(0)).unwrap_or(0);
                            if n==0||raw.len()>8192 {violations.lock().unwrap().push("incomplete/oversized request".into());return;}
                            raw.extend_from_slice(&buffer[..n]);
                        }
                        let text=String::from_utf8_lossy(&raw);let mut lines=text.lines();
                        let first=lines.next().unwrap_or("");let parts:Vec<_>=first.split_whitespace().collect();
                        let uri=parts.get(1).unwrap_or(&"").trim_start_matches('/').to_owned();
                        let mut headers=HashMap::new();
                        for line in lines.filter(|line|!line.is_empty()) {
                            let Some((key,value))=line.split_once(':') else{violations.lock().unwrap().push("malformed header".into());return;};
                            if headers.insert(key.to_ascii_lowercase(),value.trim().to_owned()).is_some(){violations.lock().unwrap().push("duplicate header".into());return;}
                        }
                        let key=match headers.get("range"){Some(range)=>format!("{uri};range={range}"),None=>uri.clone()};
                        let actual_count={let mut rows=requests.lock().unwrap();rows.push(key.clone());rows.iter().filter(|v|**v==key).count()};
                        let allowed=["host","accept","accept-encoding","user-agent","range"];
                        let canonical=uri.strip_prefix("https://gh-proxy.org/").or_else(||uri.strip_prefix("https://ghfast.top/")).unwrap_or(&uri);
                        let accept=if canonical.starts_with("https://github.com/") || canonical.starts_with("https://release-assets.githubusercontent.com/") || canonical.starts_with("https://objects.githubusercontent.com/") {"application/octet-stream"}else{"application/vnd.github+json"};
                        if parts.len()!=3||parts[0]!="GET"||parts[2]!="HTTP/1.1"||headers.keys().any(|key|!allowed.contains(&key.as_str()))||headers.get("host")!=Some(&authority)||headers.get("accept-encoding").map(String::as_str)!=Some("identity")||headers.get("user-agent").map(String::as_str)!=Some("decky-sideloader")||headers.get("accept").map(String::as_str)!=Some(accept){violations.lock().unwrap().push(format!("unexpected method/headers: {first}"));return;}
                        let contract=contracts.lock().unwrap().get(&key).copied();
                        if contract.is_none_or(|(_,max)|actual_count>max){violations.lock().unwrap().push(format!("unknown/overconsumed route {key}"));let _=stream.write_all(b"HTTP/1.1 500 Fixture mismatch\r\nContent-Length: 0\r\nConnection: close\r\n\r\n").await;return;}
                        let scripted=sequences.lock().unwrap().get_mut(&uri).and_then(VecDeque::pop_front);
                        let reply=scripted.or_else(||table.lock().unwrap().get(&uri).cloned());
                        let Some(mut reply)=reply else {violations.lock().unwrap().push(format!("missing fixture body {uri}"));return;};
                        let mut response_headers=reply.headers.clone();
                        if let Some(range)=headers.get("range") {
                            if !reply.ignore_range&&reply.status==200 {
                                let size=reply.body.len();let Some(span)=range.strip_prefix("bytes=") else{violations.lock().unwrap().push("invalid range".into());return;};
                                let parsed=if let Some(last)=span.strip_prefix('-'){last.parse::<usize>().ok().map(|n|(size.saturating_sub(n),size.saturating_sub(1)))}else{span.split_once('-').and_then(|(a,b)|Some((a.parse::<usize>().ok()?,b.parse::<usize>().ok()?.min(size.saturating_sub(1)))))};
                                let Some((start,end))=parsed.filter(|(a,b)|a<=b&&*b<size) else {violations.lock().unwrap().push("out-of-bounds range".into());return;};
                                response_headers.push(("Content-Range".into(),format!("bytes {start}-{end}/{size}")));reply.body=reply.body[start..=end].to_vec();reply.status=206;
                            }
                        } else if let Some((entered,release))=&reply.block {entered.notify_one();release.cancelled().await;}
                        let declared=reply.declared.unwrap_or(reply.body.len());
                        let mut response=format!("HTTP/1.1 {} Test\r\nContent-Length: {declared}\r\nConnection: close\r\n",reply.status);
                        for (key,value) in response_headers {response.push_str(&format!("{key}: {value}\r\n"));}response.push_str("\r\n");
                        if stream.write_all(response.as_bytes()).await.is_err(){return;}
                        let body=&reply.body[..reply.disconnect_after.unwrap_or(reply.body.len()).min(reply.body.len())];
                        let(prefix,body)=body.split_at(reply.body_prefix.min(body.len()));
                        if !prefix.is_empty() && stream.write_all(prefix).await.is_err(){return;}
                        if let Some((size,delay))=reply.chunks {for chunk in body.chunks(size){tokio::time::sleep(delay).await;if stream.write_all(chunk).await.is_err(){return;}}}else{let _=stream.write_all(body).await;}
                        let _=stream.shutdown().await;
                    };
                    tokio::select!{_=stop.cancelled()=>{},_=work=>{}}
                });
            }
        });
        Self {
            origin,
            routes,
            seen,
            rules,
            scripts,
            errors,
            stop,
            tasks,
        }
    }
    pub fn put(&self, url: &str, reply: Reply) {
        self.routes.lock().unwrap().insert(url.into(), reply);
        self.rules
            .lock()
            .unwrap()
            .entry(url.into())
            .or_insert((1, 1));
    }
    pub fn expect_range(&self, url: &str, range: &str, count: usize) {
        self.rules
            .lock()
            .unwrap()
            .insert(format!("{url};range={range}"), (count, count));
    }
    pub fn asset(&self, url: &str, reply: Reply, probes: usize, downloads: usize) {
        let mut archive = zip::ZipArchive::new(std::io::Cursor::new(&reply.body)).unwrap();
        let metadata = archive
            .file_names()
            .find(|name| name.ends_with("/package.json"))
            .unwrap()
            .to_owned();
        let entry = archive.by_name(&metadata).unwrap();
        let offset = entry.header_start();
        let content = entry.data_start();
        let size = entry.compressed_size();
        drop(entry);
        drop(archive);
        self.expect_range(url, "bytes=-65557", probes);
        self.expect_range(url, &format!("bytes={offset}-{}", offset + 29), probes);
        self.expect_range(
            url,
            &format!("bytes={}-{}", offset + 30, content - 1),
            probes,
        );
        self.expect_range(
            url,
            &format!("bytes={content}-{}", content + size - 1),
            probes,
        );
        self.expect(url, reply, downloads);
    }
    pub fn optional(&self, url: &str) {
        self.rules
            .lock()
            .unwrap()
            .get_mut(url)
            .expect("route must be declared")
            .0 = 0;
    }
    pub fn expect_count(&self, url: &str, count: usize) {
        self.rules
            .lock()
            .unwrap()
            .insert(url.into(), (count, count));
    }
    pub fn expect(&self, url: &str, reply: Reply, count: usize) {
        self.put(url, reply);
        self.rules
            .lock()
            .unwrap()
            .insert(url.into(), (count, count));
    }
    pub fn sequence(&self, url: &str, replies: Vec<Reply>) {
        assert!(!replies.is_empty());
        self.expect(url, replies.last().unwrap().clone(), replies.len());
        self.scripts
            .lock()
            .unwrap()
            .insert(url.into(), replies.into());
    }
    pub fn count(&self) -> usize {
        self.seen.lock().unwrap().len()
    }
    pub fn count_url(&self, url: &str) -> usize {
        self.seen
            .lock()
            .unwrap()
            .iter()
            .filter(|v| v.as_str() == url)
            .count()
    }
    pub async fn finish(&self) {
        self.stop.cancel();
        self.tasks.close();
        tokio::time::timeout(Duration::from_secs(3), self.tasks.wait())
            .await
            .expect("HTTP fixture tasks leaked");
        let errors = self.errors.lock().unwrap().clone();
        assert!(errors.is_empty(), "HTTP fixture violations: {errors:?}");
        for (url, (min, max)) in self.rules.lock().unwrap().iter() {
            let actual = self.count_url(url);
            assert!(
                (*min..=*max).contains(&actual),
                "HTTP route {url}: expected {min}..={max}, got {actual}"
            );
        }
        assert!(
            self.scripts
                .lock()
                .unwrap()
                .values()
                .all(VecDeque::is_empty),
            "HTTP script not consumed"
        );
    }
}
impl Drop for Server {
    fn drop(&mut self) {
        self.stop.cancel();
    }
}
