use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    fmt,
    fs::{self, File, OpenOptions},
    io::{Read, Write},
    os::unix::fs::{MetadataExt, OpenOptionsExt, PermissionsExt},
    path::{Path, PathBuf},
};
use tokio_util::sync::CancellationToken;

pub type Result<T> = std::result::Result<T, Error>;
#[derive(Debug, Clone, Serialize)]
pub struct Error {
    pub code: String,
    pub message: String,
}
impl Error {
    pub fn new(code: &str, message: impl Into<String>) -> Self {
        Self {
            code: code.into(),
            message: message.into(),
        }
    }
    pub fn invalid(message: impl Into<String>) -> Self {
        Self::new("invalid_state", message)
    }
}
impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.message)
    }
}
impl std::error::Error for Error {}
impl From<std::io::Error> for Error {
    fn from(e: std::io::Error) -> Self {
        let code = match e.raw_os_error() {
            Some(libc::ENOSPC | libc::EDQUOT) => "disk_full",
            Some(libc::EACCES | libc::EPERM) => "permission_denied",
            Some(libc::ENOENT) => "file_missing",
            _ => "io_failed",
        };
        Self::new(code, format!("文件操作失败：{e}"))
    }
}
impl From<serde_json::Error> for Error {
    fn from(_: serde_json::Error) -> Self {
        Self::new("invalid_response", "JSON 格式无效")
    }
}
impl From<zip::result::ZipError> for Error {
    fn from(e: zip::result::ZipError) -> Self {
        Self::new("invalid_archive", format!("安装包不可读：{e}"))
    }
}
pub fn ensure(value: bool, message: &str) -> Result<()> {
    if value {
        Ok(())
    } else {
        Err(Error::invalid(message))
    }
}
pub fn text(v: &Value) -> &str {
    v.as_str().unwrap_or("")
}
pub fn string(v: &Value) -> Option<String> {
    v.as_str().map(str::to_owned)
}
pub fn array(v: &Value) -> &[Value] {
    v.as_array().map(Vec::as_slice).unwrap_or(&[])
}
pub fn now() -> String {
    chrono::Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Micros, false)
}
pub fn seconds() -> f64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs_f64()
}
pub fn id() -> String {
    uuid::Uuid::new_v4().simple().to_string()
}
pub fn sha(data: impl AsRef<[u8]>) -> String {
    hex::encode(Sha256::digest(data.as_ref()))
}
pub fn valid_hash(v: &str) -> bool {
    v.len() == 64
        && v.bytes()
            .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
}
pub fn valid_id(v: &str) -> bool {
    v.len() == 32
        && v.bytes()
            .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
}
pub fn valid_text(v: &Value, limit: usize) -> bool {
    v.as_str().is_some_and(|s| {
        !s.is_empty() && s.chars().count() <= limit && !s.chars().any(|c| c.is_control())
    })
}
pub fn safe_relative(v: &str) -> bool {
    !v.is_empty()
        && !v.starts_with('/')
        && !v.contains(['\\', '\0'])
        && v.trim_end_matches('/')
            .split('/')
            .all(|p| !p.is_empty() && p != "." && p != "..")
}
pub fn safe_component(v: &str) -> bool {
    safe_relative(v) && !v.contains('/')
}
pub fn cancelled(cancel: &CancellationToken) -> Result<()> {
    ensure(!cancel.is_cancelled(), "任务已取消，未继续准备或提交")
}
pub fn read_file(path: &Path, max: u64) -> Result<Vec<u8>> {
    let mut file = OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK)
        .open(path)?;
    let info = file.metadata()?;
    ensure(
        info.is_file() && info.len() <= max && info.nlink() == 1,
        "文件不可用或超过读取上限",
    )?;
    let mut data = Vec::new();
    Read::by_ref(&mut file)
        .take(max + 1)
        .read_to_end(&mut data)?;
    ensure(data.len() as u64 <= max, "文件超过读取上限")?;
    Ok(data)
}
pub fn read_json(path: &Path, max: u64) -> Result<Value> {
    Ok(serde_json::from_slice(&read_file(path, max)?)?)
}
pub fn optional_json(path: &Path, max: u64) -> Result<Option<Value>> {
    match fs::symlink_metadata(path) {
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(None),
        Err(e) => Err(e.into()),
        Ok(_) => read_json(path, max).map(Some),
    }
}
pub fn directory(path: &Path) -> Result<()> {
    if !path.exists() {
        fs::create_dir_all(path)?;
        fs::set_permissions(path, fs::Permissions::from_mode(0o700))?;
    }
    let info = fs::symlink_metadata(path)?;
    ensure(
        info.is_dir() && !info.file_type().is_symlink(),
        "目录不可用或是符号链接",
    )
}
pub fn atomic_json(path: &Path, value: &Value) -> Result<()> {
    atomic_bytes(path, &serde_json::to_vec_pretty(value)?, 0o600)
}
#[cfg(test)]
type AtomicWriteMatch = Box<dyn Fn(&[u8]) -> bool>;
#[cfg(test)]
struct AtomicWriteHook {
    path: PathBuf,
    matches: AtomicWriteMatch,
    run: Box<dyn FnOnce() -> Box<dyn std::any::Any>>,
}
#[cfg(test)]
thread_local! {
    static ATOMIC_WRITE_HOOK: std::cell::RefCell<Option<AtomicWriteHook>> = const { std::cell::RefCell::new(None) };
}
#[cfg(test)]
pub(crate) fn atomic_write_hook<T: 'static>(path: PathBuf, run: impl FnOnce() -> T + 'static) {
    atomic_write_hook_when(path, |_| true, run);
}
#[cfg(test)]
pub(crate) fn atomic_write_hook_when<T: 'static>(
    path: PathBuf,
    matches: impl Fn(&[u8]) -> bool + 'static,
    run: impl FnOnce() -> T + 'static,
) {
    ATOMIC_WRITE_HOOK.with(|slot| {
        assert!(slot.borrow().is_none());
        *slot.borrow_mut() = Some(AtomicWriteHook {
            path,
            matches: Box::new(matches),
            run: Box::new(move || Box::new(run())),
        });
    });
}
pub fn atomic_bytes(path: &Path, data: &[u8], mode: u32) -> Result<()> {
    let parent = path
        .parent()
        .ok_or_else(|| Error::invalid("文件目录无效"))?;
    directory(parent)?;
    if let Ok(info) = fs::symlink_metadata(path) {
        ensure(
            info.is_file() && !info.file_type().is_symlink() && info.nlink() == 1,
            "目标文件不可用",
        )?;
    }
    #[cfg(test)]
    let _write_fault = ATOMIC_WRITE_HOOK.with(|slot| {
        let hook = slot
            .borrow_mut()
            .take_if(|hook| hook.path == path && (hook.matches)(data));
        hook.map(|hook| (hook.run)())
    });
    let mut file = tempfile::NamedTempFile::new_in(parent)?;
    file.as_file()
        .set_permissions(fs::Permissions::from_mode(mode))?;
    file.write_all(data)?;
    file.as_file().sync_all()?;
    file.persist(path).map_err(|e| Error::from(e.error))?;
    File::open(parent)?.sync_all()?;
    Ok(())
}
pub fn file_hash(path: &Path, cancel: &CancellationToken) -> Result<String> {
    let mut file = OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK)
        .open(path)?;
    ensure(file.metadata()?.is_file(), "校验目标不是普通文件")?;
    let mut h = Sha256::new();
    let mut buf = vec![0; 1024 * 1024];
    loop {
        cancelled(cancel)?;
        let n = file.read(&mut buf)?;
        if n == 0 {
            break;
        }
        h.update(&buf[..n]);
    }
    Ok(hex::encode(h.finalize()))
}
#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Host {
    pub home: PathBuf,
    pub plugin_dir: PathBuf,
    pub settings_dir: PathBuf,
    pub runtime_dir: PathBuf,
    pub log_dir: PathBuf,
    pub user_home: PathBuf,
    pub name: String,
    pub version: String,
    pub loader_version: String,
}
impl Host {
    pub fn validate(&self) -> Result<()> {
        for path in [
            &self.home,
            &self.plugin_dir,
            &self.settings_dir,
            &self.runtime_dir,
            &self.log_dir,
            &self.user_home,
        ] {
            ensure(path.is_absolute(), "Decky 运行目录不可用")?;
        }
        ensure(
            self.name == "Sideloader"
                && self.version == env!("CARGO_PKG_VERSION")
                && self
                    .plugin_dir
                    .file_name()
                    .is_some_and(|p| p == "decky-sideloader"),
            "Sideloader 运行身份不一致",
        )
    }
    pub fn settings(&self) -> PathBuf {
        self.settings_dir.join("settings.json")
    }
}
pub fn default_settings() -> Value {
    json!({"bindings":{},"autoCheck":false,"autoCheckMinutes":360,"networkMode":"direct","checkNotifications":true,"showHomeTaskNotice":true,"deletePackageAfterInstall":true,"downloadTimeoutMinutes":3,"skippedReleases":{},"lastCheck":null,"lastScheduledCheck":null})
}
pub fn load_settings(host: &Host) -> Result<Value> {
    let raw = match optional_json(&host.settings(), 4 * 1024 * 1024) {
        Ok(Some(v)) if v.is_object() => v,
        Ok(_) => json!({}),
        Err(e) if e.code == "invalid_response" => json!({}),
        Err(e) => return Err(e),
    };
    let mut value = default_settings();
    for key in ["bindings", "skippedReleases"] {
        if raw[key].is_object() {
            value[key] = raw[key].clone();
        }
    }
    for key in [
        "autoCheck",
        "checkNotifications",
        "showHomeTaskNotice",
        "deletePackageAfterInstall",
    ] {
        if raw[key].is_boolean() {
            value[key] = raw[key].clone();
        }
    }
    if raw["autoCheckMinutes"]
        .as_u64()
        .is_some_and(|n| (60..=1440).contains(&n))
    {
        value["autoCheckMinutes"] = raw["autoCheckMinutes"].clone();
    }
    if [3, 5, 10, 15, 30].contains(&raw["downloadTimeoutMinutes"].as_u64().unwrap_or(0)) {
        value["downloadTimeoutMinutes"] = raw["downloadTimeoutMinutes"].clone();
    }
    if ["direct", "auto", "gh-proxy", "ghfast"].contains(&text(&raw["networkMode"])) {
        value["networkMode"] = raw["networkMode"].clone();
    }
    for key in ["lastCheck", "lastScheduledCheck"] {
        if valid_report(&raw[key]) {
            value[key] = raw[key].clone();
        }
    }
    let mut skipped = serde_json::Map::new();
    if let Some(rows) = raw["skippedReleases"].as_object() {
        for (name, row) in rows.iter().take(64) {
            let repo = crate::metadata::parse_repo(text(&row["repo"]));
            if !name.trim().is_empty() && name.len() <= 128 && repo.is_some() {
                let mut tags = Vec::new();
                for tag in array(&row["tags"]) {
                    if valid_text(tag, 128) && !text(tag).trim().is_empty() && !tags.contains(tag) {
                        tags.push(tag.clone());
                    }
                    if tags.len() == 20 {
                        break;
                    }
                }
                if !tags.is_empty() {
                    skipped.insert(name.clone(), json!({"repo":repo,"tags":tags}));
                }
            }
        }
    }
    value["skippedReleases"] = skipped.into();
    Ok(value)
}
fn valid_report(v: &Value) -> bool {
    v.is_object()
        && v["items"].is_array()
        && chrono::DateTime::parse_from_rfc3339(text(&v["checkedAt"])).is_ok()
        && array(&v["items"]).iter().all(|r| {
            r["name"].is_string()
                && [
                    "update",
                    "current",
                    "ahead",
                    "uncomparable",
                    "unbound",
                    "no_release",
                    "no_asset",
                    "error",
                ]
                .contains(&text(&r["status"]))
        })
}
pub fn skipped_tags(settings: &Value, name: &str, repo: &Value) -> Vec<String> {
    let entry = &settings["skippedReleases"][name];
    if &entry["repo"] == repo && repo.is_string() {
        array(&entry["tags"]).iter().filter_map(string).collect()
    } else {
        vec![]
    }
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Stage {
    Checking,
    Backup,
    Ready,
    Uninstalling,
    Validating,
    Awaiting,
    Installing,
    Verified,
    Failed,
    Unknown,
    Cancelled,
    Closed,
    Downloading,
    Verifying,
    Completed,
}
impl Stage {
    pub fn parse(v: &Value) -> Result<Self> {
        serde_json::from_value(v.clone()).map_err(|_| Error::invalid("任务阶段无效"))
    }
    pub fn allows(self, next: Self) -> bool {
        if self == next {
            return true;
        }
        use Stage::*;
        match self {
            Checking => matches!(next, Backup | Failed | Cancelled),
            Backup => matches!(next, Ready | Failed | Cancelled),
            Ready => matches!(next, Validating | Awaiting | Failed | Cancelled),
            Validating => matches!(next, Awaiting | Uninstalling | Failed | Unknown | Cancelled),
            Uninstalling => matches!(next, Validating | Awaiting | Unknown | Cancelled | Failed),
            Awaiting => matches!(
                next,
                Installing | Unknown | Failed | Cancelled | Verifying | Completed
            ),
            Installing => matches!(next, Verified | Unknown | Failed | Verifying | Completed),
            Verified => matches!(next, Closed),
            Unknown => matches!(
                next,
                Installing | Verified | Verifying | Completed | Failed | Cancelled | Closed
            ),
            Failed => matches!(next, Checking | Closed),
            Cancelled => matches!(next, Closed),
            Downloading => matches!(next, Ready | Failed | Cancelled),
            Verifying => matches!(next, Completed | Unknown),
            Closed | Completed => false,
        }
    }
    pub fn terminal(self) -> bool {
        matches!(
            self,
            Self::Failed | Self::Cancelled | Self::Closed | Self::Completed
        )
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn paths_reject_traversal() {
        for s in ["../a", "/a", "a//b", "a/./b", "a\\b"] {
            assert!(!safe_relative(s));
        }
        assert!(safe_relative("folder/file.py"));
    }
    #[test]
    fn state_names_are_checked() {
        assert!(Stage::parse(&json!("imaginary")).is_err());
        assert!(Stage::parse(&json!("unknown")).is_ok());
    }
    #[test]
    fn atomic_write_and_symlink_guard() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("state.json");
        atomic_json(&path, &json!({"id":1})).unwrap();
        assert_eq!(read_json(&path, 100).unwrap()["id"], 1);
        let link = dir.path().join("link");
        std::os::unix::fs::symlink(&path, &link).unwrap();
        assert!(atomic_json(&link, &json!({})).is_err());
    }
}
