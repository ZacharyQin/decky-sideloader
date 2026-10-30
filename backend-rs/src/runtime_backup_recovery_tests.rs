use super::*;
use std::{
    collections::BTreeMap,
    io::{Cursor, Read},
    os::unix::fs::PermissionsExt,
};

fn setup() -> (tempfile::TempDir, std::path::PathBuf, String, Value) {
    let temp = tempfile::tempdir().unwrap();
    let id = "a".repeat(32);
    write(
        temp.path().join("settings/example/config.json"),
        b"{\"private\":true}\n",
    );
    write(
        temp.path().join("data/example/state.bin"),
        b"original private state\n",
    );
    let destination = temp
        .path()
        .join("installations")
        .join(&id)
        .join("data-backup.zip");
    std::fs::create_dir_all(destination.parent().unwrap()).unwrap();
    let metadata = package::backup(
        temp.path(),
        "example",
        &destination,
        &CancellationToken::new(),
    )
    .unwrap();
    (temp, destination, id, metadata)
}
fn rewrite(bytes: &[u8], mutate: impl FnOnce(&mut Value)) -> Vec<u8> {
    let mut input = zip::ZipArchive::new(Cursor::new(bytes)).unwrap();
    let mut files = BTreeMap::new();
    for index in 0..input.len() {
        let mut entry = input.by_index(index).unwrap();
        let mut data = Vec::new();
        entry.read_to_end(&mut data).unwrap();
        files.insert(entry.name().to_owned(), data);
    }
    let mut manifest: Value = serde_json::from_slice(&files["backup-manifest.json"]).unwrap();
    mutate(&mut manifest);
    files.insert(
        "backup-manifest.json".into(),
        serde_json::to_vec(&manifest).unwrap(),
    );
    let mut cursor = Cursor::new(b"decky-sideloader-data-backup-v1\n".to_vec());
    cursor.set_position(cursor.get_ref().len() as u64);
    let mut output = zip::ZipWriter::new(cursor);
    for (name, data) in files {
        output
            .start_file(
                name,
                zip::write::SimpleFileOptions::default()
                    .compression_method(zip::CompressionMethod::Stored)
                    .unix_permissions(0o600),
            )
            .unwrap();
        output.write_all(&data).unwrap();
    }
    output.finish().unwrap().into_inner()
}

#[test]
fn completed_backup_recovery_preserves_bytes_age_and_all_unrelated_partial_candidates() {
    let (temp, destination, id, metadata) = setup();
    let original = std::fs::read(&destination).unwrap();
    let parent = destination.parent().unwrap();
    let marker = b"decky-sideloader-data-backup-v1\nowned interrupted copy";
    let owned = parent.join(".backup-AbCd12.tmp");
    write(&owned, marker);
    std::fs::set_permissions(&owned, std::fs::Permissions::from_mode(0o600)).unwrap();
    let unknown = parent.join(".backup-Unknown.tmp");
    write(&unknown, b"unrelated user bytes");
    std::fs::set_permissions(&unknown, std::fs::Permissions::from_mode(0o600)).unwrap();
    let private = temp.path().join("private");
    write(&private, marker);
    std::fs::set_permissions(&private, std::fs::Permissions::from_mode(0o600)).unwrap();
    let symlink = parent.join(".backup-Symlink.tmp");
    std::os::unix::fs::symlink(&private, &symlink).unwrap();
    let hardlink = parent.join(".backup-Hardlink.tmp");
    std::fs::hard_link(&private, &hardlink).unwrap();
    let mode = parent.join(".backup-ModeFail.tmp");
    write(&mode, marker);
    std::fs::set_permissions(&mode, std::fs::Permissions::from_mode(0o644)).unwrap();
    let other = parent.join("unrelated.tmp");
    write(&other, marker);
    let recovered = package::resume_backup(
        temp.path(),
        "example",
        &destination,
        &id,
        &CancellationToken::new(),
    )
    .unwrap()
    .unwrap();
    assert_eq!(recovered, metadata);
    assert_eq!(std::fs::read(&destination).unwrap(), original);
    assert!(!owned.exists());
    assert_eq!(std::fs::read(unknown).unwrap(), b"unrelated user bytes");
    assert!(symlink.is_symlink());
    assert!(hardlink.exists());
    assert_eq!(std::fs::read(private).unwrap(), marker);
    assert!(mode.exists());
    assert!(other.exists());
}

#[test]
fn backup_recovery_requires_complete_owned_archive_and_exact_operation_manifest() {
    for variant in [
        "unknown",
        "symlink",
        "hardlink",
        "operation",
        "archive",
        "scope",
        "count",
        "crc",
        "truncated",
    ] {
        let (temp, destination, id, _) = setup();
        let original = std::fs::read(&destination).unwrap();
        match variant {
            "unknown" => std::fs::write(&destination, b"unrelated user backup").unwrap(),
            "symlink" => {
                let saved = destination.with_file_name("user-saved.zip");
                std::fs::rename(&destination, &saved).unwrap();
                std::os::unix::fs::symlink(saved, &destination).unwrap();
            }
            "hardlink" => {
                std::fs::hard_link(&destination, destination.with_file_name("user-linked.zip"))
                    .unwrap()
            }
            "operation" => std::fs::write(
                &destination,
                rewrite(&original, |manifest| {
                    manifest["operation_id"] = json!("b".repeat(32))
                }),
            )
            .unwrap(),
            "archive" => std::fs::write(
                &destination,
                rewrite(&original, |manifest| {
                    manifest["archive"] = json!("/unrelated/data-backup.zip")
                }),
            )
            .unwrap(),
            "scope" => std::fs::write(
                &destination,
                rewrite(&original, |manifest| {
                    manifest["paths"] = json!(["/unrelated/private"])
                }),
            )
            .unwrap(),
            "count" => std::fs::write(
                &destination,
                rewrite(&original, |manifest| manifest["files"] = json!(99)),
            )
            .unwrap(),
            "crc" => {
                let mut changed = original.clone();
                let mut archive = zip::ZipArchive::new(Cursor::new(&original)).unwrap();
                let offset = archive
                    .by_name("data/example/state.bin")
                    .unwrap()
                    .data_start() as usize;
                changed[offset] ^= 1;
                std::fs::write(&destination, changed).unwrap();
            }
            _ => std::fs::write(&destination, &original[..original.len() - 12]).unwrap(),
        }
        let before = std::fs::read(&destination).unwrap();
        let result = package::resume_backup(
            temp.path(),
            "example",
            &destination,
            &id,
            &CancellationToken::new(),
        );
        assert!(result.is_err(), "invalid {variant} backup was reused");
        assert_eq!(
            std::fs::read(&destination).unwrap(),
            before,
            "rejected {variant} backup was overwritten"
        );
        if variant == "symlink" {
            assert!(destination.is_symlink());
        }
        assert_eq!(
            std::fs::read(temp.path().join("settings/example/config.json")).unwrap(),
            b"{\"private\":true}\n"
        );
        assert_eq!(
            std::fs::read(temp.path().join("data/example/state.bin")).unwrap(),
            b"original private state\n"
        );
    }
}
