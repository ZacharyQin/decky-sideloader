use crate::{
    common::*,
    library_model::{Catalog, ReleaseCache, RepositoryMap},
    native::Native,
    transport::Transport,
};
use serde_json::{Value, json};
use std::{
    collections::HashMap,
    sync::{Arc, Mutex as StdMutex},
};
use tokio::sync::{Mutex, OnceCell, mpsc};
use tokio_util::{sync::CancellationToken, task::TaskTracker};
pub type SharedRead = Arc<OnceCell<Result<Value>>>;
pub struct State {
    pub operation: Option<Value>,
    pub operation_error: Option<String>,
    pub op_cancel: CancellationToken,
    pub op_done: Option<CancellationToken>,
    pub self_record: Value,
    pub self_error: Option<String>,
    pub self_cancel: CancellationToken,
    pub self_done: Option<CancellationToken>,
    pub self_preparing: bool,
    pub self_check: Option<SharedRead>,
    pub mapping_update: Option<SharedRead>,
    pub map: RepositoryMap,
    pub catalog: Catalog,
    pub releases: ReleaseCache,
    pub release_reads: HashMap<String, SharedRead>,
    pub probes: HashMap<String, (std::time::Instant, Value)>,
    pub checking: bool,
    pub library_snapshot: Value,
}
pub struct Engine {
    pub host: Host,
    pub boot: String,
    pub binary_hash: String,
    pub glue_hash: String,
    pub events: mpsc::Sender<Value>,
    pub stop: CancellationToken,
    pub tasks: TaskTracker,
    pub state: Mutex<State>,
    pub mutation: Mutex<()>,
    pub check_lock: Mutex<()>,
    pub probe_lock: Mutex<()>,
    pub last_route: Arc<StdMutex<Option<Value>>>,
    pub native: Native,
    pub diagnostics: crate::diagnostics::Diagnostics,
    pub sharing: crate::sharing::Sharing,
    #[cfg(test)]
    pub test_origin: StdMutex<Option<String>>,
}
impl Engine {
    pub fn new(host: Host, events: mpsc::Sender<Value>, binary_hash: String) -> Result<Arc<Self>> {
        host.validate()?;
        let stop = CancellationToken::new();
        let mut operation_error = None;
        let operation = match optional_json(
            &host.runtime_dir.join("installations/operation.json"),
            4 * 1024 * 1024,
        ) {
            Ok(Some(mut v)) => {
                if !valid_id(text(&v["id"])) || Stage::parse(&v["stage"]).is_err() {
                    operation_error = Some("安装记录损坏，已阻止管理操作".into());
                    Some(v)
                } else {
                    let stage = Stage::parse(&v["stage"])?;
                    let mut changed = false;
                    if v["outcome"] == "success" && v["saw_finish"] != true {
                        v["stage"] = json!("unknown");
                        v["outcome"] = Value::Null;
                        v["close_reason"] = Value::Null;
                        v["error"] = json!(
                            "旧任务未取得 Loader 完成证据，已撤销成功判定。请核对 Decky 安装结果。"
                        );
                        changed = true;
                    }
                    if stage == Stage::Uninstalling {
                        v["stage"] = json!("unknown");
                        v["uninstall_blocked"] = json!(true);
                        v["error"] = json!("卸载被插件重载中断，不会继续安装或重放卸载");
                        changed = true;
                    }
                    if v["cancel_requested"] == true && stage == Stage::Awaiting {
                        v["stage"] = json!("unknown");
                        v["error"] = json!("取消请求被重载中断，结果未知，不会重放原生请求");
                        changed = true;
                    }
                    if v["cancel_requested"] == true && stage == Stage::Ready {
                        v["stage"] = json!("cancelled");
                        changed = true;
                    }
                    if matches!(stage, Stage::Checking | Stage::Backup | Stage::Validating) {
                        v["stage"] = json!(if v["cancel_requested"] == true {
                            "cancelled"
                        } else {
                            "failed"
                        });
                        v["error"] = json!("准备被插件重载中断，尚未交给 Loader。请重新准备。");
                        for key in ["download", "dependency_download"] {
                            let dependency_active = key == "dependency_download"
                                && stage == Stage::Checking
                                && array(&v["observations"]).iter().any(|item| {
                                    item["id"] == "dependencies" && item["status"] == "running"
                                });
                            if v[key].is_object()
                                && (v[key]["status"] != "complete" || dependency_active)
                            {
                                v[key]["status"] = json!("failed");
                            }
                        }
                        changed = true;
                    }
                    if changed {
                        atomic_json(&host.runtime_dir.join("installations/operation.json"), &v)?;
                    }
                    Some(v)
                }
            }
            Ok(None) => None,
            Err(_) => {
                operation_error = Some("安装记录损坏，已阻止管理操作".into());
                None
            }
        };
        let mut self_error = None;
        let self_record = match optional_json(
            &host.runtime_dir.join("self-update/state.json"),
            2 * 1024 * 1024,
        ) {
            Ok(Some(mut v))
                if v["schema"] == 1
                    && (v["check"].is_null() || v["check"].is_object())
                    && (v["operation"].is_null()
                        || valid_id(text(&v["operation"]["id"]))
                            && Stage::parse(&v["operation"]["stage"]).is_ok()) =>
            {
                if v["operation"]["stage"] == "downloading" {
                    v["operation"]["stage"] = json!("failed");
                    v["operation"]["error"] = json!("自更新准备被重载中断，请重新下载");
                    atomic_json(&host.runtime_dir.join("self-update/state.json"), &v)?;
                }
                v
            }
            Ok(None) => json!({"schema":1,"check":null,"operation":null}),
            _ => {
                self_error = Some("自更新记录不可用".into());
                json!({"schema":1,"check":null,"operation":null})
            }
        };
        let map = RepositoryMap::load(&host)?;
        let catalog = Catalog::load(&host);
        let releases = ReleaseCache::load(&host);
        let native = Native::new(&host, events.clone(), stop.clone());
        let glue_hash = legacy_glue_hash(&host).unwrap_or_default();
        let diagnostics = crate::diagnostics::Diagnostics::new(&host, &binary_hash);
        let sharing = crate::sharing::Sharing::new();
        Ok(Arc::new(Self {
            host,
            boot: id(),
            binary_hash,
            glue_hash,
            events,
            stop,
            tasks: TaskTracker::new(),
            state: Mutex::new(State {
                operation,
                operation_error,
                op_cancel: CancellationToken::new(),
                op_done: None,
                self_record,
                self_error,
                self_cancel: CancellationToken::new(),
                self_done: None,
                self_preparing: false,
                self_check: None,
                mapping_update: None,
                map,
                catalog,
                releases,
                release_reads: HashMap::new(),
                probes: HashMap::new(),
                checking: false,
                library_snapshot: json!({"available":false}),
            }),
            mutation: Mutex::new(()),
            check_lock: Mutex::new(()),
            probe_lock: Mutex::new(()),
            last_route: Arc::new(StdMutex::new(None)),
            native,
            diagnostics,
            sharing,
            #[cfg(test)]
            test_origin: StdMutex::new(crate::transport::PROCESS_TEST_ORIGIN.get().cloned()),
        }))
    }
    pub fn transport(&self) -> Result<Transport> {
        #[cfg_attr(not(test), allow(unused_mut))]
        let mut transport = Transport::new(
            text(&load_settings(&self.host)?["networkMode"]),
            self.last_route.clone(),
        )?;
        let diagnostics = self.diagnostics.clone();
        transport.observer = Some(Arc::new(move |value| {
            diagnostics.record("network_attempt", value)
        }));
        #[cfg(test)]
        {
            transport.test_origin = self.test_origin.lock().unwrap().clone();
        }
        Ok(transport)
    }
    pub async fn require_running(&self) -> Result<()> {
        ensure(!self.stop.is_cancelled(), "插件正在停止，请重新打开后重试")
    }
    pub async fn no_self_update(&self) -> Result<()> {
        let s = self.state.lock().await;
        ensure(
            s.self_error.is_none(),
            s.self_error.as_deref().unwrap_or("自更新记录不可用"),
        )?;
        ensure(
            !s.self_preparing && s.self_done.is_none() && !self_active(&s.self_record["operation"]),
            "请先完成或取消 Sideloader 自更新",
        )
    }
    pub async fn binding_idle(&self, name: &str) -> Result<()> {
        self.require_running().await?;
        self.no_self_update().await?;
        let state = self.state.lock().await;
        ensure(
            state.operation_error.is_none(),
            state.operation_error.as_deref().unwrap_or("安装记录不可用"),
        )?;
        if let Some(op) = &state.operation {
            let active = !Stage::parse(&op["stage"])?.terminal();
            ensure(
                !(active
                    && [
                        text(&op["existing_name"]),
                        text(&op["package"]["name"]),
                        text(&op["target"]["name"]),
                    ]
                    .contains(&name)),
                "此插件有正在处理的安装任务，请结束任务后修改关联",
            )?;
        }
        Ok(())
    }
    pub async fn set_setting(&self, key: &str, value: Value) -> Result<Value> {
        let _guard = self.mutation.lock().await;
        self.require_running().await?;
        let mut settings = load_settings(&self.host)?;
        settings[key] = value;
        atomic_json(&self.host.settings(), &settings)?;
        settings["lastNetworkRoute"] = self
            .last_route
            .lock()
            .unwrap()
            .clone()
            .unwrap_or(Value::Null);
        Ok(settings)
    }
    pub async fn change_operation(&self, changes: Value) -> Result<Value> {
        let mut state = self.state.lock().await;
        let op = state
            .operation
            .clone()
            .ok_or_else(|| Error::invalid("安装任务已变化"))?;
        let mut candidate = transition(&op, &changes)?;
        if changes.get("error").is_some() && changes.get("diagnostic_error").is_none() {
            candidate["diagnostic_error"] = if candidate["error"].is_null() {
                json!({})
            } else {
                json!({"errorCode":if candidate["evidence_conflict"]==true{"evidence_conflict"}else if ["awaiting","installing","uninstalling"].contains(&text(&op["stage"])){"loader_failed"}else{"invalid_state"}})
            };
        }
        if candidate["stage"] != op["stage"] {
            let failed = ["failed", "cancelled"].contains(&text(&candidate["stage"]));
            let at = now();
            let mut observations = array(&candidate["observations"]).to_vec();
            if candidate["stage"] != "unknown" {
                for row in &mut observations {
                    if row["status"] == "running" {
                        row["status"] = json!(if failed { "failed" } else { "complete" });
                        row["updated_at"] = json!(at);
                        if failed {
                            row["detail"] = candidate["error"]
                                .as_str()
                                .map_or(json!("任务已取消"), |v| json!(v));
                        }
                    }
                }
            }
            let label = match text(&candidate["stage"]) {
                "backup" => Some("数据备份"),
                "awaiting" => Some("等待原生确认"),
                "installing" => Some("Loader 安装"),
                "verified" => Some("保存安装结果"),
                "uninstalling" => Some("原生卸载"),
                _ => None,
            };
            if let Some(label) = label {
                observations.retain(|row| row["id"] != candidate["stage"]);
                observations.push(json!({"id":candidate["stage"],"label":label,"status":"running","updated_at":at}));
            }
            candidate["observations"] = json!(observations);
            if failed
                && ["connecting", "downloading"]
                    .contains(&text(&candidate["dependency_download"]["status"]))
            {
                candidate["dependency_download"]["status"] = json!("failed");
            }
        }
        atomic_json(
            &self.host.runtime_dir.join("installations/operation.json"),
            &candidate,
        )?;
        #[cfg(test)]
        if candidate["stage"] == "verified" && op["stage"] != "verified" {
            crate::backup_gate::checkpoint_at("install-verified")?;
        }
        state.operation = Some(candidate.clone());
        let evidence = [
            "stage",
            "outcome",
            "saw_start",
            "saw_finish",
            "binding_saved",
            "cancel_requested",
            "evidence_conflict",
            "uninstall_started",
            "uninstall_blocked",
            "uninstall_result_received",
            "error",
            "artifact_deleted",
            "backup_deleted",
        ];
        if evidence.iter().any(|key| candidate[key] != op[key]) {
            let mut fields = json!({"operationId":candidate["id"],"stage":candidate["stage"],"status":if candidate["outcome"]=="success"{"success"}else{text(&candidate["stage"])},"operation":crate::diagnostics::operation_snapshot(&candidate)});
            if let Some(errors) = candidate["diagnostic_error"].as_object() {
                for (key, value) in errors {
                    fields[key] = value.clone();
                }
            }
            self.diagnostics.record("operation_stage", fields);
        }
        for (key, event) in [
            ("download", "download_stage"),
            ("dependency_download", "dependency_stage"),
        ] {
            if ["status", "route", "attempt", "index"]
                .iter()
                .any(|field| candidate[key][field] != op[key][field])
            {
                let mut fields = candidate[key].clone();
                if !fields.is_object() {
                    fields = json!({});
                }
                fields["operationId"] = candidate["id"].clone();
                fields["stage"] = candidate["stage"].clone();
                self.diagnostics.record(event, fields);
            }
        }
        Ok(candidate)
    }
    pub async fn change_self(&self, changes: Value) -> Result<Value> {
        let mut state = self.state.lock().await;
        let mut record = state.self_record.clone();
        ensure(record["operation"].is_object(), "自更新任务已变化")?;
        record["operation"] = transition(&record["operation"], &changes)?;
        atomic_json(
            &self.host.runtime_dir.join("self-update/state.json"),
            &record,
        )?;
        state.self_record = record.clone();
        Ok(record)
    }
    pub async fn emit(&self, event: &str, args: Vec<Value>) -> Result<()> {
        self.events
            .send(json!({"type":"event","event":event,"args":args}))
            .await
            .map_err(|_| Error::new("ipc", "Decky 事件连接已关闭"))
    }
    pub fn begin(self: &Arc<Self>) {
        let engine = self.clone();
        self.tasks.spawn(async move{let mut poll=tokio::time::interval(std::time::Duration::from_secs(60));poll.tick().await;loop{tokio::select!{_=engine.stop.cancelled()=>break,_=poll.tick()=>{let settings=load_settings(&engine.host).unwrap_or_else(|_|default_settings());if settings["autoCheck"]!=true{continue;}let busy={let s=engine.state.lock().await;s.self_preparing||self_active(&s.self_record["operation"])};if busy{continue;}let report=&settings["lastCheck"];let last=report.get("fullCheckedAt").filter(|v|v.is_string()).unwrap_or(&report["checkedAt"]);if chrono::DateTime::parse_from_rfc3339(text(last)).is_ok_and(|v|seconds()-(v.timestamp() as f64)<settings["autoCheckMinutes"].as_f64().unwrap_or(360.0)*60.0){continue;}let result=engine.check_updates("scheduled").await;let(report,self_update)=match result{Ok(v)=>(v["last_check"].clone(),v["self_update"].clone()),Err(e)=>{let report=json!({"checkedAt":now(),"source":"scheduled","items":[],"error":e.message});let mut settings=load_settings(&engine.host).unwrap_or_else(|_|default_settings());settings["lastCheck"]=report.clone();settings["lastScheduledCheck"]=report.clone();let _=atomic_json(&engine.host.settings(),&settings);(report,Value::Null)}};let _=engine.emit("scheduled_check_completed",vec![json!({"report":report,"checkNotifications":settings["checkNotifications"],"self_update":self_update})]).await;}}}});
        let engine = self.clone();
        self.tasks.spawn(async move{let mut interval=tokio::time::interval(std::time::Duration::from_secs(3600));loop{tokio::select!{_=engine.stop.cancelled()=>break,_=interval.tick()=>{let _=engine.cleanup_backups(true).await;}}}});
    }
    pub async fn shutdown(&self) {
        self.stop.cancel();
        {
            let state = self.state.lock().await;
            state.op_cancel.cancel();
            state.self_cancel.cancel();
        }
        self.native.close().await;
        self.sharing.stop(None).await;
        self.tasks.close();
        self.tasks.wait().await;
        self.diagnostics.record("shutdown", json!({}));
    }
}
pub(crate) fn transition(current: &Value, changes: &Value) -> Result<Value> {
    let updates = changes
        .as_object()
        .ok_or_else(|| Error::invalid("任务变化无效"))?;
    if let Some(expected) = updates.get("_expected_id") {
        ensure(current["id"] == *expected, "任务已变化")?;
    }
    if let Some(expected) = updates.get("_expected_stage") {
        ensure(current["stage"] == *expected, "任务阶段已变化")?;
    }
    let mut candidate = current.clone();
    for (key, value) in updates {
        if !key.starts_with("_expected_") {
            candidate[key] = value.clone();
        }
    }
    ensure(candidate["id"] == current["id"], "任务标识不能改变")?;
    let from = Stage::parse(&current["stage"])?;
    let to = Stage::parse(&candidate["stage"])?;
    ensure(from.allows(to), "任务阶段已变化，不会恢复或重复提交")?;
    if from != to && candidate["cancel_requested"] == true {
        ensure(
            !matches!(
                to,
                Stage::Checking
                    | Stage::Backup
                    | Stage::Ready
                    | Stage::Validating
                    | Stage::Awaiting
                    | Stage::Uninstalling
            ),
            "任务已取消，不会继续准备或提交",
        )?;
    }
    Ok(candidate)
}
pub fn self_active(op: &Value) -> bool {
    [
        "downloading",
        "ready",
        "awaiting",
        "installing",
        "verifying",
        "unknown",
    ]
    .contains(&text(&op["stage"]))
}
pub fn legacy_glue_hash(host: &Host) -> Result<String> {
    use sha2::{Digest, Sha256};
    let mut hash = Sha256::new();
    hash.update(b"main.py\0");
    hash.update(read_file(&host.plugin_dir.join("main.py"), 1024 * 1024)?);
    hash.update([0]);
    let modules = host.plugin_dir.join("py_modules/sideload");
    if modules.is_dir() {
        let mut files = std::fs::read_dir(modules)?.collect::<std::io::Result<Vec<_>>>()?;
        files.sort_by_key(|v| v.file_name());
        for file in files {
            if file.path().extension().is_some_and(|v| v == "py") {
                hash.update(
                    format!("sideload/{}\0", file.file_name().to_string_lossy()).as_bytes(),
                );
                hash.update(read_file(&file.path(), 1024 * 1024)?);
                hash.update([0]);
            }
        }
    }
    Ok(hex::encode(hash.finalize()))
}
