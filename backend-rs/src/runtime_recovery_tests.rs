use super::*;
#[path = "runtime_backup_recovery_tests.rs"]
mod backups;
#[path = "runtime_persistence_tests.rs"]
mod persistence;

#[tokio::test]
async fn retry_cannot_bypass_a_failed_repository_release_revalidation() {
    let f = Fixture::new().await;
    let releases = "https://api.github.com/repos/a/b/releases?per_page=100&page=1";
    f.server
        .sequence(releases, vec![Reply::status(503), Reply::json(json!([]))]);
    let before = std::fs::read(f.core.host.home.join("plugins/example/package.json")).unwrap();
    let started = f
        .core
        .prepare_install(
            "a/b",
            "v2",
            "https://github.com/a/b/releases/download/v2/plugin.zip",
            Some("Example"),
            None,
            "install",
        )
        .await
        .unwrap();
    let done = f.core.state.lock().await.op_done.clone().unwrap();
    tokio::time::timeout(Duration::from_secs(3), done.cancelled())
        .await
        .unwrap();
    assert_eq!(f.core.operation_public().await.unwrap()["stage"], "failed");
    f.core
        .reuse_download(text(&started["id"]), true)
        .await
        .unwrap();
    let done = f.core.state.lock().await.op_done.clone().unwrap();
    tokio::time::timeout(Duration::from_secs(3), done.cancelled())
        .await
        .unwrap();
    let failed = f.core.operation_public().await.unwrap();
    assert_eq!(failed["stage"], "failed");
    assert!(failed["request_id"].is_null());
    assert_eq!(
        f.server.count_url(releases),
        2,
        "retry must revalidate the selected release instead of downloading an unvalidated URL"
    );
    assert_eq!(
        f.server.count(),
        2,
        "failed revalidation must not contact the asset URL"
    );
    assert_eq!(
        std::fs::read(f.core.host.home.join("plugins/example/package.json")).unwrap(),
        before
    );
    f.close().await;
}

#[tokio::test]
async fn completed_download_retry_and_cancelled_or_foreign_operation_reuse_are_rejected() {
    let f = Fixture::new().await;
    let (identifier, _) = f.prepare().await;
    let count = f.server.count();
    assert!(f.core.reuse_download(&identifier, true).await.is_err());
    assert!(f.core.reuse_download(&"a".repeat(32), false).await.is_err());
    f.core.cancel_operation(&identifier).await.unwrap();
    for download in [true, false] {
        assert!(f.core.reuse_download(&identifier, download).await.is_err());
    }
    assert_eq!(
        f.core.operation_public().await.unwrap()["stage"],
        "cancelled"
    );
    assert_eq!(f.server.count(), count);
    f.close().await;
}
