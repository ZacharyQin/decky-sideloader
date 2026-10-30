use crate::{common::*, scoped::Dir, transport::Transport};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs::{self, File, OpenOptions},
    io::{Read, Seek, SeekFrom, Write},
    os::unix::fs::{FileExt, MetadataExt, OpenOptionsExt, PermissionsExt},
    path::Path,
};
use tokio_util::sync::CancellationToken;
use zip::{ZipArchive, ZipWriter, write::SimpleFileOptions};
pub const MAX_DOWNLOAD: u64 = 128 * 1024 * 1024;
pub const MAX_EXPANDED: u64 = 512 * 1024 * 1024;
pub const MAX_ENTRIES: usize = 10000;
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Package {
    pub name: String,
    pub folder: String,
    pub version: String,
    pub hashes: BTreeMap<String, String>,
    pub sha256: String,
    pub warnings: Vec<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub frontend: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub backend: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub binary: Option<String>,
}
impl Package {
    pub fn public(&self) -> Value {
        let mut v = serde_json::to_value(self).unwrap();
        v.as_object_mut().unwrap().remove("hashes");
        v
    }
}
fn file(path: &Path) -> Result<File> {
    let file = OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK)
        .open(path)?;
    ensure(file.metadata()?.is_file(), "安装包不是普通文件")?;
    Ok(file)
}
fn metadata(zip: &mut ZipArchive<File>, path: &str) -> Result<Value> {
    let entry = zip.by_name(path)?;
    ensure(entry.size() <= 1024 * 1024, "插件元数据过大")?;
    let mut bytes = Vec::new();
    entry.take(1024 * 1024 + 1).read_to_end(&mut bytes)?;
    ensure(bytes.len() <= 1024 * 1024, "插件元数据过大")?;
    Ok(serde_json::from_slice(&bytes)?)
}
fn check_directory(
    source: &File,
    start: u64,
    indexed: usize,
    limit: usize,
    cancel: &CancellationToken,
) -> Result<()> {
    let length = source.metadata()?.len();
    let mut offset = start;
    let mut count = 0;
    loop {
        cancelled(cancel)?;
        let mut header = [0u8; 46];
        source.read_exact_at(&mut header[..4], offset)?;
        if &header[..4] != b"PK\x01\x02" {
            break;
        }
        source.read_exact_at(&mut header, offset)?;
        count += 1;
        ensure(count <= limit, "安装包文件过多")?;
        let field = |at| u16::from_le_bytes([header[at], header[at + 1]]) as u64;
        offset = offset
            .checked_add(46 + field(28) + field(30) + field(32))
            .ok_or_else(|| Error::invalid("安装包中央目录偏移溢出"))?;
        ensure(offset <= length, "安装包中央目录损坏")?;
    }
    ensure(count == indexed, "安装包包含重复路径或中央目录不一致")
}
pub fn inspect(
    path: &Path,
    existing: Option<&Value>,
    installed: &[Value],
    target: Option<&Value>,
    updating_self: bool,
    cancel: &CancellationToken,
) -> Result<Package> {
    let source = file(path)?;
    let validation_source = source.try_clone()?;
    let mut zip = ZipArchive::new(source)?;
    check_directory(
        &validation_source,
        zip.central_directory_start(),
        zip.len(),
        MAX_ENTRIES,
        cancel,
    )?;
    ensure(
        !zip.is_empty() && zip.len() <= MAX_ENTRIES,
        "安装包为空、文件过多或解压大小超过 512 MiB",
    )?;
    let mut seen = BTreeSet::new();
    let mut folders = BTreeSet::new();
    let mut expanded = 0u64;
    for i in 0..zip.len() {
        cancelled(cancel)?;
        let entry = zip.by_index(i)?;
        let raw = entry.name();
        ensure(safe_relative(raw), "安装包包含不安全路径")?;
        let parts: Vec<_> = raw.trim_end_matches('/').split('/').collect();
        ensure(folder_valid(parts[0]), "插件目录名无效")?;
        ensure(
            parts.len() > 1 || entry.is_dir(),
            "安装包必须只有一个插件顶层目录",
        )?;
        if let Some(mode) = entry.unix_mode() {
            let kind = mode & libc::S_IFMT;
            ensure(
                [0, libc::S_IFREG, libc::S_IFDIR].contains(&kind),
                "安装包包含符号链接或特殊文件",
            )?;
        }
        ensure(
            seen.insert(raw.trim_end_matches('/').to_owned()),
            "安装包包含重复路径",
        )?;
        folders.insert(parts[0].to_owned());
        expanded = expanded
            .checked_add(entry.size())
            .ok_or_else(|| Error::invalid("安装包过大"))?;
        ensure(expanded <= MAX_EXPANDED, "安装包解压大小超过 512 MiB")?;
    }
    ensure(folders.len() == 1, "安装包包含多个插件目录")?;
    let folder = folders.into_iter().next().unwrap();
    for path in &seen {
        let mut current = String::new();
        let components: Vec<_> = path.split('/').collect();
        for part in components.iter().take(components.len() - 1) {
            if !current.is_empty() {
                current.push('/');
            }
            current.push_str(part);
            if let Ok(entry) = zip.by_name(&current) {
                ensure(entry.is_dir(), "安装包的文件与目录路径冲突")?;
            }
        }
    }
    let plugin = metadata(&mut zip, &format!("{folder}/plugin.json"))?;
    let package = metadata(&mut zip, &format!("{folder}/package.json"))?;
    let name = text(&plugin["name"]);
    let version = text(&package["version"]);
    ensure(
        valid_text(&plugin["name"], 128) && !name.trim().is_empty() && !name.contains(['/', '\\']),
        "plugin.json 缺少合法插件名",
    )?;
    ensure(
        valid_text(&package["version"], 128) && !version.trim().is_empty(),
        "package.json 缺少版本",
    )?;
    ensure(
        zip.by_name(&format!("{folder}/dist/index.js"))
            .is_ok_and(|v| v.is_file()),
        "安装包缺少 dist/index.js",
    )?;
    if let Some(target) = target {
        ensure(
            target["name"] == name && target["package_name"] == package["name"],
            "安装包身份与所选仓库插件不一致，请核对 monorepo 的插件、版本和 ZIP",
        )?;
    }
    if updating_self {
        ensure(
            name == "Sideloader"
                && folder == "decky-sideloader"
                && package["name"] == "decky-sideloader"
                && existing.is_some(),
            "自更新安装包身份不一致",
        )?;
    } else {
        ensure(
            name != "Sideloader" && folder != "decky-sideloader",
            "不能通过本插件替换 Sideloader 自身",
        )?;
    }
    if let Some(existing) = existing {
        ensure(
            existing["name"] == name && existing["folder"] == folder,
            "新包名称或目录与旧插件不一致，已阻止替换以保护数据",
        )?;
    } else {
        ensure(
            !installed
                .iter()
                .any(|p| p["name"] == name || p["folder"] == folder),
            "插件已安装，请从插件列表进入版本管理",
        )?;
    }
    let mut warnings = Vec::new();
    if zip.by_name(&format!("{folder}/LICENSE")).is_err() {
        warnings.push("安装包未附带 LICENSE，请自行核对作者的分发许可。".into());
    }
    if array(&plugin["flags"]).contains(&json!("root")) {
        warnings.push("此插件声明了 root 权限，请仅安装可信作者的发布。".into());
    }
    let mut hashes = BTreeMap::new();
    let mut actual = 0u64;
    for i in 0..zip.len() {
        let mut entry = zip.by_index(i)?;
        if entry.is_dir() {
            continue;
        }
        let relative = entry
            .name()
            .strip_prefix(&format!("{folder}/"))
            .ok_or_else(|| Error::invalid("安装包路径无效"))?
            .to_owned();
        let mut h = Sha256::new();
        let mut buf = vec![0; 1024 * 1024];
        let mut copied = 0u64;
        loop {
            cancelled(cancel)?;
            let n = entry.read(&mut buf)?;
            if n == 0 {
                break;
            }
            actual += n as u64;
            copied += n as u64;
            ensure(
                actual <= MAX_EXPANDED && copied <= entry.size(),
                "安装包解压大小超过上限",
            )?;
            h.update(&buf[..n]);
        }
        ensure(copied == entry.size(), "安装包文件长度不匹配")?;
        hashes.insert(relative, hex::encode(h.finalize()));
    }
    let mut result = Package {
        name: name.into(),
        folder,
        version: version.into(),
        hashes,
        sha256: file_hash(path, cancel)?,
        warnings,
        frontend: None,
        backend: None,
        binary: None,
    };
    if updating_self {
        let build = metadata(&mut zip, "decky-sideloader/dist/build.json")?;
        ensure(
            build["version"] == result.version
                && build["bundle"]
                    == result
                        .hashes
                        .get("dist/index.js")
                        .cloned()
                        .unwrap_or_default()
                && valid_hash(text(&build["fingerprint"])),
            "自更新前端构建身份无效",
        )?;
        let backend = metadata(&mut zip, "decky-sideloader/dist/backend.json")?;
        ensure(
            backend["version"] == result.version
                && backend["protocol"] == 1
                && backend["target"] == env!("SIDELOADER_TARGET")
                && valid_hash(text(&backend["fingerprint"]))
                && backend["sha256"]
                    == result
                        .hashes
                        .get("bin/sideloader-core")
                        .cloned()
                        .unwrap_or_default(),
            "自更新 Rust 后端构建身份无效",
        )?;
        let mut entry = zip.by_name("decky-sideloader/bin/sideloader-core")?;
        ensure(
            entry.unix_mode().is_some_and(|m| m & 0o111 != 0),
            "Rust 后端缺少执行权限",
        )?;
        let mut header = [0u8; 64];
        entry.read_exact(&mut header)?;
        ensure(
            &header[..5] == b"\x7fELF\x02"
                && header[5] == 1
                && u16::from_le_bytes([header[18], header[19]]) == 62,
            "Rust 后端不是 Linux x86-64 可执行文件",
        )?;
        result.frontend = string(&build["fingerprint"]);
        result.backend = string(&backend["fingerprint"]);
        result.binary = string(&backend["sha256"]);
    }
    Ok(result)
}
pub fn folder_valid(v: &str) -> bool {
    let bytes = v.as_bytes();
    !v.is_empty()
        && v.len() <= 128
        && safe_component(v)
        && bytes
            .first()
            .is_some_and(|c| c.is_ascii_alphanumeric() || b"_-".contains(c))
        && bytes
            .last()
            .is_some_and(|c| c.is_ascii_alphanumeric() || b"_-".contains(c))
        && v.chars()
            .all(|c| c.is_ascii_alphanumeric() || "_. -".contains(c))
}
pub fn stamp(home: &Path, folder: &str, cancel: &CancellationToken) -> Result<Value> {
    ensure(folder_valid(folder), "插件目录名无效")?;
    let root = home.join("plugins").join(folder);
    if !root.exists() {
        return Ok(Value::Null);
    }
    let dir = Dir::open(&root)?;
    if dir.file("package.json").is_err() {
        return Ok(Value::Null);
    }
    let mut out = Vec::new();
    for relative in ["package.json", "plugin.json", "dist/index.js", "main.py"] {
        let file = match dir.beneath(relative) {
            Ok(v) => v,
            Err(e) => {
                if !root.join(relative).exists() {
                    out.push(json!([relative, null]));
                    continue;
                }
                return Err(e);
            }
        };
        let info = file.metadata()?;
        out.push(json!([
            relative,
            info.ino(),
            info.mtime() as i128 * 1_000_000_000 + info.mtime_nsec() as i128,
            info.ctime() as i128 * 1_000_000_000 + info.ctime_nsec() as i128,
            info.len(),
            hash_open(file, cancel)?
        ]));
    }
    Ok(json!(out))
}
fn hash_open(mut file: File, cancel: &CancellationToken) -> Result<String> {
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
pub fn verify(
    home: &Path,
    package: &Package,
    previous: &Value,
    cancel: &CancellationToken,
) -> Result<bool> {
    let current = stamp(home, &package.folder, cancel)?;
    if current.is_null() || &current == previous {
        return Ok(false);
    }
    let dir = Dir::open(&home.join("plugins").join(&package.folder))?;
    for (relative, expected) in &package.hashes {
        if hash_open(dir.beneath(relative)?, cancel)? != *expected {
            return Ok(false);
        }
    }
    Ok(true)
}
fn walk(
    root: &Path,
    path: &Path,
    out: &mut Vec<(std::path::PathBuf, String, fs::Metadata)>,
    total: &mut u64,
    cancel: &CancellationToken,
) -> Result<()> {
    for entry in fs::read_dir(path)? {
        cancelled(cancel)?;
        let entry = entry?;
        let info = fs::symlink_metadata(entry.path())?;
        ensure(
            info.is_file() || info.is_dir(),
            "备份源包含符号链接或特殊文件",
        )?;
        if info.is_dir() {
            walk(root, &entry.path(), out, total, cancel)?;
        } else {
            *total = total
                .checked_add(info.len())
                .ok_or_else(|| Error::invalid("备份过大"))?;
            out.push((
                entry.path(),
                entry
                    .path()
                    .strip_prefix(root)
                    .unwrap()
                    .to_string_lossy()
                    .into(),
                info,
            ));
            ensure(
                *total <= MAX_EXPANDED && out.len() <= MAX_ENTRIES,
                "备份超过 512 MiB 或 10000 个文件，已停止替换",
            )?;
        }
    }
    Ok(())
}
const BACKUP_MARKER: &[u8] = b"decky-sideloader-data-backup-v1\n";
const BACKUP_SCOPE: &str =
    "仅 Decky 标准 settings 和 data 目录，不含插件自行写入的其他位置。备份时插件仍可能运行。";
#[cfg(test)]
thread_local! {
    static BACKUP_COPY_HOOK: std::cell::RefCell<Option<Box<dyn FnOnce()>>> = const { std::cell::RefCell::new(None) };
}
#[cfg(test)]
pub(crate) fn backup_copy_hook(action: impl FnOnce() + 'static) {
    BACKUP_COPY_HOOK.with(|slot| {
        assert!(slot.borrow().is_none());
        *slot.borrow_mut() = Some(Box::new(action));
    });
}
pub fn backup(
    home: &Path,
    folder: &str,
    destination: &Path,
    cancel: &CancellationToken,
) -> Result<Value> {
    ensure(folder_valid(folder), "插件目录名无效")?;
    let mut paths = Vec::new();
    let mut files = Vec::new();
    let mut total = 0u64;
    for category in ["settings", "data"] {
        let root = home.join(category);
        let source = root.join(folder);
        if fs::symlink_metadata(&source).is_err() {
            continue;
        }
        Dir::open(&root)?;
        Dir::open(&source)?;
        paths.push(source.to_string_lossy().to_string());
        walk(home, &source, &mut files, &mut total, cancel)?;
    }
    let dir = Dir::open(home)?;
    let parent = destination
        .parent()
        .ok_or_else(|| Error::invalid("备份输出目录无效"))?;
    Dir::open(parent)?;
    match fs::symlink_metadata(destination) {
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
        Err(error) => return Err(error.into()),
        Ok(_) => return Err(Error::invalid("备份目标已存在")),
    }
    let operation = parent
        .file_name()
        .and_then(|value| value.to_str())
        .filter(|value| valid_id(value));
    let mut output = tempfile::Builder::new()
        .prefix(".backup-")
        .suffix(".tmp")
        .tempfile_in(parent)?;
    output
        .as_file()
        .set_permissions(std::fs::Permissions::from_mode(0o600))?;
    output.write_all(BACKUP_MARKER)?;
    let mut zip = ZipWriter::new(output.as_file_mut());
    let manifest = json!({"format":1,"operation_id":operation,"created_at":now(),"paths":paths,"files":files.len(),"bytes":total,"archive":destination,"scope":BACKUP_SCOPE});
    for (_path, relative, before) in files {
        cancelled(cancel)?;
        let mut input = dir.beneath(&relative)?;
        let after = input.metadata()?;
        ensure(
            (
                before.dev(),
                before.ino(),
                before.len(),
                before.mtime(),
                before.mtime_nsec(),
            ) == (
                after.dev(),
                after.ino(),
                after.len(),
                after.mtime(),
                after.mtime_nsec(),
            ),
            "备份期间数据发生变化，请停止插件活动后重试",
        )?;
        zip.start_file(
            &relative,
            SimpleFileOptions::default()
                .compression_method(zip::CompressionMethod::Stored)
                .unix_permissions(0o600),
        )?;
        let mut buf = vec![0; 1024 * 1024];
        let mut copied = 0;
        loop {
            cancelled(cancel)?;
            let n = input.read(&mut buf)?;
            if n == 0 {
                break;
            }
            copied += n as u64;
            ensure(copied <= before.len(), "备份期间数据发生变化")?;
            zip.write_all(&buf[..n])?;
            #[cfg(test)]
            BACKUP_COPY_HOOK.with(|slot| {
                let action = slot.borrow_mut().take();
                if let Some(action) = action {
                    action();
                }
            });
            #[cfg(test)]
            crate::backup_gate::checkpoint()?;
        }
        let final_info = dir.beneath(&relative)?.metadata()?;
        ensure(
            copied == before.len()
                && (
                    final_info.ino(),
                    final_info.len(),
                    final_info.mtime(),
                    final_info.mtime_nsec(),
                ) == (
                    before.ino(),
                    before.len(),
                    before.mtime(),
                    before.mtime_nsec(),
                ),
            "备份期间数据发生变化",
        )?;
    }
    zip.start_file(
        "backup-manifest.json",
        SimpleFileOptions::default().unix_permissions(0o600),
    )?;
    zip.write_all(&serde_json::to_vec(&manifest)?)?;
    zip.finish()?.sync_all()?;
    let manifest = read_backup(home, folder, output.path(), destination, operation, cancel)?;
    cancelled(cancel)?;
    output
        .persist_noclobber(destination)
        .map_err(|error| Error::from(error.error))?;
    File::open(parent)?.sync_all()?;
    Ok(manifest)
}
fn read_backup(
    home: &Path,
    folder: &str,
    path: &Path,
    destination: &Path,
    operation: Option<&str>,
    cancel: &CancellationToken,
) -> Result<Value> {
    let mut source = file(path)?;
    let before = source.metadata()?;
    ensure(
        before.nlink() == 1
            && before.uid() == unsafe { libc::getuid() }
            && before.mode() & 0o777 == 0o600
            && before.len() <= MAX_EXPANDED * 2,
        "备份文件身份无效",
    )?;
    let mut marker = vec![0; BACKUP_MARKER.len()];
    source.read_exact(&mut marker)?;
    ensure(marker == BACKUP_MARKER, "已有备份不属于本任务")?;
    let mut validation = source.try_clone()?;
    let mut archive = ZipArchive::new(source)?;
    check_directory(
        &validation,
        archive.central_directory_start(),
        archive.len(),
        MAX_ENTRIES + 1,
        cancel,
    )?;
    ensure(
        !archive.is_empty() && archive.len() <= MAX_ENTRIES + 1,
        "备份文件数量无效",
    )?;
    let mut manifest = {
        let entry = archive.by_name("backup-manifest.json")?;
        ensure(entry.size() <= 65536, "备份清单过大")?;
        let mut bytes = Vec::new();
        entry.take(65537).read_to_end(&mut bytes)?;
        ensure(bytes.len() <= 65536, "备份清单过大")?;
        serde_json::from_slice::<Value>(&bytes)?
    };
    ensure(
        manifest["format"] == 1
            && manifest["operation_id"] == json!(operation)
            && manifest["archive"] == json!(destination)
            && manifest["scope"] == BACKUP_SCOPE
            && chrono::DateTime::parse_from_rfc3339(text(&manifest["created_at"])).is_ok(),
        "备份清单与本任务不匹配",
    )?;
    ensure(
        manifest["paths"].is_array() && array(&manifest["paths"]).len() <= 2,
        "备份范围无效",
    )?;
    let allowed: BTreeSet<String> = ["settings", "data"]
        .iter()
        .map(|category| {
            home.join(category)
                .join(folder)
                .to_string_lossy()
                .into_owned()
        })
        .collect();
    let mut paths = BTreeSet::new();
    for path in array(&manifest["paths"]) {
        ensure(
            path.is_string() && allowed.contains(text(path)) && paths.insert(text(path).to_owned()),
            "备份范围无效",
        )?;
    }
    let mut count = 0usize;
    let mut total = 0u64;
    let mut names = BTreeSet::new();
    let mut buffer = vec![0; 65536];
    for index in 0..archive.len() {
        cancelled(cancel)?;
        let mut entry = archive.by_index(index)?;
        let name = entry.name().to_owned();
        ensure(
            names.insert(name.clone())
                && !entry.is_dir()
                && entry.unix_mode().is_some_and(|mode| {
                    mode & 0o777 == 0o600 && [0, 0o100000].contains(&(mode & 0o170000))
                }),
            "备份成员无效",
        )?;
        if name != "backup-manifest.json" {
            let parts: Vec<_> = name.split('/').collect();
            ensure(
                parts.len() >= 3
                    && ["settings", "data"].contains(&parts[0])
                    && parts[1] == folder
                    && parts.iter().all(|part| safe_component(part))
                    && !name.contains('\\')
                    && paths.contains(
                        &home
                            .join(parts[0])
                            .join(folder)
                            .to_string_lossy()
                            .into_owned(),
                    ),
                "备份成员超出范围",
            )?;
            count += 1;
            total = total
                .checked_add(entry.size())
                .ok_or_else(|| Error::invalid("备份过大"))?;
            ensure(
                count <= MAX_ENTRIES && total <= MAX_EXPANDED,
                "备份超过上限",
            )?;
        }
        loop {
            cancelled(cancel)?;
            if entry.read(&mut buffer)? == 0 {
                break;
            }
        }
    }
    ensure(
        manifest["files"] == count && manifest["bytes"] == total,
        "备份清单内容不匹配",
    )?;
    validation.seek(SeekFrom::Start(0))?;
    let mut hash = Sha256::new();
    loop {
        cancelled(cancel)?;
        let size = validation.read(&mut buffer)?;
        if size == 0 {
            break;
        }
        hash.update(&buffer[..size]);
    }
    let digest = hex::encode(hash.finalize());
    let after = file(path)?.metadata()?;
    ensure(
        (
            before.dev(),
            before.ino(),
            before.len(),
            before.mtime(),
            before.mtime_nsec(),
            before.ctime(),
            before.ctime_nsec(),
            before.nlink(),
            before.mode(),
        ) == (
            after.dev(),
            after.ino(),
            after.len(),
            after.mtime(),
            after.mtime_nsec(),
            after.ctime(),
            after.ctime_nsec(),
            after.nlink(),
            after.mode(),
        ),
        "备份文件已变化",
    )?;
    manifest["sha256"] = json!(digest);
    Ok(manifest)
}
pub fn resume_backup(
    home: &Path,
    folder: &str,
    destination: &Path,
    operation: &str,
    cancel: &CancellationToken,
) -> Result<Option<Value>> {
    ensure(
        folder_valid(folder)
            && valid_id(operation)
            && destination
                .file_name()
                .is_some_and(|value| value == "data-backup.zip")
            && destination
                .parent()
                .and_then(|path| path.file_name())
                .and_then(|value| value.to_str())
                == Some(operation),
        "备份恢复范围无效",
    )?;
    let parent = destination.parent().unwrap();
    let root = Dir::open(parent)?;
    let pattern = regex::Regex::new(r"^\.backup-[A-Za-z0-9_-]{6,64}\.tmp$").unwrap();
    for entry in fs::read_dir(parent)? {
        cancelled(cancel)?;
        let entry = entry?;
        let name = entry.file_name();
        let Some(name) = name.to_str() else {
            continue;
        };
        if !pattern.is_match(name) {
            continue;
        }
        let Ok(mut file) = root.file(name) else {
            continue;
        };
        let info = file.metadata()?;
        if !info.is_file()
            || info.nlink() != 1
            || info.uid() != unsafe { libc::getuid() }
            || info.mode() & 0o777 != 0o600
            || info.len() > MAX_EXPANDED * 2
        {
            continue;
        }
        let mut marker = vec![0; BACKUP_MARKER.len()];
        if file.read_exact(&mut marker).is_err() || marker != BACKUP_MARKER {
            continue;
        }
        root.unlink(name, &info)?;
    }
    match fs::symlink_metadata(destination) {
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(None),
        Err(error) => Err(error.into()),
        Ok(_) => read_backup(
            home,
            folder,
            destination,
            destination,
            Some(operation),
            cancel,
        )
        .map(Some),
    }
}
pub fn clean_backups(root: &Path, protected: &BTreeSet<String>, expired: bool) -> Result<Value> {
    let mut result = json!({"deleted":0,"bytes":0,"skipped":0,"failed":0});
    if !root.exists() {
        return Ok(result);
    }
    let rootfd = Dir::open(root)?;
    for entry in fs::read_dir(root)? {
        let entry = entry?;
        let name = entry.file_name().to_string_lossy().to_string();
        if !valid_id(&name) {
            continue;
        }
        if protected.contains(&name) {
            result["skipped"] = json!(result["skipped"].as_u64().unwrap() + 1);
            continue;
        }
        let attempt = (|| -> Result<Option<u64>> {
            let dir = rootfd.child(&name)?;
            let info = dir.metadata()?;
            let original = fs::symlink_metadata(entry.path())?;
            ensure(
                info.ino() == original.ino()
                    && info.dev() == original.dev()
                    && info.uid() == unsafe { libc::getuid() },
                "备份目录已变化",
            )?;
            let file = match dir.file("data-backup.zip") {
                Ok(v) => v,
                Err(_) => return Ok(None),
            };
            let info = file.metadata()?;
            if info.nlink() != 1 || info.uid() != unsafe { libc::getuid() } {
                return Ok(None);
            }
            if expired && info.mtime() as f64 >= seconds() - 7.0 * 86400.0 {
                return Ok(None);
            }
            dir.unlink("data-backup.zip", &info)?;
            Ok(Some(info.len()))
        })();
        match attempt {
            Ok(Some(n)) => {
                result["deleted"] = json!(result["deleted"].as_u64().unwrap() + 1);
                result["bytes"] = json!(result["bytes"].as_u64().unwrap() + n);
            }
            Ok(None) => {}
            Err(_) => result["failed"] = json!(result["failed"].as_u64().unwrap() + 1),
        }
    }
    Ok(result)
}
const REMOTE_WORK_MARKER: &str = ".sideloader-work.json";
fn remote_work_owner(parent: &Path, source: &str, count: usize) -> Value {
    let operation = parent
        .file_name()
        .and_then(|value| value.to_str())
        .filter(|value| valid_id(value));
    json!({"format":1,"operation_id":operation,"source_sha256":source,"dependencies":count})
}
fn clean_remote_work(
    parent: &Path,
    source: &str,
    count: usize,
    cancel: &CancellationToken,
) -> Result<()> {
    let owner = remote_work_owner(parent, source, count);
    let pattern = regex::Regex::new(r"^\.remote-binaries-[A-Za-z0-9_-]{6,64}$").unwrap();
    let mut allowed: BTreeSet<String> = (0..count)
        .map(|index| format!("dependency-{index}"))
        .collect();
    allowed.extend([
        REMOTE_WORK_MARKER.to_owned(),
        "runtime.zip".into(),
        "source.zip".into(),
    ]);
    for entry in fs::read_dir(parent)? {
        cancelled(cancel)?;
        let entry = entry?;
        let name = entry.file_name();
        let Some(name) = name.to_str() else {
            continue;
        };
        if !pattern.is_match(name) {
            continue;
        }
        let info = fs::symlink_metadata(entry.path())?;
        if !info.is_dir() || info.uid() != unsafe { libc::getuid() } || info.mode() & 0o777 != 0o700
        {
            continue;
        }
        let Ok(dir) = Dir::open(&entry.path()) else {
            continue;
        };
        let opened = dir.metadata()?;
        if opened.dev() != info.dev() || opened.ino() != info.ino() {
            continue;
        }
        let marker = match dir.file(REMOTE_WORK_MARKER) {
            Ok(file) => file,
            Err(_) => continue,
        };
        let marker_info = marker.metadata()?;
        if marker_info.nlink() != 1
            || marker_info.uid() != unsafe { libc::getuid() }
            || marker_info.mode() & 0o777 != 0o600
            || marker_info.len() > 4096
        {
            continue;
        }
        let mut bytes = Vec::new();
        marker.take(4097).read_to_end(&mut bytes)?;
        if bytes.len() > 4096
            || serde_json::from_slice::<Value>(&bytes).ok().as_ref() != Some(&owner)
        {
            continue;
        }
        let mut names = Vec::new();
        for child in fs::read_dir(entry.path())?.take(count + 4) {
            let child = child?;
            names.push(child.file_name().to_string_lossy().into_owned());
        }
        if names.len() > count + 3 || names.iter().any(|name| !allowed.contains(name)) {
            continue;
        }
        let mut files = Vec::new();
        let mut valid = true;
        for name in names {
            let Ok(file) = dir.file(&name) else {
                valid = false;
                break;
            };
            let metadata = file.metadata()?;
            if !metadata.is_file()
                || metadata.nlink() != 1
                || metadata.uid() != unsafe { libc::getuid() }
                || metadata.mode() & 0o777 != 0o600
                || metadata.len() > MAX_EXPANDED * 2
            {
                valid = false;
                break;
            }
            files.push((name, metadata));
        }
        if !valid {
            continue;
        }
        files.sort_by_key(|(name, _)| name == REMOTE_WORK_MARKER);
        for (name, info) in files {
            cancelled(cancel)?;
            dir.unlink(&name, &info)?;
        }
        let current = fs::symlink_metadata(entry.path())?;
        ensure(
            current.dev() == info.dev() && current.ino() == info.ino(),
            "依赖临时目录已变化",
        )?;
        fs::remove_dir(entry.path())?;
        File::open(parent)?.sync_all()?;
    }
    Ok(())
}
fn retained_source(path: &Path, expected: &str, cancel: &CancellationToken) -> Result<bool> {
    match fs::symlink_metadata(path) {
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(false),
        Err(error) => Err(error.into()),
        Ok(info) => {
            ensure(
                info.is_file() && info.nlink() == 1 && info.uid() == unsafe { libc::getuid() },
                "source.zip 原始包保留路径不安全",
            )?;
            ensure(
                file_hash(path, cancel)? == expected,
                "source.zip 已存在且不匹配原始安装包",
            )?;
            Ok(true)
        }
    }
}
pub async fn remote_binaries<F>(
    archive: &Path,
    package: &Package,
    transport: &Transport,
    minutes: u64,
    cancel: &CancellationToken,
    progress: F,
) -> Result<Option<Value>>
where
    F: Fn(Value) + Send + Sync,
{
    let mut original = ZipArchive::new(file(archive)?)?;
    let meta_path = format!("{}/package.json", package.folder);
    let mut metadata = metadata(&mut original, &meta_path)?;
    let Some(raw) = metadata
        .as_object_mut()
        .and_then(|v| v.remove("remote_binary"))
    else {
        return Ok(None);
    };
    let dependencies = raw
        .as_array()
        .ok_or_else(|| Error::invalid("依赖声明必须是数组"))?;
    if dependencies.is_empty() {
        return Ok(Some(json!({"count":0,"names":[]})));
    }
    ensure(dependencies.len() <= MAX_ENTRIES, "依赖文件过多")?;
    let mut names = BTreeSet::new();
    let mut targets = BTreeSet::new();
    for dep in dependencies {
        let name = text(&dep["name"]);
        ensure(
            !name.is_empty()
                && name.len() <= 255
                && safe_component(name)
                && name
                    .as_bytes()
                    .first()
                    .is_some_and(|c| c.is_ascii_alphanumeric() || b"_-".contains(c))
                && name
                    .bytes()
                    .all(|c| c.is_ascii_alphanumeric() || b"_.-".contains(&c))
                && names.insert(name.to_owned()),
            "依赖文件名无效或重复",
        )?;
        ensure(
            valid_hash(&text(&dep["sha256hash"]).to_lowercase()),
            "sha256hash 必须是 64 位十六进制 SHA-256",
        )?;
        ensure(
            crate::transport::allowed(text(&dep["url"]), crate::transport::Kind::Download, true),
            "不支持的依赖来源：仅支持 GitHub Release HTTPS 下载地址",
        )?;
        for parent in [package.folder.clone(), format!("{}/bin", package.folder)] {
            if let Ok(entry) = original.by_name(&parent) {
                ensure(entry.is_dir(), "依赖路径冲突：父目录不是目录")?;
            }
        }
        let target = format!("{}/bin/{name}", package.folder);
        for i in 0..original.len() {
            let entry = original.by_index(i)?;
            ensure(
                !entry.name().starts_with(&format!("{target}/"))
                    && !(entry.name().trim_end_matches('/') == target && entry.is_dir()),
                "依赖路径冲突",
            )?;
        }
        targets.insert(target);
    }
    let source = archive.with_file_name("source.zip");
    retained_source(&source, &package.sha256, cancel)?;
    let parent = archive.parent().unwrap();
    clean_remote_work(parent, &package.sha256, dependencies.len(), cancel)?;
    let temporary = tempfile::Builder::new()
        .prefix(".remote-binaries-")
        .tempdir_in(parent)?;
    fs::set_permissions(temporary.path(), std::fs::Permissions::from_mode(0o700))?;
    atomic_json(
        &temporary.path().join(REMOTE_WORK_MARKER),
        &remote_work_owner(parent, &package.sha256, dependencies.len()),
    )?;
    let mut binaries = Vec::new();
    let mut expanded = 0u64;
    for i in 0..original.len() {
        expanded = expanded
            .checked_add(original.by_index(i)?.size())
            .ok_or_else(|| Error::invalid("安装包过大"))?;
    }
    ensure(expanded <= MAX_EXPANDED, "安装包过大")?;
    for (i, dep) in dependencies.iter().enumerate() {
        cancelled(cancel)?;
        let binary = temporary.path().join(format!("dependency-{i}"));
        let remaining = MAX_EXPANDED
            .checked_sub(expanded)
            .ok_or_else(|| Error::invalid("合并后解压大小超过 512 MiB"))?;
        transport
            .download(
                text(&dep["url"]),
                &binary,
                remaining,
                minutes,
                cancel,
                |mut value| {
                    value["name"] = dep["name"].clone();
                    value["index"] = json!(i + 1);
                    value["count"] = json!(dependencies.len());
                    progress(value);
                },
            )
            .await?;
        ensure(
            file_hash(&binary, cancel)? == text(&dep["sha256hash"]).to_lowercase(),
            "依赖 SHA-256 校验失败",
        )?;
        expanded += binary.metadata()?.len();
        ensure(expanded <= MAX_EXPANDED, "合并后解压大小超过 512 MiB")?;
        binaries.push(binary);
    }
    let existing: BTreeSet<_> = original.file_names().map(str::to_owned).collect();
    let additions = targets
        .iter()
        .filter(|target| !existing.contains(target.as_str()))
        .count();
    ensure(
        original.len() + additions <= MAX_ENTRIES,
        "合并后文件数超过 10000",
    )?;
    let runtime = temporary.path().join("runtime.zip");
    let mut zip = ZipWriter::new(
        OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .open(&runtime)?,
    );
    zip.set_raw_comment(original.comment().into());
    for i in 0..original.len() {
        cancelled(cancel)?;
        let mut entry = original.by_index(i)?;
        let name = entry.name().to_owned();
        if targets.contains(&name) {
            continue;
        }
        let options = SimpleFileOptions::default()
            .compression_method(entry.compression())
            .unix_permissions(entry.unix_mode().unwrap_or(0o644));
        if entry.is_dir() {
            zip.add_directory(name, options)?;
        } else {
            zip.start_file(&name, options)?;
            if name == meta_path {
                zip.write_all(&serde_json::to_vec(&metadata)?)?;
            } else {
                copy_checked(&mut entry, &mut zip, cancel)?;
            }
        }
    }
    for (dep, binary) in dependencies.iter().zip(binaries) {
        zip.start_file(
            format!("{}/bin/{}", package.folder, text(&dep["name"])),
            SimpleFileOptions::default().unix_permissions(0o755),
        )?;
        copy_checked(&mut file(&binary)?, &mut zip, cancel)?;
    }
    zip.finish()?.sync_all()?;
    if !retained_source(&source, &package.sha256, cancel)? {
        let preserved = temporary.path().join("source.zip");
        let mut output = OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .open(&preserved)?;
        copy_checked(&mut file(archive)?, &mut output, cancel)?;
        output.sync_all()?;
        cancelled(cancel)?;
        fs::hard_link(&preserved, &source)?;
        fs::remove_file(preserved)?;
    }
    ensure(
        retained_source(&source, &package.sha256, cancel)?,
        "原始安装包保留文件已消失",
    )?;
    cancelled(cancel)?;
    fs::rename(runtime, archive)?;
    File::open(parent)?.sync_all()?;
    Ok(Some(
        json!({"count":dependencies.len(),"names":dependencies.iter().map(|v|v["name"].clone()).collect::<Vec<_>>()}),
    ))
}
fn copy_checked<R: Read, W: Write>(
    reader: &mut R,
    writer: &mut W,
    cancel: &CancellationToken,
) -> Result<()> {
    let mut buf = vec![0; 1024 * 1024];
    loop {
        cancelled(cancel)?;
        let n = reader.read(&mut buf)?;
        if n == 0 {
            return Ok(());
        }
        writer.write_all(&buf[..n])?;
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn valid_folder() {
        assert!(folder_valid("My Plugin"));
        for v in ["../x", ".x", "a/dir", "a ", "a\\b"] {
            assert!(!folder_valid(v));
        }
    }
}
