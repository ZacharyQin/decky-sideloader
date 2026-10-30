use crate::{common::*, engine::Engine, library_model as model, metadata};
use serde_json::{Value, json};
use std::{collections::BTreeSet, sync::Arc};
use tokio::sync::OnceCell;
impl Engine {
    pub async fn list_plugins(self: &Arc<Self>, refresh: bool) -> Result<Value> {
        if refresh {
            let _guard = self.mutation.lock().await;
            self.binding_idle("Sideloader").await?;
            let busy = {
                let s = self.state.lock().await;
                s.operation.as_ref().is_some_and(|v| {
                    !matches!(text(&v["stage"]), "failed" | "closed" | "cancelled")
                })
            };
            ensure(!busy, "任务进行中，请结束后刷新")?;
            self.list_impl(true).await
        } else {
            self.list_impl(false).await
        }
    }
    pub async fn list_impl(self: &Arc<Self>, refresh: bool) -> Result<Value> {
        let (native, capable) = self.native.read(refresh).await?;
        let host = self.host.clone();
        let mut local = tokio::task::spawn_blocking(move || model::scan(&host.home))
            .await
            .map_err(|_| Error::invalid("插件读取任务失败"))??;
        let mut warning = None;
        let mut live = false;
        if refresh {
            let healthy = self.state.lock().await.catalog.error.is_none();
            if healthy {
                let transport = self.transport()?;
                let fetch = transport
                    .store(model::STORE_URL, 4 * 1024 * 1024, &self.stop)
                    .await
                    .and_then(|v| {
                        let items = v
                            .as_array()
                            .ok_or_else(|| Error::invalid("商店目录格式异常"))?;
                        ensure(!items.is_empty(), "商店目录为空、格式异常或超过上限")?;
                        model::names(&json!(
                            items.iter().map(|p| p["name"].clone()).collect::<Vec<_>>()
                        ))
                    });
                match fetch {
                    Ok(names) => {
                        let observed: Vec<_> = local
                            .iter()
                            .filter(|r| r["name"] != "Sideloader")
                            .cloned()
                            .collect();
                        let mut s = self.state.lock().await;
                        match s.catalog.refresh(&names, &observed) {
                            Ok(()) => live = true,
                            Err(e) => warning = Some(e.message),
                        }
                    }
                    Err(e) => warning = Some(e.message),
                }
                let fallback = {
                    let s = self.state.lock().await;
                    !live && s.catalog.value["timestamp"].is_null()
                };
                if fallback {
                    let result = transport
                        .bytes(model::REGISTRY_URL, 1024 * 1024, &self.stop)
                        .await
                        .and_then(|v| {
                            String::from_utf8(v)
                                .map_err(|_| Error::invalid("官方登记数据库格式异常"))
                        })
                        .and_then(|v| model::parse_registry(&v));
                    match result {
                        Ok(entries) => self.state.lock().await.catalog.registry = entries,
                        Err(e) => {
                            warning = Some(format!(
                                "{}；官方登记辅助失败：{}",
                                warning.unwrap_or_default(),
                                e.message
                            ))
                        }
                    }
                }
            }
        }
        let settings = load_settings(&self.host)?;
        model::apply_bindings(&mut local, &settings["bindings"]);
        let state = self.state.lock().await;
        let catalog = state.catalog.status(live, warning);
        let store_names: modelset::Names =
            model::names(&state.catalog.value["names"]).unwrap_or_default();
        let history: modelset::Names =
            model::names(&state.catalog.value["store_history"]).unwrap_or_default();
        let native_names: Option<BTreeSet<String>> = if native["ok"] == true {
            Some(
                array(&native["plugins"])
                    .iter()
                    .filter_map(|p| string(&p["name"]))
                    .collect(),
            )
        } else {
            None
        };
        for row in &mut local {
            let is_self = row["name"] == "Sideloader";
            row["is_self"] = json!(is_self);
            row["in_store"] = if live {
                json!(store_names.contains(text(&row["name"])))
            } else {
                Value::Null
            };
            row["loader_managed"] = if let Some(names) = &native_names {
                if !names.contains(text(&row["name"])) {
                    json!(false)
                } else if let Some(capable) = &capable {
                    json!(capable.contains(text(&row["name"])))
                } else {
                    Value::Null
                }
            } else {
                Value::Null
            };
            let memory = state.catalog.memory(row);
            row["registration"] = json!(if state.catalog.registry.contains(&(
                text(&row["folder"]).into(),
                text(&row["repo"]).to_lowercase()
            )) {
                "registered"
            } else {
                "unknown"
            });
            row["cached_in_store"] = json!(history.contains(text(&row["name"])));
            row["memory_in_store"] = memory["in_store"].clone();
            row["memory_changed"] = json!(
                row["binding_changed"] == true
                    || memory["changed"] == true && row["binding_confirmed"] != true
            );
            row["memory_error"] = json!(state.catalog.error);
            model::resolve_repository(row, &memory, &state.map.value["repositories"]);
        }
        let (ledger, error) = match model::ledger(&self.host) {
            Ok(v) => (v, None),
            Err(e) => (json!({"version":1,"entries":[]}), Some(e.message)),
        };
        let frozen = model::frozen(&self.host).ok();
        model::resolve_ownership(&mut local, frozen.as_ref(), &ledger, error.as_deref());
        for row in &mut local {
            row["skipped_tags"] = json!(skipped_tags(&settings, text(&row["name"]), &row["repo"]));
        }
        let snapshots: Vec<_> = local.iter().filter_map(|p| state.releases.get(p)).collect();
        drop(state);
        let mut state = self.state.lock().await;
        state.library_snapshot =
            crate::diagnostics::library_snapshot(&local, &native, &catalog, frozen.as_ref());
        drop(state);
        self.diagnostics.record("list_summary",json!({"source":catalog["source"],"count":local.len(),"ok":native["ok"]==true&&catalog["warning"].is_null(),"loaderOk":native["ok"],"catalogOk":catalog["warning"].is_null()}));
        Ok(
            json!({"native_snapshot":native,"catalog":catalog,"plugins":local,"store_ok":live,"store_error":catalog["warning"],"last_check":settings["lastCheck"],"release_snapshots":snapshots}),
        )
    }
    pub fn require_plugin(&self, name: &str) -> Result<Value> {
        let rows = model::scan(&self.host.home)?;
        let matches: Vec<_> = rows.into_iter().filter(|p| p["name"] == name).collect();
        ensure(
            matches.len() == 1 && name != "Sideloader",
            "插件不存在、名称重复或是 Sideloader 自身",
        )?;
        Ok(matches[0].clone())
    }
    pub async fn eligible(self: &Arc<Self>, name: &str) -> Result<Value> {
        self.require_plugin(name)?;
        let listing = self.list_plugins(false).await?;
        let matches: Vec<_> = array(&listing["plugins"])
            .iter()
            .filter(|p| p["name"] == name)
            .collect();
        ensure(
            matches.len() == 1 && matches[0]["ownership"]["eligible"] == true,
            "此插件由商店负责或归属未确认，请先显式接管并完成冻结回查",
        )?;
        Ok(matches[0].clone())
    }
    async fn binding_plugin(self: &Arc<Self>, name: &str) -> Result<Value> {
        self.require_plugin(name)?;
        let listing = self.list_plugins(false).await?;
        let plugin = array(&listing["plugins"])
            .iter()
            .find(|p| p["name"] == name)
            .ok_or_else(|| Error::invalid("插件不存在"))?
            .clone();
        let owner = &plugin["ownership"];
        ensure(
            owner["eligible"] == true
                || owner["state"] == "unknown"
                    && owner["error"].is_null()
                    && owner["intent"].is_null()
                    && plugin["cached_in_store"] != true,
            "此插件由商店负责或接管未确认，请先使用显式接管",
        )?;
        Ok(plugin)
    }
    pub async fn authorize(self: &Arc<Self>, name: &str) -> Result<Value> {
        let _guard = self.mutation.lock().await;
        self.binding_idle(name).await?;
        self.eligible(name).await?;
        Ok(Value::Null)
    }
    pub async fn set_binding(self: &Arc<Self>, name: &str, raw: Option<&str>) -> Result<Value> {
        let _guard = self.mutation.lock().await;
        self.binding_idle(name).await?;
        let plugin = self.binding_plugin(name).await?;
        ensure(
            plugin["ownership"]["state"] != "owned",
            if raw.is_some() {
                "已接管商店插件的仓库已由接管记录确认；请先交还后重新接管以更换仓库"
            } else {
                "请使用交还商店，避免解除关联后商店仍暂停检查"
            },
        )?;
        let mut settings = load_settings(&self.host)?;
        let result = if let Some(raw) = raw {
            let repo =
                metadata::parse_repo(raw).ok_or_else(|| Error::invalid("GitHub 链接无效"))?;
            let disk = self.require_plugin(name)?;
            self.binding_idle(name).await?;
            settings["bindings"][name] = json!({"repo":repo,"overridden":true,"folder":disk["folder"],"package_name":disk["package_name"],"package_repo":disk["repo"]});
            json!({"name":name,"repo":repo})
        } else {
            self.binding_idle(name).await?;
            settings["bindings"][name] = json!({"disabled":true});
            Value::Null
        };
        atomic_json(&self.host.settings(), &settings)?;
        Ok(result)
    }
    pub async fn begin_ownership(
        self: &Arc<Self>,
        name: &str,
        folder: &str,
        repo: &str,
        action: &str,
    ) -> Result<Value> {
        let _guard = self.mutation.lock().await;
        self.binding_idle(name).await?;
        let disk = self.require_plugin(name)?;
        ensure(disk["folder"] == folder, "插件目录已变化")?;
        let listing = self.list_plugins(false).await?;
        let plugin = array(&listing["plugins"])
            .iter()
            .find(|p| p["name"] == name)
            .ok_or_else(|| Error::invalid("插件不存在"))?;
        self.binding_idle(name).await?;
        let mut ledger = model::ledger(&self.host)?;
        let old = model::ownership_entry(plugin, &ledger)
            .cloned()
            .unwrap_or(Value::Null);
        ensure(
            old.is_null() || ["active", "released"].contains(&text(&old["state"])),
            "已有未确认的接管操作，请先回查或显式继续",
        )?;
        if action == "return" {
            ensure(old["state"] == "active", "此插件没有可交还的接管记录")?;
        } else {
            ensure(
                action == "takeover" && plugin["loader_managed"] == true,
                "此插件没有已确认的商店记录，不能接管",
            )?;
            ensure(old["state"] != "active", "此插件已经接管")?;
        }
        let parsed =
            metadata::parse_repo(repo).ok_or_else(|| Error::invalid("请确认有效的 GitHub 仓库"))?;
        let frozen = model::frozen(&self.host)?;
        let row = json!({"id":id(),"name":name,"folder":folder,"repo":if action=="return"{text(&old["repo"])}else{&parsed},"prior_frozen":if action=="return"{old["prior_frozen"].clone()}else{json!(frozen.contains(name))},"action":action,"state":"prepared"});
        let mut entries: Vec<_> = array(&ledger["entries"])
            .iter()
            .filter(|r| r["name"] != name || r["folder"] != folder)
            .cloned()
            .collect();
        entries.push(row.clone());
        ledger["entries"] = json!(entries);
        model::validate_ledger(&ledger)?;
        atomic_json(&self.host.settings_dir.join("ownership.json"), &ledger)?;
        self.state
            .lock()
            .await
            .catalog
            .remember(&disk, text(&row["repo"]), "user")?;
        self.diagnostics.record("ownership_stage",json!({"pluginId":crate::diagnostics::identifier("plugin",name),"ownershipId":row["id"],"action":action,"phase":"prepared"}));
        Ok(row)
    }
    pub async fn ownership_step(self: &Arc<Self>, identifier: &str, phase: &str) -> Result<Value> {
        let _guard = self.mutation.lock().await;
        let mut ledger = model::ledger(&self.host)?;
        let row = array(&ledger["entries"])
            .iter()
            .find(|r| r["id"] == identifier)
            .ok_or_else(|| Error::invalid("接管意图已变化"))?
            .clone();
        let name = text(&row["name"]);
        self.binding_idle(name).await?;
        ensure(
            self.require_plugin(name)?["folder"] == row["folder"],
            "插件目录已变化，不能继续原接管意图",
        )?;
        let frozen = model::frozen(&self.host)?;
        if phase == "read" {
            return Ok(json!({"intent":row,"frozen":frozen}));
        }
        let target = match phase {
            "writing" => {
                ensure(
                    ["prepared", "writing", "confirming", "active"].contains(&text(&row["state"])),
                    "原生写入已领取，请先回查结果，不会自动重放",
                )?;
                "writing"
            }
            "confirming" => {
                ensure(row["state"] == "writing", "原生写入状态已变化")?;
                "confirming"
            }
            "complete" => {
                ensure(
                    row["state"] != "prepared",
                    "尚未领取原生冻结写入，请显式继续操作",
                )?;
                let desired = if row["action"] == "takeover" {
                    true
                } else {
                    row["prior_frozen"] == true
                };
                ensure(
                    frozen.contains(name) == desired,
                    "原生冻结回查未匹配，接管结果仍未确认",
                )?;
                if row["action"] == "takeover" {
                    "active"
                } else {
                    "released"
                }
            }
            _ => return Err(Error::invalid("接管阶段无效")),
        };
        let item = ledger["entries"]
            .as_array_mut()
            .unwrap()
            .iter_mut()
            .find(|r| r["id"] == identifier)
            .unwrap();
        item["state"] = json!(target);
        let updated = item.clone();
        model::validate_ledger(&ledger)?;
        atomic_json(&self.host.settings_dir.join("ownership.json"), &ledger)?;
        self.diagnostics.record("ownership_stage",json!({"pluginId":crate::diagnostics::identifier("plugin",name),"ownershipId":identifier,"action":updated["action"],"phase":target}));
        Ok(json!({"intent":updated,"frozen":frozen}))
    }
    pub async fn update_map(self: &Arc<Self>) -> Result<Value> {
        let cell = {
            let mut s = self.state.lock().await;
            s.mapping_update
                .get_or_insert_with(|| Arc::new(OnceCell::new()))
                .clone()
        };
        let result = cell
            .get_or_init(|| async {
                self.binding_idle("Sideloader").await?;
                {
                    let s = self.state.lock().await;
                    ensure(
                        !s.checking
                            && s.op_done.is_none()
                            && s.operation.as_ref().is_none_or(|op| {
                                matches!(text(&op["stage"]), "failed" | "closed" | "cancelled")
                            }),
                        "任务进行中，请结束后更新仓库映射表",
                    )?;
                }
                let payload = self
                    .transport()?
                    .bytes(model::MAP_URL, 1024 * 1024, &self.stop)
                    .await?;
                let _guard = self.mutation.lock().await;
                self.binding_idle("Sideloader").await?;
                let mut s = self.state.lock().await;
                ensure(
                    !s.checking
                        && s.op_done.is_none()
                        && s.operation.as_ref().is_none_or(|op| {
                            matches!(text(&op["stage"]), "failed" | "closed" | "cancelled")
                        }),
                    "任务进行中，请结束后更新仓库映射表",
                )?;
                s.map.install(&payload)
            })
            .await
            .clone();
        let mut s = self.state.lock().await;
        if s.mapping_update
            .as_ref()
            .is_some_and(|c| Arc::ptr_eq(c, &cell))
        {
            s.mapping_update = None;
        }
        result
    }
}
mod modelset {
    pub type Names = std::collections::BTreeSet<String>;
}
