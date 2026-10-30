use crate::{common::*, metadata::parse_repo, scoped::Dir};
use serde_json::{Value, json};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    path::{Path, PathBuf},
};
pub const MAP_URL: &str =
    "https://raw.githubusercontent.com/ZacharyQin/decky-sideloader/main/data/repositories.json";
pub const REGISTRY_URL: &str =
    "https://raw.githubusercontent.com/SteamDeckHomebrew/decky-plugin-database/main/.gitmodules";
pub const STORE_URL: &str = "https://plugins.deckbrew.xyz/plugins";
pub fn detect_repo(package: &Value) -> Option<String> {
    let repository = &package["repository"];
    let raw = if repository.is_string() {
        text(repository)
    } else {
        text(&repository["url"])
    };
    parse_repo(raw).or_else(|| parse_repo(text(&package["homepage"])))
}
pub fn scan(home: &Path) -> Result<Vec<Value>> {
    let root = home.join("plugins");
    if !root.exists() {
        return Ok(vec![]);
    }
    let mut paths: Vec<_> = fs::read_dir(&root)?.collect::<std::io::Result<Vec<_>>>()?;
    paths.sort_by_key(|e| e.file_name());
    let mut rows = Vec::new();
    for child in paths {
        if !child.file_type()?.is_dir() {
            continue;
        }
        let Ok(dir) = Dir::open(&child.path()) else {
            continue;
        };
        let Ok(data) = dir.bytes("plugin.json", 1024 * 1024) else {
            continue;
        };
        let Ok(plugin) = serde_json::from_slice::<Value>(&data) else {
            continue;
        };
        let name = text(&plugin["name"]);
        if name.trim().is_empty() {
            continue;
        }
        let package = dir
            .bytes("package.json", 1024 * 1024)
            .ok()
            .and_then(|d| serde_json::from_slice::<Value>(&d).ok())
            .unwrap_or(Value::Null);
        rows.push(json!({"name":name,"folder":child.file_name().to_string_lossy(),"version":string(&package["version"]),"package_name":string(&package["name"]),"author":text(&plugin["author"]),"repo":detect_repo(&package)}));
        ensure(rows.len() <= 4096, "插件清单超过上限")?;
    }
    Ok(rows)
}
pub fn apply_bindings(rows: &mut [Value], bindings: &Value) {
    for row in rows {
        let binding = &bindings[text(&row["name"])];
        if binding.is_object() && binding.get("folder").is_some() {
            let package_repo = parse_repo(text(&row["repo"]));
            let saved = &binding["package_repo"];
            let saved_valid = saved.is_null()
                || saved.is_string() && parse_repo(text(saved)).as_deref() == saved.as_str();
            let changed = binding["folder"] != row["folder"]
                || binding["package_name"] != row["package_name"]
                || !saved_valid
                || saved
                    .as_str()
                    .zip(package_repo.as_deref())
                    .is_some_and(|(a, b)| !a.eq_ignore_ascii_case(b));
            row["binding_changed"] = json!(changed);
            row["binding_confirmed"] = json!(!changed);
        }
        if binding["disabled"] == true {
            row["repo"] = Value::Null;
            row["repo_source"] = json!("disabled");
        } else if binding["repo"].is_string() && !text(&binding["repo"]).trim().is_empty() {
            row["repo"] = json!(text(&binding["repo"]).trim());
            row["repo_source"] = json!("user");
        } else {
            row["repo_source"] = if row["repo"].is_string() {
                json!("package.json")
            } else {
                Value::Null
            };
        }
    }
}
struct Unique(Value);
impl<'de> serde::Deserialize<'de> for Unique {
    fn deserialize<D: serde::Deserializer<'de>>(d: D) -> std::result::Result<Self, D::Error> {
        struct Visitor;
        impl<'de> serde::de::Visitor<'de> for Visitor {
            type Value = Unique;
            fn expecting(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result {
                write!(f, "JSON without duplicate keys")
            }
            fn visit_bool<E: serde::de::Error>(self, v: bool) -> std::result::Result<Unique, E> {
                Ok(Unique(json!(v)))
            }
            fn visit_i64<E: serde::de::Error>(self, v: i64) -> std::result::Result<Unique, E> {
                Ok(Unique(json!(v)))
            }
            fn visit_u64<E: serde::de::Error>(self, v: u64) -> std::result::Result<Unique, E> {
                Ok(Unique(json!(v)))
            }
            fn visit_f64<E: serde::de::Error>(self, v: f64) -> std::result::Result<Unique, E> {
                Ok(Unique(json!(v)))
            }
            fn visit_str<E: serde::de::Error>(self, v: &str) -> std::result::Result<Unique, E> {
                Ok(Unique(json!(v)))
            }
            fn visit_string<E: serde::de::Error>(
                self,
                v: String,
            ) -> std::result::Result<Unique, E> {
                Ok(Unique(json!(v)))
            }
            fn visit_unit<E: serde::de::Error>(self) -> std::result::Result<Unique, E> {
                Ok(Unique(Value::Null))
            }
            fn visit_seq<A: serde::de::SeqAccess<'de>>(
                self,
                mut seq: A,
            ) -> std::result::Result<Unique, A::Error> {
                let mut out = Vec::new();
                while let Some(v) = seq.next_element::<Unique>()? {
                    out.push(v.0);
                }
                Ok(Unique(json!(out)))
            }
            fn visit_map<A: serde::de::MapAccess<'de>>(
                self,
                mut map: A,
            ) -> std::result::Result<Unique, A::Error> {
                let mut out = serde_json::Map::new();
                while let Some((k, v)) = map.next_entry::<String, Unique>()? {
                    if out.insert(k, v.0).is_some() {
                        return Err(serde::de::Error::custom("duplicate JSON key"));
                    }
                }
                Ok(Unique(Value::Object(out)))
            }
        }
        d.deserialize_any(Visitor)
    }
}
pub fn decode_map(data: &[u8]) -> Result<Value> {
    ensure(data.len() <= 1024 * 1024, "仓库映射表格式无效或不受支持")?;
    let Unique(value) = serde_json::from_slice(data)
        .map_err(|_| Error::new("invalid_response", "仓库映射表格式无效或不受支持"))?;
    let valid = (|| -> Option<()> {
        let keys = value
            .as_object()?
            .keys()
            .map(String::as_str)
            .collect::<BTreeSet<_>>();
        if keys != BTreeSet::from(["schema_version", "revision", "updated_at", "repositories"]) {
            return None;
        }
        if value["schema_version"] != 1
            || !value["revision"]
                .as_u64()
                .is_some_and(|v| (1..=9007199254740991).contains(&v))
        {
            return None;
        }
        let date = text(&value["updated_at"]);
        if chrono::NaiveDate::parse_from_str(date, "%Y-%m-%d")
            .ok()?
            .format("%Y-%m-%d")
            .to_string()
            != date
        {
            return None;
        }
        let repositories = value["repositories"].as_object()?;
        if repositories.len() > 4096 {
            return None;
        }
        for (name, repo) in repositories {
            if name.is_empty()
                || name.chars().count() > 128
                || name.trim() != name
                || name.chars().any(|c| c.is_control() || "/\\".contains(c))
                || !repo.is_string()
                || text(repo).len() > 256
                || parse_repo(text(repo)).as_deref() != repo.as_str()
                || text(repo).eq_ignore_ascii_case("steamdeckhomebrew/decky-plugin-template")
            {
                return None;
            }
        }
        Some(())
    })();
    ensure(valid.is_some(), "仓库映射表格式无效或不受支持")?;
    Ok(value)
}
#[derive(Clone)]
pub struct RepositoryMap {
    pub value: Value,
    pub bundled: Value,
    pub source: String,
    pub warning: Option<String>,
    path: PathBuf,
}
impl RepositoryMap {
    pub fn load(host: &Host) -> Result<Self> {
        let bundled = decode_map(&read_file(
            &host.plugin_dir.join("data/repositories.json"),
            1024 * 1024,
        )?)?;
        let path = host.runtime_dir.join("repository-map.json");
        let mut result = Self {
            value: bundled.clone(),
            bundled,
            source: "bundled".into(),
            warning: None,
            path,
        };
        match fs::symlink_metadata(&result.path) {
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
            _ => match read_file(&result.path, 1024 * 1024).and_then(|d| decode_map(&d)) {
                Ok(saved) if saved["revision"].as_u64() > result.value["revision"].as_u64() => {
                    result.value = saved;
                    result.source = "downloaded".into();
                }
                Ok(saved)
                    if saved["revision"] != result.value["revision"] || saved == result.value => {}
                _ => result.warning = Some("已下载的仓库映射表不可用，正在使用随包版本".into()),
            },
        }
        Ok(result)
    }
    pub fn status(&self) -> Value {
        json!({"revision":self.value["revision"],"updated_at":self.value["updated_at"],"count":self.value["repositories"].as_object().map_or(0,|v|v.len()),"source":self.source,"warning":self.warning})
    }
    pub fn install(&mut self, data: &[u8]) -> Result<Value> {
        let candidate = decode_map(data)?;
        ensure(
            candidate["revision"].as_u64() >= self.value["revision"].as_u64(),
            "远端仓库映射表版本较旧，已保留当前版本",
        )?;
        if candidate["revision"] == self.value["revision"] {
            ensure(
                candidate == self.value,
                "仓库映射表修订号未变化但内容不同，已保留当前版本",
            )?;
            if self.warning.is_none() {
                let mut status = self.status();
                status["changed"] = json!(false);
                return Ok(status);
            }
        }
        let changed = candidate != self.value;
        atomic_json(&self.path, &candidate)?;
        self.source = if candidate["revision"].as_u64() > self.bundled["revision"].as_u64() {
            "downloaded"
        } else {
            "bundled"
        }
        .into();
        self.value = candidate;
        self.warning = None;
        let mut status = self.status();
        status["changed"] = json!(changed);
        Ok(status)
    }
}
pub fn resolve_repository(row: &mut Value, memory: &Value, repositories: &Value) {
    let source = text(&row["repo_source"]);
    if ["user", "disabled"].contains(&source) || row["is_self"] == true {
        return;
    }
    if memory["repo_source"] == "user"
        && memory["repo"].is_string()
        && row["memory_changed"] != true
    {
        row["repo"] = memory["repo"].clone();
        row["repo_source"] = json!("remembered");
    } else if repositories.get(text(&row["name"])).is_some() {
        row["repo"] = repositories[text(&row["name"])].clone();
        row["repo_source"] = json!("mapping");
    } else if row["repo_source"].is_null() && memory["repo"].is_string() {
        row["repo"] = memory["repo"].clone();
        row["repo_source"] = json!("remembered");
    }
}
pub fn names(value: &Value) -> Result<BTreeSet<String>> {
    let rows = value
        .as_array()
        .ok_or_else(|| Error::invalid("Loader 名称清单无效"))?;
    ensure(
        rows.len() <= 4096 && rows.iter().all(|v| valid_text(v, 256)),
        "Loader 名称清单无效",
    )?;
    let names: BTreeSet<_> = rows.iter().filter_map(string).collect();
    ensure(names.len() == rows.len(), "Loader 名称清单无效")?;
    Ok(names)
}
pub fn frozen(host: &Host) -> Result<BTreeSet<String>> {
    let value = read_json(&host.home.join("settings/loader.json"), 4 * 1024 * 1024)
        .map_err(|_| Error::invalid("无法核验 Loader 冻结设置，请恢复来源读取后重试"))?;
    names(value.get("frozenPlugins").unwrap_or(&json!([])))
        .map_err(|_| Error::invalid("无法核验 Loader 冻结设置，请恢复来源读取后重试"))
}
pub fn ledger(host: &Host) -> Result<Value> {
    let value = optional_json(&host.settings_dir.join("ownership.json"), 1024 * 1024)
        .map_err(|_| Error::invalid("接管记录损坏，已阻止管理操作"))?
        .unwrap_or_else(|| json!({"version":1,"entries":[]}));
    validate_ledger(&value)?;
    Ok(value)
}
pub fn validate_ledger(value: &Value) -> Result<()> {
    let mut identities = BTreeSet::new();
    let mut ids = BTreeSet::new();
    ensure(
        value["version"] == 1
            && value.as_object().is_some_and(|m| m.len() == 2)
            && value["entries"].is_array()
            && array(&value["entries"]).len() <= 512,
        "接管记录损坏，已阻止管理操作",
    )?;
    for row in array(&value["entries"]) {
        let name = text(&row["name"]);
        let folder = text(&row["folder"]);
        ensure(
            row.as_object().is_some_and(|m| m.len() == 7)
                && valid_text(&row["name"], 256)
                && valid_text(&row["folder"], 256)
                && safe_component(folder)
                && valid_id(text(&row["id"]))
                && row["repo"].is_string()
                && parse_repo(text(&row["repo"])).as_deref() == row["repo"].as_str()
                && text(&row["repo"]).len() <= 256
                && row["prior_frozen"].is_boolean()
                && ["takeover", "return"].contains(&text(&row["action"]))
                && ["prepared", "writing", "confirming", "active", "released"]
                    .contains(&text(&row["state"]))
                && !(row["state"] == "active" && row["action"] != "takeover")
                && !(row["state"] == "released" && row["action"] != "return")
                && identities.insert((name, folder))
                && ids.insert(text(&row["id"])),
            "接管记录损坏，已阻止管理操作",
        )?;
    }
    Ok(())
}
pub fn ownership_entry<'a>(plugin: &Value, ledger: &'a Value) -> Option<&'a Value> {
    array(&ledger["entries"])
        .iter()
        .find(|r| r["name"] == plugin["name"] && r["folder"] == plugin["folder"])
}
pub fn resolve_ownership(
    rows: &mut [Value],
    frozen: Option<&BTreeSet<String>>,
    ledger: &Value,
    error: Option<&str>,
) {
    let mut counts = BTreeMap::new();
    for r in rows.iter() {
        *counts.entry(text(&r["name"]).to_owned()).or_insert(0) += 1;
    }
    for row in rows {
        let entry = ownership_entry(row, ledger).cloned().unwrap_or(Value::Null);
        let (mut state, mut eligible) = ("unknown", false);
        if row["is_self"] == true || counts.get(text(&row["name"])) != Some(&1) {
            state = "readonly";
        } else if error.is_some() || row["memory_changed"] == true {
        } else if !entry.is_null() && !["active", "released"].contains(&text(&entry["state"])) {
            state = "pending";
        } else if entry["state"] == "released" {
            state = if row["loader_managed"] == true {
                "store"
            } else if row["loader_managed"] == false {
                "nonstore"
            } else {
                "unknown"
            };
        } else if entry["state"] == "active" {
            state = if frozen.is_some_and(|v| v.contains(text(&row["name"]))) {
                "owned"
            } else {
                "pending"
            };
            eligible = state == "owned";
            row["repo"] = entry["repo"].clone();
            row["repo_source"] = json!("user");
        } else if !row["memory_error"].is_null() {
        } else if row["loader_managed"] == true {
            state = "store";
        } else if row["loader_managed"] == false {
            state = "nonstore";
            eligible = true;
        }
        row["ownership"] = json!({"state":state,"eligible":eligible,"intent":entry,"error":error.map(|s|json!(s)).unwrap_or_else(||row["memory_error"].clone())});
        row["sideload"] = json!(eligible);
    }
}
pub fn identity(plugin: &Value) -> Value {
    json!({"name":plugin["name"],"folder":plugin["folder"],"repo":plugin["repo"],"current":plugin["version"],"ownership_id":plugin["ownership"]["intent"]["id"]})
}
pub fn snapshot_matches(snapshot: &Value, plugin: &Value) -> bool {
    plugin["ownership"]["eligible"] == true
        && identity(plugin)
            .as_object()
            .unwrap()
            .iter()
            .all(|(k, v)| snapshot[k] == *v)
}
pub fn valid_snapshot(v: &Value) -> bool {
    v.is_object()
        && v["name"].is_string()
        && v["repo"].is_string()
        && chrono::DateTime::parse_from_rfc3339(text(&v["checkedAt"])).is_ok()
        && ["folder", "current", "ownership_id"]
            .iter()
            .all(|k| v[k].is_null() || v[k].is_string())
        && v["releases"].is_array()
        && array(&v["releases"]).len() <= 1000
        && array(&v["releases"]).iter().all(|r| {
            ["tag", "title", "body", "published_at", "repo"]
                .iter()
                .all(|k| r[k].is_string())
                && r["repo"] == v["repo"]
                && r["has_zip"].is_boolean()
                && r["prerelease"].is_boolean()
                && (r["comparison"].is_null()
                    || r["comparison"]
                        .as_i64()
                        .is_some_and(|n| (-1..=1).contains(&n)))
                && r["zips"].is_array()
                && array(&r["zips"])
                    .iter()
                    .all(|z| z["name"].is_string() && z["url"].is_string())
                && (r["version_evidence"].is_null() || {
                    let evidence = &r["version_evidence"];
                    evidence.as_object().is_some_and(|m| {
                        m.len() == 3
                            && ["package_state", "package_version", "git_version"]
                                .iter()
                                .all(|k| m.contains_key(*k))
                    }) && ["skipped", "unknown", "semver", "raw", "absent"]
                        .contains(&text(&evidence["package_state"]))
                        && ["package_version", "git_version"]
                            .iter()
                            .all(|k| evidence[k].is_null() || evidence[k].is_string())
                })
        })
}
pub struct ReleaseCache {
    path: PathBuf,
    entries: Vec<Value>,
}
impl ReleaseCache {
    pub fn load(host: &Host) -> Self {
        let path = host.runtime_dir.join("cache/releases.json");
        let rows = read_json(&path, 16 * 1024 * 1024).unwrap_or(Value::Null);
        Self {
            path,
            entries: array(&rows)
                .iter()
                .filter(|v| valid_snapshot(v))
                .cloned()
                .collect(),
        }
    }
    pub fn get(&self, plugin: &Value) -> Option<Value> {
        self.entries
            .iter()
            .find(|v| v["name"] == plugin["name"] && snapshot_matches(v, plugin))
            .cloned()
    }
    pub fn put(&mut self, plugin: &Value, releases: &[Value], checked: &str) -> Value {
        let mut snapshot = identity(plugin);
        snapshot["checkedAt"] = json!(checked);
        snapshot["releases"] = json!(releases);
        if serde_json::to_vec_pretty(&json!([snapshot.clone()]))
            .is_ok_and(|v| v.len() <= 16 * 1024 * 1024)
        {
            self.entries.retain(|v| v["name"] != plugin["name"]);
            self.entries.push(snapshot.clone());
            while serde_json::to_vec_pretty(&self.entries).is_ok_and(|v| v.len() > 16 * 1024 * 1024)
            {
                self.entries.remove(0);
            }
            let _ = atomic_json(&self.path, &json!(self.entries));
        }
        snapshot
    }
}
#[derive(Clone)]
pub struct Catalog {
    path: PathBuf,
    pub value: Value,
    pub error: Option<String>,
    pub registry: Vec<(String, String)>,
}
impl Catalog {
    pub fn load(host: &Host) -> Self {
        let path = host.settings_dir.join("catalog.json");
        let mut out = Self {
            path,
            value: json!({"version":1,"timestamp":null,"names":[],"plugins":[],"store_history":[]}),
            error: None,
            registry: vec![],
        };
        match optional_json(&out.path, 4 * 1024 * 1024) {
            Ok(Some(mut v)) => {
                if validate_catalog(&v).is_ok() {
                    if v.get("version").is_none() {
                        v["version"] = json!(1);
                        v["plugins"] = json!([]);
                        v["store_history"] = v["names"].clone();
                    }
                    out.value = v;
                } else {
                    out.error = Some("本地插件记录无法读取，请核对后重试".into());
                }
            }
            Ok(None) => {}
            Err(_) => out.error = Some("本地插件记录无法读取，请核对后重试".into()),
        }
        out
    }
    pub fn memory(&self, plugin: &Value) -> Value {
        let related: Vec<_> = array(&self.value["plugins"])
            .iter()
            .filter(|v| v["name"] == plugin["name"])
            .collect();
        let row = related
            .iter()
            .find(|v| {
                v["folder"] == plugin["folder"] && v["package_name"] == plugin["package_name"]
            })
            .copied();
        let package_repo = detect_repo(&json!({"repository":plugin["repo"]}));
        let changed = !related.is_empty() && row.is_none()
            || row.is_some_and(|r| {
                r["package_repo"]
                    .as_str()
                    .zip(package_repo.as_deref())
                    .is_some_and(|(a, b)| !a.eq_ignore_ascii_case(b))
            });
        let known = array(&self.value["store_history"]).contains(&plugin["name"]);
        json!({"in_store":if changed{Value::Null}else if known{json!(true)}else{row.map_or(Value::Null,|r|r["in_store"].clone())},"repo":if changed{Value::Null}else{row.map_or(Value::Null,|r|r["repo"].clone())},"repo_source":if changed{Value::Null}else{row.map_or(Value::Null,|r|r["repo_source"].clone())},"changed":changed})
    }
    pub fn remember(&mut self, plugin: &Value, repo: &str, source: &str) -> Result<()> {
        let parsed = parse_repo(repo).ok_or_else(|| Error::invalid("仓库记录无效"))?;
        ensure(["user", "package.json"].contains(&source), "仓库记录无效")?;
        let old = self.memory(plugin);
        let row = json!({"name":plugin["name"],"folder":plugin["folder"],"package_name":plugin["package_name"],"package_repo":parse_repo(text(&plugin["repo"])),"repo":parsed,"repo_source":source,"in_store":if array(&self.value["store_history"]).contains(&plugin["name"]){json!(true)}else{old["in_store"].clone()},"timestamp":seconds()});
        let mut value = self.value.clone();
        let mut rows: Vec<_> = array(&value["plugins"])
            .iter()
            .filter(|r| r["name"] != plugin["name"])
            .cloned()
            .collect();
        rows.push(row);
        value["plugins"] = json!(rows);
        self.save(value)
    }
    fn save(&mut self, value: Value) -> Result<()> {
        ensure(
            self.error.is_none(),
            self.error.as_deref().unwrap_or("本地插件记录不可用"),
        )?;
        validate_catalog(&value)?;
        ensure(
            serde_json::to_vec_pretty(&value)?.len() <= 4 * 1024 * 1024,
            "本地插件记录已达容量上限",
        )?;
        atomic_json(&self.path, &value)?;
        self.value = value;
        Ok(())
    }
    pub fn refresh(&mut self, store: &BTreeSet<String>, plugins: &[Value]) -> Result<()> {
        ensure(!store.is_empty(), "商店目录为空、格式异常或超过上限")?;
        let mut value = self.value.clone();
        let mut rows = array(&value["plugins"]).to_vec();
        let mut history: BTreeSet<_> = array(&value["store_history"])
            .iter()
            .filter_map(string)
            .collect();
        history.extend(store.iter().cloned());
        for plugin in plugins {
            if plugins
                .iter()
                .filter(|p| p["name"] == plugin["name"])
                .count()
                != 1
                || self.memory(plugin)["changed"] == true
            {
                continue;
            }
            let previous = rows
                .iter()
                .find(|v| {
                    v["name"] == plugin["name"]
                        && v["folder"] == plugin["folder"]
                        && v["package_name"] == plugin["package_name"]
                })
                .cloned()
                .unwrap_or(Value::Null);
            let repo = parse_repo(text(&plugin["repo"]));
            let user = previous["repo_source"] == "user";
            let row = json!({"name":plugin["name"],"folder":plugin["folder"],"package_name":plugin["package_name"],"package_repo":repo.clone().map(|v|json!(v)).unwrap_or(previous["package_repo"].clone()),"repo":if user{previous["repo"].clone()}else{repo.clone().map(|v|json!(v)).unwrap_or(previous["repo"].clone())},"repo_source":if user{json!("user")}else if repo.is_some(){json!("package.json")}else{previous["repo_source"].clone()},"in_store":history.contains(text(&plugin["name"])),"timestamp":seconds()});
            rows.retain(|r| r["name"] != row["name"] || r["folder"] != row["folder"]);
            rows.push(row);
        }
        value["names"] = json!(store);
        value["timestamp"] = json!(seconds());
        value["store_history"] = json!(history);
        value["plugins"] = json!(rows);
        self.save(value)
    }
    pub fn status(&self, live: bool, warning: Option<String>) -> Value {
        let cached = self.value["timestamp"].is_number();
        json!({"source":if live{"live"}else if cached{"cache"}else if !self.registry.is_empty(){"registry"}else{"unavailable"},"timestamp":if !cached&&!self.registry.is_empty(){json!(seconds())}else{self.value["timestamp"].clone()},"stale":cached&&(seconds()-self.value["timestamp"].as_f64().unwrap_or(0.0)>=300.0||warning.is_some()),"warning":warning.or(self.error.clone())})
    }
}
fn validate_catalog(value: &Value) -> Result<()> {
    ensure(
        value.is_object() && value["names"].is_array(),
        "本地插件记录无效",
    )?;
    if value.get("version").is_some() {
        ensure(
            value["version"] == 1
                && value.as_object().unwrap().len() == 5
                && value["plugins"].is_array()
                && value["store_history"].is_array(),
            "本地插件记录无效",
        )?;
    } else {
        ensure(value.as_object().unwrap().len() == 2, "本地插件记录无效")?;
    }
    let timestamp = &value["timestamp"];
    ensure(
        timestamp.is_null() && array(&value["names"]).is_empty()
            || timestamp
                .as_f64()
                .is_some_and(|n| n.is_finite() && n >= 0.0 && n <= seconds()),
        "本地插件记录无效",
    )?;
    names(&value["names"])?;
    if value.get("store_history").is_some() {
        names(&value["store_history"])?;
    }
    let mut keys = BTreeSet::new();
    ensure(array(&value["plugins"]).len() <= 512, "本地插件记录无效")?;
    for row in array(&value["plugins"]) {
        ensure(
            row.as_object().is_some_and(|m| m.len() == 8)
                && valid_text(&row["name"], 256)
                && safe_component(text(&row["folder"]))
                && keys.insert((text(&row["name"]), text(&row["folder"])))
                && row["timestamp"]
                    .as_f64()
                    .is_some_and(|n| n.is_finite() && n >= 0.0 && n <= seconds())
                && (row["repo_source"].is_null()
                    || row["repo_source"]
                        .as_str()
                        .is_some_and(|s| ["user", "package.json"].contains(&s)))
                && (row["package_name"].is_null() || valid_text(&row["package_name"], 256))
                && (row["in_store"].is_null() || row["in_store"].is_boolean()),
            "本地插件记录无效",
        )?;
        for key in ["package_repo", "repo"] {
            ensure(
                row[key].is_null()
                    || row[key].is_string()
                        && parse_repo(text(&row[key])).as_deref() == row[key].as_str(),
                "本地插件记录无效",
            )?;
        }
    }
    Ok(())
}
pub fn parse_registry(raw: &str) -> Result<Vec<(String, String)>> {
    let mut rows = Vec::new();
    let mut current: Option<String> = None;
    let mut fields: BTreeMap<String, String> = BTreeMap::new();
    let section = regex::Regex::new(r#"^\[submodule "(plugins/[A-Za-z0-9_.-]+)"\]$"#).unwrap();
    let mut sections = 0;
    let finish = |path: &str,
                  fields: &BTreeMap<String, String>,
                  rows: &mut Vec<(String, String)>|
     -> Result<()> {
        ensure(
            fields.get("path").is_some_and(|v| v == path)
                && fields.get("url").is_some()
                && regex::Regex::new(r"^plugins/[A-Za-z0-9_-][A-Za-z0-9_.-]{0,127}$")
                    .unwrap()
                    .is_match(path),
            "官方登记数据库格式异常或不完整",
        )?;
        let raw = &fields["url"];
        let normalized = raw
            .strip_prefix("git@github.com:")
            .map_or_else(|| raw.clone(), |r| format!("https://github.com/{r}"));
        let url = url::Url::parse(&normalized)
            .map_err(|_| Error::invalid("官方登记数据库包含损坏仓库地址"))?;
        ensure(
            url.scheme() == "https"
                && url.host_str().is_some()
                && url.username().is_empty()
                && url.password().is_none()
                && url.query().is_none()
                && url.fragment().is_none()
                && !normalized.chars().any(char::is_whitespace),
            "官方登记数据库包含损坏仓库地址",
        )?;
        if url.host_str() == Some("github.com") {
            let repo = parse_repo(&normalized)
                .ok_or_else(|| Error::invalid("官方登记数据库包含损坏 GitHub 仓库地址"))?;
            ensure(
                url.path().trim_matches('/').split('/').count() == 2,
                "官方登记数据库包含损坏 GitHub 仓库地址",
            )?;
            if !repo.eq_ignore_ascii_case("steamdeckhomebrew/decky-plugin-template") {
                rows.push((
                    path.strip_prefix("plugins/").unwrap().to_owned(),
                    repo.to_lowercase(),
                ));
            }
        }
        Ok(())
    };
    for line in raw.lines() {
        let line = line.trim();
        if line.is_empty() || line.starts_with(['#', ';']) {
            continue;
        }
        if let Some(c) = section.captures(line) {
            if let Some(path) = current.take() {
                finish(&path, &fields, &mut rows)?;
            }
            fields.clear();
            current = Some(c[1].into());
            sections += 1;
            ensure(sections <= 4096, "官方登记数据库超过上限")?;
        } else {
            ensure(current.is_some(), "官方登记数据库格式异常或不完整")?;
            let (k, v) = line
                .split_once('=')
                .ok_or_else(|| Error::invalid("官方登记数据库格式异常或不完整"))?;
            ensure(
                ["path", "url", "branch"].contains(&k.trim())
                    && !v.trim().is_empty()
                    && fields.insert(k.trim().into(), v.trim().into()).is_none(),
                "官方登记数据库格式异常或不完整",
            )?;
        }
    }
    if let Some(path) = current {
        finish(&path, &fields, &mut rows)?;
    }
    ensure(sections > 0, "官方登记数据库为空")?;
    rows.sort();
    rows.dedup();
    Ok(rows)
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn duplicate_map_keys_fail() {
        assert!(decode_map(br#"{"schema_version":1,"revision":1,"updated_at":"2026-10-28","repositories":{"a":"x/y","a":"x/z"}}"#).is_err());
    }
    #[test]
    fn identities_include_owner() {
        let p = json!({"name":"x","folder":"x","repo":"a/b","version":"1","ownership":{"eligible":true,"intent":{"id":"a"}}});
        let mut snapshot = identity(&p);
        assert!(snapshot_matches(&snapshot, &p));
        snapshot["ownership_id"] = json!("b");
        assert!(!snapshot_matches(&snapshot, &p));
    }
}
