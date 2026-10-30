use super::*;
use std::io::Read;

fn read_zip(archive: &mut zip::ZipArchive<std::fs::File>, name: &str) -> Vec<u8> {
    let mut bytes = Vec::new();
    archive
        .by_name(name)
        .unwrap()
        .read_to_end(&mut bytes)
        .unwrap();
    bytes
}

#[tokio::test]
async fn query_and_real_remote_dependency_use_all_four_route_policies() {
    const API: &str = "https://api.github.com/repos/a/b";
    for mode in ["direct", "auto", "gh-proxy", "ghfast"] {
        let f = Fixture::new().await;
        f.core
            .set_setting("networkMode", json!(mode))
            .await
            .unwrap();
        let api = if mode == "gh-proxy" {
            format!("https://gh-proxy.org/{API}")
        } else {
            API.to_owned()
        };
        let asset = match mode {
            "gh-proxy" => format!("https://gh-proxy.org/{DEP_A}"),
            "ghfast" => format!("https://ghfast.top/{DEP_A}"),
            _ => DEP_A.to_owned(),
        };
        f.server
            .expect(&api, Reply::json(json!({"fixed":"query"})), 1);
        f.server
            .expect(&asset, Reply::bytes(b"fixed dependency".to_vec()), 1);
        let transport = f.core.transport().unwrap();
        assert_eq!(
            transport
                .json(API, 4096, &CancellationToken::new())
                .await
                .unwrap(),
            json!({"fixed":"query"})
        );
        let bytes = dependency_package(json!([dep("engine", DEP_A, b"fixed dependency")]), vec![]);
        let archive = f.temp.path().join("plugin.zip");
        write(&archive, &bytes);
        let cancel = CancellationToken::new();
        let package = package::inspect(&archive, None, &[], None, false, &cancel).unwrap();
        let result = package::remote_binaries(&archive, &package, &transport, 1, &cancel, |_| {})
            .await
            .unwrap()
            .unwrap();
        assert_eq!(result["names"], json!(["engine"]));
        let mut merged = zip::ZipArchive::new(std::fs::File::open(&archive).unwrap()).unwrap();
        assert_eq!(
            read_zip(&mut merged, "example/bin/engine"),
            b"fixed dependency"
        );
        assert_eq!(
            std::fs::read(archive.with_file_name("source.zip")).unwrap(),
            bytes
        );
        f.close().await;
    }
}

#[tokio::test]
async fn dependency_source_collisions_preserve_the_original_and_private_files() {
    for collision in ["correct", "different", "symlink", "hardlink"] {
        let f = Fixture::new().await;
        let bytes = dependency_package(json!([dep("engine", DEP_A, b"fixed")]), vec![]);
        let archive = f.temp.path().join("plugin.zip");
        let source = archive.with_file_name("source.zip");
        let private = f.temp.path().join("private.txt");
        write(&archive, &bytes);
        let private_bytes = if collision == "hardlink" {
            bytes.clone()
        } else {
            b"unrelated private bytes".to_vec()
        };
        write(&private, &private_bytes);
        match collision {
            "correct" => write(&source, &bytes),
            "different" => write(&source, b"unrelated source bytes"),
            "hardlink" => std::fs::hard_link(&private, &source).unwrap(),
            _ => std::os::unix::fs::symlink(&private, &source).unwrap(),
        }
        if collision == "correct" {
            f.server.expect(DEP_A, Reply::bytes(b"fixed".to_vec()), 1);
        } else {
            f.server.put(DEP_A, Reply::bytes(b"fixed".to_vec()));
            f.server.optional(DEP_A);
        }
        let cancel = CancellationToken::new();
        let package = package::inspect(&archive, None, &[], None, false, &cancel).unwrap();
        let result = package::remote_binaries(
            &archive,
            &package,
            &f.core.transport().unwrap(),
            1,
            &cancel,
            |_| {},
        )
        .await;
        if collision == "correct" {
            assert_eq!(result.unwrap().unwrap()["count"], 1);
            assert_eq!(std::fs::read(&source).unwrap(), bytes);
        } else {
            assert!(
                result.is_err(),
                "source.zip collision {collision} was accepted and overwritten"
            );
            assert_eq!(std::fs::read(&archive).unwrap(), bytes);
        }
        if collision == "different" {
            assert_eq!(std::fs::read(&source).unwrap(), b"unrelated source bytes");
        }
        if collision == "symlink" {
            assert!(source.is_symlink());
        }
        assert_eq!(std::fs::read(private).unwrap(), private_bytes);
        if collision != "correct" {
            assert!(
                f.server.seen.lock().unwrap().is_empty(),
                "invalid retained source caused a network request"
            );
        }
        no_dependency_temps(f.temp.path());
        f.close().await;
    }
}

