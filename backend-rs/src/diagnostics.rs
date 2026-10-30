use crate::{common::*, engine::Engine, scoped::Dir};
use serde_json::{Value, json};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    io::{Read, Write},
    os::unix::fs::{MetadataExt, PermissionsExt},
    path::{Path, PathBuf},
    sync::{Arc, LazyLock, Mutex},
};
use tokio_util::sync::CancellationToken;
static SCHEMA: LazyLock<Value> =
    LazyLock::new(|| serde_json::from_str(include_str!("diagnostic_schema.json")).unwrap());
pub const PACKAGE_BYTES: u64 = 6 * 1024 * 1024;
const LOG_BYTES: usize = 1024 * 1024;
const LOG_MARKER: &[u8] = b"{\"event\":\"log_header\",\"format\":1}\n";
pub const ZIP_MARKER: &[u8] = b"decky-sideloader-diagnostics-v1\n";
const LOG_NAMES: [&str; 4] = [
    "active.jsonl",
    "history-1.jsonl",
    "history-2.jsonl",
    "history-3.jsonl",
];
tokio::task_local! {pub static REQUEST:Value;}
fn member(v: &Value, key: &str) -> bool {
    array(v).iter().any(|s| s == key)
}
pub fn identifier(kind: &str, value: &str) -> Value {
    if value.is_empty() || value.chars().count() > 4096 {
        Value::Null
    } else {
        json!(&sha(format!("{kind}:{value}"))[..32])
    }
}
fn version(v: &Value) -> bool {
    v.as_str().is_some_and(|s| {
        regex::Regex::new(
            r"^v?\d{1,4}(?:\.\d{1,4}){0,3}(?:-(?:pre|debug|beta|alpha|rc)(?:\.?\d{1,4})?)?$",
        )
        .unwrap()
        .is_match(s)
    })
}
fn diagnostic_code(value: &Value) -> Option<&str> {
    let value = value.as_str()?;
    let code = match value {
        "filesystem" => "io_failed",
        "invalid_archive" => "package_invalid",
        "network" => "connection_failed",
        "timeout" | "total_timeout" => "network_timeout",
        "server_error" | "http" => "http_failed",
        other => other,
    };
    member(&SCHEMA["enums"]["errorCode"], code).then_some(code)
}
pub fn sanitize_snapshot(value: &Value, depth: usize) -> Value {
    let mut result = json!({});
    if depth > 6 {
        return result;
    }
    let Some(rows) = value.as_object() else {
        return result;
    };
    let schema = &SCHEMA["snapshot"];
    for (key, item) in rows {
        if key == "errorCode" {
            if let Some(code) = diagnostic_code(item) {
                result[key] = json!(code);
            }
            continue;
        }
        let valid = if schema["enums"].get(key).is_some() {
            item.as_str()
                .is_some_and(|v| member(&schema["enums"][key], v))
        } else if member(&schema["nums"], key) {
            item.is_null() || item.as_u64().is_some_and(|n| n <= 9007199254740991)
        } else if member(&schema["bools"], key) {
            item.is_null() || item.is_boolean()
        } else if member(&schema["ids"], key) {
            item.as_str().is_some_and(valid_id)
        } else if member(&schema["hashes"], key) {
            item.as_str().is_some_and(valid_hash)
        } else if member(&schema["versions"], key) {
            version(item)
        } else if schema["metadata"].get(key).is_some() {
            item.as_str().is_some_and(|v| {
                regex::Regex::new(&format!("^(?:{})$", text(&schema["metadata"][key])))
                    .is_ok_and(|r| r.is_match(v))
            })
        } else {
            false
        };
        if valid {
            result[key] = item.clone();
        } else if member(&schema["children"], key) {
            if item.is_object() {
                result[key] = sanitize_snapshot(item, depth + 1);
            } else if let Some(items) = item.as_array() {
                let limit = if key == "frames" {
                    8
                } else if key == "observations" {
                    16
                } else {
                    512
                };
                result[key] = json!(
                    items
                        .iter()
                        .take(limit)
                        .filter(|v| v.is_object())
                        .map(|v| sanitize_snapshot(v, depth + 1))
                        .collect::<Vec<_>>()
                );
                if items.len() > limit {
                    result["truncated"] = json!(items.len() - limit);
                }
            }
        }
    }
    result
}
pub fn sanitize(event: &str, fields: &Value) -> Option<Value> {
    if !member(&SCHEMA["events"], event) {
        return None;
    }
    let mut row = json!({"event":event});
    if let Some(enums) = SCHEMA["enums"].as_object() {
        for (key, allowed) in enums {
            if fields[key]
                .as_str()
                .is_some_and(|v| v.len() <= 64 && member(allowed, v))
            {
                row[key] = fields[key].clone();
            }
        }
    }
    if let Some(code) = diagnostic_code(&fields["errorCode"]) {
        row["errorCode"] = json!(code);
    }
    for key in array(&SCHEMA["counts"]) {
        let key = text(key);
        if fields[key].as_u64().is_some_and(|n| n <= 9007199254740991) {
            row[key] = fields[key].clone();
        }
    }
    for key in array(&SCHEMA["ids"]) {
        let key = text(key);
        if fields[key].as_str().is_some_and(valid_id) {
            row[key] = fields[key].clone();
        }
    }
    for key in array(&SCHEMA["bools"]) {
        let key = text(key);
        if fields[key].is_boolean() {
            row[key] = fields[key].clone();
        }
    }
    if fields["fingerprint"].as_str().is_some_and(valid_hash) {
        row["fingerprint"] = fields["fingerprint"].clone();
    }
    if version(&fields["version"]) {
        row["version"] = fields["version"].clone();
    }
    if fields["frames"].is_array() {
        row["frames"] = sanitize_snapshot(&json!({"frames":fields["frames"]}), 0)["frames"].clone();
    }
    if fields["operation"].is_object() {
        row["operation"] = sanitize_snapshot(&fields["operation"], 0);
    }
    Some(row)
}
fn encoded(value: &Value) -> Result<Vec<u8>> {
    let mut out = serde_json::to_vec(value)?;
    out.push(b'\n');
    Ok(out)
}
pub(crate) fn secure_directory(path: &Path, create: bool) -> Result<Dir> {
    ensure(
        path.is_absolute() && path != Path::new("/"),
        "诊断目录不可用",
    )?;
    let mut current = PathBuf::from("/");
    for part in path.components().skip(1) {
        current.push(part.as_os_str());
        match fs::symlink_metadata(&current) {
            Ok(info) => ensure(
                info.is_dir() && !info.file_type().is_symlink(),
                "诊断目录不可用",
            )?,
            Err(e) if create && e.kind() == std::io::ErrorKind::NotFound => {
                fs::create_dir(&current)?;
                fs::set_permissions(&current, fs::Permissions::from_mode(0o700))?;
            }
            Err(e) => return Err(e.into()),
        }
    }
    let dir = Dir::open(path)?;
    ensure(
        dir.metadata()?.uid() == unsafe { libc::getuid() },
        "诊断目录归属无效",
    )?;
    Ok(dir)
}
fn owned(dir: &Dir, name: &str, marker: &[u8], max: u64) -> Result<Option<fs::Metadata>> {
    let mut file = match dir.file(name) {
        Ok(v) => v,
        Err(_) => return Ok(None),
    };
    let info = file.metadata()?;
    if info.nlink() != 1
        || info.uid() != unsafe { libc::getuid() }
        || info.mode() & 0o777 != 0o600
        || info.len() > max
    {
        return Ok(None);
    }
    let mut data = vec![0; marker.len()];
    if file.read_exact(&mut data).is_err() || data != marker {
        return Ok(None);
    }
    Ok(Some(info))
}
#[derive(Clone)]
pub struct Diagnostics {
    inner: Arc<Mutex<Inner>>,
    host: Host,
    binary: String,
}
struct Inner {
    status: Value,
    health: Value,
    session: String,
    sequence: u64,
}
impl Diagnostics {
    pub fn new(host: &Host, binary: &str) -> Self {
        let result = Self {
            inner: Arc::new(Mutex::new(Inner {
                status: json!({"state":"idle"}),
                health: json!({"dropped":0}),
                session: id(),
                sequence: 0,
            })),
            host: host.clone(),
            binary: binary.into(),
        };
        let _ = result.prune_exports(3);
        result
    }
    #[track_caller]
    pub fn record(&self, event: &str, mut fields: Value) {
        if let Ok(context) = REQUEST.try_with(Clone::clone) {
            if let Some(c) = context.as_object() {
                for (k, v) in c {
                    if fields.get(k).is_none() {
                        fields[k] = v.clone();
                    }
                }
            }
        }
        let location = std::panic::Location::caller();
        if fields["module"].is_null() {
            fields["module"] = json!(
                Path::new(location.file())
                    .file_stem()
                    .and_then(|s| s.to_str())
                    .unwrap_or("engine")
            );
            fields["line"] = json!(location.line());
        }
        let Some(mut row) = sanitize(event, &fields) else {
            return;
        };
        let mut inner = match self.inner.lock() {
            Ok(v) => v,
            Err(_) => return,
        };
        inner.sequence += 1;
        row["timestamp"] = json!(seconds() as u64);
        row["timestampMs"] = json!((seconds() * 1000.0) as u64);
        row["sessionId"] = json!(inner.session);
        row["sequence"] = json!(inner.sequence);
        let result = (|| -> Result<()> {
            let data = encoded(&row)?;
            ensure(data.len() <= 8192, "诊断行超过上限")?;
            let dir = secure_directory(&self.host.log_dir.join("diagnostics"), true)?;
            let mut existing = owned(&dir, LOG_NAMES[0], LOG_MARKER, LOG_BYTES as u64)?;
            if existing
                .as_ref()
                .is_some_and(|v| v.len() + data.len() as u64 > LOG_BYTES as u64)
            {
                if let Some(info) = owned(&dir, LOG_NAMES[3], LOG_MARKER, LOG_BYTES as u64)? {
                    dir.unlink(LOG_NAMES[3], &info)?;
                }
                for i in (0..3).rev() {
                    if owned(&dir, LOG_NAMES[i], LOG_MARKER, LOG_BYTES as u64)?.is_some() {
                        ensure(dir.file(LOG_NAMES[i + 1]).is_err(), "日志轮转目标不可用")?;
                        dir.rename(LOG_NAMES[i], LOG_NAMES[i + 1])?;
                    }
                }
                existing = None;
            }
            let mut file = if let Some(info) = existing {
                dir.append(LOG_NAMES[0], &info)?
            } else {
                let mut file = dir.create(LOG_NAMES[0])?;
                file.write_all(LOG_MARKER)?;
                file
            };
            file.write_all(&data)?;
            Ok(())
        })();
        if result.is_err() {
            inner.health["dropped"] = json!(
                inner.health["dropped"]
                    .as_u64()
                    .unwrap_or(0)
                    .saturating_add(1)
                    .min(65535)
            );
        }
        for (key, e) in [
            ("loader", "loader_read"),
            ("listing", "list_summary"),
            ("frontend", "frontend_build"),
            ("cleanup", "backup_cleanup"),
        ] {
            if event == e {
                inner.health[key] = sanitize(event, &fields).unwrap_or(Value::Null);
            }
        }
    }
    pub fn record_batch(&self, events: &Value, dropped: u64) -> Result<Value> {
        let events = events
            .as_array()
            .ok_or_else(|| Error::invalid("诊断批次无效"))?;
        ensure(events.len() <= 16 && dropped <= 65535, "诊断批次无效")?;
        let mut clean = Vec::new();
        for event in events {
            let name = text(&event["event"]);
            let definition = &SCHEMA["front"][name];
            ensure(definition.is_array() && event.is_object(), "诊断事件无效")?;
            let required: BTreeSet<_> = array(&definition[0]).iter().map(text).collect();
            let optional: BTreeSet<_> = array(&definition[1]).iter().map(text).collect();
            let keys: BTreeSet<_> = event
                .as_object()
                .unwrap()
                .keys()
                .map(String::as_str)
                .collect();
            ensure(
                required.is_subset(&keys)
                    && keys.is_subset(&required.union(&optional).copied().collect()),
                "诊断事件无效",
            )?;
            let value = sanitize(name, event).ok_or_else(|| Error::invalid("诊断事件无效"))?;
            ensure(value == *event, "诊断字段无效")?;
            clean.push(value);
        }
        ensure(
            encoded(&json!({"events":clean,"dropped":dropped}))?.len() <= 8192,
            "诊断批次过大",
        )?;
        for event in &clean {
            self.record(text(&event["event"]), event.clone());
        }
        if dropped > 0 {
            self.record("frontend_drop", json!({"dropped":dropped}));
        }
        Ok(json!({"accepted":clean.len()}))
    }
    pub fn status(&self) -> Value {
        self.inner.lock().unwrap().status.clone()
    }
    pub fn export_directory(&self) -> Result<PathBuf> {
        ensure(
            self.host.user_home.is_absolute() && self.host.user_home != Path::new("/root"),
            "SDK 用户目录不可用",
        )?;
        Ok(self
            .host
            .user_home
            .join("Downloads/decky-sideloader-diagnostics"))
    }
    fn exports(&self, dir: &Dir) -> Result<Vec<String>> {
        let regex = regex::Regex::new(r"^diagnostics-[0-9]{19}-[a-f0-9]{32}\.zip$").unwrap();
        let mut result = Vec::new();
        for name in dir.entries()? {
            if regex.is_match(&name) && owned(dir, &name, ZIP_MARKER, PACKAGE_BYTES)?.is_some() {
                result.push(name);
            }
        }
        Ok(result)
    }
    fn prune_exports(&self, retain: usize) -> Result<()> {
        let dir = secure_directory(&self.export_directory()?, false)?;
        let mut names = self.exports(&dir)?;
        while names.len() > retain {
            let name = names.remove(0);
            if let Some(info) = owned(&dir, &name, ZIP_MARKER, PACKAGE_BYTES)? {
                dir.unlink(&name, &info)?;
            }
        }
        Ok(())
    }
    pub async fn export(&self, summary: Value, cancel: CancellationToken) -> Result<Value> {
        {
            let mut inner = self.inner.lock().unwrap();
            ensure(
                inner.status["state"] != "exporting" && !cancel.is_cancelled(),
                "诊断导出正在进行或插件正在停止",
            )?;
            inner.status = json!({"state":"exporting"});
        }
        let this = self.clone();
        let result = tokio::task::spawn_blocking(move || this.build_export(summary, &cancel))
            .await
            .map_err(|_| Error::invalid("诊断导出任务失败"))?;
        let status = match result {
            Ok(v) => v,
            Err(e) => {
                self.record(
                    "diagnostic_export",
                    json!({"status":"failed","errorCode":e.code}),
                );
                json!({"state":"error","error":"诊断导出失败，请检查目录权限和可用空间后重试"})
            }
        };
        self.inner.lock().unwrap().status = status.clone();
        Ok(status)
    }
    fn build_export(&self, summary: Value, cancel: &CancellationToken) -> Result<Value> {
        let target = self.export_directory()?;
        let dir = secure_directory(&target, true)?;
        let mut entries = BTreeMap::<String, Vec<u8>>::new();
        let inner = self.inner.lock().unwrap();
        if let Ok(logdir) = secure_directory(&self.host.log_dir.join("diagnostics"), false) {
            for name in LOG_NAMES {
                if owned(&logdir, name, LOG_MARKER, LOG_BYTES as u64)?.is_none() {
                    continue;
                }
                let data = logdir.bytes(name, LOG_BYTES as u64)?;
                let mut sanitized = LOG_MARKER.to_vec();
                for raw in data.split(|c| *c == b'\n').skip(1) {
                    if raw.is_empty() || raw.len() > 8192 {
                        continue;
                    }
                    if let Ok(value) = serde_json::from_slice::<Value>(raw) {
                        if let Some(mut row) = sanitize(text(&value["event"]), &value) {
                            for key in ["timestamp", "timestampMs", "sequence"] {
                                if value[key].as_u64().is_some_and(|n| n <= 9007199254740991) {
                                    row[key] = value[key].clone();
                                }
                            }
                            let line = encoded(&row)?;
                            if sanitized.len() + line.len() <= LOG_BYTES {
                                sanitized.extend(line);
                            }
                        }
                    }
                }
                entries.insert(format!("logs/{name}"), sanitized);
            }
        }
        let health = inner.health.clone();
        drop(inner);
        let installed =
            read_json(&self.host.plugin_dir.join("dist/build.json"), 8192).unwrap_or(Value::Null);
        let installed_bundle = file_hash(&self.host.plugin_dir.join("dist/index.js"), cancel).ok();
        let installed_frontend = if installed_bundle.as_deref() == installed["bundle"].as_str()
            && installed_bundle.is_some()
        {
            installed["fingerprint"].clone()
        } else {
            Value::Null
        };
        let mut builds = json!({"backend":env!("SIDELOADER_BUILD"),"installedBundle":installed_bundle,"installedFrontend":installed_frontend,"binary":self.binary,"installedBinary":file_hash(&self.host.plugin_dir.join("bin/sideloader-core"),cancel).ok()});
        if health["frontend"].is_object() {
            builds["frontend"] = health["frontend"].clone();
            builds["matchesInstalled"] =
                json!(health["frontend"]["fingerprint"] == builds["installedFrontend"]);
        }
        let mut details = sanitize_snapshot(&summary["details"], 0);
        let mut environment = environment_snapshot(&self.host.user_home);
        if summary["details"]["environment"]["steamClientVersion"]
            .as_u64()
            .is_some_and(|n| n > 0 && n <= 9007199254740991)
        {
            environment["steamClientVersion"] =
                summary["details"]["environment"]["steamClientVersion"].clone();
        }
        details["environment"] = environment;
        let summary = json!({"versions":{"backend":self.host.version,"frontend":health["frontend"]["version"],"loader":self.host.loader_version,"sdk":null},"snapshot":sanitize("operation_stage",&summary),"details":details,"builds":sanitize_snapshot(&builds,0),"health":health,"invalidLines":0});
        entries.insert("summary.json".into(), encoded(&summary)?);
        let manifest = json!({"format":2,"limits":{"lineBytes":8192,"logFileBytes":LOG_BYTES,"logFiles":4,"packageBytes":PACKAGE_BYTES,"expandedBytes":PACKAGE_BYTES,"retainedPackages":3},"privacy":"Allowlisted codes, source locations, counts, versions, content fingerprints and pseudonymous identifiers. No original messages, URLs, paths, credentials, configuration files or uploads.","identifiers":"sha256(kind + ':' + value), first 32 hex characters; names/repositories/tags are never exported verbatim","files":entries.iter().map(|(name,data)|json!({"name":name,"bytes":data.len(),"sha256":sha(data)})).collect::<Vec<_>>()});
        entries.insert("manifest.json".into(), encoded(&manifest)?);
        ensure(
            entries.values().map(|d| d.len() as u64).sum::<u64>() <= PACKAGE_BYTES - 65536,
            "诊断包超过上限",
        )?;
        let temporary = format!("building-{}.tmp", id());
        let result = (|| -> Result<Value> {
            let mut output = dir.create(&temporary)?;
            output.write_all(ZIP_MARKER)?;
            let mut zip = zip::ZipWriter::new(output);
            zip.set_comment(String::from_utf8_lossy(ZIP_MARKER));
            for (name, data) in entries {
                cancelled(cancel)?;
                zip.start_file(
                    name,
                    zip::write::SimpleFileOptions::default()
                        .compression_method(zip::CompressionMethod::Stored)
                        .unix_permissions(0o600),
                )?;
                zip.write_all(&data)?;
            }
            let output = zip.finish()?;
            output.sync_all()?;
            let bytes = output.metadata()?.len();
            ensure(bytes <= PACKAGE_BYTES, "诊断包超过上限")?;
            cancelled(cancel)?;
            self.prune_exports(2)?;
            let nanos = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap_or_default()
                .as_nanos();
            let name = format!("diagnostics-{nanos:019}-{}.zip", id());
            ensure(dir.file(&name).is_err(), "诊断目标已存在")?;
            dir.rename(&temporary, &name)?;
            Ok(json!({"state":"success","file":name,"path":target.join(&name),"bytes":bytes}))
        })();
        if result.is_err() {
            if let Ok(file) = dir.file(&temporary) {
                let _ = dir.unlink(&temporary, &file.metadata()?);
            }
        }
        result
    }
}
pub fn operation_snapshot(op: &Value) -> Value {
    if !op.is_object() {
        return json!({"available":false});
    }
    let package = &op["package"];
    let mut result = json!({"available":true,"operationId":op["id"],"stage":op["stage"],"mode":op["network_mode"],"installMode":op["mode"],"outcome":op["outcome"],"closeReason":op["close_reason"],"pluginId":identifier("plugin",op["package"]["name"].as_str().or(op["target"]["name"].as_str()).or(op["existing_name"].as_str()).unwrap_or("")),"repoId":identifier("repo",text(&op["repo"])),"releaseId":identifier("release",text(&op["tag"])),"pathId":identifier("path",text(&op["target"]["path"])),"targetVersion":package.get("version").unwrap_or(&op["target"]["version"]),"packageHash":package["sha256"],"requestId":identifier("loader-request",text(&op["request_id"])),"hasError":!op["error"].is_null(),"error":op["diagnostic_error"]});
    for (raw, key) in [
        ("saw_start", "sawStart"),
        ("saw_finish", "sawFinish"),
        ("binding_saved", "bindingSaved"),
        ("cancel_requested", "cancelRequested"),
        ("evidence_conflict", "evidenceConflict"),
        ("uninstall_started", "uninstallStarted"),
        ("uninstall_blocked", "uninstallBlocked"),
        ("uninstall_result_received", "uninstallResultReceived"),
        ("artifact_deleted", "artifactDeleted"),
        ("backup_deleted", "backupDeleted"),
        ("notice_dismissed", "noticeDismissed"),
    ] {
        result[key] = json!(op[raw] == true);
    }
    for (raw, key) in [
        ("download", "download"),
        ("dependency_download", "dependency"),
    ] {
        if op[raw].is_object() {
            let mut progress = json!({});
            for k in [
                "status", "route", "attempt", "received", "total", "index", "count",
            ] {
                progress[k] = op[raw][k].clone();
            }
            result[key] = progress;
        }
    }
    for (raw, key) in [("created_at", "createdAt"), ("completed_at", "completedAt")] {
        if let Ok(t) = chrono::DateTime::parse_from_rfc3339(text(&op[raw])) {
            result[key] = json!(t.timestamp_millis());
        }
    }
    if op["backup"].is_object() {
        result["backup"] = json!({"available":op["backup_deleted"]!=true,"files":op["backup"]["files"],"bytes":op["backup"]["bytes"],"archiveHash":op["backup"]["sha256"]});
    }
    result["observations"] = json!(
        array(&op["observations"])
            .iter()
            .take(16)
            .map(|v| json!({"step":v["id"],"status":v["status"]}))
            .collect::<Vec<_>>()
    );
    sanitize_snapshot(&result, 0)
}
pub fn library_snapshot(
    plugins: &[Value],
    native: &Value,
    catalog: &Value,
    frozen: Option<&BTreeSet<String>>,
) -> Value {
    sanitize_snapshot(
        &json!({"available":true,"collectedAt":(seconds()*1000.0)as u64,"count":plugins.len(),"truncated":plugins.len().saturating_sub(512),"loaderOk":native["ok"],"source":catalog["source"],"catalogOk":catalog["warning"].is_null(),"stale":catalog["stale"],"plugins":plugins.iter().take(512).map(|p|json!({"pluginId":identifier("plugin",text(&p["name"])),"repoId":identifier("repo",text(&p["repo"])),"currentVersion":p["version"],"ownership":p["ownership"]["state"],"eligible":p["ownership"]["eligible"],"ownershipId":p["ownership"]["intent"]["id"],"ownershipState":p["ownership"]["intent"]["state"],"priorFrozen":p["ownership"]["intent"]["prior_frozen"],"loaderManaged":p["loader_managed"],"registration":p["registration"],"cachedInStore":p["cached_in_store"],"memoryChanged":p["memory_changed"],"frozen":frozen.map(|f|f.contains(text(&p["name"]))),"hasError":!p["ownership"]["error"].is_null()||!p["memory_error"].is_null()})).collect::<Vec<_>>()}),
        0,
    )
}
fn read_environment_file(path: &Path, max: usize) -> Result<Vec<u8>> {
    // sysfs reports an allocation size rather than the length of the text value.
    use std::os::unix::fs::OpenOptionsExt;
    let mut file = std::fs::OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK)
        .open(path)?;
    ensure(file.metadata()?.is_file(), "设备信息不可用")?;
    let mut data = Vec::new();
    (&mut file).take(max as u64 + 1).read_to_end(&mut data)?;
    ensure(data.len() <= max, "设备信息超过上限")?;
    Ok(data)
}
fn environment_snapshot(user_home: &Path) -> Value {
    let mut value =
        json!({"system":std::env::consts::OS,"machine":std::env::consts::ARCH,"isSteamDeck":null});
    for path in ["/etc/os-release", "/usr/lib/os-release"] {
        if let Ok(data) = read_file(Path::new(path), 8192) {
            if let Ok(raw) = std::str::from_utf8(&data) {
                for line in raw.lines() {
                    if let Some((k, v)) = line.split_once('=') {
                        let field = match k {
                            "ID" => "osId",
                            "NAME" => "osName",
                            "VERSION_ID" => "osVersion",
                            "BUILD_ID" => "osBuild",
                            "VARIANT_ID" => "osVariant",
                            _ => continue,
                        };
                        value[field] = json!(v.trim().trim_matches(['\'', '"']));
                    }
                }
                break;
            }
        }
    }
    if let Ok(data) = read_file(Path::new("/proc/sys/kernel/osrelease"), 128) {
        if let Ok(v) = std::str::from_utf8(&data) {
            value["kernelVersion"] = json!(v.trim());
        }
    }
    for (source, field) in [
        ("sys_vendor", "deviceVendor"),
        ("product_name", "deviceModel"),
        ("board_name", "boardName"),
        ("product_version", "productVersion"),
    ] {
        if let Ok(data) =
            read_environment_file(&Path::new("/sys/devices/virtual/dmi/id").join(source), 128)
        {
            if let Ok(v) = std::str::from_utf8(&data) {
                value[field] = json!(v.trim());
            }
        }
    }
    let mut value = sanitize_snapshot(&value, 0);
    let vendor = text(&value["deviceVendor"]).to_lowercase();
    let model = text(&value["deviceModel"]).to_lowercase();
    if ["valve", "valve corporation"].contains(&vendor.as_str()) {
        if ["jupiter", "galileo"].contains(&model.as_str()) {
            value["isSteamDeck"] = json!(true);
            value["steamDeckModel"] = json!(if model == "jupiter" { "lcd" } else { "oled" });
        }
    } else if !vendor.is_empty() {
        value["isSteamDeck"] = json!(false);
    }
    use std::os::unix::ffi::OsStrExt;
    if let Ok(path) = std::ffi::CString::new(user_home.as_os_str().as_bytes()) {
        let mut usage = std::mem::MaybeUninit::<libc::statvfs>::uninit();
        if unsafe { libc::statvfs(path.as_ptr(), usage.as_mut_ptr()) } == 0 {
            let usage = unsafe { usage.assume_init() };
            value["freeBytes"] = json!(usage.f_bavail.saturating_mul(usage.f_frsize));
        }
    }
    sanitize_snapshot(&value, 0)
}
impl Engine {
    pub async fn diagnostic_summary(&self, steam: &Value) -> Value {
        let (state_op, library) = {
            let s = self.state.lock().await;
            (s.operation.clone(), s.library_snapshot.clone())
        };
        let op = state_op
            .or_else(|| {
                read_json(
                    &self.host.runtime_dir.join("installations/operation.json"),
                    4 * 1024 * 1024,
                )
                .ok()
            })
            .unwrap_or(Value::Null);
        let settings = read_json(&self.host.settings(), 4 * 1024 * 1024).unwrap_or(Value::Null);
        let mut safe_settings =
            json!({"available":settings.is_object(),"mode":settings["networkMode"]});
        for key in [
            "autoCheck",
            "autoCheckMinutes",
            "checkNotifications",
            "showHomeTaskNotice",
            "deletePackageAfterInstall",
            "downloadTimeoutMinutes",
        ] {
            safe_settings[key] = settings[key].clone();
        }
        let report = &settings["lastCheck"];
        let checks = json!({"available":report.is_object(),"source":report["source"],"hasError":!report["error"].is_null(),"count":array(&report["items"]).len(),"items":array(&report["items"]).iter().take(512).map(|r|json!({"pluginId":identifier("plugin",text(&r["name"])),"repoId":identifier("repo",text(&r["repo"])),"status":r["status"],"currentVersion":r["current"],"targetVersion":r["latest"],"hasError":!r["error"].is_null()})).collect::<Vec<_>>()});
        let details = sanitize_snapshot(
            &json!({"collectedAt":(seconds()*1000.0)as u64,"library":library,"operation":operation_snapshot(&op),"settings":safe_settings,"checks":checks,"environment":{"steamClientVersion":steam}}),
            0,
        );
        json!({"operationId":op["id"],"stage":op["stage"],"mode":settings["networkMode"],"details":details})
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn privacy_boundary() {
        let v=sanitize("rpc_failed",&json!({"rpc":"check_updates","errorCode":"disk_full","message":"SECRET","url":"https://secret.invalid","requestId":"a".repeat(32)})).unwrap();
        assert!(v.get("message").is_none());
        assert!(v.get("url").is_none());
        assert_eq!(v["errorCode"], "disk_full");
    }
    #[test]
    fn classified_errors_survive_both_log_and_snapshot_allowlists() {
        for (input, expected) in [
            ("timeout", "network_timeout"),
            ("invalid_archive", "package_invalid"),
            ("network", "connection_failed"),
            ("server_error", "http_failed"),
        ] {
            assert_eq!(
                sanitize("rpc_failed", &json!({"errorCode":input})).unwrap()["errorCode"],
                expected
            );
            assert_eq!(
                sanitize_snapshot(&json!({"operation":{"errorCode":input}}), 0)["operation"]["errorCode"],
                expected
            );
        }
        assert_eq!(
            Error::from(std::io::Error::from_raw_os_error(libc::ENOSPC)).code,
            "disk_full"
        );
        assert_eq!(
            Error::from(std::io::Error::from_raw_os_error(libc::EACCES)).code,
            "permission_denied"
        );
        assert!(
            sanitize("rpc_failed", &json!({"errorCode":"PRIVATE_MESSAGE"})).unwrap()["errorCode"]
                .is_null()
        );
    }
    #[test]
    fn deep_snapshots_are_bounded() {
        let value = json!({"operation":{"operation":{"operation":{"operation":{"operation":{"operation":{"operation":{"count":5}}}}}}}});
        assert!(!sanitize_snapshot(&value, 0).to_string().contains('5'));
    }
}
