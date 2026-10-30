use super::*;
use std::os::fd::AsRawFd;

async fn phase(f: &Fixture, expected: &str) {
    tokio::time::timeout(Duration::from_secs(3), async {
        loop {
            if f.core.sharing.status().await["state"] == expected {
                return;
            }
            tokio::time::sleep(Duration::from_millis(5)).await;
        }
    })
    .await
    .unwrap();
}
async fn counts(f: &Fixture, expected: (u64, u64)) {
    tokio::time::timeout(Duration::from_secs(3), async {
        loop {
            if f.core.sharing.fixture_counts().await == expected {
                return;
            }
            tokio::time::sleep(Duration::from_millis(5)).await;
        }
    })
    .await
    .unwrap();
}
async fn connect(url: &str) -> tokio::net::TcpStream {
    let parsed = url::Url::parse(url).unwrap();
    let mut stream = tokio::time::timeout(
        Duration::from_secs(3),
        tokio::net::TcpStream::connect((parsed.host_str().unwrap(), parsed.port().unwrap())),
    )
    .await
    .unwrap()
    .unwrap();
    stream
        .write_all(
            format!(
                "GET {} HTTP/1.1\r\nHost: {}:{}\r\n\r\n",
                parsed.path(),
                parsed.host_str().unwrap(),
                parsed.port().unwrap()
            )
            .as_bytes(),
        )
        .await
        .unwrap();
    stream
}
async fn header(stream: &mut tokio::net::TcpStream) -> Vec<u8> {
    tokio::time::timeout(Duration::from_secs(3), async {
        let mut bytes = Vec::new();
        while !bytes.ends_with(b"\r\n\r\n") {
            let mut byte = [0];
            assert_eq!(stream.read(&mut byte).await.unwrap(), 1);
            bytes.push(byte[0]);
            assert!(bytes.len() < 4096);
        }
        bytes
    })
    .await
    .unwrap()
}
fn body(bytes: &[u8]) -> &[u8] {
    assert!(bytes.starts_with(b"HTTP/1.1 200"));
    let end = bytes.windows(4).position(|v| v == b"\r\n\r\n").unwrap() + 4;
    let body = &bytes[end..];
    assert!(body.starts_with(crate::diagnostics::ZIP_MARKER));
    let mut archive = zip::ZipArchive::new(std::io::Cursor::new(body)).unwrap();
    assert!(archive.by_name("summary.json").is_ok());
    for index in 0..archive.len() {
        let mut entry = archive.by_index(index).unwrap();
        let mut content = Vec::new();
        std::io::Read::read_to_end(&mut entry, &mut content).unwrap();
        assert!(
            !content
                .windows(b"PRIVATE_SHARE_SENTINEL".len())
                .any(|v| v == b"PRIVATE_SHARE_SENTINEL")
        );
    }
    body
}

#[tokio::test]
async fn share_expires_or_loses_its_interface_and_closes_the_listener() {
    for expire in [true, false] {
        let f = Fixture::new().await;
        let status = f
            .core
            .start_share_loopback_fixture(&"c".repeat(32), None, &Value::Null)
            .await
            .unwrap();
        let url = text(&status["url"]).to_owned();
        assert!(pickup(&url, "HEAD", "").await.starts_with(b"HTTP/1.1 200"));
        if expire {
            f.core.sharing.fixture_expire().await;
        } else {
            let absent = std::net::Ipv4Addr::new(192, 168, 254, 253);
            assert!(!crate::sharing::addresses().contains(&absent.to_string()));
            f.core.sharing.fixture_address(absent).await;
        }
        phase(&f, if expire { "expired" } else { "error" }).await;
        let parsed = url::Url::parse(&url).unwrap();
        assert!(
            tokio::net::TcpStream::connect((parsed.host_str().unwrap(), parsed.port().unwrap()))
                .await
                .is_err()
        );
        assert!(f.core.sharing.status().await["url"].is_null());
        assert_eq!(f.core.sharing.fixture_counts().await, (0, 0));
        f.close().await;
    }
}

#[tokio::test]
async fn two_reserved_transfers_reject_competitors_but_not_head_and_finish_exactly_three() {
    let f = Fixture::new().await;
    f.core.diagnostics.record(
        "rpc_failed",
        json!({"rpc":"check_updates","message":"PRIVATE_SHARE_SENTINEL"}),
    );
    let session = "d".repeat(32);
    let status = f
        .core
        .start_share_loopback_fixture(&session, None, &Value::Null)
        .await
        .unwrap();
    let url = text(&status["url"]).to_owned();
    let (entered, release) = f.core.sharing.fixture_hold_transfers().await;
    let first_url = url.clone();
    let first = tokio::spawn(async move { pickup(&first_url, "GET", "").await });
    tokio::time::timeout(Duration::from_secs(3), entered.notified())
        .await
        .unwrap();
    let second_url = url.clone();
    let second = tokio::spawn(async move { pickup(&second_url, "GET", "").await });
    tokio::time::timeout(Duration::from_secs(3), entered.notified())
        .await
        .unwrap();
    assert_eq!(f.core.sharing.fixture_counts().await, (0, 2));
    assert!(pickup(&url, "GET", "").await.starts_with(b"HTTP/1.1 429"));
    assert!(pickup(&url, "HEAD", "").await.starts_with(b"HTTP/1.1 200"));
    assert_eq!(f.core.sharing.fixture_counts().await, (0, 2));
    release.cancel();
    let one = first.await.unwrap();
    let two = second.await.unwrap();
    assert_eq!(body(&one), body(&two));
    assert_eq!(
        body(&one).len() as u64,
        status["package"]["bytes"].as_u64().unwrap()
    );
    counts(&f, (2, 0)).await;
    let third = pickup(&url, "GET", "").await;
    assert_eq!(body(&third), body(&one));
    phase(&f, "exhausted").await;
    assert_eq!(f.core.sharing.fixture_counts().await, (3, 0));
    f.close().await;
}