#[tokio::test]
async fn allowed_download_redirects_are_followed_with_an_exact_limit() {
    for redirects in [1, 5, 6] {
        let f = Fixture::new().await;
        let start = "https://github.com/a/b/releases/download/v2/hop-0";
        for index in 0..redirects.min(6) {
            let next = if redirects == 1 {
                "https://release-assets.githubusercontent.com/fixed/asset.bin".to_owned()
            } else {
                format!(
                    "https://github.com/a/b/releases/download/v2/hop-{}",
                    index + 1
                )
            };
            let mut reply = Reply::status(302);
            reply.headers.push(("Location".into(), next));
            f.server.expect(
                &format!("https://github.com/a/b/releases/download/v2/hop-{index}"),
                reply,
                1,
            );
        }
        let last = if redirects == 1 {
            "https://release-assets.githubusercontent.com/fixed/asset.bin".to_owned()
        } else {
            format!("https://github.com/a/b/releases/download/v2/hop-{redirects}")
        };
        if redirects < 6 {
            f.server
                .expect(&last, Reply::bytes(b"fixed redirected bytes".to_vec()), 1);
        }
        let path = f.temp.path().join("redirect.bin");
        let result = f
            .core
            .transport()
            .unwrap()
            .download(start, &path, 4096, 1, &CancellationToken::new(), |_| {})
            .await;
        if redirects < 6 {
            assert_eq!(result.unwrap()["received"], 22);
            assert_eq!(std::fs::read(path).unwrap(), b"fixed redirected bytes");
        } else {
            assert!(result.unwrap_err().message.contains("重定向"));
            assert_eq!(std::fs::read(path).unwrap(), b"");
        }
        f.close().await;
    }
}

#[tokio::test]
async fn query_and_download_header_timeouts_close_the_blocked_request() {
    for query in [true, false] {
        let f = Fixture::new().await;
        let url = if query {
            "https://api.github.com/repos/a/b"
        } else {
            DEP_A
        };
        let entered = Arc::new(Notify::new());
        let release = CancellationToken::new();
        let mut reply = Reply::bytes(b"blocked".to_vec());
        reply.block = Some((entered.clone(), release));
        f.server.expect(url, reply, 1);
        let transport = f.core.transport().unwrap();
        let path = f.temp.path().join("timed-out.bin");
        let job = tokio::spawn(async move {
            if query {
                transport.json(url, 4096, &CancellationToken::new()).await
            } else {
                transport
                    .download(url, &path, 4096, 1, &CancellationToken::new(), |_| {})
                    .await
            }
        });
        tokio::time::timeout(Duration::from_secs(3), entered.notified())
            .await
            .unwrap();
        tokio::time::pause();
        tokio::time::advance(Duration::from_secs(if query { 6 } else { 61 })).await;
        tokio::time::resume();
        let error = tokio::time::timeout(Duration::from_secs(3), job)
            .await
            .unwrap()
            .unwrap()
            .unwrap_err();
        assert_eq!(error.code, "timeout");
        f.close().await;
    }
}

