use crate::{common::*, engine::Engine};
use serde_json::{Value, json};
use std::sync::Arc;
pub const METHODS: &[&str] = &[
    "ping",
    "receive_native_management",
    "get_app_settings",
    "set_network_mode",
    "set_check_notifications",
    "set_show_home_task_notice",
    "set_delete_package_after_install",
    "set_download_timeout",
    "set_auto_check",
    "get_repository_map_status",
    "update_repository_map",
    "get_open_source_notices",
    "list_sideload_plugins",
    "set_binding",
    "clear_binding",
    "authorize_plugin_mutation",
    "begin_store_ownership",
    "ownership_native_step",
    "search_repositories",
    "probe_repository",
    "list_releases",
    "get_plugin_releases",
    "set_skipped_release",
    "check_updates",
    "prepare_install",
    "get_operation",
    "continue_prepared",
    "retry_download",
    "submit_prepared",
    "claim_uninstall",
    "complete_uninstall",
    "record_install_event",
    "cancel_operation",
    "dismiss_operation",
    "close_operation",
    "delete_install_package",
    "delete_backups",
    "get_self_update_status",
    "check_self_update",
    "prepare_self_update",
    "submit_self_update",
    "cancel_self_update",
    "record_self_update_event",
    "get_diagnostics_addresses",
    "get_diagnostics_share",
    "start_diagnostics_share",
    "stop_diagnostics_share",
    "record_diagnostic_events",
    "get_diagnostics_status",
    "export_diagnostics",
];
struct Args<'a>(&'a [Value]);
impl<'a> Args<'a> {
    fn count(&self, min: usize, max: usize) -> Result<()> {
        ensure(
            self.0.len() >= min && self.0.len() <= max,
            "RPC 参数数量无效",
        )
    }
    fn value(&self, i: usize) -> &'a Value {
        self.0.get(i).unwrap_or(&Value::Null)
    }
    fn string(&self, i: usize) -> Result<&'a str> {
        self.value(i)
            .as_str()
            .filter(|s| s.len() <= 8192 && !s.contains('\0'))
            .ok_or_else(|| Error::new("invalid_input", "RPC 字符串参数无效"))
    }
    fn optional(&self, i: usize) -> Result<Option<&'a str>> {
        if self.value(i).is_null() {
            Ok(None)
        } else {
            self.string(i).map(Some)
        }
    }
    fn boolean(&self, i: usize, default: Option<bool>) -> Result<bool> {
        if self.0.get(i).is_none() {
            default.ok_or_else(|| Error::invalid("开关必须为布尔值"))
        } else {
            self.value(i)
                .as_bool()
                .ok_or_else(|| Error::invalid("开关必须为布尔值"))
        }
    }
    fn integer(&self, i: usize, default: Option<u64>) -> Result<u64> {
        if self.0.get(i).is_none() {
            default.ok_or_else(|| Error::invalid("整数参数无效"))
        } else {
            self.value(i)
                .as_u64()
                .ok_or_else(|| Error::invalid("整数参数无效"))
        }
    }
}
impl Engine {
    pub async fn dispatch(self: &Arc<Self>, method: &str, args: &[Value]) -> Result<Value> {
        ensure(METHODS.contains(&method), "未知的后端方法")?;
        self.require_running().await?;
        let a = Args(args);
        match method {
            "ping" => {
                a.count(0, 0)?;
                Ok(json!("pong"))
            }
            "receive_native_management" => {
                a.count(2, 2)?;
                self.native.receive(a.string(0)?, a.value(1)).await
            }
            "get_app_settings" => {
                a.count(0, 0)?;
                let mut settings = load_settings(&self.host)?;
                settings["lastNetworkRoute"] = self
                    .last_route
                    .lock()
                    .unwrap()
                    .clone()
                    .unwrap_or(Value::Null);
                Ok(settings)
            }
            "set_network_mode" => {
                a.count(1, 1)?;
                let mode = a.string(0)?;
                ensure(
                    ["direct", "auto", "gh-proxy", "ghfast"].contains(&mode),
                    "GitHub 连接模式无效",
                )?;
                self.set_setting("networkMode", json!(mode)).await
            }
            "set_check_notifications"
            | "set_show_home_task_notice"
            | "set_delete_package_after_install" => {
                a.count(1, 1)?;
                let key = match method {
                    "set_check_notifications" => "checkNotifications",
                    "set_show_home_task_notice" => "showHomeTaskNotice",
                    _ => "deletePackageAfterInstall",
                };
                self.set_setting(key, json!(a.boolean(0, None)?)).await
            }
            "set_download_timeout" => {
                a.count(1, 1)?;
                let minutes = a.integer(0, None)?;
                ensure(
                    [3, 5, 10, 15, 30].contains(&minutes),
                    "下载超时应为 3、5、10、15 或 30 分钟",
                )?;
                self.set_setting("downloadTimeoutMinutes", json!(minutes))
                    .await
            }
            "set_auto_check" => {
                a.count(1, 2)?;
                let minutes = a.integer(1, Some(360))?;
                ensure(
                    [60, 360, 720, 1440].contains(&minutes),
                    "检查间隔应为 1、6、12 或 24 小时",
                )?;
                let _guard = self.mutation.lock().await;
                let mut settings = load_settings(&self.host)?;
                settings["autoCheck"] = json!(a.boolean(0, None)?);
                settings["autoCheckMinutes"] = json!(minutes);
                atomic_json(&self.host.settings(), &settings)?;
                Ok(settings)
            }
            "get_repository_map_status" => {
                a.count(0, 0)?;
                Ok(self.state.lock().await.map.status())
            }
            "update_repository_map" => {
                a.count(0, 0)?;
                self.update_map().await
            }
            "get_open_source_notices" => {
                a.count(0, 0)?;
                read_json(
                    &self.host.plugin_dir.join("dist/notices.json"),
                    16 * 1024 * 1024,
                )
            }
            "list_sideload_plugins" => {
                a.count(0, 1)?;
                self.list_plugins(a.boolean(0, Some(false))?).await
            }
            "set_binding" => {
                a.count(2, 2)?;
                self.set_binding(a.string(0)?, Some(a.string(1)?)).await
            }
            "clear_binding" => {
                a.count(1, 1)?;
                self.set_binding(a.string(0)?, None).await
            }
            "authorize_plugin_mutation" => {
                a.count(1, 1)?;
                self.authorize(a.string(0)?).await
            }
            "begin_store_ownership" => {
                a.count(4, 4)?;
                self.begin_ownership(a.string(0)?, a.string(1)?, a.string(2)?, a.string(3)?)
                    .await
            }
            "ownership_native_step" => {
                a.count(2, 2)?;
                self.ownership_step(a.string(0)?, a.string(1)?).await
            }
            "search_repositories" => {
                a.count(1, 1)?;
                crate::metadata::search(&self.transport()?, a.string(0)?, &self.stop).await
            }
            "probe_repository" => {
                a.count(1, 1)?;
                self.probe_repo(a.string(0)?).await
            }
            "list_releases" => {
                a.count(1, 2)?;
                self.read_releases(a.string(0)?, a.optional(1)?.unwrap_or(""))
                    .await
            }
            "get_plugin_releases" => {
                a.count(4, 5)?;
                self.get_plugin_releases(
                    a.string(0)?,
                    a.value(1),
                    a.string(2)?,
                    a.value(3),
                    a.boolean(4, Some(false))?,
                )
                .await
            }
            "set_skipped_release" => {
                a.count(3, 3)?;
                self.skip_release(a.string(0)?, a.string(1)?, a.boolean(2, None)?)
                    .await
            }
            "check_updates" => {
                a.count(0, 0)?;
                self.check_updates("manual").await
            }
            "prepare_install" => {
                a.count(3, 6)?;
                self.prepare_install(
                    a.string(0)?,
                    a.string(1)?,
                    a.string(2)?,
                    a.optional(3)?,
                    a.optional(4)?,
                    a.optional(5)?.unwrap_or("install"),
                )
                .await
            }
            "get_operation" => {
                a.count(0, 0)?;
                self.operation_status().await
            }
            "continue_prepared" => {
                a.count(1, 1)?;
                self.reuse_download(a.string(0)?, false).await
            }
            "retry_download" => {
                a.count(1, 1)?;
                self.reuse_download(a.string(0)?, true).await
            }
            "submit_prepared" | "claim_uninstall" | "complete_uninstall" => {
                a.count(1, 1)?;
                self.submit_operation(
                    a.string(0)?,
                    method == "complete_uninstall",
                    method == "claim_uninstall",
                )
                .await
            }
            "record_install_event" => {
                a.count(3, 5)?;
                self.install_event(
                    a.string(0)?,
                    a.string(1)?,
                    a.string(2)?,
                    a.optional(3)?.unwrap_or(""),
                    a.optional(4)?.unwrap_or(""),
                )
                .await
            }
            "cancel_operation" => {
                a.count(1, 1)?;
                self.cancel_operation(a.string(0)?).await
            }
            "dismiss_operation" | "close_operation" => {
                a.count(1, 1)?;
                self.finish_operation(a.string(0)?, method == "dismiss_operation")
                    .await
            }
            "delete_install_package" => {
                a.count(1, 1)?;
                self.delete_package(a.string(0)?).await
            }
            "delete_backups" => {
                a.count(0, 0)?;
                self.cleanup_backups(false).await
            }
            "get_self_update_status" => {
                a.count(0, 1)?;
                self.self_status(a.value(0)).await
            }
            "check_self_update" => {
                a.count(0, 0)?;
                self.check_self().await
            }
            "prepare_self_update" => {
                a.count(2, 2)?;
                self.prepare_self(a.string(0)?, a.string(1)?).await
            }
            "submit_self_update" => {
                a.count(2, 2)?;
                self.submit_self(a.string(0)?, a.value(1)).await
            }
            "cancel_self_update" => {
                a.count(1, 1)?;
                self.cancel_self(a.string(0)?).await
            }
            "record_self_update_event" => {
                a.count(2, 4)?;
                self.self_event(
                    a.string(0)?,
                    a.string(1)?,
                    a.optional(2)?.unwrap_or(""),
                    a.optional(3)?.unwrap_or(""),
                )
                .await
            }
            "get_diagnostics_addresses" => {
                a.count(0, 0)?;
                Ok(json!({"addresses":crate::sharing::addresses()}))
            }
            "get_diagnostics_share" => {
                a.count(0, 0)?;
                Ok(self.sharing.status().await)
            }
            "start_diagnostics_share" => {
                a.count(2, 4)?;
                self.start_share(a.string(0)?, a.string(1)?, a.optional(2)?, a.value(3))
                    .await
            }
            "stop_diagnostics_share" => {
                a.count(1, 1)?;
                ensure(valid_id(a.string(0)?), "领取请求无效")?;
                Ok(self.sharing.stop(Some(a.string(0)?)).await)
            }
            "record_diagnostic_events" => {
                a.count(1, 2)?;
                self.diagnostics
                    .record_batch(a.value(0), a.integer(1, Some(0))?)
            }
            "get_diagnostics_status" => {
                a.count(0, 0)?;
                Ok(self.diagnostics.status())
            }
            "export_diagnostics" => {
                a.count(0, 1)?;
                self.export_diagnostics(a.value(0)).await
            }
            _ => Err(Error::invalid("未知的后端方法")),
        }
    }
}
