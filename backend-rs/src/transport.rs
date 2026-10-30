use crate::common::*;
use reqwest::{Client, Response, StatusCode, header};
use serde_json::{Value, json};
use std::{
    path::Path,
    sync::{Arc, Mutex},
    time::{Duration, Instant},
};
use tokio::{io::AsyncWriteExt, time::timeout};
use tokio_util::sync::CancellationToken;
use url::Url;

#[cfg(test)]
pub(crate) static PROCESS_TEST_ORIGIN: std::sync::OnceLock<String> = std::sync::OnceLock::new();

#[derive(Clone)]
pub struct Transport {
    pub mode: String,
    client: Client,
    pub last: Arc<Mutex<Option<Value>>>,
    pub observer: Option<Arc<dyn Fn(Value) + Send + Sync>>,
    #[cfg(test)]
    pub test_origin: Option<String>,
}
#[derive(Clone, Copy, PartialEq)]
pub enum Kind {
    Query,
    Download,
    Store,
}
fn proxy_origin(route: &str) -> Option<&'static str> {
    match route {
        "gh-proxy" => Some("https://gh-proxy.org"),
        "ghfast" => Some("https://ghfast.top"),
        _ => None,
    }
}
pub fn allowed(raw: &str, kind: Kind, initial: bool) -> bool {
    if raw.chars().any(|c| c.is_whitespace() || c.is_control()) || raw.contains('\\') {
        return false;
    }
    let Ok(url) = Url::parse(raw) else {
        return false;
    };
    if url.scheme() != "https"
        || !url.username().is_empty()
        || url.password().is_some()
        || url.fragment().is_some()
        || url.port().is_some_and(|p| p != 443)
    {
        return false;
    }
    match kind {
        Kind::Query => matches!(
            url.host_str(),
            Some("api.github.com" | "raw.githubusercontent.com")
        ),
        Kind::Store => url.host_str() == Some("plugins.deckbrew.xyz"),
        Kind::Download => {
            if initial {
                let parts: Vec<_> = url.path().split('/').collect();
                url.host_str() == Some("github.com")
                    && parts.len() >= 7
                    && parts[3] == "releases"
                    && parts[4] == "download"
                    && parts.iter().skip(1).all(|s| !s.is_empty())
            } else {
                matches!(
                    url.host_str(),
                    Some(
                        "github.com"
                            | "release-assets.githubusercontent.com"
                            | "objects.githubusercontent.com"
                    )
                )
            }
        }
    }
}
fn retry(e: &Error) -> bool {
    matches!(
        e.code.as_str(),
        "network" | "timeout" | "rate_limited" | "server_error"
    )
}
impl Transport {
    pub fn new(mode: &str, last: Arc<Mutex<Option<Value>>>) -> Result<Self> {
        ensure(
            ["direct", "auto", "gh-proxy", "ghfast"].contains(&mode),
            "GitHub 连接模式无效",
        )?;
        let client = Client::builder()
            .no_proxy()
            .redirect(reqwest::redirect::Policy::none())
            .connect_timeout(Duration::from_secs(60))
            .pool_idle_timeout(Duration::from_secs(30))
            .user_agent("decky-sideloader")
            .build()
            .map_err(|_| Error::new("network", "TLS 客户端初始化失败"))?;
        Ok(Self {
            mode: mode.into(),
            client,
            last,
            observer: None,
            #[cfg(test)]
            test_origin: PROCESS_TEST_ORIGIN.get().cloned(),
        })
    }
    pub fn routes(&self, url: &str) -> Vec<&'static str> {
        let api = Url::parse(url)
            .ok()
            .and_then(|u| u.host_str().map(|v| v == "api.github.com"))
            .unwrap_or(false);
        match self.mode.as_str() {
            "direct" => vec!["direct"],
            "gh-proxy" => vec!["gh-proxy"],
            "ghfast" => {
                if api {
                    vec!["direct", "gh-proxy"]
                } else {
                    vec!["ghfast"]
                }
            }
            _ => {
                if api {
                    vec!["direct", "gh-proxy"]
                } else {
                    vec!["direct", "gh-proxy", "ghfast"]
                }
            }
        }
    }
    fn record(&self, route: &str, attempt: usize) {
        if let Some(observer) = &self.observer {
            observer(json!({"route":route,"attempt":attempt,"status":"running"}));
        }
    }
    async fn open(
        &self,
        canonical: &str,
        route: &str,
        kind: Kind,
        wait: Duration,
        range: Option<&str>,
        cancel: &CancellationToken,
    ) -> Result<Response> {
        ensure(allowed(canonical, kind, true), "GitHub 连接目标不受支持")?;
        let mut current = if let Some(origin) = proxy_origin(route) {
            format!("{origin}/{canonical}")
        } else {
            canonical.to_owned()
        };
        let started = Instant::now();
        for _ in 0..=5 {
            cancelled(cancel)?;
            let remaining = wait
                .checked_sub(started.elapsed())
                .ok_or_else(|| Error::new("timeout", "等待响应超时"))?;
            #[cfg(test)]
            let effective = self
                .test_origin
                .as_ref()
                .map_or_else(|| current.clone(), |origin| format!("{origin}/{current}"));
            #[cfg(not(test))]
            let effective = current.clone();
            let mut req = self
                .client
                .get(&effective)
                .header(
                    header::ACCEPT,
                    if kind == Kind::Download {
                        "application/octet-stream"
                    } else {
                        "application/vnd.github+json"
                    },
                )
                .header(header::ACCEPT_ENCODING, "identity");
            if let Some(range) = range {
                req = req.header(header::RANGE, range);
            }
            let res = tokio::select! {_ = cancel.cancelled()=>return Err(Error::new("cancelled","下载已停止，尚未交给 Loader")),res=timeout(remaining,req.send())=>res.map_err(|_|Error::new("timeout","等待响应超时"))?.map_err(|e|Error::new(if e.is_timeout(){"timeout"}else{"network"},"GitHub 连接失败"))?};
            if res.status().is_redirection() {
                let next = res
                    .headers()
                    .get(header::LOCATION)
                    .and_then(|v| v.to_str().ok())
                    .ok_or_else(|| Error::new("invalid_response", "重定向地址缺失"))?;
                let base = Url::parse(&current).map_err(|_| Error::invalid("重定向地址无效"))?;
                let next = base
                    .join(next)
                    .map_err(|_| Error::invalid("重定向地址无效"))?;
                let direct = allowed(next.as_str(), kind, false);
                let routed = proxy_origin(route).is_some_and(|origin| {
                    next.as_str()
                        .strip_prefix(&format!("{origin}/"))
                        .is_some_and(|v| allowed(v, kind, false))
                });
                ensure(direct || routed, "GitHub 重定向目标不受支持")?;
                current = next.to_string();
                continue;
            }
            if !res.status().is_success() {
                let code = res.status();
                let rate = code == StatusCode::TOO_MANY_REQUESTS
                    || code == StatusCode::FORBIDDEN
                        && res
                            .headers()
                            .get("x-ratelimit-remaining")
                            .is_some_and(|s| s == "0");
                return Err(Error::new(
                    if rate {
                        "rate_limited"
                    } else if code.is_server_error() {
                        "server_error"
                    } else {
                        "http"
                    },
                    if rate {
                        "GitHub 请求已限流，请稍后重试".into()
                    } else {
                        format!("HTTP {} {canonical}", code.as_u16())
                    },
                ));
            }
            ensure(
                res.headers().get_all(header::CONTENT_LENGTH).iter().count() <= 1
                    && !(res.headers().contains_key(header::CONTENT_LENGTH)
                        && res.headers().contains_key(header::TRANSFER_ENCODING)),
                "Content-Length 重复或与传输编码冲突",
            )?;
            if kind == Kind::Query {
                if let Ok(mut last) = self.last.lock() {
                    *last = Some(json!({"route":route,"timestamp":seconds()}));
                }
            }
            return Ok(res);
        }
        Err(Error::new("invalid_response", "GitHub 重定向次数超过上限"))
    }
    pub async fn bytes(
        &self,
        url: &str,
        max: usize,
        cancel: &CancellationToken,
    ) -> Result<Vec<u8>> {
        self.fetch(url, max, None, Kind::Query, cancel)
            .await
            .map(|v| v.0)
    }
    pub async fn json(&self, url: &str, max: usize, cancel: &CancellationToken) -> Result<Value> {
        Ok(serde_json::from_slice(
            &self.bytes(url, max, cancel).await?,
        )?)
    }
    pub async fn store(&self, url: &str, max: usize, cancel: &CancellationToken) -> Result<Value> {
        let (data, _) = self.fetch(url, max, None, Kind::Store, cancel).await?;
        Ok(serde_json::from_slice(&data)?)
    }
    async fn fetch(
        &self,
        url: &str,
        max: usize,
        range: Option<String>,
        kind: Kind,
        cancel: &CancellationToken,
    ) -> Result<(Vec<u8>, Option<(u64, u64, u64)>)> {
        let routes = if kind == Kind::Store {
            vec!["direct"]
        } else {
            self.routes(url)
        };
        let mut last = Error::new("network", "GitHub 查询失败");
        let range_pattern = regex::Regex::new(r"^bytes (\d+)-(\d+)/(\d+|\*)$").unwrap();
        for (i, route) in routes.iter().enumerate() {
            self.record(route, i + 1);
            let result=async{
                let mut res=self.open(url,route,kind,Duration::from_secs(5),range.as_deref(),cancel).await?;
                if let Some(size)=res.content_length(){ensure(size<=max as u64,"响应超过读取上限")?;}
                let content_range=if range.is_some(){
                    if res.status()==StatusCode::OK {
                        let size=res.content_length().ok_or_else(||Error::invalid("服务器未按范围返回"))?;
                        ensure(size>0&&size<=max as u64,"服务器未按范围返回")?;
                        Some((0,size-1,size))
                    } else {
                        ensure(res.status()==StatusCode::PARTIAL_CONTENT,"安装包范围请求没有返回部分内容")?;
                        let raw=res.headers().get(header::CONTENT_RANGE).and_then(|v|v.to_str().ok()).unwrap_or("");
                        let caps=range_pattern.captures(raw).ok_or_else(||Error::new("invalid_response","Content-Range 无效"))?;
                        let parse=|n|caps.get(n).unwrap().as_str().parse::<u64>().map_err(|_|Error::invalid("Content-Range 无效"));
                        let(a,b)=(parse(1)?,parse(2)?);let total=if &caps[3]=="*"{b.checked_add(1).ok_or_else(||Error::invalid("Content-Range 无效"))?}else{parse(3)?};
                        ensure(a<=b&&b<total&&total<=2*1024*1024*1024,"Content-Range 无效")?;Some((a,b,total))
                    }
                }else{None};
                let declared=res.content_length();let mut bytes=Vec::new();
                loop{let chunk=tokio::select!{_=cancel.cancelled()=>return Err(Error::new("cancelled","查询已取消")),v=timeout(Duration::from_secs(5),res.chunk())=>v.map_err(|_|Error::new("timeout","响应正文超过 5 秒没有进展"))?.map_err(|_|Error::new("network","响应正文不完整"))?};let Some(chunk)=chunk else{break};ensure(bytes.len().saturating_add(chunk.len())<=max,"响应超过读取上限")?;bytes.extend_from_slice(&chunk);}
                ensure(declared.is_none_or(|n|n==bytes.len() as u64),"响应正文长度不完整")?;
                if let Some((a,b,_))=content_range{ensure(b-a+1==bytes.len() as u64,"范围响应长度不匹配")?;}
                Ok((bytes,content_range))
            }.await;
            match result {
                Ok(value) => return Ok(value),
                Err(e) => {
                    if !retry(&e) {
                        return Err(e);
                    }
                    last = e;
                }
            }
        }
        Err(last)
    }
    pub async fn range(
        &self,
        url: &str,
        start: Option<u64>,
        end: u64,
        cancel: &CancellationToken,
    ) -> Result<(Vec<u8>, u64, u64)> {
        let range = match start {
            Some(a) => format!("bytes={a}-{end}"),
            None => format!("bytes=-{end}"),
        };
        let max = match start {
            Some(a) => end
                .checked_sub(a)
                .and_then(|n| n.checked_add(1))
                .ok_or_else(|| Error::invalid("范围无效"))?,
            None => end,
        };
        ensure(max > 0 && max <= 256 * 1024, "范围超过读取上限")?;
        let (data, position) = self
            .fetch(url, max as usize, Some(range), Kind::Download, cancel)
            .await?;
        let (a, b, total) = position.ok_or_else(|| Error::invalid("范围响应无效"))?;
        ensure(
            match start {
                Some(s) => a == s && b == end,
                None => b + 1 == total && a == total.saturating_sub(end),
            },
            "范围响应与请求不匹配",
        )?;
        Ok((data, a, total))
    }
    pub async fn download<F>(
        &self,
        url: &str,
        path: &Path,
        max: u64,
        minutes: u64,
        cancel: &CancellationToken,
        progress: F,
    ) -> Result<Value>
    where
        F: Fn(Value) + Send + Sync,
    {
        ensure(
            allowed(url, Kind::Download, true),
            "安装包必须来自 GitHub Release 的 HTTPS 下载地址",
        )?;
        let file = std::fs::OpenOptions::new()
            .create_new(true)
            .write(true)
            .open(path)?;
        use std::os::unix::fs::PermissionsExt;
        file.set_permissions(std::fs::Permissions::from_mode(0o600))?;
        let mut output = tokio::fs::File::from_std(file);
        let routes = self.routes(url);
        let limit = Duration::from_secs(minutes * 60);
        let mut began = Instant::now();
        let mut last = Error::new("network", "安装包下载失败");
        for (i, route) in routes.iter().enumerate() {
            self.record(route, i + 1);
            cancelled(cancel)?;
            output.set_len(0).await?;
            use tokio::io::AsyncSeekExt;
            output.seek(std::io::SeekFrom::Start(0)).await?;
            progress(
                json!({"received":0,"total":null,"status":"connecting","route":route,"attempt":i+1}),
            );
            let result=async{
                let remain=limit.checked_sub(began.elapsed()).ok_or_else(||Error::new("total_timeout","安装包下载超过总时限"))?;
                let mut res=self.open(url,route,Kind::Download,Duration::from_secs(60).min(remain),None,cancel).await?;
                let total=res.content_length();ensure(total.is_none_or(|v|v<=max),"安装包过大")?;
                let mut received=0u64;let mut last_report=Instant::now()-Duration::from_secs(1);
                loop{
                    let remain=limit.checked_sub(began.elapsed()).ok_or_else(||Error::new("total_timeout","安装包下载超过总时限"))?;
                    let chunk=tokio::select!{_=cancel.cancelled()=>return Err(Error::new("cancelled","下载已停止，尚未交给 Loader")),v=timeout(Duration::from_secs(60).min(remain),res.chunk())=>v.map_err(|_|Error::new(if began.elapsed()>=limit{"total_timeout"}else{"timeout"},"下载等待数据超时"))?.map_err(|_|Error::new("network","安装包下载不完整"))?};
                    let Some(chunk)=chunk else{break};received=received.checked_add(chunk.len() as u64).ok_or_else(||Error::invalid("安装包过大"))?;
                    ensure(received<=max&&total.is_none_or(|n|received<=n),"安装包过大或超过声明长度")?;
                    cancelled(cancel)?;output.write_all(&chunk).await?;
                    if last_report.elapsed()>=Duration::from_millis(250){progress(json!({"received":received,"total":total,"status":"downloading","route":route,"attempt":i+1}));last_report=Instant::now();}
                }
                ensure(total.is_none_or(|n|n==received),"安装包下载不完整，与 Content-Length 不符")?;
                output.flush().await?;output.sync_all().await?;cancelled(cancel)?;
                let completed=json!({"received":received,"total":total,"status":"complete","route":route,"attempt":i+1});progress(completed.clone());Ok(completed)
            }.await;
            match result {
                Ok(progress) => return Ok(progress),
                Err(e) => {
                    if e.code == "total_timeout" {
                        began = Instant::now();
                    } else if !retry(&e) {
                        return Err(e);
                    }
                    last = e;
                }
            }
        }
        Err(last)
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn url_policy() {
        assert!(allowed(
            "https://github.com/a/b/releases/download/v1/a.zip",
            Kind::Download,
            true
        ));
        for bad in [
            "http://github.com/a/b/releases/download/v1/a.zip",
            "https://evil.test/a.zip",
            "https://github.com:444/a/b/releases/download/v1/a.zip",
            "https://user@github.com/a/b/releases/download/v1/a.zip",
        ] {
            assert!(!allowed(bad, Kind::Download, true));
        }
    }
    #[test]
    fn route_policy() {
        let t = Transport::new("ghfast", Arc::new(Mutex::new(None))).unwrap();
        assert_eq!(
            t.routes("https://api.github.com/repos/a/b"),
            vec!["direct", "gh-proxy"]
        );
        assert_eq!(
            t.routes("https://raw.githubusercontent.com/a/b/main/a"),
            vec!["ghfast"]
        );
    }
}
