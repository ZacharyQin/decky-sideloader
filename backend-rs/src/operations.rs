use crate::{
    common::*,
    engine::Engine,
    library_model as model, metadata,
    package::{self, Package},
    transport::Transport,
    zip_version,
};
use serde_json::{Value, json};
use std::{collections::BTreeSet, path::PathBuf, sync::Arc};
use tokio::sync::watch;
use tokio_util::sync::CancellationToken;
fn terminal(op: &Value) -> bool {
    matches!(text(&op["stage"]), "failed" | "closed" | "cancelled")
}
impl Engine {
    pub fn op_directory(&self, identifier: &str) -> Result<PathBuf> {
        ensure(valid_id(identifier), "任务标识无效")?;
        Ok(self.host.runtime_dir.join("installations").join(identifier))
    }
    pub async fn operation_public(&self) -> Result<Value> {
        let state = self.state.lock().await;
        ensure(
            state.operation_error.is_none(),
            state.operation_error.as_deref().unwrap_or("安装记录不可用"),
        )?;
        let Some(op) = &state.operation else {
            return Ok(Value::Null);
        };
        let mut result = op.clone();
        for key in ["previous_stamp", "source", "_timeout_minutes"] {
            result.as_object_mut().unwrap().remove(key);
        }
        if result["package"].is_object() {
            result["package"].as_object_mut().unwrap().remove("hashes");
        }
        if !op["backup"].is_null() {
            let path = self.op_directory(text(&op["id"]))?.join("data-backup.zip");
            result["backup_deleted"] =
                json!(!std::fs::symlink_metadata(path).is_ok_and(|m| m.is_file()));
        }
        Ok(result)
    }
    async fn op_checked(
        &self,
        identifier: &str,
        cancel: Option<&CancellationToken>,
    ) -> Result<Value> {
        self.require_running().await?;
        let s = self.state.lock().await;
        let op = s
            .operation
            .as_ref()
            .ok_or_else(|| Error::invalid("安装任务已变化"))?;
        ensure(
            op["id"] == identifier
                && op["cancel_requested"] != true
                && cancel.is_none_or(|c| !c.is_cancelled()),
            "任务已取消，不会继续准备或提交",
        )?;
        Ok(op.clone())
    }
    pub async fn observe(
        &self,
        identifier: &str,
        label: &str,
        status: &str,
        detail: &str,
    ) -> Result<()> {
        let op = self
            .state
            .lock()
            .await
            .operation
            .clone()
            .ok_or_else(|| Error::invalid("安装任务已变化"))?;
        let at = now();
        let mut rows = array(&op["observations"]).to_vec();
        for row in &mut rows {
            if row["id"] != identifier && row["status"] == "running" {
                row["status"] = json!("complete");
                row["updated_at"] = json!(at);
            }
        }
        let entry =
            json!({"id":identifier,"label":label,"status":status,"detail":detail,"updated_at":at});
        if let Some(index) = rows.iter().position(|r| r["id"] == identifier) {
            rows[index] = entry;
        } else {
            rows.push(entry);
        }
        self.change_operation(json!({"observations":rows})).await?;
        Ok(())
    }
    pub async fn cleanup_backups(self: &Arc<Self>, expired: bool) -> Result<Value> {
        let protected = {
            let s = self.state.lock().await;
            ensure(
                s.operation_error.is_none(),
                s.operation_error.as_deref().unwrap_or("安装记录不可用"),
            )?;
            let mut protected = BTreeSet::new();
            if let Some(op) = &s.operation {
                if !terminal(op) || s.op_done.is_some() {
                    protected.insert(text(&op["id"]).to_owned());
                }
            }
            protected
        };
        let root = self.host.runtime_dir.join("installations");
        let result =
            tokio::task::spawn_blocking(move || package::clean_backups(&root, &protected, expired))
                .await
                .map_err(|_| Error::invalid("备份清理任务失败"))??;
        self.diagnostics.record("backup_cleanup", result.clone());
        Ok(result)
    }
    pub async fn prepare_install(
        self: &Arc<Self>,
        raw: &str,
        tag: &str,
        url: &str,
        existing: Option<&str>,
        plugin_path: Option<&str>,
        mode: &str,
    ) -> Result<Value> {
        let _guard = self.mutation.lock().await;
        self.require_running().await?;
        self.no_self_update().await?;
        ensure(
            ["install", "reinstall"].contains(&mode) && (mode != "reinstall" || existing.is_some()),
            "卸载并安装必须选择现有插件",
        )?;
        let repo =
            metadata::parse_repo(raw).ok_or_else(|| Error::invalid("仓库、版本或安装包无效"))?;
        ensure(!tag.is_empty() && !url.is_empty(), "仓库、版本或安装包无效")?;
        {
            let s = self.state.lock().await;
            ensure(
                s.operation_error.is_none(),
                s.operation_error.as_deref().unwrap_or("安装记录不可用"),
            )?;
            ensure(
                s.operation.as_ref().is_none_or(terminal) && s.op_done.is_none(),
                "已有安装任务，请先查看其结果",
            )?;
        }
        let target = if let Some(name) = existing {
            let plugin = self.eligible(name).await?;
            self.check_owned_repo(&plugin, &repo)?;
            None
        } else {
            let proof = self.probe_repo(&repo).await?;
            ensure(
                !array(&proof["plugins"]).is_empty(),
                "仓库中未确认 Decky 插件，已阻止新装",
            )?;
            ensure(
                plugin_path.is_some() || array(&proof["plugins"]).len() == 1,
                "仓库包含多个插件，请先选择要安装的插件目录",
            )?;
            let candidate = if let Some(path) = plugin_path {
                array(&proof["plugins"]).iter().find(|p| p["path"] == path)
            } else {
                array(&proof["plugins"]).first()
            }
            .ok_or_else(|| Error::invalid("所选插件目录不在探查结果中，请重新探查"))?;
            if model::scan(&self.host.home)?
                .iter()
                .any(|p| p["name"] == candidate["name"])
            {
                self.eligible(text(&candidate["name"])).await?;
                return Err(Error::invalid("同名插件已安装，请从插件详情进入管理"));
            }
            let mut target = candidate.clone();
            target["tree_sha"] = proof["tree_sha"].clone();
            Some(target)
        };
        self.cleanup_backups(true).await?;
        self.require_running().await?;
        let root = self.host.runtime_dir.join("installations");
        directory(&root)?;
        let size = directory_size(&root)?;
        ensure(
            size <= 768 * 1024 * 1024,
            "安装记录与备份超过 2 GiB，请先检查并清理安装记录",
        )?;
        let identifier = id();
        let dir = self.op_directory(&identifier)?;
        std::fs::create_dir(&dir)?;
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&dir, std::fs::Permissions::from_mode(0o700))?;
        let transport = self.transport()?;
        let settings = load_settings(&self.host)?;
        let cancel = self.stop.child_token();
        let done = CancellationToken::new();
        let op = json!({"id":identifier,"stage":"checking","repo":repo,"tag":tag,"asset_url":url,"existing_name":existing,"target":target,"network_mode":transport.mode,"download":null,"created_at":now(),"error":null,"backup":null,"artifact":format!("file://{}",dir.join("plugin.zip").display()),"request_id":null,"package":null,"saw_finish":false,"saw_start":false,"binding_saved":false,"mode":mode,"outcome":null,"directory":dir,"_timeout_minutes":settings["downloadTimeoutMinutes"],"observations":[{"id":"download","label":"主安装包下载","status":"running","detail":"","updated_at":now()}]});
        {
            let mut s = self.state.lock().await;
            atomic_json(&root.join("operation.json"), &op)?;
            s.operation = Some(op);
            s.op_cancel = cancel.clone();
            s.op_done = Some(done.clone());
        }
        self.spawn_preparation(identifier, transport, cancel, done, true, true);
        self.operation_public().await
    }
    fn check_owned_repo(&self, plugin: &Value, repo: &str) -> Result<()> {
        if plugin["ownership"]["state"] == "owned" {
            ensure(
                metadata::parse_repo(repo).is_some_and(|v| {
                    v.eq_ignore_ascii_case(text(&plugin["ownership"]["intent"]["repo"]))
                }),
                "安装仓库与接管时确认的仓库不一致；请先交还后重新接管以更换仓库",
            )?;
        }
        Ok(())
    }
    fn spawn_preparation(
        self: &Arc<Self>,
        identifier: String,
        transport: Transport,
        cancel: CancellationToken,
        done: CancellationToken,
        download: bool,
        recheck_release: bool,
    ) {
        let engine = self.clone();
        self.tasks.spawn(async move {
            let result = engine.prepare_work(&identifier, &transport, &cancel, download, recheck_release).await;
            if let Err(error) = result {
                let current = engine.state.lock().await.operation.clone();
                if let Some(op) = current.filter(|op| op["id"] == identifier) {
                    let cancelled = cancel.is_cancelled() || op["cancel_requested"] == true;
                    let mut changes = json!({"_expected_id":identifier,"_expected_stage":op["stage"],
                        "stage":if cancelled {"cancelled"} else {"failed"},
                        "error":if cancelled {json!("任务已取消，未继续安装")} else {json!(error.message)},
                        "diagnostic_error":{"errorCode":error.code}});
                    for key in ["download", "dependency_download"] {
                        let dependency_active = key == "dependency_download" && op["stage"] == "checking"
                            && array(&op["observations"]).iter().any(|item| item["id"] == "dependencies" && item["status"] == "running");
                        if op[key].is_object() && (op[key]["status"] != "complete" || dependency_active) {
                            changes[key] = op[key].clone();
                            changes[key]["status"] = json!("failed");
                        }
                    }
                    let _ = engine.change_operation(changes).await;
                }
            }
            let mut state = engine.state.lock().await;
            if state.operation.as_ref().is_some_and(|op| op["id"] == identifier) {
                state.op_done = None;
            }
            done.cancel();
        });
    }
    fn progress_channel(
        self: &Arc<Self>,
        identifier: &str,
        key: &'static str,
    ) -> (watch::Sender<Option<Value>>, tokio::task::JoinHandle<()>) {
        let (sender, mut receiver) = watch::channel::<Option<Value>>(None);
        let engine = self.clone();
        let identifier = identifier.to_owned();
        let worker = self.tasks.spawn(async move {
            while receiver.changed().await.is_ok() {
                let value = receiver.borrow_and_update().clone();
                if let Some(value) = value {
                    engine.progress(&identifier, key, value).await;
                }
            }
        });
        (sender, worker)
    }
    async fn progress(self: &Arc<Self>, identifier: &str, key: &str, value: Value) {
        let current = self.state.lock().await.operation.clone();
        if let Some(op) = current {
            if op["id"] == identifier
                && op["stage"] == "checking"
                && op["cancel_requested"] != true
                && !self.stop.is_cancelled()
            {
                let old = &op[key];
                let same_dependency =
                    key != "dependency_download" || old["index"] == value["index"];
                if key == "dependency_download" && old["index"].as_u64() > value["index"].as_u64()
                    || same_dependency
                        && (old["attempt"].as_u64() > value["attempt"].as_u64()
                            || old["attempt"] == value["attempt"]
                                && old["received"].as_u64() > value["received"].as_u64())
                {
                    return;
                }
                let _ = self
                    .change_operation(
                        json!({"_expected_id":identifier,"_expected_stage":"checking",key:value}),
                    )
                    .await;
            }
        }
    }
    async fn prepare_work(
        self: &Arc<Self>,
        identifier: &str,
        transport: &Transport,
        cancel: &CancellationToken,
        download: bool,
        recheck: bool,
    ) -> Result<()> {
        let op = self.op_checked(identifier, Some(cancel)).await?;
        let directory = self.op_directory(identifier)?;
        let archive = directory.join("plugin.zip");
        let minutes = op["_timeout_minutes"].as_u64().unwrap_or(3);
        if recheck {
            let installed = model::scan(&self.host.home)?;
            let existing = installed.iter().find(|p| p["name"] == op["existing_name"]);
            ensure(
                op["existing_name"].is_null() || existing.is_some(),
                "原插件已不存在，请刷新列表",
            )?;
            let releases = metadata::releases(
                transport,
                text(&op["repo"]),
                existing.map_or("", |p| text(&p["version"])),
                cancel,
            )
            .await?;
            let release = releases
                .iter()
                .find(|r| {
                    r["tag"] == op["tag"]
                        && array(&r["zips"])
                            .iter()
                            .any(|z| z["url"] == op["asset_url"])
                })
                .ok_or_else(|| Error::invalid("所选安装包不属于此仓库和版本，请重新读取"))?;
            let probe = zip_version::probe(transport, text(&op["asset_url"]), cancel).await;
            ensure(
                probe["package_state"] != "absent",
                "安装包没有可读取的版本，已取消下载",
            )?;
            self.op_checked(identifier, Some(cancel)).await?;
            self.change_operation(json!({"repo":release["repo"]}))
                .await?;
        }
        if download {
            if archive.exists() {
                ensure(!archive.is_symlink(), "安装包路径不安全")?;
                std::fs::remove_file(&archive)?;
            }
            let (progress, worker) = self.progress_channel(identifier, "download");
            let updates = progress.clone();
            let result = transport
                .download(
                    text(&op["asset_url"]),
                    &archive,
                    package::MAX_DOWNLOAD,
                    minutes,
                    cancel,
                    move |value| {
                        let _ = updates.send(Some(value));
                    },
                )
                .await;
            drop(progress);
            worker
                .await
                .map_err(|_| Error::invalid("下载进度任务失败"))?;
            let completed = result?;
            self.change_operation(json!({"_expected_id":identifier,"_expected_stage":"checking","download":completed})).await?;
        }
        self.op_checked(identifier, Some(cancel)).await?;
        let installed = model::scan(&self.host.home)?;
        let existing = installed
            .iter()
            .find(|p| p["name"] == op["existing_name"])
            .cloned();
        ensure(
            op["existing_name"].is_null() || existing.is_some(),
            "原插件已不存在，请刷新列表",
        )?;
        self.observe("archive", "安装包校验", "running", "").await?;
        let target = op["target"].as_object().map(|_| op["target"].clone());
        let a = archive.clone();
        let c = cancel.clone();
        let rows = installed.clone();
        let old = existing.clone();
        let t = target.clone();
        let mut package = tokio::task::spawn_blocking(move || {
            package::inspect(&a, old.as_ref(), &rows, t.as_ref(), false, &c)
        })
        .await
        .map_err(|_| Error::invalid("安装包校验任务失败"))??;
        let home = self.host.home.clone();
        let folder = package.folder.clone();
        let c = cancel.clone();
        let previous = tokio::task::spawn_blocking(move || package::stamp(&home, &folder, &c))
            .await
            .map_err(|_| Error::invalid("插件核验任务失败"))??;
        if existing.is_none() {
            ensure(
                !self
                    .host
                    .home
                    .join("plugins")
                    .join(&package.folder)
                    .exists(),
                "目标目录已存在，请从已安装插件进入管理",
            )?;
        }
        self.op_checked(identifier, Some(cancel)).await?;
        self.observe("dependencies", "额外依赖准备", "running", "")
            .await?;
        let (progress, worker) = self.progress_channel(identifier, "dependency_download");
        let updates = progress.clone();
        let result = package::remote_binaries(
            &archive,
            &package,
            transport,
            minutes,
            cancel,
            move |value| {
                let _ = updates.send(Some(value));
            },
        )
        .await;
        drop(progress);
        worker
            .await
            .map_err(|_| Error::invalid("依赖进度任务失败"))?;
        let dependencies = result?;
        if dependencies
            .as_ref()
            .is_some_and(|v| v["count"].as_u64().unwrap_or(0) > 0)
        {
            let a = archive.clone();
            let c = cancel.clone();
            package = tokio::task::spawn_blocking(move || {
                package::inspect(
                    &a,
                    existing.as_ref(),
                    &installed,
                    target.as_ref(),
                    false,
                    &c,
                )
            })
            .await
            .map_err(|_| Error::invalid("安装包校验任务失败"))??;
        }
        self.op_checked(identifier, Some(cancel)).await?;
        self.observe(
            "dependencies",
            "额外依赖准备",
            "complete",
            if dependencies.is_some() {
                "依赖已核验"
            } else {
                "无额外依赖"
            },
        )
        .await?;
        let dependencies = dependencies.or_else(|| {
            if directory.join("source.zip").is_file() {
                op.get("dependencies").cloned()
            } else {
                None
            }
        });
        self.change_operation(json!({"package":package,"previous_stamp":previous,"dependencies":dependencies,"stage":"backup"})).await?;
        if !op["existing_name"].is_null() {
            self.observe("backup", "数据备份", "running", "").await?;
            let home = self.host.home.clone();
            let folder = package.folder.clone();
            let target = directory.join("data-backup.zip");
            let c = cancel.clone();
            let id = identifier.to_owned();
            let backup = tokio::task::spawn_blocking(move || {
                match package::resume_backup(&home, &folder, &target, &id, &c)? {
                    Some(backup) => Ok(backup),
                    None => package::backup(&home, &folder, &target, &c),
                }
            })
            .await
            .map_err(|_| Error::invalid("备份任务失败"))??;
            self.change_operation(json!({"backup":backup})).await?;
        }
        self.op_checked(identifier, Some(cancel)).await?;
        self.change_operation(json!({"stage":"ready","error":null}))
            .await?;
        Ok(())
    }
    pub async fn reuse_download(
        self: &Arc<Self>,
        identifier: &str,
        download: bool,
    ) -> Result<Value> {
        let _guard = self.mutation.lock().await;
        self.require_running().await?;
        self.no_self_update().await?;
        let mut s = self.state.lock().await;
        let op = s
            .operation
            .as_ref()
            .ok_or_else(|| Error::invalid("没有可继续的安装任务"))?
            .clone();
        ensure(
            op["id"] == identifier
                && op["stage"] == "failed"
                && op["cancel_requested"] != true
                && op["request_id"].is_null()
                && op["saw_start"] != true
                && s.op_done.is_none(),
            "没有可继续的安装任务",
        )?;
        ensure(
            (op["download"]["status"] == "complete") != download,
            if download {
                "下载已完成，请继续安装而不是重新下载"
            } else {
                "下载尚未完成，不能继续安装"
            },
        )?;
        let dir = self.op_directory(identifier)?;
        ensure(
            !dir.is_symlink()
                && !dir.join("plugin.zip").is_symlink()
                && (download || dir.join("plugin.zip").is_file()),
            "安装包已不在，请重新下载",
        )?;
        let transport = self.transport()?;
        let cancel = self.stop.child_token();
        let done = CancellationToken::new();
        let mut candidate = crate::engine::transition(
            &op,
            &json!({"stage":"checking","error":null,"download":if download{Value::Null}else{op["download"].clone()},"dependencies":if download{Value::Null}else{op["dependencies"].clone()},"dependency_download":if download{Value::Null}else{op["dependency_download"].clone()}}),
        )?;
        candidate["observations"] = json!([{"id":"download","label":"主安装包下载","status":if download{"running"}else{"complete"},"detail":"","updated_at":now()}]);
        atomic_json(
            &self.host.runtime_dir.join("installations/operation.json"),
            &candidate,
        )?;
        s.operation = Some(candidate);
        s.op_cancel = cancel.clone();
        s.op_done = Some(done.clone());
        drop(s);
        self.spawn_preparation(
            identifier.into(),
            transport,
            cancel,
            done,
            download,
            download,
        );
        self.operation_public().await
    }
    async fn gate_operation(self: &Arc<Self>, identifier: &str, absent: bool) -> Result<Value> {
        let op = self
            .state
            .lock()
            .await
            .operation
            .clone()
            .ok_or_else(|| Error::invalid("安装任务已变化"))?;
        ensure(op["id"] == identifier, "安装任务已变化")?;
        let name = op["existing_name"]
            .as_str()
            .or(op["package"]["name"].as_str())
            .or(op["target"]["name"].as_str())
            .ok_or_else(|| Error::invalid("尚未确认安装目标"))?;
        let local = model::scan(&self.host.home)?;
        if local.iter().any(|p| p["name"] == name) {
            let plugin = self.eligible(name).await?;
            self.check_owned_repo(&plugin, text(&op["repo"]))?;
        } else if !op["existing_name"].is_null() {
            ensure(
                absent && op["stage"] == "uninstalling" && op["mode"] == "reinstall",
                "原插件已不存在",
            )?;
            let ledger = model::ledger(&self.host)?;
            if let Some(entry) = model::ownership_entry(
                &json!({"name":name,"folder":op["package"]["folder"]}),
                &ledger,
            ) {
                ensure(
                    entry["state"] == "active" && model::frozen(&self.host)?.contains(name),
                    "接管状态未确认，已阻止继续安装",
                )?;
                ensure(
                    metadata::parse_repo(text(&op["repo"]))
                        .is_some_and(|r| r.eq_ignore_ascii_case(text(&entry["repo"]))),
                    "安装仓库与接管时确认的仓库不一致；请先交还后重新接管以更换仓库",
                )?;
            } else {
                let listing = self.list_plugins(false).await?;
                ensure(
                    listing["native_snapshot"]["ok"] == true
                        && !array(&listing["native_snapshot"]["plugins"])
                            .iter()
                            .any(|p| p["name"] == name),
                    "原插件的商店归属不能确认，已阻止继续安装",
                )?;
            }
        }
        Ok(op)
    }
    async fn check_prepared(
        &self,
        op: &Value,
        absent: bool,
        cancel: &CancellationToken,
    ) -> Result<()> {
        let dir = self.op_directory(text(&op["id"]))?;
        let package: Package = serde_json::from_value(op["package"].clone())?;
        let archive = dir.join("plugin.zip");
        let expected = package.sha256.clone();
        let c = cancel.clone();
        ensure(
            tokio::task::spawn_blocking(move || file_hash(&archive, &c))
                .await
                .map_err(|_| Error::invalid("安装包校验失败"))??
                == expected,
            "准备后的安装包发生变化",
        )?;
        if !op["backup"].is_null() {
            let path = dir.join("data-backup.zip");
            let expected = text(&op["backup"]["sha256"]).to_owned();
            let c = cancel.clone();
            ensure(
                tokio::task::spawn_blocking(move || file_hash(&path, &c))
                    .await
                    .map_err(|_| Error::invalid("备份校验失败"))??
                    == expected,
                "备份发生变化，已阻止操作",
            )?;
        }
        let host = self.host.clone();
        let previous = op["previous_stamp"].clone();
        let c = cancel.clone();
        let package2 = package.clone();
        let current =
            tokio::task::spawn_blocking(move || package::stamp(&host.home, &package2.folder, &c))
                .await
                .map_err(|_| Error::invalid("插件核验失败"))??;
        if absent {
            ensure(
                current.is_null()
                    && !self
                        .host
                        .home
                        .join("plugins")
                        .join(&package.folder)
                        .exists(),
                "卸载结果尚未确认，不会继续安装",
            )?;
        } else {
            ensure(current == previous, "已安装程序已变化，请重新准备")?;
        }
        cancelled(cancel)
    }
    pub async fn submit_operation(
        self: &Arc<Self>,
        identifier: &str,
        after_uninstall: bool,
        claim: bool,
    ) -> Result<Value> {
        let _guard = self.mutation.lock().await;
        self.require_running().await?;
        self.no_self_update().await?;
        let initial = self
            .state
            .lock()
            .await
            .operation
            .clone()
            .ok_or_else(|| Error::invalid("安装任务已变化"))?;
        if after_uninstall && initial["cancel_requested"] == true {
            return self.complete_cancelled_uninstall(identifier).await;
        }
        let op = self.gate_operation(identifier, after_uninstall).await?;
        ensure(
            op["stage"]
                == if after_uninstall {
                    "uninstalling"
                } else {
                    "ready"
                },
            "任务尚未准备好或已经提交，禁止重复安装",
        )?;
        let reinstall = op["mode"] == "reinstall";
        ensure(
            if claim {
                reinstall && !after_uninstall
            } else {
                reinstall == after_uninstall
            },
            "卸载并安装必须先完成原生卸载",
        )?;
        ensure(
            !after_uninstall
                || seconds() - op["uninstall_claimed_at"].as_f64().unwrap_or(0.0) <= 120.0,
            "卸载确认超时，不会继续安装",
        )?;
        let cancel = self.state.lock().await.op_cancel.clone();
        self.op_checked(identifier, Some(&cancel)).await?;
        self.change_operation(json!({"stage":"validating"})).await?;
        let result = self.check_prepared(&op, after_uninstall, &cancel).await;
        match result {
            Ok(()) => {
                self.op_checked(identifier, Some(&cancel)).await?;
                self.change_operation(if claim{json!({"stage":"uninstalling","uninstall_claimed_at":seconds(),"uninstall_started":false})}else{json!({"stage":"awaiting","submitted_at":seconds()})}).await?;
                self.observe(
                    if claim { "uninstalling" } else { "awaiting" },
                    if claim {
                        "原生卸载"
                    } else {
                        "等待原生确认"
                    },
                    "running",
                    "",
                )
                .await?;
            }
            Err(e) => {
                let cancelled = self
                    .state
                    .lock()
                    .await
                    .operation
                    .as_ref()
                    .is_some_and(|v| v["cancel_requested"] == true);
                self.change_operation(json!({"stage":if cancelled&&!after_uninstall{"cancelled"}else if after_uninstall{"unknown"}else{"failed"},"uninstall_blocked":after_uninstall,"error":e.message})).await?;
                return Err(e);
            }
        }
        self.operation_public().await
    }
    async fn complete_cancelled_uninstall(&self, identifier: &str) -> Result<Value> {
        let op = self
            .state
            .lock()
            .await
            .operation
            .clone()
            .ok_or_else(|| Error::invalid("没有可回查的已取消卸载任务"))?;
        ensure(
            op["id"] == identifier
                && op["mode"] == "reinstall"
                && op["cancel_requested"] == true
                && op["uninstall_started"] == true
                && ["uninstalling", "unknown", "cancelled"].contains(&text(&op["stage"]))
                && op["submitted_at"].is_null()
                && op["request_id"].is_null()
                && op["saw_start"] != true,
            "没有可回查的已取消卸载任务",
        )?;
        if op["stage"] == "cancelled" {
            return self.operation_public().await;
        }
        self.change_operation(json!({"uninstall_result_received":true}))
            .await?;
        let cancel = CancellationToken::new();
        match self.check_prepared(&op, true, &cancel).await {
            Ok(()) => {
                self.change_operation(
                    json!({"stage":"cancelled","error":"任务已取消，未继续安装"}),
                )
                .await?;
            }
            Err(e) => {
                self.change_operation(json!({"stage":"unknown","uninstall_blocked":true,"error":format!("已停止后续安装，卸载结果尚未确认：{}",e.message)})).await?;
            }
        }
        self.operation_public().await
    }
    pub async fn cancel_operation(self: &Arc<Self>, identifier: &str) -> Result<Value> {
        let (op, cancel, done) = {
            let mut state = self.state.lock().await;
            let op = state
                .operation
                .clone()
                .ok_or_else(|| Error::invalid("任务已变化"))?;
            ensure(op["id"] == identifier, "任务已变化")?;
            if terminal(&op) {
                drop(state);
                return self.operation_public().await;
            }
            ensure(
                !["installing", "verified"].contains(&text(&op["stage"]))
                    && op["saw_start"] != true,
                "Decky安装已开始，无法中断；仍将核验并保存结果",
            )?;
            if op["cancel_requested"] != true {
                ensure(
                    op["stage"] != "unknown"
                        && !(op["stage"] == "awaiting" && op["request_id"].is_null()),
                    "原生请求尚未确认，无法安全取消",
                )?;
                let candidate = crate::engine::transition(&op, &json!({"cancel_requested":true}))?;
                atomic_json(
                    &self.host.runtime_dir.join("installations/operation.json"),
                    &candidate,
                )?;
                state.operation = Some(candidate);
            }
            (op, state.op_cancel.clone(), state.op_done.clone())
        };
        cancel.cancel();
        if let Some(done) = done {
            done.cancelled().await;
        }
        let current = self
            .state
            .lock()
            .await
            .operation
            .clone()
            .ok_or_else(|| Error::invalid("任务已变化"))?;
        if current["id"] == identifier
            && (["checking", "backup", "validating", "ready"].contains(&text(&current["stage"]))
                || current["stage"] == "uninstalling" && current["uninstall_started"] != true)
        {
            self.change_operation(json!({"_expected_id":identifier,"_expected_stage":current["stage"],"stage":"cancelled","error":"任务已取消，未继续安装"})).await?;
        }
        let _ = op;
        self.operation_public().await
    }
    pub async fn install_event(
        &self,
        identifier: &str,
        kind: &str,
        name: &str,
        value: &str,
        evidence: &str,
    ) -> Result<Value> {
        let _guard = self.mutation.lock().await;
        let op = self.state.lock().await.operation.clone();
        let Some(op) = op else { return Ok(Value::Null) };
        if op["id"] != identifier || op["package"]["name"] != name {
            return self.operation_public().await;
        }
        let stage = text(&op["stage"]);
        let mut change = json!({});
        match kind {
            "dispatch_not_started"
                if stage == "awaiting" && op["request_id"].is_null() && op["saw_start"] != true =>
            {
                change = json!({"stage":"cancelled","cancel_requested":true,"error":"已取消，未发出原生安装请求"})
            }
            "uninstall_started" if stage == "uninstalling" && op["cancel_requested"] != true => {
                change = json!({"uninstall_started":true})
            }
            "uninstall_not_started"
                if op["cancel_requested"] == true
                    && ["uninstalling", "cancelled"].contains(&stage) =>
            {
                change = json!({"stage":"cancelled","error":"任务已取消，未继续安装"})
            }
            "uninstall_cancelled" if stage == "ready" && op["mode"] == "reinstall" => {
                change = json!({"stage":"cancelled"})
            }
            "uninstall_error" if stage == "uninstalling" => {
                change = json!({"stage":"unknown","error":if value.is_empty(){"卸载结果未知，不会继续安装"}else{value},"uninstall_blocked":true})
            }
            _ if ["awaiting", "installing", "unknown"].contains(&stage) => match kind {
                "prompt" if !value.is_empty() => {
                    if op["package"]["sha256"] != evidence
                        || !op["request_id"].is_null() && op["request_id"] != value
                    {
                        change = json!({"stage":"unknown","evidence_conflict":true,"error":"检测到同名的其他安装请求，无法关联完成事件"});
                    } else if op["request_id"].is_null() {
                        change = json!({"request_id":value});
                    }
                }
                "start"
                    if !op["request_id"].is_null()
                        && op["saw_start"] != true
                        && op["evidence_conflict"] != true =>
                {
                    self.observe("awaiting", "等待原生确认", "complete", "")
                        .await?;
                    change = json!({"stage":"installing","saw_start":true});
                }
                "finish" if !op["request_id"].is_null() => {
                    change = if op["saw_start"] == true {
                        json!({"saw_finish":true})
                    } else {
                        json!({"stage":"unknown","error":"缺少本次安装开始事件，无法关联完成证据"})
                    }
                }
                "progress"
                    if op["saw_start"] == true
                        && op["saw_finish"] != true
                        && op["evidence_conflict"] != true =>
                {
                    let step = match value.rsplit('.').next().unwrap_or("") {
                        "downloading" => "读取本地安装包",
                        "uninstalling_previous" => "卸载旧版",
                        "installing_plugin" => "解压安装包",
                        "download_remote" => "检查额外依赖",
                        _ => "",
                    };
                    if !step.is_empty() {
                        self.observe("installing", "Loader 安装", "running", step)
                            .await?;
                    }
                }
                "install_error" | "confirm_returned"
                    if op["request_id"] == evidence && op["evidence_conflict"] != true =>
                {
                    if kind == "install_error" || op["saw_finish"] != true {
                        change = json!({"stage":"failed","error":if value.is_empty(){"Loader 安装已结束，未报告安装完成。".into()}else{value.chars().take(1024).collect::<String>()}});
                    }
                }
                "dispatch_error" => {
                    change = json!({"stage":"unknown","evidence_conflict":true,"error":if value.is_empty(){"安装请求结果未知，禁止自动重试"}else{value}})
                }
                "cancel_unknown" if op["cancel_requested"] == true && op["saw_start"] != true => {
                    change = json!({"stage":"unknown","error":"取消结果无法确认，不会重放请求；请核对Decky任务"})
                }
                "cancelled" if !op["request_id"].is_null() && op["saw_start"] != true => {
                    change = json!({"stage":"cancelled"})
                }
                _ => {}
            },
            _ => {}
        }
        if !change.as_object().unwrap().is_empty() {
            self.change_operation(change).await?;
        }
        self.operation_public().await
    }
    pub async fn operation_status(self: &Arc<Self>) -> Result<Value> {
        let _guard = self.mutation.lock().await;
        let Some(mut op) = self.state.lock().await.operation.clone() else {
            return self.operation_public().await;
        };
        if self.stop.is_cancelled() {
            return self.operation_public().await;
        }
        if op["stage"] == "unknown"
            && op["uninstall_result_received"] == true
            && op["mode"] == "reinstall"
            && op["cancel_requested"] == true
            && op["uninstall_started"] == true
        {
            let _ = self.complete_cancelled_uninstall(text(&op["id"])).await;
            op = self.state.lock().await.operation.clone().unwrap();
        }
        if op["stage"] == "uninstalling"
            && seconds() - op["uninstall_claimed_at"].as_f64().unwrap_or(0.0) > 120.0
        {
            self.change_operation(json!({"stage":"unknown","uninstall_blocked":true,"error":"卸载结果超时，不会继续安装"})).await?;
        } else if ["awaiting", "installing", "unknown"].contains(&text(&op["stage"])) {
            let can_verify = op["saw_start"] == true
                && op["saw_finish"] == true
                && op["evidence_conflict"] != true
                && op["uninstall_blocked"] != true;
            if can_verify {
                self.observe("verification", "安装文件核验", "running", "")
                    .await?;
                let package: Package = serde_json::from_value(op["package"].clone())?;
                let host = self.host.clone();
                let previous = op["previous_stamp"].clone();
                let c = self.stop.clone();
                let matched = tokio::task::spawn_blocking(move || {
                    package::verify(&host.home, &package, &previous, &c)
                })
                .await
                .map_err(|_| Error::invalid("安装文件校验失败"))?
                .unwrap_or(false);
                self.change_operation(if matched{json!({"stage":"verified","error":null})}else{json!({"stage":"failed","error":"Loader 流程已结束，但安装文件校验失败（包括额外依赖）。旧版可能已卸载，请检查 Decky。"})}).await?;
            } else if op["submitted_at"]
                .as_f64()
                .is_some_and(|t| seconds() - t > 120.0)
            {
                self.change_operation(json!({"stage":"unknown","error":if op["error"].is_string(){op["error"].clone()}else{json!("未取得完成证据。可能仍在等待确认、已取消或安装未完成；不会自动重试。")}})).await?;
            }
        }
        let op = self.state.lock().await.operation.clone().unwrap();
        if op["stage"] == "verified" {
            let mut settings = load_settings(&self.host)?;
            let name = text(&op["package"]["name"]);
            let mut binding = json!({"repo":op["repo"],"overridden":true});
            let matches: Vec<_> = model::scan(&self.host.home)?
                .into_iter()
                .filter(|p| p["name"] == name)
                .collect();
            if matches.len() == 1 && matches[0]["folder"] == op["package"]["folder"] {
                let raw = &matches[0];
                binding["folder"] = raw["folder"].clone();
                binding["package_name"] = raw["package_name"].clone();
                binding["package_repo"] = raw["repo"].clone();
            }
            settings["bindings"][name] = binding;
            atomic_json(&self.host.settings(), &settings)?;
            self.change_operation(json!({"stage":"closed","binding_saved":true,"outcome":"success","close_reason":"verified","completed_at":now()})).await?;
        }
        let op = self.state.lock().await.operation.clone().unwrap();
        if op["outcome"] == "success"
            && op["artifact_deleted"] != true
            && load_settings(&self.host)?["deletePackageAfterInstall"] == true
        {
            let _ = self.delete_package_inner(text(&op["id"])).await;
        }
        self.operation_public().await
    }
    async fn delete_package_inner(&self, identifier: &str) -> Result<()> {
        let op = self
            .state
            .lock()
            .await
            .operation
            .clone()
            .ok_or_else(|| Error::invalid("任务已变化"))?;
        ensure(
            op["id"] == identifier && terminal(&op),
            "安装尚未结束，不能删除安装包",
        )?;
        if op["artifact_deleted"] == true {
            return Ok(());
        }
        let dir = self.op_directory(identifier)?;
        let scoped = crate::scoped::Dir::open(&dir)?;
        let mut files = Vec::new();
        for name in ["plugin.zip", "source.zip"] {
            if dir.join(name).exists() || dir.join(name).is_symlink() {
                let file = scoped.file(name)?;
                let info = file.metadata()?;
                use std::os::unix::fs::MetadataExt;
                ensure(info.nlink() == 1, "安装包不是独立的普通文件")?;
                files.push((name, info));
            }
        }
        for (name, info) in files {
            scoped.unlink(name, &info)?;
        }
        self.change_operation(json!({"artifact_deleted":true}))
            .await?;
        Ok(())
    }
    pub async fn delete_package(&self, identifier: &str) -> Result<Value> {
        let _guard = self.mutation.lock().await;
        self.delete_package_inner(identifier).await?;
        self.operation_public().await
    }
    pub async fn finish_operation(&self, identifier: &str, dismiss: bool) -> Result<Value> {
        let _guard = self.mutation.lock().await;
        let op = self
            .state
            .lock()
            .await
            .operation
            .clone()
            .ok_or_else(|| Error::invalid("任务已变化"))?;
        ensure(op["id"] == identifier, "任务已变化")?;
        if dismiss {
            ensure(terminal(&op), "活动任务不能关闭提示")?;
            self.change_operation(json!({"notice_dismissed":true}))
                .await?;
        } else {
            ensure(
                !(op["stage"] == "unknown"
                    && (op["cancel_requested"] == true || op["uninstall_blocked"] == true)),
                "原生操作结果未知，不能结束跟踪并放行新任务",
            )?;
            ensure(
                ![
                    "checking",
                    "backup",
                    "validating",
                    "installing",
                    "awaiting",
                    "uninstalling",
                    "verified",
                ]
                .contains(&text(&op["stage"])),
                "任务仍在进行或收尾，请等待结果",
            )?;
            if op["stage"] != "closed" {
                self.change_operation(json!({"stage":"closed","close_reason":"manual"}))
                    .await?;
            }
        }
        self.operation_public().await
    }
}
fn directory_size(root: &std::path::Path) -> Result<u64> {
    let mut total = 0u64;
    for entry in std::fs::read_dir(root)? {
        let entry = entry?;
        let info = std::fs::symlink_metadata(entry.path())?;
        if info.is_file() {
            total = total.saturating_add(info.len());
        } else if info.is_dir() {
            total = total.saturating_add(directory_size(&entry.path())?);
        }
    }
    Ok(total)
}
