use crate::{
    common::*,
    engine::{Engine, self_active},
    library_model as model, metadata,
    package::{self, Package},
};
use serde_json::{Value, json};
use std::sync::Arc;
use tokio::sync::OnceCell;
use tokio_util::sync::CancellationToken;
const REPOSITORY: &str = "ZacharyQin/decky-sideloader";
pub fn select_release(payload: &Value, current: &str) -> Result<Value> {
    ensure(
        payload.is_object() && payload["draft"] == false && payload["prerelease"] == false,
        "自更新只接受正式发布",
    )?;
    let tag = text(&payload["tag_name"]);
    let caps = regex::Regex::new(r"^v?((?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*))$")
        .unwrap();
    let m = caps
        .captures(tag)
        .ok_or_else(|| Error::invalid("自更新版本无法比较"))?;
    let version = &m[1];
    let comparison =
        metadata::compare(version, current).ok_or_else(|| Error::invalid("自更新版本无法比较"))?;
    let filename = format!("decky-sideloader-{version}.zip");
    let assets: Vec<_> = array(&payload["assets"])
        .iter()
        .filter(|a| a["name"] == filename)
        .collect();
    ensure(assets.len() == 1, "正式发布缺少唯一的 Sideloader 安装包")?;
    let asset = assets[0];
    let url = format!("https://github.com/{REPOSITORY}/releases/download/{tag}/{filename}");
    let hash = text(&asset["digest"]).strip_prefix("sha256:").unwrap_or("");
    ensure(
        asset["browser_download_url"] == url
            && valid_hash(hash)
            && asset["id"].as_u64().is_some_and(|n| n > 0)
            && asset["size"]
                .as_u64()
                .is_some_and(|n| n > 0 && n <= package::MAX_DOWNLOAD),
        "自更新安装包来源或校验信息无效",
    )?;
    Ok(
        json!({"repo":REPOSITORY,"tag":tag,"version":version,"title":if text(&payload["name"]).is_empty(){tag.chars().take(256).collect::<String>()}else{text(&payload["name"]).chars().take(256).collect::<String>()},"body":text(&payload["body"]).chars().take(20000).collect::<String>(),"published_at":text(&payload["published_at"]).chars().take(64).collect::<String>(),"prerelease":false,"has_zip":true,"comparison":comparison,"zips":[{"name":filename,"url":url}],"sha256":hash,"asset_id":asset["id"],"size":asset["size"]}),
    )
}
impl Engine {
    fn self_frontend_matches(&self, frontend: &Value) -> bool {
        frontend["version"] == self.host.version
            && valid_hash(text(&frontend["fingerprint"]))
            && read_json(&self.host.plugin_dir.join("dist/build.json"), 8192).is_ok_and(|build| {
                build["version"] == self.host.version
                    && build["fingerprint"] == frontend["fingerprint"]
                    && file_hash(
                        &self.host.plugin_dir.join("dist/index.js"),
                        &CancellationToken::new(),
                    )
                    .is_ok_and(|hash| build["bundle"] == hash)
            })
    }
    pub async fn self_public(&self, frontend: &Value) -> Value {
        let state = self.state.lock().await;
        let op = &state.self_record["operation"];
        let public = if op.is_object() {
            let mut result = json!({});
            for key in [
                "id",
                "stage",
                "error",
                "release",
                "request_id",
                "saw_start",
                "progress",
                "cancel_requested",
                "artifact",
                "package",
            ] {
                if let Some(v) = op.get(key) {
                    result[key] = v.clone();
                }
            }
            if result["package"].is_object() {
                let package = result["package"].clone();
                let mut value = json!({});
                for key in ["name", "folder", "version", "sha256", "warnings"] {
                    value[key] = package[key].clone();
                }
                result["package"] = value;
            }
            result
        } else {
            Value::Null
        };
        json!({"current":self.host.version,"check":state.self_record["check"],"operation":public,"checking":state.self_check.is_some(),"preparing":state.self_preparing,"frontend_compatible":self.self_frontend_matches(frontend),"error":state.self_error})
    }
    pub async fn self_status(self: &Arc<Self>, frontend: &Value) -> Result<Value> {
        let _guard = self.mutation.lock().await;
        let (op, error) = {
            let s = self.state.lock().await;
            (s.self_record["operation"].clone(), s.self_error.clone())
        };
        if ["awaiting", "installing", "verifying", "unknown"].contains(&text(&op["stage"]))
            && error.is_none()
        {
            if let Ok(package) = serde_json::from_value::<Package>(op["package"].clone()) {
                let legacy = package.binary.is_none();
                let expected_binary = package
                    .binary
                    .as_ref()
                    .or_else(|| package.hashes.get("bin/sideloader-core"));
                let binary_matches = expected_binary == Some(&self.binary_hash);
                let backend_matches = if legacy {
                    package.backend.as_deref() == Some(self.glue_hash.as_str())
                } else {
                    package.backend.as_deref() == Some(env!("SIDELOADER_BUILD"))
                };
                if op["from_boot"] != self.boot
                    && self.host.version == package.version
                    && binary_matches
                    && backend_matches
                {
                    let host = self.host.clone();
                    let expected = package.clone();
                    let previous = op["previous_stamp"].clone();
                    let cancel = self.stop.clone();
                    let matched = tokio::task::spawn_blocking(move || {
                        package::verify(&host.home, &expected, &previous, &cancel)
                    })
                    .await
                    .map_err(|_| Error::invalid("自更新核验任务失败"))?
                    .unwrap_or(false);
                    if matched
                        && self.self_frontend_matches(frontend)
                        && frontend["fingerprint"].as_str() == package.frontend.as_deref()
                    {
                        self.change_self(json!({"stage":"completed","error":null}))
                            .await?;
                        if load_settings(&self.host)?["deletePackageAfterInstall"] == true {
                            self.remove_self_package(text(&op["id"]))?;
                        }
                    } else if matched && op["stage"] != "verifying" {
                        self.change_self(json!({"stage":"verifying","error":null}))
                            .await?;
                    }
                } else if ["awaiting", "installing"].contains(&text(&op["stage"]))
                    && seconds() - op["submitted_at"].as_f64().unwrap_or(0.0) > 120.0
                {
                    self.change_self(
                        json!({"stage":"unknown","error":"自更新结果尚未确认，不会重复安装"}),
                    )
                    .await?;
                }
            }
        }
        Ok(self.self_public(frontend).await)
    }
    pub async fn check_self(self: &Arc<Self>) -> Result<Value> {
        self.require_running().await?;
        let cell = {
            let mut s = self.state.lock().await;
            ensure(
                s.self_error.is_none(),
                s.self_error.as_deref().unwrap_or("自更新记录不可用"),
            )?;
            ensure(
                s.self_done.is_none() && !self_active(&s.self_record["operation"]),
                "请先完成或取消 Sideloader 自更新",
            )?;
            s.self_check
                .get_or_insert_with(|| Arc::new(OnceCell::new()))
                .clone()
        };
        let result=cell.get_or_init(||async{let at=now();let payload=self.transport()?.json(&format!("https://api.github.com/repos/{REPOSITORY}/releases/latest"),256*1024,&self.stop).await.and_then(|v|select_release(&v,&self.host.version));let mut state=self.state.lock().await;let check=match payload{Ok(release)=>json!({"status":match release["comparison"].as_i64(){Some(1)=>"update",Some(0)=>"current",_=>"ahead"},"release":release,"checked_at":at,"error":null}),Err(e)=>json!({"status":"error","release":state.self_record["check"]["release"],"checked_at":at,"error":e.message.chars().take(1024).collect::<String>()})};if !self.stop.is_cancelled(){let mut record=state.self_record.clone();record["check"]=check;atomic_json(&self.host.runtime_dir.join("self-update/state.json"),&record)?;state.self_record=record;}Ok(Value::Null)}).await.clone();
        {
            let mut s = self.state.lock().await;
            if s.self_check.as_ref().is_some_and(|v| Arc::ptr_eq(v, &cell)) {
                s.self_check = None;
            }
        }
        result?;
        Ok(self.self_public(&Value::Null).await)
    }
    async fn self_idle(&self) -> Result<()> {
        self.require_running().await?;
        let s = self.state.lock().await;
        ensure(
            s.self_error.is_none(),
            s.self_error.as_deref().unwrap_or("自更新记录不可用"),
        )?;
        ensure(
            s.operation_error.is_none(),
            s.operation_error.as_deref().unwrap_or("安装记录不可用"),
        )?;
        ensure(
            s.self_done.is_none() && !self_active(&s.self_record["operation"]),
            "请先完成或取消 Sideloader 自更新",
        )?;
        ensure(
            s.op_done.is_none()
                && s.operation.as_ref().is_none_or(|op| {
                    matches!(text(&op["stage"]), "failed" | "closed" | "cancelled")
                }),
            "请先结束当前插件安装任务",
        )?;
        ensure(
            !s.checking && s.mapping_update.is_none() && self.check_lock.try_lock().is_ok(),
            "正在检查版本，请稍后更新 Sideloader",
        )
    }
    pub async fn prepare_self(self: &Arc<Self>, tag: &str, hash: &str) -> Result<Value> {
        {
            let _guard = self.mutation.lock().await;
            self.self_idle().await?;
            let mut s = self.state.lock().await;
            ensure(!s.self_preparing, "请先完成或取消 Sideloader 自更新")?;
            s.self_preparing = true;
        }
        let result=async{self.check_self().await?;let _guard=self.mutation.lock().await;self.self_idle().await?;let release={let s=self.state.lock().await;let check=&s.self_record["check"];ensure(check["status"]=="update"&&check["release"]["tag"]==tag&&check["release"]["sha256"]==hash,"更新版本已变化，请重新检查")?;check["release"].clone()};let old=self.state.lock().await.self_record["operation"].clone();if !old.is_null(){self.remove_self_package(text(&old["id"]))?;}let identifier=id();let dir=self.self_directory(&identifier)?;directory(dir.parent().unwrap())?;std::fs::create_dir(&dir)?;use std::os::unix::fs::PermissionsExt;std::fs::set_permissions(&dir,std::fs::Permissions::from_mode(0o700))?;let previous=package::stamp(&self.host.home,"decky-sideloader",&self.stop)?;let transport=self.transport()?;let minutes=load_settings(&self.host)?["downloadTimeoutMinutes"].as_u64().unwrap_or(3);let op=json!({"id":identifier,"stage":"downloading","release":release,"package":null,"artifact":format!("file://{}",dir.join("plugin.zip").display()),"request_id":null,"saw_start":false,"error":null,"cancel_requested":false,"from_boot":self.boot,"from_version":self.host.version,"from_backend":env!("SIDELOADER_BUILD"),"from_binary":self.binary_hash,"previous_stamp":previous});let cancel=self.stop.child_token();let done=CancellationToken::new();{let mut s=self.state.lock().await;let mut record=s.self_record.clone();record["operation"]=op;atomic_json(&self.host.runtime_dir.join("self-update/state.json"),&record)?;s.self_record=record;s.self_cancel=cancel.clone();s.self_done=Some(done.clone());}
            let engine=self.clone();self.tasks.spawn(async move{let result=async{let archive=dir.join("plugin.zip");let sender=engine.clone();let id=identifier.clone();let completed=transport.download(text(&release["zips"][0]["url"]),&archive,package::MAX_DOWNLOAD,minutes,&cancel,move|v|{let e=sender.clone();let id=id.clone();let tracker=e.tasks.clone();tracker.spawn(async move{let current=e.state.lock().await.self_record["operation"].clone();if current["id"]==id&&current["stage"]=="downloading"{let _=e.change_self(json!({"_expected_id":id,"_expected_stage":"downloading","progress":v})).await;}});}).await?;engine.change_self(json!({"_expected_id":identifier,"_expected_stage":"downloading","progress":completed})).await?;
                    let host=engine.host.clone();let target=release.clone();let c=cancel.clone();let package=tokio::task::spawn_blocking(move||{let installed=model::scan(&host.home)?;let matches:Vec<_>=installed.iter().filter(|p|p["name"]=="Sideloader"||p["folder"]=="decky-sideloader").collect();ensure(matches.len()==1&&matches[0]["name"]=="Sideloader"&&matches[0]["folder"]=="decky-sideloader"&&matches[0]["package_name"]=="decky-sideloader","Sideloader 运行身份不一致")?;let package=package::inspect(&archive,Some(matches[0]),&installed,Some(&json!({"name":"Sideloader","package_name":"decky-sideloader"})),true,&c)?;ensure(package.sha256==text(&target["sha256"])&&archive.metadata()?.len()==target["size"].as_u64().unwrap_or(0)&&package.version==text(&target["version"]),"自更新安装包与发布信息不一致")?;Ok::<_,Error>(package)}).await.map_err(|_|Error::invalid("自更新校验任务失败"))??;cancelled(&cancel)?;engine.change_self(json!({"stage":"ready","package":package,"error":null})).await?;Ok::<_,Error>(())}.await;
                if let Err(error)=result{let cancelled=cancel.is_cancelled();let _=engine.change_self(json!({"stage":if cancelled{"cancelled"}else{"failed"},"error":if cancelled{Value::Null}else{json!(error.message)}})).await;let _=engine.remove_self_package(&identifier);}engine.state.lock().await.self_done=None;done.cancel();});Ok(self.self_public(&Value::Null).await)}.await;
        self.state.lock().await.self_preparing = false;
        result
    }
    fn self_directory(&self, identifier: &str) -> Result<std::path::PathBuf> {
        ensure(valid_id(identifier), "自更新任务已变化")?;
        Ok(self.host.runtime_dir.join("self-update").join(identifier))
    }
    fn remove_self_package(&self, identifier: &str) -> Result<()> {
        let dir = self.self_directory(identifier)?;
        if !dir.exists() && !dir.is_symlink() {
            return Ok(());
        }
        let scoped = crate::scoped::Dir::open(&dir)?;
        let archive = dir.join("plugin.zip");
        if archive.exists() || archive.is_symlink() {
            let file = scoped.file("plugin.zip")?;
            scoped.unlink("plugin.zip", &file.metadata()?)?;
        }
        std::fs::remove_dir(&dir)?;
        Ok(())
    }
    pub async fn submit_self(
        self: &Arc<Self>,
        identifier: &str,
        frontend: &Value,
    ) -> Result<Value> {
        let _guard = self.mutation.lock().await;
        self.require_running().await?;
        let op = self.state.lock().await.self_record["operation"].clone();
        ensure(
            op["id"] == identifier && op["stage"] == "ready" && op["cancel_requested"] != true,
            "自更新任务尚未准备好或已提交",
        )?;
        ensure(
            self.self_frontend_matches(frontend)
                && op["from_version"] == self.host.version
                && op["from_backend"] == env!("SIDELOADER_BUILD")
                && op["from_binary"] == self.binary_hash,
            "Sideloader 运行身份不一致",
        )?;
        let archive = self.self_directory(identifier)?.join("plugin.zip");
        ensure(
            !archive.is_symlink()
                && archive.is_file()
                && package::stamp(&self.host.home, "decky-sideloader", &self.stop)?
                    == op["previous_stamp"],
            "自更新安装包或已安装程序已变化",
        )?;
        let expected = text(&op["package"]["sha256"]).to_owned();
        let c = self.stop.clone();
        ensure(
            tokio::task::spawn_blocking(move || file_hash(&archive, &c))
                .await
                .map_err(|_| Error::invalid("自更新校验任务失败"))??
                == expected,
            "自更新安装包或已安装程序已变化",
        )?;
        self.require_running().await?;
        self.change_self(
            json!({"stage":"awaiting","submitted_at":seconds(),"from_boot":self.boot}),
        )
        .await?;
        Ok(self.self_public(frontend).await)
    }
    pub async fn cancel_self(self: &Arc<Self>, identifier: &str) -> Result<Value> {
        let (op, cancel, done) = {
            let mut state = self.state.lock().await;
            let op = state.self_record["operation"].clone();
            ensure(op["id"] == identifier, "自更新任务已变化")?;
            let preparing = ["downloading", "ready"].contains(&text(&op["stage"]));
            let pending = ["awaiting", "unknown"].contains(&text(&op["stage"]))
                && op["saw_start"] != true
                && !op["request_id"].is_null();
            ensure(
                preparing || pending,
                "已交给 Loader，不能取消正在替换的程序",
            )?;
            let mut record = state.self_record.clone();
            record["operation"] =
                crate::engine::transition(&op, &json!({"cancel_requested":true}))?;
            atomic_json(
                &self.host.runtime_dir.join("self-update/state.json"),
                &record,
            )?;
            state.self_record = record;
            (op, state.self_cancel.clone(), state.self_done.clone())
        };
        if ["downloading", "ready"].contains(&text(&op["stage"])) {
            cancel.cancel();
            if let Some(done) = done {
                done.cancelled().await;
            } else {
                self.change_self(json!({"_expected_id":identifier,"_expected_stage":op["stage"],"stage":"cancelled","error":null})).await?;
                self.remove_self_package(identifier)?;
            }
        }
        Ok(self.self_public(&Value::Null).await)
    }
    pub async fn self_event(
        &self,
        identifier: &str,
        kind: &str,
        value: &str,
        evidence: &str,
    ) -> Result<Value> {
        let _guard = self.mutation.lock().await;
        let op = self.state.lock().await.self_record["operation"].clone();
        if op["id"] != identifier
            || !["awaiting", "installing", "unknown"].contains(&text(&op["stage"]))
        {
            return Ok(self.self_public(&Value::Null).await);
        }
        let mut change = json!({});
        let mut remove = false;
        match kind {
            "prompt" if !value.is_empty() => {
                if op["package"]["sha256"] != evidence
                    || !op["request_id"].is_null() && op["request_id"] != value
                {
                    change = json!({"stage":"unknown","error":"自更新确认请求不匹配，不会重复安装","evidence_conflict":true});
                } else if op["evidence_conflict"] != true {
                    change = json!({"request_id":value});
                }
            }
            "start" if !op["request_id"].is_null() && op["evidence_conflict"] != true => {
                change = json!({"stage":"installing","saw_start":true})
            }
            "cancelled" if op["request_id"] == evidence && op["saw_start"] != true => {
                change = json!({"stage":"cancelled","error":null});
                remove = true;
            }
            "not_dispatched" if op["request_id"].is_null() && op["saw_start"] != true => {
                change = json!({"stage":"cancelled","error":null});
                remove = true;
            }
            "install_error" | "confirm_returned"
                if op["request_id"] == evidence && op["evidence_conflict"] != true =>
            {
                change = json!({"stage":"failed","error":if value.is_empty(){"原生安装已结束，但新版尚未启动".into()}else{value.chars().take(1024).collect::<String>()}})
            }
            "dispatch_error" | "cancel_unknown" => {
                change = json!({"stage":"unknown","error":"自更新结果尚未确认，不会重复安装"})
            }
            _ => {}
        }
        if !change.as_object().unwrap().is_empty() {
            self.change_self(change).await?;
        }
        if remove {
            self.remove_self_package(identifier)?;
        }
        Ok(self.self_public(&Value::Null).await)
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn fixed_stable_release() {
        let payload = json!({"draft":false,"prerelease":false,"tag_name":"v0.7.0","assets":[{"name":"decky-sideloader-0.7.0.zip","id":1,"size":100,"digest":format!("sha256:{}","a".repeat(64)),"browser_download_url":"https://github.com/ZacharyQin/decky-sideloader/releases/download/v0.7.0/decky-sideloader-0.7.0.zip"}]});
        assert_eq!(select_release(&payload, "0.6.0").unwrap()["comparison"], 1);
        let mut bad = payload;
        bad["assets"][0]["browser_download_url"] = json!("https://evil.test/a.zip");
        assert!(select_release(&bad, "0.6.0").is_err());
    }
}
