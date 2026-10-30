use super::*;
use futures_util::FutureExt;
use std::panic::AssertUnwindSafe;

#[tokio::test]
async fn strict_http_scripts_and_unknown_overconsumed_missing_routes_are_detected() {
    let url = "https://api.github.com/repos/a/b";
    let server = Server::new().await;
    server.sequence(
        url,
        vec![
            Reply::json(json!({"sequence":1})),
            Reply::json(json!({"sequence":2})),
        ],
    );
    let mut transport =
        crate::transport::Transport::new("direct", Arc::new(StdMutex::new(None))).unwrap();
    transport.test_origin = Some(server.origin.clone());
    for expected in [1, 2] {
        assert_eq!(
            transport
                .json(url, 100, &CancellationToken::new())
                .await
                .unwrap()["sequence"],
            expected
        );
    }
    server.finish().await;
    for violation in ["missing", "unknown", "overconsumed"] {
        let server = Server::new().await;
        let mut transport =
            crate::transport::Transport::new("direct", Arc::new(StdMutex::new(None))).unwrap();
        transport.test_origin = Some(server.origin.clone());
        if violation != "unknown" {
            server.expect(url, Reply::json(json!({"ok":true})), 1);
        }
        if violation != "missing" {
            let _ = transport.json(url, 100, &CancellationToken::new()).await;
        }
        if violation == "overconsumed" {
            let _ = transport.json(url, 100, &CancellationToken::new()).await;
        }
        assert!(
            AssertUnwindSafe(server.finish())
                .catch_unwind()
                .await
                .is_err(),
            "fixture accepted {violation}"
        );
    }
}

fn add_to_valid_package(additions: Vec<(&str, &[u8])>, symlink: bool) -> Vec<u8> {
    let baseline = package_bytes("example", "Example", "2.0.0", "safe");
    let mut original = zip::ZipArchive::new(std::io::Cursor::new(baseline)).unwrap();
    let mut output = zip::ZipWriter::new(std::io::Cursor::new(Vec::new()));
    for index in 0..original.len() {
        let mut entry = original.by_index(index).unwrap();
        let mut bytes = Vec::new();
        std::io::Read::read_to_end(&mut entry, &mut bytes).unwrap();
        output
            .start_file(entry.name(), zip::write::SimpleFileOptions::default())
            .unwrap();
        output.write_all(&bytes).unwrap();
    }
    for (name, data) in additions {
        if symlink {
            output
                .add_symlink(
                    name,
                    String::from_utf8_lossy(data),
                    zip::write::SimpleFileOptions::default(),
                )
                .unwrap();
        } else {
            output
                .start_file(name, zip::write::SimpleFileOptions::default())
                .unwrap();
            output.write_all(data).unwrap();
        }
    }
    output.finish().unwrap().into_inner()
}
fn inspect_bytes(bytes: &[u8]) -> Result<package::Package> {
    let temp = tempfile::tempdir().unwrap();
    let path = temp.path().join("input.zip");
    write(&path, bytes);
    package::inspect(&path, None, &[], None, false, &CancellationToken::new())
}

#[test]
fn otherwise_valid_archives_reject_only_the_targeted_security_violation() {
    assert!(inspect_bytes(&package_bytes("example", "Example", "2.0.0", "safe")).is_ok());
    assert!(
        inspect_bytes(&add_to_valid_package(
            vec![("example/path:stream", b"valid Linux name")],
            false
        ))
        .is_ok()
    );
    for path in [
        "../escape",
        "/absolute",
        "example/../escape",
        "example\\escape",
    ] {
        let error = inspect_bytes(&add_to_valid_package(vec![(path, b"bad")], false)).unwrap_err();
        assert!(error.message.contains("不安全路径"), "{path}: {error:?}");
    }
    let error = inspect_bytes(&add_to_valid_package(
        vec![("example/link", b"/etc/passwd")],
        true,
    ))
    .unwrap_err();
    assert!(error.message.contains("符号链接"), "{error:?}");
    let error = inspect_bytes(&add_to_valid_package(
        vec![("example/bin", b"file"), ("example/bin/tool", b"child")],
        false,
    ))
    .unwrap_err();
    assert!(error.message.contains("路径冲突"), "{error:?}");
    let valid = add_to_valid_package(vec![("example/evil.py", b"second")], false);
    assert!(inspect_bytes(&valid).is_ok());
    let mut duplicate = valid;
    let old = b"example/evil.py";
    let new = b"example/main.py";
    assert_eq!(old.len(), new.len());
    let positions: Vec<_> = duplicate
        .windows(old.len())
        .enumerate()
        .filter_map(|(i, part)| (part == old).then_some(i))
        .collect();
    assert_eq!(positions.len(), 2);
    for at in positions {
        duplicate[at..at + old.len()].copy_from_slice(new);
    }
    let error = inspect_bytes(&duplicate).unwrap_err();
    assert!(error.message.contains("重复路径"), "{error:?}");
}

#[test]
fn archive_limits_and_crc_fail_without_allocating_expanded_payload() {
    let valid = package_bytes("example", "Example", "2.0.0", "safe");
    let mut oversized = valid.clone();
    let central = oversized
        .windows(4)
        .position(|part| part == b"PK\x01\x02")
        .unwrap();
    oversized[central + 24..central + 28]
        .copy_from_slice(&((package::MAX_EXPANDED + 1) as u32).to_le_bytes());
    let error = inspect_bytes(&oversized).unwrap_err();
    assert!(error.message.contains("512 MiB"), "{error:?}");
    let mut corrupt = valid.clone();
    let central = corrupt
        .windows(4)
        .position(|part| part == b"PK\x01\x02")
        .unwrap();
    corrupt[central + 16..central + 20].copy_from_slice(&0u32.to_le_bytes());
    let error = inspect_bytes(&corrupt).unwrap_err();
    assert!(
        error.message.to_lowercase().contains("checksum")
            || error.message.to_lowercase().contains("crc"),
        "{error:?}"
    );
    let names: Vec<_> = (0..package::MAX_ENTRIES)
        .map(|i| format!("example/entry-{i}"))
        .collect();
    let additions = names
        .iter()
        .map(|name| (name.as_str(), b"".as_slice()))
        .collect();
    let error = inspect_bytes(&add_to_valid_package(additions, false)).unwrap_err();
    assert!(error.message.contains("文件过多"), "{error:?}");
}
