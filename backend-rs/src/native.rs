use crate::{common::*, library_model::names};
use serde_json::{Value, json};
use std::{collections::HashMap, path::PathBuf};
use tokio::sync::{Mutex, mpsc, oneshot};
use tokio_util::sync::CancellationToken;
type Reply = Result<(Value, Option<std::collections::BTreeSet<String>>)>;
pub struct Native {
    path: PathBuf,
    cache: Mutex<Value>,
    pending: Mutex<HashMap<String, (oneshot::Sender<Reply>, bool)>>,
    events: mpsc::Sender<Value>,
    stop: CancellationToken,
}
fn valid_cache(v: &Value) -> Result<()> {
    ensure(
        v.as_object().is_some_and(|m| m.len() == 2) && valid_text(&v["channel"], 4096),
        "Loader 更新目录无效",
    )?;
    names(&v["names"])?;
    Ok(())
}
fn snapshot(v: &Value) -> Result<Value> {
    ensure(v["ok"].is_boolean(), "Loader 管理清单无效")?;
    if v["ok"] == false {
        return Ok(json!({"ok":false,"error":"Loader 管理状态未知"}));
    }
    let plugins = v["plugins"]
        .as_array()
        .ok_or_else(|| Error::invalid("Loader 管理清单无效"))?;
    names(&json!(
        plugins
            .iter()
            .map(|p| p["name"].clone())
            .collect::<Vec<_>>()
    ))?;
    let mut rows = Vec::new();
    for p in plugins {
        ensure(
            (p["version"].is_null() || valid_text(&p["version"], 256))
                && p["load_type"].as_i64().is_some()
                && p["disabled"].is_boolean(),
            "Loader 管理条目无效",
        )?;
        rows.push(json!({"name":p["name"],"version":p["version"],"load_type":p["load_type"],"disabled":p["disabled"]}));
    }
    Ok(json!({"ok":true,"plugins":rows}))
}
impl Native {
    pub fn new(host: &Host, events: mpsc::Sender<Value>, stop: CancellationToken) -> Self {
        let path = host.runtime_dir.join("cache/native-updates.json");
        let cache = read_json(&path, 4 * 1024 * 1024)
            .ok()
            .filter(|v| valid_cache(v).is_ok())
            .unwrap_or(Value::Null);
        Self {
            path,
            cache: Mutex::new(cache),
            pending: Mutex::new(HashMap::new()),
            events,
            stop,
        }
    }
    pub async fn read(&self, refresh: bool) -> Reply {
        let unknown = || Ok((json!({"ok":false,"error":"Loader 管理状态未知"}), None));
        if self.stop.is_cancelled() {
            return unknown();
        }
        let identifier = id();
        let (tx, rx) = oneshot::channel();
        {
            let mut pending = self.pending.lock().await;
            ensure(pending.len() < 64, "Loader 读取请求过多")?;
            pending.insert(identifier.clone(), (tx, refresh));
        }
        let cache = self.cache.lock().await.clone();
        let event = json!({"type":"event","event":"native_management_requested","args":[{"id":identifier,"refresh":refresh,"cache":cache}]});
        let sent = self.events.send(event).await.is_ok();
        let result = if sent {
            tokio::select! {_=self.stop.cancelled()=>None,result=tokio::time::timeout(std::time::Duration::from_secs(if refresh{30}else{8}),rx)=>result.ok().and_then(|v|v.ok())}
        } else {
            None
        };
        self.pending.lock().await.remove(&identifier);
        match result {
            Some(v) => v,
            None => {
                if refresh {
                    Err(Error::new(
                        "loader_failed",
                        "Loader 管理读取超时，未替换原有记录",
                    ))
                } else {
                    unknown()
                }
            }
        }
    }
    pub async fn receive(&self, identifier: &str, payload: &Value) -> Result<Value> {
        let Some((tx, refresh)) = self.pending.lock().await.remove(identifier) else {
            return Ok(Value::Null);
        };
        let result = (|| -> Reply {
            ensure(
                serde_json::to_vec(payload)?.len() <= 4 * 1024 * 1024,
                "Loader 响应无效",
            )?;
            let snap = snapshot(&payload["snapshot"])?;
            let capable = if payload["capable"].is_null() {
                None
            } else {
                let capable = names(&payload["capable"])?;
                let registered = names(&json!(
                    array(&snap["plugins"])
                        .iter()
                        .map(|p| p["name"].clone())
                        .collect::<Vec<_>>()
                ))?;
                ensure(
                    snap["ok"] == true && capable.is_subset(&registered),
                    "Loader 更新能力与登记清单不匹配",
                )?;
                Some(capable)
            };
            if refresh {
                ensure(
                    payload["error"].is_null() && snap["ok"] == true,
                    "Loader 更新目录读取失败，未替换原有记录",
                )?;
                valid_cache(&payload["cache"])?;
                ensure(
                    capable.as_ref().is_some_and(|v| {
                        names(&payload["cache"]["names"]).is_ok_and(|n| v.is_subset(&n))
                    }),
                    "Loader 更新能力与频道目录不匹配",
                )?;
                atomic_json(&self.path, &payload["cache"])?;
            }
            Ok((snap, capable))
        })();
        if result.is_ok() && refresh {
            *self.cache.lock().await = payload["cache"].clone();
        }
        let _ = tx.send(result);
        Ok(Value::Null)
    }
    pub async fn close(&self) {
        for (_, (tx, _)) in self.pending.lock().await.drain() {
            let _ = tx.send(Ok((json!({"ok":false,"error":"插件正在停止"}), None)));
        }
    }
}