#[tokio::test]
async fn stalled_download_body_obeys_all_mode_policies_and_fallback_discards_prefix() {
    for mode in ["direct", "gh-proxy", "ghfast", "auto"] {
        let f = Fixture::new().await;
        f.core
            .set_setting("networkMode", json!(mode))
            .await
            .unwrap();
        let url = match mode {
            "gh-proxy" => format!("https://gh-proxy.org/{MAIN}"),
            "ghfast" => format!("https://ghfast.top/{MAIN}"),
            _ => MAIN.to_owned(),
        };
        let mut stalled = Reply::bytes(b"bad!tail".to_vec());
        stalled.body_prefix = 4;
        stalled.chunks = Some((1, Duration::from_secs(120)));
        f.server.expect(&url, stalled, 1);
        let good = b"fresh independent payload";
        if mode == "auto" {
            f.server.expect(
                &format!("https://gh-proxy.org/{MAIN}"),
                Reply::bytes(good.to_vec()),
                1,
            );
        }
        let entered = Arc::new(Notify::new());
        let signal = entered.clone();
        let updates = Arc::new(std::sync::Mutex::new(Vec::<Value>::new()));
        let captured = updates.clone();
        let transport = f.core.transport().unwrap();
        let path = f.temp.path().join("body-timeout.zip");
        let output = path.clone();
        let job = tokio::spawn(async move {
            transport
                .download(
                    MAIN,
                    &output,
                    4096,
                    3,
                    &CancellationToken::new(),
                    move |value| {
                        let received = value["received"] == 4 && value["status"] == "downloading";
                        captured.lock().unwrap().push(value);
                        if received {
                            signal.notify_one();
                        }
                    },
                )
                .await
        });
        tokio::time::timeout(Duration::from_secs(3), entered.notified())
            .await
            .unwrap();
        tokio::time::pause();
        tokio::time::advance(Duration::from_secs(61)).await;
        tokio::time::resume();
        let result = tokio::time::timeout(Duration::from_secs(3), job)
            .await
            .unwrap()
            .unwrap();
        if mode == "auto" {
            let value = result.unwrap();
            assert_eq!(value["route"], "gh-proxy");
            assert_eq!(value["attempt"], 2);
            assert_eq!(std::fs::read(&path).unwrap(), good);
            assert!(
                updates
                    .lock()
                    .unwrap()
                    .iter()
                    .any(|value| value["attempt"] == 2 && value["received"] == 0)
            );
        } else {
            assert_eq!(result.unwrap_err().code, "timeout");
            assert_eq!(std::fs::read(&path).unwrap(), b"bad!");
            assert!(
                !updates
                    .lock()
                    .unwrap()
                    .iter()
                    .any(|value| value["status"] == "complete")
            );
        }
        f.close().await;
    }
}

#[tokio::test]
async fn progressing_download_body_cannot_extend_the_configured_total_budget() {
    let f = Fixture::new().await;
    let mut reply = Reply::bytes(b"ABCDE".to_vec());
    reply.body_prefix = 1;
    reply.chunks = Some((1, Duration::from_secs(25)));
    f.server.expect(MAIN, reply, 1);
    let transport = f.core.transport().unwrap();
    let path = f.temp.path().join("total-timeout.zip");
    let updates = Arc::new(std::sync::Mutex::new(Vec::<Value>::new()));
    let captured = updates.clone();
    let began = std::time::Instant::now();
    let error = tokio::time::timeout(
        Duration::from_secs(65),
        transport.download(
            MAIN,
            &path,
            4096,
            1,
            &CancellationToken::new(),
            move |value| captured.lock().unwrap().push(value),
        ),
    )
    .await
    .unwrap()
    .unwrap_err();
    assert!(began.elapsed() >= Duration::from_secs(60));
    assert_eq!(error.code, "total_timeout");
    assert_eq!(std::fs::read(path).unwrap(), b"ABC");
    let updates = updates.lock().unwrap().clone();
    assert_eq!(
        updates
            .iter()
            .filter(|value| value["status"] == "downloading")
            .map(|value| value["received"].as_u64().unwrap())
            .collect::<Vec<_>>(),
        [1, 2, 3]
    );
    assert!(!updates.iter().any(|value| value["status"] == "complete"));
    f.close().await;
}

#[tokio::test]
async fn changing_network_mode_does_not_redirect_an_already_started_download() {
    let f = Fixture::new().await;
    let entered = Arc::new(Notify::new());
    let release = CancellationToken::new();
    let mut reply = Reply::bytes(b"direct old task".to_vec());
    reply.block = Some((entered.clone(), release.clone()));
    f.server.expect(DEP_A, reply, 1);
    f.server.expect(
        &format!("https://ghfast.top/{DEP_A}"),
        Reply::bytes(b"ghfast new task".to_vec()),
        1,
    );
    let transport = f.core.transport().unwrap();
    let old = f.temp.path().join("old.bin");
    let path = old.clone();
    let job = tokio::spawn(async move {
        transport
            .download(DEP_A, &path, 4096, 1, &CancellationToken::new(), |_| {})
            .await
    });
    tokio::time::timeout(Duration::from_secs(3), entered.notified())
        .await
        .unwrap();
    f.core
        .set_setting("networkMode", json!("ghfast"))
        .await
        .unwrap();
    release.cancel();
    assert_eq!(
        tokio::time::timeout(Duration::from_secs(3), job)
            .await
            .unwrap()
            .unwrap()
            .unwrap()["route"],
        "direct"
    );
    let new = f.temp.path().join("new.bin");
    assert_eq!(
        f.core
            .transport()
            .unwrap()
            .download(DEP_A, &new, 4096, 1, &CancellationToken::new(), |_| {})
            .await
            .unwrap()["route"],
        "ghfast"
    );
    assert_eq!(std::fs::read(old).unwrap(), b"direct old task");
    assert_eq!(std::fs::read(new).unwrap(), b"ghfast new task");
    f.close().await;
}
