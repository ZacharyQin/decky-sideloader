use super::*;
#[path = "runtime_share_tests.rs"]
mod share_lifecycle;

#[tokio::test]
async fn reinstall_waits_for_confirmed_absence_before_installation() {
    let f = Fixture::new().await;
    let (identifier, bytes) = f.prepare_mode("reinstall").await;
    assert!(
        f.core
            .submit_operation(&identifier, false, false)
            .await
            .is_err()
    );
    assert_eq!(
        f.core
            .submit_operation(&identifier, false, true)
            .await
            .unwrap()["stage"],
        "uninstalling"
    );
    f.core
        .install_event(&identifier, "uninstall_started", "Example", "", "")
        .await
        .unwrap();
    assert!(
        f.core
            .submit_operation(&identifier, true, false)
            .await
            .is_err()
    );
    // A failed absence check is terminal: it must never become a second install attempt.
    assert_eq!(f.core.operation_public().await.unwrap()["stage"], "unknown");
    assert!(
        f.core
            .submit_operation(&identifier, true, false)
            .await
            .is_err()
    );
    assert!(
        f.core
            .op_directory(&identifier)
            .unwrap()
            .join("plugin.zip")
            .exists()
    );
    assert!(!bytes.is_empty());
    f.close().await;
}

#[tokio::test]
async fn confirmed_reinstall_preserves_backup_and_requires_new_loader_evidence() {
    let f = Fixture::new().await;
    let (identifier, bytes) = f.prepare_mode("reinstall").await;
    f.core
        .submit_operation(&identifier, false, true)
        .await
        .unwrap();
    f.core
        .install_event(&identifier, "uninstall_started", "Example", "", "")
        .await
        .unwrap();
    std::fs::remove_dir_all(f.core.host.home.join("plugins/example")).unwrap();
    f.native.lock().unwrap()["snapshot"]["plugins"] =
        json!([{"name":"Sideloader","version":"0.6.0","load_type":0,"disabled":false}]);
    let pending = f
        .core
        .submit_operation(&identifier, true, false)
        .await
        .unwrap();
    assert_eq!(pending["stage"], "awaiting");
    extract(&bytes, &f.core.host.home.join("plugins"));
    let digest = text(&pending["package"]["sha256"]);
    f.core
        .install_event(&identifier, "prompt", "Example", "reinstall-native", digest)
        .await
        .unwrap();
    f.core
        .install_event(&identifier, "start", "Example", "", "")
        .await
        .unwrap();
    f.core
        .install_event(&identifier, "finish", "Example", "", "")
        .await
        .unwrap();
    assert_eq!(
        f.core.operation_status().await.unwrap()["outcome"],
        "success"
    );
    assert!(
        f.core
            .op_directory(&identifier)
            .unwrap()
            .join("data-backup.zip")
            .exists()
    );
    f.close().await;
}

#[tokio::test]
async fn cancellation_during_native_uninstall_never_continues_installation() {
    let f = Fixture::new().await;
    let (identifier, _) = f.prepare_mode("reinstall").await;
    f.core
        .submit_operation(&identifier, false, true)
        .await
        .unwrap();
    f.core
        .install_event(&identifier, "uninstall_started", "Example", "", "")
        .await
        .unwrap();
    assert_eq!(
        f.core.cancel_operation(&identifier).await.unwrap()["cancel_requested"],
        true
    );
    std::fs::remove_dir_all(f.core.host.home.join("plugins/example")).unwrap();
    let cancelled = f
        .core
        .submit_operation(&identifier, true, false)
        .await
        .unwrap();
    assert_eq!(cancelled["stage"], "cancelled");
    assert!(cancelled["submitted_at"].is_null());
    assert!(cancelled["request_id"].is_null());
    f.close().await;
}

async fn pickup(url: &str, method: &str, headers: &str) -> Vec<u8> {
    pickup_exchange(url, method, headers, false).await
}

