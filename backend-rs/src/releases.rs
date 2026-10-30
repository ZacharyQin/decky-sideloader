use crate::{common::*, engine::Engine, library_model as model, metadata, zip_version};
use base64::Engine as _;
use serde_json::{Value, json};
use std::sync::Arc;
use tokio::sync::OnceCell;
pub fn cached_result(plugin: &Value, releases: &[Value], checked: &Value) -> Value {
    let mut result = model::identity(plugin);
    result["latest"] = Value::Null;
    result["status"] = json!("unbound");
    result["error"] = Value::Null;
    result["checkedAt"] = checked.clone();
    if !plugin["repo"].is_string() {
        return result;
    }
    let tags: Vec<_> = array(&plugin["skipped_tags"])
        .iter()
        .filter_map(string)
        .collect();
    let visible: Vec<_> = releases
        .iter()
        .filter(|r| !tags.iter().any(|t| r["tag"] == t.as_str()))
        .cloned()
        .collect();
    if !releases.is_empty() && visible.is_empty() {
        result["status"] = json!("current");
        result["notice"] = json!("已跳过全部新版本");
        return result;
    }
    let chosen = visible
        .iter()
        .find(|r| r["has_zip"] == true)
        .or(visible.first());
    let assessed = metadata::assess(
        &visible,
        text(&plugin["version"]),
        chosen.map_or(&Value::Null, |r| &r["version_evidence"]),
    );
    for (k, v) in assessed.as_object().unwrap() {
        result[k] = v.clone();
    }
    if let Some(chosen) = chosen {
        result["release_tag"] = chosen["tag"].clone();
    }
    if !tags.is_empty() && !visible.is_empty() {
        let mut tags = tags;
        tags.sort();
        let note = format!(
            "已跳过 {}",
            tags.iter().take(3).cloned().collect::<Vec<_>>().join("、")
        );
        result["notice"] = json!(if result["notice"].is_string() {
            format!("{}，{note}", text(&result["notice"]))
        } else {
            note
        });
    }
    result
}
impl Engine {
    pub async fn probe_repo(self: &Arc<Self>, raw: &str) -> Result<Value> {
        let parsed = metadata::parse_repo(raw).ok_or_else(|| Error::invalid("GitHub 链接无效"))?;
        let _guard = self.probe_lock.lock().await;
        {
            let mut s = self.state.lock().await;
            s.probes.retain(|_, (at, _)| at.elapsed().as_secs() < 300);
            if let Some((_, value)) = s.probes.get(&parsed.to_lowercase()) {
                return Ok(value.clone());
            }
        }
        let result = metadata::probe(&self.transport()?, &parsed, &self.stop).await?;
        let mut s = self.state.lock().await;
        if s.probes.len() >= 10 {
            if let Some(key) = s
                .probes
                .iter()
                .min_by_key(|(_, v)| v.0)
                .map(|(k, _)| k.clone())
            {
                s.probes.remove(&key);
            }
        }
        s.probes.insert(
            parsed.to_lowercase(),
            (std::time::Instant::now(), result.clone()),
        );
        Ok(result)
    }
    pub async fn read_releases(self: &Arc<Self>, repo: &str, current: &str) -> Result<Value> {
        Ok(json!(
            metadata::releases(&self.transport()?, repo, current, &self.stop).await?
        ))
    }
    async fn git_version(self: &Arc<Self>, repo: &str, tag: &str, name: &str) -> Option<String> {
        let transport = self.transport().ok()?;
        let tag = url::form_urlencoded::byte_serialize(tag.as_bytes()).collect::<String>();
        let mut values = Vec::new();
        for path in ["plugin.json", "package.json"] {
            let payload = transport
                .json(
                    &format!("https://api.github.com/repos/{repo}/contents/{path}?ref={tag}"),
                    65536,
                    &self.stop,
                )
                .await
                .ok()?;
            if payload["encoding"] != "base64" {
                return None;
            }
            let raw = base64::engine::general_purpose::STANDARD
                .decode(
                    text(&payload["content"])
                        .split_whitespace()
                        .collect::<String>(),
                )
                .ok()?;
            if raw.len() > 65536 {
                return None;
            }
            values.push(serde_json::from_slice::<Value>(&raw).ok()?);
        }
        let v = text(&values[1]["version"]).trim();
        if values[0]["name"] == name && metadata::compare(v, v).is_some() {
            Some(v.into())
        } else {
            None
        }
    }
    async fn check_plugin(self: &Arc<Self>, plugin: &Value) -> (Vec<Value>, Value) {
        let mut rows = Vec::new();
        let checked = now();
        let result = async {
            let mut rows = metadata::releases(
                &self.transport()?,
                text(&plugin["repo"]),
                text(&plugin["version"]),
                &self.stop,
            )
            .await?;
            if let Some(index) = rows.iter().position(|r| r["has_zip"] == true) {
                let urls = array(&rows[index]["zips"]);
                let mut evidence = if urls.len() == 1 {
                    zip_version::probe(&self.transport()?, text(&urls[0]["url"]), &self.stop).await
                } else {
                    json!({"package_state":"skipped","package_version":null,"git_version":null})
                };
                if !["semver", "raw", "absent"].contains(&text(&evidence["package_state"]))
                    && rows[index]["version"].is_null()
                {
                    evidence["git_version"] = json!(
                        self.git_version(
                            text(&plugin["repo"]),
                            text(&rows[index]["tag"]),
                            text(&plugin["name"])
                        )
                        .await
                    );
                }
                rows[index]["version_evidence"] = evidence;
            }
            let result = cached_result(plugin, &rows, &json!(checked));
            Ok::<_, Error>((rows, result))
        }
        .await;
        let result = match result {
            Ok((value, result)) => {
                rows = value;
                result
            }
            Err(error) => {
                let mut result = model::identity(plugin);
                result["latest"] = Value::Null;
                result["status"] = json!("error");
                result["error"] = json!(error.message);
                result["checkedAt"] = json!(checked);
                result
            }
        };
        if result["status"] != "error" {
            if let Ok(current) = self.eligible(text(&plugin["name"])).await {
                if model::identity(&current) == model::identity(plugin) {
                    self.state
                        .lock()
                        .await
                        .releases
                        .put(plugin, &rows, &checked);
                }
            }
        }
        (rows, result)
    }
    fn last_item(settings: &Value, plugin: &Value) -> Value {
        array(&settings["lastCheck"]["items"])
            .iter()
            .find(|item| {
                model::identity(plugin)
                    .as_object()
                    .unwrap()
                    .iter()
                    .all(|(k, v)| item[k] == *v)
            })
            .cloned()
            .unwrap_or(Value::Null)
    }
    pub async fn get_plugin_releases(
        self: &Arc<Self>,
        repo: &str,
        current: &Value,
        name: &str,
        folder: &Value,
        refresh: bool,
    ) -> Result<Value> {
        let plugin = self.eligible(name).await?;
        ensure(
            plugin["repo"] == repo && plugin["version"] == *current && plugin["folder"] == *folder,
            "插件信息已变化，请返回列表重新打开",
        )?;
        let item = Self::last_item(&load_settings(&self.host)?, &plugin);
        let snapshot = self.state.lock().await.releases.get(&plugin);
        if !refresh && snapshot.is_some() {
            return Ok(
                json!({"snapshot":snapshot,"error":if item["status"]=="error"{item["error"].clone()}else{Value::Null}}),
            );
        }
        let key = serde_json::to_string(&model::identity(&plugin))?;
        let cell = {
            let mut s = self.state.lock().await;
            s.release_reads
                .entry(key.clone())
                .or_insert_with(|| Arc::new(OnceCell::new()))
                .clone()
        };
        let result=cell.get_or_init(||async{let _check=self.check_lock.lock().await;let current=self.eligible(name).await?;ensure(model::identity(&current)==model::identity(&plugin),"插件信息已变化，请返回列表重新打开")?;let latest=Self::last_item(&load_settings(&self.host)?,&plugin);if !latest["checkedAt"].is_null()&&latest["checkedAt"]!=item["checkedAt"]{let snapshot=self.state.lock().await.releases.get(&plugin);return Ok(json!({"snapshot":snapshot,"error":if latest["status"]=="error"{latest["error"].clone()}else{Value::Null}}));}
            let(rows,mut result)=self.check_plugin(&plugin).await;let current=self.eligible(name).await?;ensure(model::identity(&current)==model::identity(&plugin),"插件信息已变化，请返回列表重新打开")?;if result["status"]!="error"{result=cached_result(&current,&rows,&result["checkedAt"]);}
            let _mutation=self.mutation.lock().await;let mut settings=load_settings(&self.host)?;let previous=settings["lastCheck"].clone();let mut items:Vec<_>=array(&previous["items"]).iter().filter(|r|r["name"]!=name).cloned().collect();items.push(result.clone());settings["lastCheck"]=json!({"checkedAt":result["checkedAt"],"source":"manual","items":items,"fullCheckedAt":previous.get("fullCheckedAt").unwrap_or(&previous["checkedAt"])});atomic_json(&self.host.settings(),&settings)?;let snapshot=if result["status"]=="error"{self.state.lock().await.releases.get(&plugin)}else{let mut snapshot=model::identity(&plugin);snapshot["checkedAt"]=result["checkedAt"].clone();snapshot["releases"]=json!(rows);Some(snapshot)};Ok(json!({"snapshot":snapshot,"error":result["error"]}))}).await.clone();
        let mut s = self.state.lock().await;
        if s.release_reads
            .get(&key)
            .is_some_and(|c| Arc::ptr_eq(c, &cell))
        {
            s.release_reads.remove(&key);
        }
        result
    }
    pub async fn check_updates(self: &Arc<Self>, source: &str) -> Result<Value> {
        self.no_self_update().await?;
        let _check = self.check_lock.lock().await;
        self.state.lock().await.checking = true;
        let result=async{let listing=self.list_plugins(false).await?;let mut results=Vec::new();let mut plugins=Vec::new();for plugin in array(&listing["plugins"]){cancelled(&self.stop)?;if plugin["sideload"]!=true{continue;}let(_,result)=self.check_plugin(plugin).await;results.push(result);plugins.push(plugin.clone());}
            let report={let _mutation=self.mutation.lock().await;let mut settings=load_settings(&self.host)?;for(index,plugin)in plugins.iter().enumerate(){let tags=skipped_tags(&settings,text(&plugin["name"]),&plugin["repo"]);if json!(tags)!=plugin["skipped_tags"]&&results[index]["status"]!="error"{if let Some(snapshot)=self.state.lock().await.releases.get(plugin){let mut current=plugin.clone();current["skipped_tags"]=json!(tags);results[index]=cached_result(&current,array(&snapshot["releases"]),&results[index]["checkedAt"]);}}}let report=json!({"checkedAt":now(),"source":source,"items":results});settings["lastCheck"]=report.clone();if source=="scheduled"{settings["lastScheduledCheck"]=report.clone();}atomic_json(&self.host.settings(),&settings)?;report};
            self.diagnostics.record("check_summary",json!({"source":source,"count":array(&report["items"]).len(),"errors":array(&report["items"]).iter().filter(|r|r["status"]=="error").count(),"updates":array(&report["items"]).iter().filter(|r|r["status"]=="update").count()}));let mut current=self.list_plugins(false).await?;current["last_check"]=report;current["self_update"]=self.check_self().await?;Ok(current)}.await;
        self.state.lock().await.checking = false;
        result
    }
    pub async fn skip_release(
        self: &Arc<Self>,
        name: &str,
        tag: &str,
        skipped: bool,
    ) -> Result<Value> {
        ensure(
            !tag.is_empty() && tag.chars().count() <= 128,
            "版本标签无效",
        )?;
        let _mutation = self.mutation.lock().await;
        self.binding_idle(name).await?;
        let plugin = self.eligible(name).await?;
        let repo = text(&plugin["repo"]);
        ensure(
            metadata::parse_repo(repo).is_some(),
            "尚未关联 GitHub，不能跳过版本",
        )?;
        let mut settings = load_settings(&self.host)?;
        let mut tags = skipped_tags(&settings, name, &plugin["repo"]);
        tags.retain(|v| v != tag);
        if skipped {
            tags.push(tag.into());
        }
        ensure(tags.len() <= 20, "这个插件跳过的版本过多")?;
        if tags.is_empty() {
            settings["skippedReleases"]
                .as_object_mut()
                .unwrap()
                .remove(name);
        } else {
            settings["skippedReleases"][name] = json!({"repo":repo,"tags":tags});
        }
        ensure(
            settings["skippedReleases"].as_object().unwrap().len() <= 64,
            "跳过记录过多",
        )?;
        let snapshot = self.state.lock().await.releases.get(&plugin);
        let old = Self::last_item(&settings, &plugin);
        let previous = settings["lastCheck"].clone();
        if old["status"] != "error" {
            let mut items: Vec<_> = array(&previous["items"])
                .iter()
                .filter(|r| r["name"] != name)
                .cloned()
                .collect();
            if let Some(snapshot) = &snapshot {
                let mut p = plugin.clone();
                p["skipped_tags"] = json!(tags);
                let mut result =
                    cached_result(&p, array(&snapshot["releases"]), &snapshot["checkedAt"]);
                if let Some(chosen) = array(&snapshot["releases"])
                    .iter()
                    .find(|r| r["tag"] == result["release_tag"])
                {
                    if !old.is_null()
                        && chosen["version_evidence"].is_null()
                        && old["release_tag"] == result["release_tag"]
                    {
                        result["status"] = old["status"].clone();
                        result["latest"] = old["latest"].clone();
                    }
                }
                items.push(result);
            }
            if !previous.is_null() || snapshot.is_some() {
                let mut report = if previous.is_object() {
                    previous.clone()
                } else {
                    json!({})
                };
                report["checkedAt"] = previous.get("checkedAt").cloned().unwrap_or_else(|| {
                    snapshot
                        .as_ref()
                        .map_or(Value::Null, |s| s["checkedAt"].clone())
                });
                report["fullCheckedAt"] = previous
                    .get("fullCheckedAt")
                    .unwrap_or(&previous["checkedAt"])
                    .clone();
                report["items"] = json!(items);
                settings["lastCheck"] = report;
            }
        }
        atomic_json(&self.host.settings(), &settings)?;
        self.list_plugins(false).await
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn skipped_latest_uses_cached_evidence() {
        let plugin = json!({"name":"Example","folder":"example","repo":"a/b","version":"1","skipped_tags":["v2"],"ownership":{"intent":null}});
        let rows = vec![
            json!({"tag":"v2","has_zip":true,"version":"2","zips":[],"version_evidence":{"package_state":"semver","package_version":"2","git_version":null}}),
            json!({"tag":"v1","has_zip":true,"version":"1","zips":[]}),
        ];
        let v = cached_result(&plugin, &rows, &json!("2026-10-28T00:00:00Z"));
        assert_eq!(v["status"], "current");
        assert_eq!(v["release_tag"], "v1");
    }
}
