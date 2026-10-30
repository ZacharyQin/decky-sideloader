use super::*;
use std::{
    collections::BTreeSet,
    path::{Path, PathBuf},
    sync::atomic::{AtomicBool, Ordering},
};

struct WriteLimit {
    previous: libc::rlimit,
    signal: libc::sighandler_t,
}
impl WriteLimit {
    fn arm() -> Self {
        assert!(
            std::env::var_os("SIDELOADER_PERSISTENCE_CASE").is_some(),
            "process limits only inside the dedicated child"
        );
        let mut previous = libc::rlimit {
            rlim_cur: 0,
            rlim_max: 0,
        };
        assert_eq!(
            unsafe { libc::getrlimit(libc::RLIMIT_FSIZE, &mut previous) },
            0
        );
        let signal = unsafe { libc::signal(libc::SIGXFSZ, libc::SIG_IGN) };
        assert_ne!(signal, libc::SIG_ERR);
        let limit = libc::rlimit {
            rlim_cur: 1,
            rlim_max: previous.rlim_max,
        };
        assert_eq!(unsafe { libc::setrlimit(libc::RLIMIT_FSIZE, &limit) }, 0);
        Self { previous, signal }
    }
}
impl Drop for WriteLimit {
    fn drop(&mut self) {
        assert_eq!(
            unsafe { libc::setrlimit(libc::RLIMIT_FSIZE, &self.previous) },
            0
        );
        unsafe {
            libc::signal(libc::SIGXFSZ, self.signal);
        }
    }
}
fn arm(path: PathBuf) -> Arc<AtomicBool> {
    let hit = Arc::new(AtomicBool::new(false));
    let marked = hit.clone();
    atomic_write_hook(path, move || {
        marked.store(true, Ordering::SeqCst);
        WriteLimit::arm()
    });
    hit
}
fn bytes(path: &Path) -> Option<Vec<u8>> {
    std::fs::read(path).ok()
}
fn names(path: &Path) -> BTreeSet<std::ffi::OsString> {
    std::fs::read_dir(path)
        .unwrap()
        .map(|entry| entry.unwrap().file_name())
        .collect()
}
fn failed(error: Error, hit: &AtomicBool) {
    assert!(
        hit.load(Ordering::SeqCst),
        "the selected durable write was never reached"
    );
    assert_eq!(error.code, "io_failed", "{error:?}");
    assert!(
        error.message.contains("os error 27"),
        "expected actual EFBIG: {error:?}"
    );
}
async fn exercise(case: &str) {
    let f = Fixture::new().await;
    let protected = [
        (
            "settings/example/private.json",
            b"{\"keep\":true}\n".as_slice(),
        ),
        (
            "data/example/state.bin",
            b"unchanged private data".as_slice(),
        ),
    ];
    for (name, data) in protected {
        write(f.core.host.home.join(name), data);
    }
    f.core
        .set_setting("networkMode", json!("direct"))
        .await
        .unwrap();
    if case == "backup-result" {
        let (url, _) = f.ordinary_release("v2.0.0", "2.0.0", "new");
        let path = f.core.host.runtime_dir.join("installations/operation.json");
        let hit = Arc::new(AtomicBool::new(false));
        let marked = hit.clone();
        atomic_write_hook_when(
            path,
            |data| {
                serde_json::from_slice::<Value>(data)
                    .is_ok_and(|value| value["stage"] == "backup" && value["backup"].is_object())
            },
            move || {
                marked.store(true, Ordering::SeqCst);
                WriteLimit::arm()
            },
        );
        let operation = f
            .core
            .prepare_install("a/b", "v2.0.0", &url, Some("Example"), None, "install")
            .await
            .unwrap();
        let done = f.core.state.lock().await.op_done.clone().unwrap();
        tokio::time::timeout(Duration::from_secs(5), done.cancelled())
            .await
            .unwrap();
        let interrupted = f.core.operation_public().await.unwrap();
        assert!(hit.load(Ordering::SeqCst));
        assert_eq!(interrupted["stage"], "failed");
        assert_eq!(interrupted["diagnostic_error"]["errorCode"], "io_failed");
        assert!(text(&interrupted["error"]).contains("os error 27"));
        assert!(interrupted["backup"].is_null());
        let backup = f
            .core
            .op_directory(text(&operation["id"]))
            .unwrap()
            .join("data-backup.zip");
        let saved = std::fs::read(&backup).unwrap();
        assert_eq!(
            zip::ZipArchive::new(std::io::Cursor::new(&saved))
                .unwrap()
                .len(),
            3
        );
        f.core
            .reuse_download(text(&operation["id"]), false)
            .await
            .unwrap();
        let done = f.core.state.lock().await.op_done.clone().unwrap();
        tokio::time::timeout(Duration::from_secs(5), done.cancelled())
            .await
            .unwrap();
        let continued = f.core.operation_public().await.unwrap();
        assert_eq!(
            continued["stage"], "ready",
            "completed backup could not be recovered after its journal write failed: {continued}"
        );
        assert_eq!(continued["backup"]["sha256"], sha(&saved));
        assert_eq!(
            std::fs::read(backup).unwrap(),
            saved,
            "recovery overwrote a completed backup"
        );
        assert_eq!(
            f.server.count_url(&url),
            1,
            "continuation redownloaded an already verified main package"
        );
    } else if case.starts_with("install-") {
        let (id, archive) = f.prepare().await;
        let journal = f.core.host.runtime_dir.join("installations/operation.json");
        let checksum =
            text(&f.core.operation_public().await.unwrap()["package"]["sha256"]).to_owned();
        if case != "install-submit" {
            f.core.submit_operation(&id, false, false).await.unwrap();
        }
        if ["install-start", "install-result"].contains(&case) {
            f.core
                .install_event(&id, "prompt", "Example", "fixture-native", &checksum)
                .await
                .unwrap();
        }
        if case == "install-result" {
            f.core
                .install_event(&id, "start", "Example", "", "")
                .await
                .unwrap();
            extract(&archive, &f.core.host.home.join("plugins"));
            for row in f.native.lock().unwrap()["snapshot"]["plugins"]
                .as_array_mut()
                .unwrap()
            {
                if row["name"] == "Example" {
                    row["version"] = json!("2.0.0");
                }
            }
            f.core
                .install_event(&id, "finish", "Example", "", "")
                .await
                .unwrap();
        }
        let target = if case == "install-result" {
            f.core.host.settings()
        } else {
            journal.clone()
        };
        let before = bytes(&target);
        let siblings = names(target.parent().unwrap());
        let hit = arm(target.clone());
        let error = match case {
            "install-submit" => f
                .core
                .submit_operation(&id, false, false)
                .await
                .unwrap_err(),
            "install-prompt" => f
                .core
                .install_event(&id, "prompt", "Example", "fixture-native", &checksum)
                .await
                .unwrap_err(),
            "install-start" => f
                .core
                .install_event(&id, "start", "Example", "", "")
                .await
                .unwrap_err(),
            "install-result" => f.core.operation_status().await.unwrap_err(),
            _ => panic!("unknown installation persistence phase"),
        };
        failed(error, &hit);
        assert_eq!(bytes(&target), before);
        assert_eq!(
            names(target.parent().unwrap()),
            siblings,
            "partial atomic temporary file leaked"
        );
        let state = f.core.operation_public().await.unwrap();
        assert_ne!(state["outcome"], "success");
        assert_ne!(state["binding_saved"], true);
        match case {
            "install-submit" => {
                assert_eq!(state["stage"], "ready");
                f.core.submit_operation(&id, false, false).await.unwrap();
            }
            "install-prompt" => {
                assert_eq!(state["stage"], "awaiting");
                assert!(state["request_id"].is_null());
                f.core
                    .install_event(&id, "prompt", "Example", "fixture-native", &checksum)
                    .await
                    .unwrap();
            }
            "install-start" => {
                assert_eq!(state["stage"], "awaiting");
                assert_ne!(state["saw_start"], true);
                f.core
                    .install_event(&id, "start", "Example", "", "")
                    .await
                    .unwrap();
            }
            _ => {
                assert_eq!(
                    state["stage"], "verified",
                    "real file verification must persist without inventing saved-result success"
                );
                assert_eq!(
                    read_json(&journal, 1024 * 1024).unwrap()["stage"],
                    "verified"
                );
                let saved = f.core.operation_status().await.unwrap();
                assert_eq!(saved["stage"], "closed");
                assert_eq!(saved["outcome"], "success");
                assert_eq!(saved["binding_saved"], true);
                assert!(
                    f.core
                        .op_directory(&id)
                        .unwrap()
                        .join("data-backup.zip")
                        .is_file()
                );
            }
        }
    } else if case == "self-submit" {
        let archive = package_bytes("decky-sideloader", "Sideloader", "0.7.0", "future");
        let url = "https://github.com/ZacharyQin/decky-sideloader/releases/download/v0.7.0/decky-sideloader-0.7.0.zip";
        f.server.expect(url, Reply::bytes(archive.clone()), 1);
        let latest = "https://api.github.com/repos/ZacharyQin/decky-sideloader/releases/latest";
        f.server.expect(latest,Reply::json(json!({"draft":false,"prerelease":false,"tag_name":"v0.7.0","assets":[{"name":"decky-sideloader-0.7.0.zip","id":123,"size":archive.len(),"digest":format!("sha256:{}",sha(&archive)),"browser_download_url":url}]})),2);
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
        let front = json!({"version":"0.6.0","fingerprint":"b".repeat(64)});
        let ready = f.core.self_public(&front).await;
        assert_eq!(ready["operation"]["stage"], "ready");
        let id = text(&ready["operation"]["id"]);
        let path = f.core.host.runtime_dir.join("self-update/state.json");
        let before = bytes(&path);
        let siblings = names(path.parent().unwrap());
        let hit = arm(path.clone());
        failed(f.core.submit_self(id, &front).await.unwrap_err(), &hit);
        assert_eq!(bytes(&path), before);
        assert_eq!(names(path.parent().unwrap()), siblings);
        assert_eq!(
            f.core.self_public(&front).await["operation"]["stage"],
            "ready"
        );
        assert!(f.core.state.lock().await.operation.is_none());
        f.core.submit_self(id, &front).await.unwrap();
    } else {
        assert!(["ownership-intent", "ownership-result"].contains(&case));
        f.native.lock().unwrap()["capable"] = json!(["Example"]);
        let loader = f.core.host.home.join("settings/loader.json");
        write(&loader, b"{\"frozenPlugins\":[\"Other Fixture\"]}");
        let id = if case == "ownership-result" {
            let intent = f
                .core
                .begin_ownership("Example", "example", "confirmed/fork", "takeover")
                .await
                .unwrap();
            let id = text(&intent["id"]).to_owned();
            f.core.ownership_step(&id, "writing").await.unwrap();
            write(
                &loader,
                b"{\"frozenPlugins\":[\"Other Fixture\",\"Example\"]}",
            );
            f.core.ownership_step(&id, "confirming").await.unwrap();
            Some(id)
        } else {
            None
        };
        let frozen = std::fs::read(&loader).unwrap();
        let path = f.core.host.settings_dir.join("ownership.json");
        let before = bytes(&path);
        let siblings = names(path.parent().unwrap());
        let hit = arm(path.clone());
        let error = if let Some(id) = &id {
            f.core.ownership_step(id, "complete").await.unwrap_err()
        } else {
            f.core
                .begin_ownership("Example", "example", "confirmed/fork", "takeover")
                .await
                .unwrap_err()
        };
        failed(error, &hit);
        assert_eq!(bytes(&path), before);
        assert_eq!(names(path.parent().unwrap()), siblings);
        assert_eq!(std::fs::read(&loader).unwrap(), frozen);
        assert!(
            f.core.eligible("Example").await.is_err(),
            "unpersisted ownership result granted permissions"
        );
        if let Some(id) = &id {
            assert_eq!(
                model::ledger(&f.core.host).unwrap()["entries"][0]["state"],
                "confirming"
            );
            assert_eq!(
                f.core.ownership_step(id, "complete").await.unwrap()["intent"]["state"],
                "active"
            );
            assert_eq!(
                f.core.eligible("Example").await.unwrap()["repo"],
                "confirmed/fork"
            );
        } else {
            assert!(array(&model::ledger(&f.core.host).unwrap()["entries"]).is_empty());
            assert_eq!(
                f.core
                    .begin_ownership("Example", "example", "confirmed/fork", "takeover")
                    .await
                    .unwrap()["state"],
                "prepared"
            );
        }
        assert_eq!(
            std::fs::read(loader).unwrap(),
            frozen,
            "backend must not rewrite native freeze policy"
        );
    }
    for (name, data) in protected {
        assert_eq!(std::fs::read(f.core.host.home.join(name)).unwrap(), data);
    }
    f.close().await;
}

#[tokio::test]
async fn durable_write_failures_preserve_last_record_and_block_unproven_permissions() {
    if let Ok(case) = std::env::var("SIDELOADER_PERSISTENCE_CASE") {
        exercise(&case).await;
        return;
    }
    for case in [
        "backup-result",
        "install-submit",
        "install-prompt",
        "install-start",
        "install-result",
        "self-submit",
        "ownership-intent",
        "ownership-result",
    ] {
        let output=tokio::time::timeout(Duration::from_secs(15),tokio::process::Command::new(std::env::current_exe().unwrap()).args(["--exact","runtime_tests::recovery::persistence::durable_write_failures_preserve_last_record_and_block_unproven_permissions","--nocapture"]).env("SIDELOADER_PERSISTENCE_CASE",case).kill_on_drop(true).output()).await.unwrap().unwrap();
        assert!(
            output.status.success(),
            "{case}\n{}\n{}",
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );
    }
}