async fn pickup_exchange(url: &str, method: &str, headers: &str, allow_rejection: bool) -> Vec<u8> {
    let url = url::Url::parse(url).unwrap();
    let host = url.host_str().unwrap();
    let port = url.port().unwrap();
    let connection = tokio::time::timeout(
        Duration::from_secs(3),
        tokio::net::TcpStream::connect((host, port)),
    )
    .await
    .unwrap();
    let mut stream = match connection {
        Err(error)
            if allow_rejection
                && matches!(
                    error.kind(),
                    std::io::ErrorKind::ConnectionRefused
                        | std::io::ErrorKind::ConnectionReset
                        | std::io::ErrorKind::ConnectionAborted
                ) =>
        {
            return Vec::new();
        }
        result => result.unwrap(),
    };
    let written = tokio::time::timeout(
        Duration::from_secs(3),
        stream.write_all(
            format!(
                "{method} {} HTTP/1.1\r\nHost: {host}:{port}\r\n{headers}Connection: close\r\n\r\n",
                url.path()
            )
            .as_bytes(),
        ),
    )
    .await
    .unwrap();
    if let Err(error) = written {
        assert!(
            allow_rejection
                && matches!(
                    error.kind(),
                    std::io::ErrorKind::BrokenPipe | std::io::ErrorKind::ConnectionReset
                ),
            "{error}"
        );
        return Vec::new();
    }
    let mut bytes = Vec::new();
    let read = tokio::time::timeout(Duration::from_secs(3), stream.read_to_end(&mut bytes))
        .await
        .unwrap();
    if let Err(error) = read {
        assert!(
            allow_rejection
                && matches!(
                    error.kind(),
                    std::io::ErrorKind::ConnectionReset | std::io::ErrorKind::ConnectionAborted
                ),
            "{error}"
        );
        // Keep partial bytes: a reset must never conceal leaked content.
    }
    bytes
}

#[tokio::test]
async fn diagnostics_share_is_idempotent_head_safe_and_stops_after_three_downloads() {
    let f = Fixture::new().await;
    f.core.diagnostics.record("rpc_failed", json!({"rpc":"check_updates", "message":"TEST_PRIVATE_SENTINEL", "url":"https://secret.invalid"}));
    assert!(
        f.core
            .start_share("127.0.0.1", &"a".repeat(32), None, &Value::Null)
            .await
            .is_err()
    );
    let session = "a".repeat(32);
    let (first, duplicate) = tokio::time::timeout(Duration::from_secs(5), async {
        tokio::join!(
            f.core
                .start_share_loopback_fixture(&session, None, &Value::Null),
            f.core
                .start_share_loopback_fixture(&session, None, &Value::Null)
        )
    })
    .await
    .unwrap();
    let first = first.unwrap();
    let duplicate = duplicate.unwrap();
    assert_eq!(first["state"], "active", "{first}");
    assert_eq!(first["url"], duplicate["url"]);
    let url = text(&first["url"]);
    assert!(
        !pickup(&format!("{url}0"), "GET", "")
            .await
            .starts_with(b"HTTP/1.1 200")
    );
    let head = pickup(url, "HEAD", "").await;
    assert!(head.starts_with(b"HTTP/1.1 200"));
    assert!(head.ends_with(b"\r\n\r\n"));
    assert_eq!(f.core.sharing.status().await["downloads"], 0);
    assert!(
        pickup(url, "GET", "Range: bytes=0-1\r\n")
            .await
            .starts_with(b"HTTP/1.1 400")
    );
    assert!(
        pickup(url, "GET", "Purpose: prefetch\r\n")
            .await
            .starts_with(b"HTTP/1.1 400")
    );
    assert!(f.core.export_diagnostics(&Value::Null).await.is_err());
    for _ in 0..3 {
        let data = pickup(url, "GET", "").await;
        let end = data.windows(4).position(|v| v == b"\r\n\r\n").unwrap() + 4;
        assert!(data.starts_with(b"HTTP/1.1 200"));
        assert!(data[end..].starts_with(crate::diagnostics::ZIP_MARKER));
        let mut zip = zip::ZipArchive::new(std::io::Cursor::new(&data[end..])).unwrap();
        for index in 0..zip.len() {
            let mut entry = zip.by_index(index).unwrap();
            let mut contents = Vec::new();
            std::io::Read::read_to_end(&mut entry, &mut contents).unwrap();
            assert!(
                !contents
                    .windows(b"TEST_PRIVATE_SENTINEL".len())
                    .any(|part| part == b"TEST_PRIVATE_SENTINEL")
            );
            assert!(
                !contents
                    .windows(b"secret.invalid".len())
                    .any(|part| part == b"secret.invalid")
            );
        }
        assert_eq!(
            data.len() - end,
            first["package"]["bytes"].as_u64().unwrap() as usize
        );
    }
    assert!(matches!(
        text(&f.core.sharing.status().await["state"]),
        "draining" | "exhausted"
    ));
    // stop also waits for the listener and all accepted clients to drain.
    let status = f.core.sharing.stop(Some(&session)).await;
    assert!(matches!(text(&status["state"]), "stopped" | "exhausted"));
    assert!(status["url"].is_null());
    let u = url::Url::parse(url).unwrap();
    assert!(
        tokio::net::TcpStream::connect((u.host_str().unwrap(), u.port().unwrap()))
            .await
            .is_err()
    );
    f.close().await;
}

