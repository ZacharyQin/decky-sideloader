#[path = "runtime_boundary_tests.rs"]
mod boundaries;
#[path = "runtime_gap_tests.rs"]
mod gaps;
#[path = "runtime_http_fixture.rs"]
mod http_fixture;
#[path = "runtime_live_tests.rs"]
mod live;
#[path = "runtime_more_tests.rs"]
mod more;
#[path = "runtime_recovery_tests.rs"]
mod recovery;
#[path = "runtime_service_tests.rs"]
mod services;
use http_fixture::{Reply, Server};

use crate::{common::*, engine::Engine, library_model as model, package};
use serde_json::{Value, json};
use std::{
    io::Write,
    path::Path,
    sync::{Arc, Mutex as StdMutex},
    time::Duration,
};
use tokio::{
    io::{AsyncReadExt, AsyncWriteExt},
    sync::{Notify, mpsc},
};
use tokio_util::sync::CancellationToken;
struct Fixture {
    temp: tempfile::TempDir,
    core: Arc<Engine>,
    server: Server,
    native: Arc<StdMutex<Value>>,
    events: Arc<StdMutex<Vec<Value>>>,
    native_task: tokio::task::JoinHandle<()>,
}
fn write(path: impl AsRef<Path>, data: impl AsRef<[u8]>) {
    let path = path.as_ref();
    std::fs::create_dir_all(path.parent().unwrap()).unwrap();
    std::fs::write(path, data).unwrap();
}
fn installed(root: &Path, folder: &str, name: &str, version: &str, marker: &str) {
    let dir = root.join("plugins").join(folder);
    write(
        dir.join("plugin.json"),
        serde_json::to_vec(&json!({"name":name,"author":"Test"})).unwrap(),
    );
    write(
        dir.join("package.json"),
        serde_json::to_vec(&json!({"name":folder,"version":version,"repository":"a/b"})).unwrap(),
    );
    write(dir.join("main.py"), format!("# {marker}\n"));
    write(
        dir.join("dist/index.js"),
        format!("export const marker = {marker:?};"),
    );
}
fn elf(marker: &str) -> Vec<u8> {
    let mut bytes = vec![0; 64];
    bytes[..5].copy_from_slice(b"\x7fELF\x02");
    bytes[5] = 1;
    bytes[18..20].copy_from_slice(&62u16.to_le_bytes());
    bytes.extend_from_slice(marker.as_bytes());
    bytes
}
fn package_bytes(folder: &str, name: &str, version: &str, marker: &str) -> Vec<u8> {
    let mut files = vec![
        (
            "plugin.json".to_owned(),
            serde_json::to_vec(&json!({"name":name,"flags":[]})).unwrap(),
        ),
        (
            "package.json".into(),
            serde_json::to_vec(&json!({"name":folder,"version":version,"repository":"a/b"}))
                .unwrap(),
        ),
        ("main.py".into(), format!("# {marker}\n").into_bytes()),
        (
            "dist/index.js".into(),
            format!("export const marker = {marker:?};").into_bytes(),
        ),
        ("LICENSE".into(), b"BSD-3-Clause".to_vec()),
    ];
    if folder == "decky-sideloader" {
        let bundle = &files.iter().find(|v| v.0 == "dist/index.js").unwrap().1;
        files.push((
            "dist/build.json".into(),
            serde_json::to_vec(
                &json!({"version":version,"fingerprint":"b".repeat(64),"bundle":sha(bundle)}),
            )
            .unwrap(),
        ));
        let binary = elf(marker);
        files.push(("dist/backend.json".into(),serde_json::to_vec(&json!({"version":version,"protocol":1,"fingerprint":env!("SIDELOADER_BUILD"),"target":env!("SIDELOADER_TARGET"),"sha256":sha(&binary)})).unwrap()));
        files.push(("bin/sideloader-core".into(), binary));
    }
    let mut zip = zip::ZipWriter::new(std::io::Cursor::new(Vec::new()));
    for (path, data) in files {
        zip.start_file(
            format!("{folder}/{path}"),
            zip::write::SimpleFileOptions::default().unix_permissions(
                if path.starts_with("bin/") {
                    0o755
                } else {
                    0o644
                },
            ),
        )
        .unwrap();
        zip.write_all(&data).unwrap();
    }
    zip.finish().unwrap().into_inner()
}
fn extract(bytes: &[u8], root: &Path) {
    let mut zip = zip::ZipArchive::new(std::io::Cursor::new(bytes)).unwrap();
    for i in 0..zip.len() {
        let mut entry = zip.by_index(i).unwrap();
        let mut bytes = Vec::new();
        std::io::Read::read_to_end(&mut entry, &mut bytes).unwrap();
        write(root.join(entry.name()), bytes);
    }
}
impl Fixture {
    async fn new() -> Self {
        let temp = tempfile::tempdir().unwrap();
        let home = temp.path();
        let plugin_dir = home.join("plugins/decky-sideloader");
        installed(home, "decky-sideloader", "Sideloader", "0.6.0", "current");
        installed(home, "example", "Example", "1.0.0", "old");
        write(plugin_dir.join("data/repositories.json"),serde_json::to_vec(&json!({"schema_version":1,"revision":1,"updated_at":"2026-10-28","repositories":{}})).unwrap());
        write(plugin_dir.join("dist/build.json"),serde_json::to_vec(&json!({"version":"0.6.0","fingerprint":"b".repeat(64),"bundle":sha(std::fs::read(plugin_dir.join("dist/index.js")).unwrap())})).unwrap());
        write(plugin_dir.join("bin/sideloader-core"), elf("current"));
        write(home.join("settings/loader.json"), b"{\"frozenPlugins\":[]}");
        std::fs::create_dir_all(home.join("user")).unwrap();
        let host = Host {
            home: home.into(),
            plugin_dir,
            settings_dir: home.join("settings/decky-sideloader"),
            runtime_dir: home.join("data/decky-sideloader"),
            log_dir: home.join("logs/decky-sideloader"),
            user_home: home.join("user"),
            name: "Sideloader".into(),
            version: "0.6.0".into(),
            loader_version: "v3.3.0".into(),
        };
        let (tx, mut rx) = mpsc::channel(64);
        let core = Engine::new(host, tx, sha(elf("current"))).unwrap();
        let native = Arc::new(StdMutex::new(
            json!({"snapshot":{"ok":true,"plugins":[{"name":"Example","version":"1.0.0","load_type":0,"disabled":false},{"name":"Sideloader","version":"0.6.0","load_type":0,"disabled":false}]},"capable":[],"cache":{"channel":"default","names":[]},"error":null}),
        ));
        let weak = Arc::downgrade(&core);
        let payload = native.clone();
        let events = Arc::new(StdMutex::new(Vec::new()));
        let recorded = events.clone();
        let native_task = tokio::spawn(async move {
            while let Some(event) = rx.recv().await {
                recorded.lock().unwrap().push(event.clone());
                if event["event"] == "native_management_requested" {
                    let value = payload.lock().unwrap().clone();
                    if let Some(core) = weak.upgrade() {
                        let _ = core
                            .native
                            .receive(text(&event["args"][0]["id"]), &value)
                            .await;
                    }
                }
            }
        });
        let server = Server::new().await;
        *core.test_origin.lock().unwrap() = Some(server.origin.clone());
        Self {
            temp,
            core,
            server,
            native,
            events,
            native_task,
        }
    }
    fn ordinary_release(&self, tag: &str, version: &str, marker: &str) -> (String, Vec<u8>) {
        let url = format!("https://github.com/a/b/releases/download/{tag}/example-{tag}.zip");
        let bytes = package_bytes("example", "Example", version, marker);
        self.server.asset(&url, Reply::bytes(bytes.clone()), 1, 1);
        let release = json!({"draft":false,"tag_name":tag,"name":tag,"body":"# Update","published_at":"2026-10-28T00:00:00Z","assets":[{"name":format!("example-{tag}.zip"),"browser_download_url":url,"download_count":12}]});
        self.server.put(
            "https://api.github.com/repos/a/b/releases?per_page=100&page=1",
            Reply::json(json!([release])),
        );
        (url, bytes)
    }
    async fn prepare(&self) -> (String, Vec<u8>) {
        self.prepare_mode("install").await
    }
    async fn prepare_mode(&self, mode: &str) -> (String, Vec<u8>) {
        let (url, bytes) = self.ordinary_release("v2.0.0", "2.0.0", "new");
        self.core
            .prepare_install("a/b", "v2.0.0", &url, Some("Example"), None, mode)
            .await
            .unwrap();
        let done = self.core.state.lock().await.op_done.clone().unwrap();
        tokio::time::timeout(Duration::from_secs(5), done.cancelled())
            .await
            .unwrap();
        let op = self.core.operation_public().await.unwrap();
        assert_eq!(op["stage"], "ready", "{op}");
        (text(&op["id"]).into(), bytes)
    }
    async fn close(&self) {
        self.core.shutdown().await;
        self.server.finish().await;
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        self.core.stop.cancel();
        self.native_task.abort();
    }
}
#[tokio::test]
async fn preparation_does_not_submit_and_completion_needs_native_evidence() {
    let f = Fixture::new().await;
    let (id, bytes) = f.prepare().await;
    let archive = f.core.op_directory(&id).unwrap().join("plugin.zip");
    assert!(archive.is_file());
    assert!(f.core.state.lock().await.operation.as_ref().unwrap()["request_id"].is_null());
    f.core.submit_operation(&id, false, false).await.unwrap();
    extract(&bytes, &f.core.host.home.join("plugins"));
    let pending = f.core.operation_status().await.unwrap();
    assert_ne!(pending["outcome"], "success");
    let digest = text(&pending["package"]["sha256"]);
    f.core
        .install_event(&id, "prompt", "Example", "native-1", digest)
        .await
        .unwrap();
    f.core
        .install_event(&id, "start", "Example", "", "")
        .await
        .unwrap();
    f.core
        .install_event(&id, "finish", "Example", "", "")
        .await
        .unwrap();
    let finished = f.core.operation_status().await.unwrap();
    assert_eq!(finished["outcome"], "success");
    assert_eq!(finished["stage"], "closed");
    assert!(finished["artifact_deleted"] == true);
    assert!(!archive.exists());
    assert!(
        f.core
            .op_directory(&id)
            .unwrap()
            .join("data-backup.zip")
            .exists()
    );
    assert_eq!(
        load_settings(&f.core.host).unwrap()["bindings"]["Example"]["repo"],
        "a/b"
    );
    f.close().await;
}
#[tokio::test]
async fn conflicting_native_request_never_reports_success() {
    let f = Fixture::new().await;
    let (id, bytes) = f.prepare().await;
    f.core.submit_operation(&id, false, false).await.unwrap();
    extract(&bytes, &f.core.host.home.join("plugins"));
    f.core
        .install_event(&id, "prompt", "Example", "foreign", &"f".repeat(64))
        .await
        .unwrap();
    f.core
        .install_event(&id, "start", "Example", "", "")
        .await
        .unwrap();
    f.core
        .install_event(&id, "finish", "Example", "", "")
        .await
        .unwrap();
    let op = f.core.operation_status().await.unwrap();
    assert_eq!(op["stage"], "unknown");
    assert_ne!(op["outcome"], "success");
    f.close().await;
}
#[tokio::test]
async fn ready_cancellation_is_durable_and_blocks_submit() {
    let f = Fixture::new().await;
    let (id, _) = f.prepare().await;
    assert_eq!(
        f.core.cancel_operation(&id).await.unwrap()["stage"],
        "cancelled"
    );
    assert!(f.core.submit_operation(&id, false, false).await.is_err());
    let file = f.core.host.runtime_dir.join("installations/operation.json");
    assert_eq!(read_json(&file, 1024 * 1024).unwrap()["stage"], "cancelled");
    f.close().await;
}
#[tokio::test]
async fn download_cancellation_drains_the_real_worker() {
    let f = Fixture::new().await;
    let (url, _) = f.ordinary_release("v2", "2.0.0", "new");
    let entered = Arc::new(Notify::new());
    let release = CancellationToken::new();
    let mut reply = f.server.routes.lock().unwrap()[&url].clone();
    reply.block = Some((entered.clone(), release.clone()));
    f.server.put(&url, reply);
    let op = f
        .core
        .prepare_install("a/b", "v2", &url, Some("Example"), None, "install")
        .await
        .unwrap();
    tokio::time::timeout(Duration::from_secs(3), entered.notified())
        .await
        .unwrap();
    let id = text(&op["id"]);
    let op = tokio::time::timeout(Duration::from_secs(3), f.core.cancel_operation(id))
        .await
        .unwrap()
        .unwrap();
    assert_eq!(op["stage"], "cancelled");
    assert!(f.core.state.lock().await.op_done.is_none());
    assert!(op["request_id"].is_null());
    release.cancel();
    f.close().await;
}
#[tokio::test]
async fn map_does_not_grant_store_install_permission() {
    let f = Fixture::new().await;
    f.native.lock().unwrap()["capable"] = json!(["Example"]);
    f.core.state.lock().await.map.value["repositories"]["Example"] = json!("a/b");
    let listing = f.core.list_plugins(false).await.unwrap();
    let row = array(&listing["plugins"])
        .iter()
        .find(|p| p["name"] == "Example")
        .unwrap();
    assert_eq!(row["repo_source"], "mapping");
    assert_eq!(row["ownership"]["state"], "store");
    assert!(f.core.authorize("Example").await.is_err());
    assert!(
        f.core
            .set_binding("Example", Some("chosen/fork"))
            .await
            .is_err()
    );
    f.close().await;
}
#[tokio::test]
async fn ownership_requires_correlated_native_freeze_evidence() {
    let f = Fixture::new().await;
    f.native.lock().unwrap()["capable"] = json!(["Example"]);
    let original = std::fs::read(f.core.host.home.join("settings/loader.json")).unwrap();
    let intent = f
        .core
        .begin_ownership("Example", "example", "confirmed/fork", "takeover")
        .await
        .unwrap();
    let id = text(&intent["id"]);
    assert!(f.core.ownership_step(id, "complete").await.is_err());
    f.core.ownership_step(id, "writing").await.unwrap();
    assert!(f.core.ownership_step(id, "complete").await.is_err());
    assert_eq!(
        std::fs::read(f.core.host.home.join("settings/loader.json")).unwrap(),
        original
    );
    write(
        f.core.host.home.join("settings/loader.json"),
        b"{\"frozenPlugins\":[\"Example\"]}",
    );
    assert_eq!(
        f.core.ownership_step(id, "complete").await.unwrap()["intent"]["state"],
        "active"
    );
    let row = f.core.eligible("Example").await.unwrap();
    assert_eq!(row["repo"], "confirmed/fork");
    assert!(
        f.core
            .prepare_install(
                "other/repo",
                "v1",
                "https://github.com/other/repo/releases/download/v1/a.zip",
                Some("Example"),
                None,
                "install"
            )
            .await
            .is_err()
    );
    assert!(f.core.set_binding("Example", None).await.is_err());
    f.close().await;
}
#[tokio::test]
async fn successful_release_snapshot_is_reused_offline_and_skip_is_local() {
    let f = Fixture::new().await;
    let (url, bytes) = f.ordinary_release("v9", "2.0.0", "new");
    f.server.asset(&url, Reply::bytes(bytes), 1, 0);
    let result = f
        .core
        .get_plugin_releases("a/b", &json!("1.0.0"), "Example", &json!("example"), false)
        .await
        .unwrap();
    assert!(result["error"].is_null());
    let snapshot = result["snapshot"].clone();
    assert_eq!(
        snapshot["releases"][0]["version_evidence"]["package_version"],
        "2.0.0"
    );
    let count = f.server.count();
    let again = f
        .core
        .get_plugin_releases("a/b", &json!("1.0.0"), "Example", &json!("example"), false)
        .await
        .unwrap();
    assert_eq!(again["snapshot"], snapshot);
    f.core.skip_release("Example", "v9", true).await.unwrap();
    f.core.skip_release("Example", "v9", false).await.unwrap();
    assert_eq!(f.server.count(), count);
    let settings = load_settings(&f.core.host).unwrap();
    assert_eq!(settings["lastCheck"]["items"][0]["latest"], "2.0.0");
    f.close().await;
}
#[tokio::test]
async fn map_update_preserves_explicit_binding_and_refuses_activation_during_install() {
    let f = Fixture::new().await;
    f.core
        .set_binding("Example", Some("chosen/fork"))
        .await
        .unwrap();
    let before = std::fs::read(f.core.host.settings()).unwrap();
    let entered = Arc::new(Notify::new());
    let release = CancellationToken::new();
    let mut reply = Reply::json(
        json!({"schema_version":1,"revision":2,"updated_at":"2026-10-28","repositories":{"Example":"new/default"}}),
    );
    reply.block = Some((entered.clone(), release.clone()));
    f.server.put(model::MAP_URL, reply);
    let core = f.core.clone();
    let task = tokio::spawn(async move { core.update_map().await });
    tokio::time::timeout(Duration::from_secs(3), entered.notified())
        .await
        .unwrap();
    f.core.state.lock().await.operation =
        Some(json!({"id":"a".repeat(32),"stage":"ready","existing_name":"Other"}));
    release.cancel();
    assert!(task.await.unwrap().is_err());
    assert_eq!(f.core.state.lock().await.map.status()["revision"], 1);
    assert!(!f.core.host.runtime_dir.join("repository-map.json").exists());
    assert_eq!(std::fs::read(f.core.host.settings()).unwrap(), before);
    f.close().await;
}
#[tokio::test]
async fn self_update_uses_dedicated_record_and_correlated_cancel() {
    let f = Fixture::new().await;
    let bytes = package_bytes("decky-sideloader", "Sideloader", "0.7.0", "future");
    let url = "https://github.com/ZacharyQin/decky-sideloader/releases/download/v0.7.0/decky-sideloader-0.7.0.zip";
    f.server.put(url, Reply::bytes(bytes.clone()));
    let release = json!({"draft":false,"prerelease":false,"tag_name":"v0.7.0","assets":[{"name":"decky-sideloader-0.7.0.zip","id":123,"size":bytes.len(),"digest":format!("sha256:{}",sha(&bytes)),"browser_download_url":url}]});
    f.server.put(
        "https://api.github.com/repos/ZacharyQin/decky-sideloader/releases/latest",
        Reply::json(release.clone()),
    );
    f.server.expect_count(
        "https://api.github.com/repos/ZacharyQin/decky-sideloader/releases/latest",
        2,
    );
    let checked = f.core.check_self().await.unwrap();
    let target = &checked["check"]["release"];
    f.core
        .prepare_self(text(&target["tag"]), text(&target["sha256"]))
        .await
        .unwrap();
    let done = f.core.state.lock().await.self_done.clone().unwrap();
    tokio::time::timeout(Duration::from_secs(5), done.cancelled())
        .await
        .unwrap();
    let ready = f
        .core
        .self_public(&json!({"version":"0.6.0","fingerprint":"b".repeat(64)}))
        .await;
    assert_eq!(ready["operation"]["stage"], "ready", "{ready}");
    let id = text(&ready["operation"]["id"]);
    assert!(
        f.core
            .prepare_install(
                "a/b",
                "v1",
                "https://github.com/a/b/releases/download/v1/a.zip",
                Some("Example"),
                None,
                "install"
            )
            .await
            .is_err()
    );
    let front = json!({"version":"0.6.0","fingerprint":"b".repeat(64)});
    f.core.submit_self(id, &front).await.unwrap();
    assert!(f.core.submit_self(id, &front).await.is_err());
    f.core
        .self_event(id, "prompt", "native-self", text(&target["sha256"]))
        .await
        .unwrap();
    f.core.cancel_self(id).await.unwrap();
    f.core
        .self_event(id, "cancelled", "", "foreign-request")
        .await
        .unwrap();
    assert_eq!(
        f.core.self_public(&front).await["operation"]["stage"],
        "awaiting"
    );
    f.core
        .self_event(id, "cancelled", "", "native-self")
        .await
        .unwrap();
    assert_eq!(
        f.core.self_public(&front).await["operation"]["stage"],
        "cancelled"
    );
    assert!(f.core.state.lock().await.operation.is_none());
    f.close().await;
}
#[tokio::test]
async fn new_core_and_loaded_frontend_are_required_for_self_update_completion() {
    let f = Fixture::new().await;
    let bytes = package_bytes("decky-sideloader", "Sideloader", "0.6.0", "current");
    let archive = f.temp.path().join("target.zip");
    write(&archive, &bytes);
    let previous = package::stamp(
        &f.core.host.home,
        "decky-sideloader",
        &CancellationToken::new(),
    )
    .unwrap();
    extract(&bytes, &f.core.host.home.join("plugins"));
    let installed = model::scan(&f.core.host.home).unwrap();
    let existing = installed
        .iter()
        .find(|p| p["name"] == "Sideloader")
        .unwrap();
    let package = package::inspect(
        &archive,
        Some(existing),
        &installed,
        None,
        true,
        &CancellationToken::new(),
    )
    .unwrap();
    let id = "a".repeat(32);
    let record = json!({"schema":1,"check":null,"operation":{"id":id,"stage":"awaiting","release":{"tag":"v0.6.0"},"package":package,"from_boot":f.core.boot,"submitted_at":seconds(),"previous_stamp":previous,"request_id":"native","saw_start":true,"cancel_requested":false}});
    atomic_json(
        &f.core.host.runtime_dir.join("self-update/state.json"),
        &record,
    )
    .unwrap();
    f.core.state.lock().await.self_record = record;
    let frontend = json!({"version":"0.6.0","fingerprint":"b".repeat(64)});
    assert_ne!(
        f.core.self_status(&frontend).await.unwrap()["operation"]["stage"],
        "completed"
    );
    let (tx, _rx) = mpsc::channel(16);
    let new = Engine::new(f.core.host.clone(), tx, f.core.binary_hash.clone()).unwrap();
    assert_eq!(
        new.self_status(&Value::Null).await.unwrap()["operation"]["stage"],
        "verifying"
    );
    assert_eq!(
        new.self_status(&frontend).await.unwrap()["operation"]["stage"],
        "completed"
    );
    new.shutdown().await;
    f.close().await;
}
#[tokio::test]
async fn legacy_python_self_update_handoff_can_complete_in_rust() {
    let f = Fixture::new().await;
    let bytes = package_bytes("decky-sideloader", "Sideloader", "0.6.0", "current");
    let archive = f.temp.path().join("target.zip");
    write(&archive, &bytes);
    let previous = package::stamp(
        &f.core.host.home,
        "decky-sideloader",
        &CancellationToken::new(),
    )
    .unwrap();
    extract(&bytes, &f.core.host.home.join("plugins"));
    let installed = model::scan(&f.core.host.home).unwrap();
    let existing = installed
        .iter()
        .find(|p| p["name"] == "Sideloader")
        .unwrap();
    let mut package = package::inspect(
        &archive,
        Some(existing),
        &installed,
        None,
        true,
        &CancellationToken::new(),
    )
    .unwrap();
    package.backend = Some(crate::engine::legacy_glue_hash(&f.core.host).unwrap());
    package.binary = None;
    let record = json!({"schema":1,"check":null,"operation":{"id":"a".repeat(32),"stage":"installing","release":{"tag":"v0.6.0"},"package":package,"from_boot":"old-python-boot","submitted_at":seconds(),"previous_stamp":previous}});
    atomic_json(
        &f.core.host.runtime_dir.join("self-update/state.json"),
        &record,
    )
    .unwrap();
    let (tx, _rx) = mpsc::channel(16);
    let new = Engine::new(f.core.host.clone(), tx, f.core.binary_hash.clone()).unwrap();
    let frontend = json!({"version":"0.6.0","fingerprint":"b".repeat(64)});
    assert_eq!(
        new.self_status(&frontend).await.unwrap()["operation"]["stage"],
        "completed"
    );
    new.shutdown().await;
    f.close().await;
}
#[tokio::test]
async fn malformed_security_records_fail_closed() {
    let f = Fixture::new().await;
    assert!(model::decode_map(br#"{"schema_version":1,"revision":1,"updated_at":"2026-10-28","repositories":{"Example":[]}}"#).is_err());
    let invalid = json!({"version":1,"entries":[{"id":"a".repeat(32),"name":"Example","folder":"example","repo":[],"prior_frozen":false,"action":"takeover","state":"active"}]});
    assert!(model::validate_ledger(&invalid).is_err());
    f.close().await;
}
#[tokio::test]
async fn ignored_ranges_remain_bounded_by_each_requested_budget() {
    let f = Fixture::new().await;
    let (url, original) = f.ordinary_release("v9", "2.0.0", "new");
    f.server
        .optional("https://api.github.com/repos/a/b/releases?per_page=100&page=1");
    f.server.asset(&url, Reply::bytes(original.clone()), 0, 0);
    f.server.expect_range(&url, "bytes=-65557", 2);
    f.server.expect_range(&url, "bytes=0-29", 1);
    let mut source = zip::ZipArchive::new(std::io::Cursor::new(&original)).unwrap();
    let offset = source
        .by_name("example/package.json")
        .unwrap()
        .header_start();
    f.server
        .expect_range(&url, &format!("bytes={offset}-{}", offset + 29), 1);
    f.server
        .routes
        .lock()
        .unwrap()
        .get_mut(&url)
        .unwrap()
        .ignore_range = true;
    let transport = f.core.transport().unwrap();
    assert_eq!(
        transport
            .range(&url, None, 65557, &f.core.stop)
            .await
            .unwrap()
            .0,
        original
    );
    assert!(
        transport
            .range(&url, Some(0), 29, &f.core.stop)
            .await
            .is_err()
    );
    let evidence = crate::zip_version::probe(&transport, &url, &f.core.stop).await;
    assert_eq!(evidence["package_state"], "unknown");
    f.close().await;
}
#[tokio::test]
async fn continuous_query_body_may_exceed_five_seconds() {
    let f = Fixture::new().await;
    let mut reply = Reply::bytes(b"123456".to_vec());
    reply.chunks = Some((1, Duration::from_millis(1050)));
    let url = "https://raw.githubusercontent.com/a/b/main/data";
    f.server.put(url, reply);
    let bytes = f
        .core
        .transport()
        .unwrap()
        .bytes(url, 100, &f.core.stop)
        .await
        .unwrap();
    assert_eq!(bytes, b"123456");
    f.close().await;
}
#[tokio::test]
async fn invalid_json_never_triggers_proxy_fallback() {
    let f = Fixture::new().await;
    f.core
        .set_setting("networkMode", json!("auto"))
        .await
        .unwrap();
    let url = "https://api.github.com/repos/a/b/releases/latest";
    f.server.put(url, Reply::bytes(b"bad json".to_vec()));
    assert!(
        f.core
            .transport()
            .unwrap()
            .json(url, 100, &f.core.stop)
            .await
            .is_err()
    );
    assert_eq!(f.server.count(), 1);
    f.close().await;
}
#[tokio::test]
async fn rate_limited_query_falls_back_without_changing_identity() {
    let f = Fixture::new().await;
    f.core
        .set_setting("networkMode", json!("auto"))
        .await
        .unwrap();
    let url = "https://api.github.com/repos/a/b/releases/latest";
    let mut first = Reply::bytes(Vec::new());
    first.status = 429;
    f.server.put(url, first);
    f.server.put(
        &format!("https://gh-proxy.org/{url}"),
        Reply::json(json!({"ok":true})),
    );
    assert_eq!(
        f.core
            .transport()
            .unwrap()
            .json(url, 100, &f.core.stop)
            .await
            .unwrap()["ok"],
        true
    );
    assert_eq!(f.server.count(), 2);
    assert_eq!(
        f.core.last_route.lock().unwrap().as_ref().unwrap()["route"],
        "gh-proxy"
    );
    f.close().await;
}
#[tokio::test]
async fn backup_rejects_symlink_and_cleanup_preserves_unknown_task() {
    let f = Fixture::new().await;
    let source = f.core.host.home.join("data/example");
    std::fs::create_dir_all(&source).unwrap();
    let foreign = f.temp.path().join("foreign");
    write(&foreign, b"protected");
    std::os::unix::fs::symlink(&foreign, source.join("link")).unwrap();
    assert!(
        package::backup(
            &f.core.host.home,
            "example",
            &f.temp.path().join("backup.zip"),
            &CancellationToken::new()
        )
        .is_err()
    );
    let id = "a".repeat(32);
    let dir = f.core.op_directory(&id).unwrap();
    write(dir.join("data-backup.zip"), b"backup");
    f.core.state.lock().await.operation = Some(json!({"id":id,"stage":"unknown"}));
    f.core.cleanup_backups(false).await.unwrap();
    assert!(dir.join("data-backup.zip").exists());
    assert_eq!(std::fs::read(foreign).unwrap(), b"protected");
    f.close().await;
}
