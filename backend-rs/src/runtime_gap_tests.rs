use super::*;
use std::{collections::BTreeSet, os::unix::fs::PermissionsExt, path::PathBuf};
#[path = "runtime_remote_recovery_tests.rs"]
mod remote_recovery;
#[path = "runtime_transfer_tests.rs"]
mod transfer;

const DEP_A: &str = "https://github.com/a/deps/releases/download/v1/engine-a.bin";
const DEP_B: &str = "https://github.com/a/deps/releases/download/v1/engine-b.bin";
const MAIN: &str = "https://github.com/a/b/releases/download/v2/plugin.zip";
const RELEASES: &str = "https://api.github.com/repos/a/b/releases?per_page=100&page=1";

fn zip_entries(entries: Vec<(String, Vec<u8>, u32)>) -> Vec<u8> {
    let mut zip = zip::ZipWriter::new(std::io::Cursor::new(Vec::new()));
    zip.set_raw_comment(b"fixed-input-comment".to_vec().into());
    for (name, bytes, mode) in entries {
        zip.start_file(
            name,
            zip::write::SimpleFileOptions::default().unix_permissions(mode),
        )
        .unwrap();
        zip.write_all(&bytes).unwrap();
    }
    zip.finish().unwrap().into_inner()
}
fn dep(name: &str, url: &str, bytes: &[u8]) -> Value {
    json!({"name":name,"url":url,"sha256hash":sha(bytes)})
}
fn dependency_package(dependencies: Value, extra: Vec<(String, Vec<u8>, u32)>) -> Vec<u8> {
    let mut entries=vec![
        ("example/plugin.json".into(),br#"{"name":"Example","flags":[]}"#.to_vec(),0o644),
        ("example/package.json".into(),serde_json::to_vec(&json!({"name":"example","version":"2.0.0","repository":"a/b","remote_binary":dependencies,"custom":{"value":"保留","enabled":true}})).unwrap(),0o644),
        ("example/dist/index.js".into(),b"export const version = '2.0.0';".to_vec(),0o644),
        ("example/main.py".into(),b"import decky\n".to_vec(),0o644),
        ("example/LICENSE".into(),b"BSD-3-Clause".to_vec(),0o644),
    ];
    entries.extend(extra);
    zip_entries(entries)
}
fn inspect(path: &Path) -> package::Package {
    package::inspect(path, None, &[], None, false, &CancellationToken::new()).unwrap()
}
fn no_dependency_temps(parent: &Path) {
    assert!(!std::fs::read_dir(parent).unwrap().any(|entry| {
        entry
            .unwrap()
            .file_name()
            .to_string_lossy()
            .starts_with(".remote-binaries-")
    }));
}
fn register_release(f: &Fixture, bytes: Vec<u8>) {
    f.server.asset(MAIN, Reply::bytes(bytes), 1, 1);
    f.server.put(RELEASES,Reply::json(json!([{"draft":false,"prerelease":false,"tag_name":"v2","name":"Fixture","published_at":"2020-01-01T00:00:00Z","assets":[{"name":"plugin.zip","browser_download_url":MAIN}]}])));
}
async fn prepared(f: &Fixture) -> Value {
    let done = f.core.state.lock().await.op_done.clone();
    if let Some(done) = done {
        tokio::time::timeout(Duration::from_secs(5), done.cancelled())
            .await
            .unwrap();
    }
    f.core.operation_public().await.unwrap()
}

#[tokio::test]
async fn two_remote_dependencies_are_verified_merged_and_reinspected() {
    let f = Fixture::new().await;
    let a = b"actual downloaded dependency A";
    let b = b"actual downloaded dependency B";
    let original = dependency_package(
        json!([dep("engine-a", DEP_A, a), dep("engine-b", DEP_B, b)]),
        vec![
            ("example/bin/engine-a".into(), b"old".to_vec(), 0o644),
            ("example/bin/keep".into(), b"untouched".to_vec(), 0o644),
        ],
    );
    let archive = f.temp.path().join("input.zip");
    write(&archive, &original);
    let before = inspect(&archive);
    f.server.expect(DEP_A, Reply::bytes(a.to_vec()), 1);
    f.server.expect(DEP_B, Reply::bytes(b.to_vec()), 1);
    let result = package::remote_binaries(
        &archive,
        &before,
        &f.core.transport().unwrap(),
        1,
        &CancellationToken::new(),
        |_| {},
    )
    .await
    .unwrap()
    .unwrap();
    assert_eq!(result, json!({"count":2,"names":["engine-a","engine-b"]}));
    assert_eq!(
        std::fs::read(archive.with_file_name("source.zip")).unwrap(),
        original
    );
    let after = inspect(&archive);
    assert_ne!(before.sha256, after.sha256);
    assert_eq!(after.hashes["bin/engine-a"], sha(a));
    assert_eq!(after.hashes["bin/engine-b"], sha(b));
    assert_eq!(after.hashes["bin/keep"], sha(b"untouched"));
    let mut zip = zip::ZipArchive::new(std::fs::File::open(&archive).unwrap()).unwrap();
    assert_eq!(zip.comment(), b"fixed-input-comment");
    let meta: Value =
        serde_json::from_reader(zip.by_name("example/package.json").unwrap()).unwrap();
    assert!(meta.get("remote_binary").is_none());
    assert_eq!(meta["custom"], json!({"value":"保留","enabled":true}));
    for name in ["engine-a", "engine-b"] {
        assert_eq!(
            zip.by_name(&format!("example/bin/{name}"))
                .unwrap()
                .unix_mode()
                .unwrap()
                & 0o777,
            0o755
        );
    }
    assert_eq!(
        zip.file_names()
            .filter(|name| *name == "example/bin/engine-a")
            .count(),
        1
    );
    drop(zip);
    assert!(
        package::remote_binaries(
            &archive,
            &after,
            &f.core.transport().unwrap(),
            1,
            &CancellationToken::new(),
            |_| {}
        )
        .await
        .unwrap()
        .is_none()
    );
    no_dependency_temps(f.temp.path());
    f.close().await;
}

#[tokio::test]
async fn dependency_hash_short_body_and_cancel_never_replace_source() {
    for kind in ["hash", "short", "cancel"] {
        let f = Fixture::new().await;
        let expected = b"expected dependency";
        let original = dependency_package(json!([dep("engine-a", DEP_A, expected)]), vec![]);
        let archive = f.temp.path().join("input.zip");
        write(&archive, &original);
        let package = inspect(&archive);
        let cancel = CancellationToken::new();
        let mut reply = Reply::bytes(expected.to_vec());
        if kind == "hash" {
            reply.body = b"tampered dependency".to_vec();
        }
        if kind == "short" {
            reply.disconnect_after = Some(2);
        }
        if kind == "cancel" {
            cancel.cancel();
        } else {
            f.server.expect(DEP_A, reply, 1);
        }
        let result = package::remote_binaries(
            &archive,
            &package,
            &f.core.transport().unwrap(),
            1,
            &cancel,
            |_| {},
        )
        .await;
        assert!(result.is_err(), "{kind}");
        assert_eq!(std::fs::read(&archive).unwrap(), original);
        assert!(!archive.with_file_name("source.zip").exists());
        no_dependency_temps(f.temp.path());
        f.close().await;
    }
}

#[tokio::test]
async fn dependency_active_download_cancel_drains_without_committing() {
    let f = Fixture::new().await;
    let bytes = b"expected dependency";
    let original = dependency_package(json!([dep("engine-a", DEP_A, bytes)]), vec![]);
    let archive = f.temp.path().join("input.zip");
    write(&archive, &original);
    let package = inspect(&archive);
    let entered = Arc::new(Notify::new());
    let release = CancellationToken::new();
    let mut reply = Reply::bytes(bytes.to_vec());
    reply.block = Some((entered.clone(), release));
    f.server.expect(DEP_A, reply, 1);
    let cancel = CancellationToken::new();
    let child = cancel.clone();
    let transport = f.core.transport().unwrap();
    let path = archive.clone();
    let task = tokio::spawn(async move {
        package::remote_binaries(&path, &package, &transport, 1, &child, |_| {}).await
    });
    tokio::time::timeout(Duration::from_secs(3), entered.notified())
        .await
        .unwrap();
    cancel.cancel();
    let result = tokio::time::timeout(Duration::from_secs(3), task)
        .await
        .unwrap()
        .unwrap();
    assert_eq!(result.unwrap_err().code, "cancelled");
    assert_eq!(std::fs::read(archive).unwrap(), original);
    no_dependency_temps(f.temp.path());
    f.close().await;
}

#[tokio::test]
async fn dependency_declarations_conflicts_and_preserved_source_are_fail_closed() {
    let valid = dep("engine", DEP_A, b"data");
    let cases = vec![
        json!([dep("../escape", DEP_A, b"data")]),
        json!([valid.clone(), valid.clone()]),
        json!([{"name":"engine","url":DEP_A,"sha256hash":"bad"}]),
        json!([dep("engine", "http://invalid.local/file", b"data")]),
        json!({"not":"an array"}),
    ];
    for declaration in cases {
        let f = Fixture::new().await;
        let original = dependency_package(declaration, vec![]);
        let archive = f.temp.path().join("input.zip");
        write(&archive, &original);
        let package = inspect(&archive);
        assert!(
            package::remote_binaries(
                &archive,
                &package,
                &f.core.transport().unwrap(),
                1,
                &CancellationToken::new(),
                |_| {}
            )
            .await
            .is_err()
        );
        assert_eq!(f.server.count(), 0);
        assert_eq!(std::fs::read(archive).unwrap(), original);
        no_dependency_temps(f.temp.path());
        f.close().await;
    }
    let f = Fixture::new().await;
    let original = dependency_package(
        json!([valid]),
        vec![(
            "example/bin/engine/child".into(),
            b"conflict".to_vec(),
            0o644,
        )],
    );
    let archive = f.temp.path().join("input.zip");
    write(&archive, &original);
    let package = inspect(&archive);
    assert!(
        package::remote_binaries(
            &archive,
            &package,
            &f.core.transport().unwrap(),
            1,
            &CancellationToken::new(),
            |_| {}
        )
        .await
        .is_err()
    );
    assert_eq!(f.server.count(), 0);
    assert_eq!(std::fs::read(archive).unwrap(), original);
    f.close().await;
}

#[test]
fn nonempty_backup_preserves_exact_contents_manifest_and_permissions() {
    let temp = tempfile::tempdir().unwrap();
    let home = temp.path();
    let files = [
        (
            "settings/example/nested/prefs.json",
            b"{\"keep\":true}".as_slice(),
        ),
        (
            "data/example/state.bin",
            b"\x00\xff\x01fixture\x00".as_slice(),
        ),
    ];
    for (name, data) in files {
        write(home.join(name), data);
    }
    write(home.join("settings/other/private"), b"not in scope");
    let destination = home.join("backup.zip");
    let result = package::backup(home, "example", &destination, &CancellationToken::new()).unwrap();
    assert_eq!(result["files"], 2);
    assert_eq!(
        result["bytes"],
        files.iter().map(|(_, v)| v.len()).sum::<usize>()
    );
    assert_eq!(result["sha256"], sha(std::fs::read(&destination).unwrap()));
    assert_eq!(
        destination.metadata().unwrap().permissions().mode() & 0o777,
        0o600
    );
    let mut zip = zip::ZipArchive::new(std::fs::File::open(destination).unwrap()).unwrap();
    assert_eq!(zip.len(), 3);
    assert_eq!(
        zip.by_name("backup-manifest.json")
            .unwrap()
            .unix_mode()
            .unwrap()
            & 0o777,
        0o600
    );
    for (name, expected) in files {
        let mut entry = zip.by_name(name).unwrap();
        assert_eq!(entry.unix_mode().unwrap() & 0o777, 0o600);
        let mut actual = Vec::new();
        std::io::Read::read_to_end(&mut entry, &mut actual).unwrap();
        assert_eq!(actual, expected);
        assert_eq!(std::fs::read(home.join(name)).unwrap(), expected);
    }
    let manifest: Value =
        serde_json::from_reader(zip.by_name("backup-manifest.json").unwrap()).unwrap();
    assert_eq!(manifest["files"], 2);
    assert!(zip.by_name("settings/other/private").is_err());
}

#[tokio::test]
async fn backup_failure_blocks_submit_then_continues_using_original_download() {
    let f = Fixture::new().await;
    let protected = f.temp.path().join("private");
    write(protected.join("secret"), b"must not copy");
    std::fs::create_dir_all(f.core.host.home.join("data")).unwrap();
    std::os::unix::fs::symlink(&protected, f.core.host.home.join("data/example")).unwrap();
    let original_program =
        std::fs::read(f.core.host.home.join("plugins/example/package.json")).unwrap();
    register_release(&f, package_bytes("example", "Example", "2.0.0", "new"));
    let initial = f
        .core
        .prepare_install("a/b", "v2", MAIN, Some("Example"), None, "install")
        .await
        .unwrap();
    let failed = prepared(&f).await;
    assert_eq!(failed["stage"], "failed");
    assert!(failed["request_id"].is_null());
    assert!(
        f.core
            .submit_operation(text(&initial["id"]), false, false)
            .await
            .is_err()
    );
    let requests = f.server.count();
    std::fs::remove_file(f.core.host.home.join("data/example")).unwrap();
    write(
        f.core.host.home.join("data/example/state.bin"),
        b"retained real data",
    );
    f.core
        .reuse_download(text(&initial["id"]), false)
        .await
        .unwrap();
    let ready = prepared(&f).await;
    assert_eq!(ready["stage"], "ready");
    assert_eq!(ready["backup"]["files"], 1);
    assert_eq!(
        f.server.count(),
        requests,
        "continue must not redownload or requery"
    );
    assert_eq!(
        std::fs::read(f.core.host.home.join("plugins/example/package.json")).unwrap(),
        original_program
    );
    assert_eq!(
        std::fs::read(protected.join("secret")).unwrap(),
        b"must not copy"
    );
    f.close().await;
}

#[test]
fn cancelled_or_unsafe_backup_never_touches_user_data() {
    let temp = tempfile::tempdir().unwrap();
    write(temp.path().join("settings/example/prefs"), b"original");
    let cancel = CancellationToken::new();
    cancel.cancel();
    assert!(
        package::backup(
            temp.path(),
            "example",
            &temp.path().join("cancelled.zip"),
            &cancel
        )
        .is_err()
    );
    assert_eq!(
        std::fs::read(temp.path().join("settings/example/prefs")).unwrap(),
        b"original"
    );
    let destination = temp.path().join("existing.zip");
    write(&destination, b"existing backup");
    assert!(
        package::backup(
            temp.path(),
            "example",
            &destination,
            &CancellationToken::new()
        )
        .is_err()
    );
    assert_eq!(std::fs::read(destination).unwrap(), b"existing backup");
}

#[test]
fn cleanup_uses_age_and_protection_without_following_symlinks_or_hardlinks() {
    let temp = tempfile::tempdir().unwrap();
    let root = temp.path().join("installations");
    let old = "a".repeat(32);
    let recent = "b".repeat(32);
    let protected = "c".repeat(32);
    let linked = "d".repeat(32);
    let hard = "e".repeat(32);
    for id in [&old, &recent, &protected] {
        write(root.join(id).join("data-backup.zip"), id.as_bytes());
    }
    let old_file = std::fs::File::open(root.join(&old).join("data-backup.zip")).unwrap();
    old_file
        .set_times(
            std::fs::FileTimes::new()
                .set_modified(std::time::SystemTime::now() - Duration::from_secs(8 * 86400)),
        )
        .unwrap();
    let external = temp.path().join("external");
    write(external.join("data-backup.zip"), b"protected external");
    std::os::unix::fs::symlink(&external, root.join(&linked)).unwrap();
    std::fs::create_dir(root.join(&hard)).unwrap();
    std::fs::hard_link(
        external.join("data-backup.zip"),
        root.join(&hard).join("data-backup.zip"),
    )
    .unwrap();
    let result = package::clean_backups(&root, &BTreeSet::from([protected.clone()]), true).unwrap();
    assert_eq!(result["deleted"], 1);
    assert!(!root.join(old).join("data-backup.zip").exists());
    assert!(root.join(recent).join("data-backup.zip").exists());
    assert!(root.join(protected).join("data-backup.zip").exists());
    assert_eq!(
        std::fs::read(external.join("data-backup.zip")).unwrap(),
        b"protected external"
    );
}

#[tokio::test]
async fn download_fallback_discards_partial_bytes_and_all_modes_select_exact_routes() {
    for mode in ["direct", "gh-proxy", "ghfast", "auto"] {
        let f = Fixture::new().await;
        f.core
            .set_setting("networkMode", json!(mode))
            .await
            .unwrap();
        let expected = b"complete independent body";
        let route = match mode {
            "gh-proxy" => format!("https://gh-proxy.org/{MAIN}"),
            "ghfast" => format!("https://ghfast.top/{MAIN}"),
            _ => MAIN.into(),
        };
        f.server.expect(&route, Reply::bytes(expected.to_vec()), 1);
        let path = f.temp.path().join("download");
        let result = f
            .core
            .transport()
            .unwrap()
            .download(MAIN, &path, 100, 1, &CancellationToken::new(), |_| {})
            .await
            .unwrap();
        assert_eq!(std::fs::read(path).unwrap(), expected);
        assert_eq!(
            result["route"],
            if mode == "auto" { "direct" } else { mode }
        );
        assert_eq!(f.server.count(), 1);
        f.close().await;
    }
    let f = Fixture::new().await;
    f.core
        .set_setting("networkMode", json!("auto"))
        .await
        .unwrap();
    let mut broken = Reply::bytes(b"BAD-PARTIAL-BYTES".to_vec());
    broken.disconnect_after = Some(3);
    f.server.expect(MAIN, broken, 1);
    f.server.expect(
        &format!("https://gh-proxy.org/{MAIN}"),
        Reply::status(503),
        1,
    );
    f.server.expect(
        &format!("https://ghfast.top/{MAIN}"),
        Reply::bytes(b"GOOD".to_vec()),
        1,
    );
    let path = f.temp.path().join("download");
    let result = f
        .core
        .transport()
        .unwrap()
        .download(MAIN, &path, 100, 1, &CancellationToken::new(), |_| {})
        .await
        .unwrap();
    assert_eq!(std::fs::read(path).unwrap(), b"GOOD");
    assert_eq!(result["route"], "ghfast");
    assert_eq!(result["attempt"], 3);
    f.close().await;
}

#[tokio::test]
async fn query_errors_redirects_and_nonretryable_responses_follow_contract() {
    for status in [403, 404, 429, 500] {
        let f = Fixture::new().await;
        f.core
            .set_setting("networkMode", json!("auto"))
            .await
            .unwrap();
        let url = "https://api.github.com/repos/a/b";
        f.server.expect(url, Reply::status(status), 1);
        if status >= 429 {
            f.server.expect(
                &format!("https://gh-proxy.org/{url}"),
                Reply::json(json!({"via":"proxy"})),
                1,
            );
            assert_eq!(
                f.core
                    .transport()
                    .unwrap()
                    .json(url, 100, &f.core.stop)
                    .await
                    .unwrap()["via"],
                "proxy"
            );
        } else {
            assert!(
                f.core
                    .transport()
                    .unwrap()
                    .json(url, 100, &f.core.stop)
                    .await
                    .is_err()
            );
            assert_eq!(f.server.count(), 1);
        }
        f.close().await;
    }
    for location in [
        "http://github.com/a/b/releases/download/v2/x.zip",
        "https://untrusted.invalid/payload",
    ] {
        let f = Fixture::new().await;
        let mut reply = Reply::status(302);
        reply.headers.push(("Location".into(), location.into()));
        f.server.expect(MAIN, reply, 1);
        let path = f.temp.path().join("download");
        let result = f
            .core
            .transport()
            .unwrap()
            .download(MAIN, &path, 100, 1, &f.core.stop, |_| {})
            .await;
        assert!(result.is_err());
        assert_eq!(f.server.count(), 1);
        f.close().await;
    }
}

#[tokio::test]
async fn retry_download_revalidates_original_asset_and_operation_without_native_dispatch() {
    let f = Fixture::new().await;
    register_release(&f, package_bytes("example", "Example", "2.0.0", "new"));
    f.server.asset(
        MAIN,
        Reply::bytes(package_bytes("example", "Example", "2.0.0", "new")),
        1,
        2,
    );
    f.server.expect_range(MAIN, "bytes=-65557", 2);
    f.server.expect_count(RELEASES, 2);
    f.server.put(MAIN, Reply::status(503));
    let op = f
        .core
        .prepare_install("a/b", "v2", MAIN, Some("Example"), None, "install")
        .await
        .unwrap();
    let failed = prepared(&f).await;
    assert_eq!(failed["stage"], "failed");
    assert_eq!(failed["download"]["status"], "failed");
    assert!(failed["request_id"].is_null());
    f.server.put(
        MAIN,
        Reply::bytes(package_bytes("example", "Example", "2.0.0", "new")),
    );
    f.core.reuse_download(text(&op["id"]), true).await.unwrap();
    let ready = prepared(&f).await;
    assert_eq!(ready["id"], op["id"]);
    assert_eq!(ready["stage"], "ready");
    assert!(ready["request_id"].is_null());
    assert_eq!(ready["download"]["status"], "complete");
    assert!(f.core.reuse_download(text(&op["id"]), true).await.is_err());
    f.close().await;
}

#[tokio::test]
async fn next_dependency_progress_resets_bytes_before_its_body_arrives() {
    let f = Fixture::new().await;
    let first = vec![b'a'; 64];
    let second = b"b";
    let bytes = dependency_package(
        json!([
            dep("engine-a", DEP_A, &first),
            dep("engine-b", DEP_B, second)
        ]),
        vec![],
    );
    register_release(&f, bytes);
    f.server.expect(DEP_A, Reply::bytes(first), 1);
    let entered = Arc::new(Notify::new());
    let release = CancellationToken::new();
    let mut reply = Reply::bytes(second.to_vec());
    reply.block = Some((entered.clone(), release.clone()));
    f.server.expect(DEP_B, reply, 1);
    f.core
        .prepare_install("a/b", "v2", MAIN, Some("Example"), None, "install")
        .await
        .unwrap();
    tokio::time::timeout(Duration::from_secs(3), entered.notified())
        .await
        .unwrap();
    let next = tokio::time::timeout(Duration::from_secs(2), async {
        loop {
            let op = f.core.operation_public().await.unwrap();
            if op["dependency_download"]["index"] == 2 && op["dependency_download"]["received"] == 0
            {
                assert_eq!(op["dependency_download"]["name"], "engine-b");
                break;
            }
            tokio::time::sleep(Duration::from_millis(5)).await;
        }
    })
    .await;
    release.cancel();
    let op = prepared(&f).await;
    assert!(
        next.is_ok(),
        "second dependency never published its own zero-byte progress: {op}"
    );
    assert_eq!(op["stage"], "ready");
    f.close().await;
}

#[test]
fn changed_or_cancelled_nonempty_backup_removes_only_its_partial_output() {
    for changed in [false, true] {
        let temp = tempfile::tempdir().unwrap();
        let source = temp.path().join("data/example/bytes.bin");
        let original = vec![b'x'; 2 * 1024 * 1024];
        write(&source, &original);
        let destination = temp.path().join("backup.zip");
        let cancel = CancellationToken::new();
        let token = cancel.clone();
        let changing = source.clone();
        package::backup_copy_hook(move || {
            if changed {
                std::fs::write(changing, b"new user data").unwrap();
            } else {
                token.cancel();
            }
        });
        let error = package::backup(temp.path(), "example", &destination, &cancel).unwrap_err();
        assert!(
            error.message.contains(if changed {
                "数据发生变化"
            } else {
                "取消"
            }),
            "{error:?}"
        );
        assert!(
            !destination.exists(),
            "failed copy left an unusable backup that blocks continuing"
        );
        assert_eq!(
            std::fs::read(source).unwrap(),
            if changed {
                b"new user data".to_vec()
            } else {
                original
            }
        );
    }
}

#[tokio::test]
async fn backup_write_failure_is_isolated_in_child() {
    if let Some(home) = std::env::var_os("SIDELOADER_BACKUP_IO_CHILD") {
        let root = PathBuf::from(home);
        let destination = root.join("backup.zip");
        let error =
            package::backup(&root, "example", &destination, &CancellationToken::new()).unwrap_err();
        assert!(
            error.message.contains("File too large") || error.message.contains("os error 27"),
            "{error:?}"
        );
        assert!(
            !destination.exists(),
            "real write failure left a partial backup"
        );
        return;
    }
    let temp = tempfile::tempdir().unwrap();
    let original = vec![b'z'; 2 * 1024 * 1024];
    let source = temp.path().join("settings/example/bytes.bin");
    write(&source, &original);
    let mut command = tokio::process::Command::new(std::env::current_exe().unwrap());
    command
        .args([
            "--exact",
            "runtime_tests::gaps::backup_write_failure_is_isolated_in_child",
            "--nocapture",
        ])
        .env("SIDELOADER_BACKUP_IO_CHILD", temp.path())
        .kill_on_drop(true);
    unsafe {
        command.pre_exec(|| {
            libc::signal(libc::SIGXFSZ, libc::SIG_IGN);
            let limit = libc::rlimit {
                rlim_cur: 1024,
                rlim_max: 1024,
            };
            if libc::setrlimit(libc::RLIMIT_FSIZE, &limit) != 0 {
                return Err(std::io::Error::last_os_error());
            }
            Ok(())
        });
    }
    let result = tokio::time::timeout(Duration::from_secs(5), command.output())
        .await
        .unwrap()
        .unwrap();
    assert!(
        result.status.success(),
        "{}\n{}",
        String::from_utf8_lossy(&result.stdout),
        String::from_utf8_lossy(&result.stderr)
    );
    assert_eq!(
        std::fs::read(source).unwrap(),
        original,
        "failed backup changed the source data"
    );
}