#[tokio::test]
async fn diagnostic_file_replacement_is_never_served() {
    let f = Fixture::new().await;
    let session = "b".repeat(32);
    let status = f
        .core
        .start_share_loopback_fixture(&session, None, &Value::Null)
        .await
        .unwrap();
    assert_eq!(status["state"], "active", "{status}");
    assert!(
        pickup(text(&status["url"]), "HEAD", "")
            .await
            .starts_with(b"HTTP/1.1 200")
    );
    let path = f
        .core
        .diagnostics
        .export_directory()
        .unwrap()
        .join(text(&status["package"]["file"]));
    std::fs::write(path, b"REPLACED_PRIVATE_CONTENT").unwrap();
    let data = pickup_exchange(text(&status["url"]), "GET", "", true).await;
    assert!(!String::from_utf8_lossy(&data).contains("REPLACED_PRIVATE_CONTENT"));
    assert!(!data.starts_with(b"HTTP/1.1 200"));
    assert!(
        !data
            .windows(crate::diagnostics::ZIP_MARKER.len())
            .any(|part| part == crate::diagnostics::ZIP_MARKER)
    );
    tokio::time::timeout(Duration::from_secs(3), async {
        loop {
            if f.core.sharing.status().await["state"] == "error" {
                break;
            }
            tokio::time::sleep(Duration::from_millis(5)).await;
        }
    })
    .await
    .expect("periodic package identity check did not close replaced diagnostic file");
    f.core.sharing.stop(Some(&session)).await;
    f.close().await;
}

#[tokio::test]
async fn diagnostics_record_running_binary_and_detect_frontend_only_replacement() {
    let f = Fixture::new().await;
    f.core.diagnostics.record(
        "frontend_build",
        json!({"version":"0.6.0","fingerprint":"b".repeat(64)}),
    );
    write(
        f.core.host.plugin_dir.join("dist/index.js"),
        b"replaced-frontend",
    );
    let status = f.core.export_diagnostics(&json!(123456)).await.unwrap();
    assert_eq!(status["state"], "success", "{status}");
    let file = std::fs::File::open(
        f.core
            .diagnostics
            .export_directory()
            .unwrap()
            .join(text(&status["file"])),
    )
    .unwrap();
    let mut zip = zip::ZipArchive::new(file).unwrap();
    let summary: Value = serde_json::from_reader(zip.by_name("summary.json").unwrap()).unwrap();
    assert_eq!(summary["builds"]["binary"], f.core.binary_hash);
    assert_eq!(summary["builds"]["installedBinary"], f.core.binary_hash);
    assert_eq!(summary["builds"]["matchesInstalled"], false);
    assert_eq!(
        summary["details"]["environment"]["steamClientVersion"],
        123456
    );
    assert!(summary["builds"]["installedFrontend"].is_null());
    f.close().await;
}

#[test]
fn malicious_archives_cannot_escape_or_replace_another_plugin() {
    let temp = tempfile::tempdir().unwrap();
    let path = temp.path().join("malicious.zip");
    for name in [
        "../escape",
        "/absolute",
        "example/../escape",
        "example\\plugin.json",
        "example/path:stream",
    ] {
        let mut zip = zip::ZipWriter::new(std::fs::File::create(&path).unwrap());
        zip.start_file(name, zip::write::SimpleFileOptions::default())
            .unwrap();
        zip.write_all(b"malicious").unwrap();
        zip.finish().unwrap();
        assert!(
            package::inspect(&path, None, &[], None, false, &CancellationToken::new()).is_err(),
            "{name}"
        );
    }
    std::fs::write(&path, package_bytes("example", "Example", "2.0.0", "safe")).unwrap();
    assert!(
        package::inspect(
            &path,
            Some(&json!({"name":"Other","folder":"other"})),
            &[],
            None,
            false,
            &CancellationToken::new()
        )
        .is_err()
    );
    let mut zip = zip::ZipWriter::new(std::fs::File::create(&path).unwrap());
    zip.add_symlink(
        "example/plugin.json",
        "/etc/passwd",
        zip::write::SimpleFileOptions::default(),
    )
    .unwrap();
    zip.finish().unwrap();
    assert!(package::inspect(&path, None, &[], None, false, &CancellationToken::new()).is_err());
    assert!(!temp.path().join("escape").exists());
}
