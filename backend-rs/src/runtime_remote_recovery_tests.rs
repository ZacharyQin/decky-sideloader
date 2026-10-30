use super::*;
use std::os::unix::fs::PermissionsExt;

#[tokio::test]
async fn abandoned_remote_work_cleanup_requires_matching_owner_and_safe_known_members() {
    for variant in [
        "owned",
        "source",
        "unknown",
        "symlink",
        "hardlink",
        "mode",
        "missing",
        "directory-link",
    ] {
        let f = Fixture::new().await;
        let body = b"fresh dependency";
        let original = dependency_package(json!([dep("engine", DEP_A, body)]), vec![]);
        let archive = f.temp.path().join("plugin.zip");
        write(&archive, &original);
        let cancel = CancellationToken::new();
        let package = package::inspect(&archive, None, &[], None, false, &cancel).unwrap();
        let directory = f
            .temp
            .path()
            .join(format!(".remote-binaries-Case{variant}"));
        let private = f.temp.path().join("private");
        write(&private, b"unrelated private data");
        std::fs::set_permissions(&private, std::fs::Permissions::from_mode(0o600)).unwrap();
        let actual = if variant == "directory-link" {
            let target = f.temp.path().join("private-directory");
            std::fs::create_dir(&target).unwrap();
            std::os::unix::fs::symlink(&target, &directory).unwrap();
            target
        } else {
            std::fs::create_dir(&directory).unwrap();
            directory.clone()
        };
        std::fs::set_permissions(&actual, std::fs::Permissions::from_mode(0o700)).unwrap();
        let marker = actual.join(".sideloader-work.json");
        if variant != "missing" {
            atomic_json(&marker,&json!({"format":1,"operation_id":null,"source_sha256":if variant=="source"{"f".repeat(64)}else{package.sha256.clone()},"dependencies":1})).unwrap();
        }
        let old = actual.join("dependency-0");
        if variant == "symlink" {
            std::os::unix::fs::symlink(&private, &old).unwrap();
        } else if variant == "hardlink" {
            std::fs::hard_link(&private, &old).unwrap();
        } else {
            write(&old, b"old interrupted bytes");
            std::fs::set_permissions(
                &old,
                std::fs::Permissions::from_mode(if variant == "mode" { 0o644 } else { 0o600 }),
            )
            .unwrap();
        }
        if variant == "unknown" {
            write(actual.join("user-note.txt"), b"unrelated user content");
        }
        let marker_before = std::fs::read(&marker).ok();
        let old_before = std::fs::read(&old).unwrap();
        f.server.expect(DEP_A, Reply::bytes(body.to_vec()), 1);
        assert_eq!(
            package::remote_binaries(
                &archive,
                &package,
                &f.core.transport().unwrap(),
                1,
                &cancel,
                |_| {}
            )
            .await
            .unwrap()
            .unwrap()["count"],
            1
        );
        assert_eq!(
            std::fs::read(archive.with_file_name("source.zip")).unwrap(),
            original
        );
        assert_eq!(std::fs::read(&private).unwrap(), b"unrelated private data");
        if variant == "owned" {
            assert!(
                !directory.exists(),
                "verified abandoned owned work directory survived explicit re-preparation"
            );
        } else {
            assert!(directory.exists());
            assert_eq!(std::fs::read(marker).ok(), marker_before);
            assert_eq!(std::fs::read(old).unwrap(), old_before);
            if variant == "unknown" {
                assert_eq!(
                    std::fs::read(actual.join("user-note.txt")).unwrap(),
                    b"unrelated user content"
                );
            }
            if variant == "directory-link" {
                assert!(directory.is_symlink());
            }
        }
        f.close().await;
    }
}