#[tokio::test]
async fn reset_client_and_explicit_stop_release_reservations_without_false_success() {
    for reset in [true, false] {
        let f = Fixture::new().await;
        let session = "e".repeat(32);
        let status = f
            .core
            .start_share_loopback_fixture(&session, None, &Value::Null)
            .await
            .unwrap();
        let url = text(&status["url"]);
        let (entered, release) = f.core.sharing.fixture_hold_transfers().await;
        let mut stream = connect(url).await;
        tokio::time::timeout(Duration::from_secs(3), entered.notified())
            .await
            .unwrap();
        assert!(header(&mut stream).await.starts_with(b"HTTP/1.1 200"));
        assert_eq!(f.core.sharing.fixture_counts().await, (0, 1));
        assert_eq!(
            f.core.sharing.stop(Some(&"f".repeat(32))).await["state"],
            "active"
        );
        if reset {
            let linger = libc::linger {
                l_onoff: 1,
                l_linger: 0,
            };
            assert_eq!(
                unsafe {
                    libc::setsockopt(
                        stream.as_raw_fd(),
                        libc::SOL_SOCKET,
                        libc::SO_LINGER,
                        (&linger as *const libc::linger).cast(),
                        std::mem::size_of_val(&linger) as libc::socklen_t,
                    )
                },
                0
            );
            drop(stream);
            release.cancel();
            counts(&f, (0, 0)).await;
            assert_eq!(f.core.sharing.status().await["state"], "active");
            body(&pickup(url, "GET", "").await);
            counts(&f, (1, 0)).await;
            assert_eq!(
                f.core.sharing.stop(Some(&session)).await["state"],
                "stopped"
            );
        } else {
            let stopped =
                tokio::time::timeout(Duration::from_secs(3), f.core.sharing.stop(Some(&session)))
                    .await
                    .unwrap();
            assert_eq!(stopped["state"], "stopped");
            assert_eq!(f.core.sharing.fixture_counts().await, (0, 0));
            let mut remaining = Vec::new();
            tokio::time::timeout(Duration::from_secs(3), stream.read_to_end(&mut remaining))
                .await
                .unwrap()
                .unwrap();
            assert!(remaining.is_empty());
            release.cancel();
        }
        f.close().await;
    }
}

#[tokio::test]
async fn slow_incomplete_headers_time_out_without_using_download_quota() {
    let f = Fixture::new().await;
    let session = "a".repeat(32);
    let status = f
        .core
        .start_share_loopback_fixture(&session, None, &Value::Null)
        .await
        .unwrap();
    let url = text(&status["url"]);
    let parsed = url::Url::parse(url).unwrap();
    let entered = f.core.sharing.fixture_headers().await;
    let mut stream =
        tokio::net::TcpStream::connect((parsed.host_str().unwrap(), parsed.port().unwrap()))
            .await
            .unwrap();
    stream.write_all(b"GET /pickup/").await.unwrap();
    tokio::time::timeout(Duration::from_secs(3), entered.notified())
        .await
        .unwrap();
    tokio::time::pause();
    tokio::time::advance(Duration::from_secs(6)).await;
    tokio::time::resume();
    let mut bytes = Vec::new();
    tokio::time::timeout(Duration::from_secs(3), stream.read_to_end(&mut bytes))
        .await
        .unwrap()
        .unwrap();
    assert!(bytes.starts_with(b"HTTP/1.1 400"));
    assert_eq!(f.core.sharing.fixture_counts().await, (0, 0));
    body(&pickup(url, "GET", "").await);
    counts(&f, (1, 0)).await;
    f.core.sharing.stop(Some(&session)).await;
    f.close().await;
}

#[tokio::test]
async fn stopping_during_diagnostic_preparation_never_publishes_a_listener() {
    let f = Fixture::new().await;
    let session = "b".repeat(32);
    let (entered, release) = f.core.sharing.fixture_hold_preparation().await;
    let engine = f.core.clone();
    let id = session.clone();
    let preparing = tokio::spawn(async move {
        engine
            .start_share_loopback_fixture(&id, None, &Value::Null)
            .await
    });
    tokio::time::timeout(Duration::from_secs(3), entered.notified())
        .await
        .unwrap();
    assert_eq!(f.core.sharing.status().await["state"], "preparing");
    let sharing = f.core.sharing.clone();
    let stopping = tokio::spawn(async move { sharing.stop(Some(&session)).await });
    phase(&f, "draining").await;
    release.cancel();
    let prepared = tokio::time::timeout(Duration::from_secs(5), preparing)
        .await
        .unwrap()
        .unwrap()
        .unwrap();
    assert_eq!(prepared["state"], "stopped");
    assert!(prepared["url"].is_null());
    let stopped = tokio::time::timeout(Duration::from_secs(5), stopping)
        .await
        .unwrap()
        .unwrap();
    assert_eq!(stopped["state"], "stopped");
    assert!(stopped["url"].is_null());
    assert_eq!(f.core.sharing.fixture_counts().await, (0, 0));
    f.close().await;
}
