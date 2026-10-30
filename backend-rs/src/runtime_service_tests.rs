use super::*;
use base64::Engine as _;

const LATEST: &str = "https://api.github.com/repos/ZacharyQin/decky-sideloader/releases/latest";
const RELEASES: &str = "https://api.github.com/repos/a/b/releases?per_page=100&page=1";

fn releases_page(count: usize) -> Value {
    json!((0..count).map(|i|json!({"draft":false,"prerelease":false,"tag_name":format!("v1.{i}.0"),"name":"Fixed release","published_at":"2020-01-01T00:00:00Z","assets":[{"name":"a.zip","browser_download_url":format!("https://github.com/a/b/releases/download/v1.{i}.0/a.zip")},{"name":"b.zip","browser_download_url":format!("https://github.com/a/b/releases/download/v1.{i}.0/b.zip")}]})).collect::<Vec<_>>())
}
#[tokio::test]
async fn release_pagination_and_multiple_assets_are_preserved_or_fail_without_partial_results() {
    for failure in [false, true] {
        let f = Fixture::new().await;
        f.server
            .expect(RELEASES, Reply::json(releases_page(100)), 1);
        let next = "https://api.github.com/repos/a/b/releases?per_page=100&page=2";
        f.server.expect(
            next,
            if failure {
                Reply::status(502)
            } else {
                Reply::json(json!([]))
            },
            1,
        );
        let result =
            crate::metadata::releases(&f.core.transport().unwrap(), "a/b", "1.0.0", &f.core.stop)
                .await;
        if failure {
            assert!(result.is_err());
        } else {
            let rows = result.unwrap();
            assert_eq!(rows.len(), 100);
            assert!(
                rows.iter()
                    .all(|row| row["zips"].as_array().unwrap().len() == 2)
            );
            assert!(rows.iter().all(|row| row["has_zip"] == true));
        }
        f.close().await;
    }
    let f = Fixture::new().await;
    for page in 1..=10 {
        f.server.expect(
            &format!("https://api.github.com/repos/a/b/releases?per_page=100&page={page}"),
            Reply::json(releases_page(100)),
            1,
        );
    }
    let error =
        crate::metadata::releases(&f.core.transport().unwrap(), "a/b", "1.0.0", &f.core.stop)
            .await
            .unwrap_err();
    assert!(error.message.contains("1000"));
    assert_eq!(f.server.count(), 10);
    f.close().await;
}

#[tokio::test]
async fn keyword_search_filters_invalid_duplicates_and_preserves_forks() {
    let f = Fixture::new().await;
    f.server.expect("https://api.github.com/search/repositories?q=deck+input+fork%3Atrue&per_page=20",Reply::json(json!({"items":[{"full_name":"a/b","description":"fixed","stargazers_count":7,"fork":true,"archived":false},{"full_name":"a/b","stargazers_count":999},{"full_name":"../invalid"},{"full_name":"c/d","stargazers_count":3,"archived":true}]})),1);
    let result =
        crate::metadata::search(&f.core.transport().unwrap(), " deck input ", &f.core.stop)
            .await
            .unwrap();
    assert_eq!(
        result,
        json!([{"repo":"a/b","description":"fixed","stars":7,"fork":true,"archived":false},{"repo":"c/d","description":"","stars":3,"fork":false,"archived":true}])
    );
    for query in [String::new(), "x".repeat(101)] {
        assert!(
            crate::metadata::search(&f.core.transport().unwrap(), &query, &f.core.stop)
                .await
                .is_err()
        );
    }
    assert_eq!(f.server.count(), 1);
    f.close().await;
}

#[tokio::test]
async fn monorepo_probe_reads_both_plugins_and_reuses_case_insensitive_cache() {
    let f = Fixture::new().await;
    let mut tree = Vec::new();
    let inputs = [
        (
            "packages/alpha/plugin.json",
            json!({"name":"Alpha","flags":["root"]}),
        ),
        (
            "packages/alpha/package.json",
            json!({"name":"alpha","version":"1.2.0","dependencies":{"@decky/api":"1"}}),
        ),
        (
            "packages/beta/plugin.json",
            json!({"name":"Beta","flags":[]}),
        ),
        (
            "packages/beta/package.json",
            json!({"name":"beta","version":"2.3.0","devDependencies":{"@decky/rollup":"1"}}),
        ),
    ];
    for (index, (path, data)) in inputs.into_iter().enumerate() {
        let bytes = serde_json::to_vec(&data).unwrap();
        let key = format!("{:040x}", index + 1);
        tree.push(json!({"path":path,"type":"blob","mode":"100644","sha":key,"size":bytes.len()}));
        f.server.expect(&format!("https://api.github.com/repos/a/b/git/blobs/{key}"),Reply::json(json!({"encoding":"base64","content":base64::engine::general_purpose::STANDARD.encode(bytes)})),1);
    }
    f.server.expect(
        "https://api.github.com/repos/a/b/git/trees/HEAD?recursive=1",
        Reply::json(json!({"sha":"a".repeat(40),"truncated":false,"tree":tree})),
        1,
    );
    let result = f.core.probe_repo("a/b").await.unwrap();
    assert_eq!(
        result["plugins"],
        json!([{"path":"packages/alpha","name":"Alpha","package_name":"alpha","version":"1.2.0","evidence":["依赖 @decky/api"],"requires_root":true},{"path":"packages/beta","name":"Beta","package_name":"beta","version":"2.3.0","evidence":["依赖 @decky/rollup"],"requires_root":false}])
    );
    assert_eq!(f.core.probe_repo("A/B").await.unwrap(), result);
    assert_eq!(f.server.count(), 5);
    f.close().await;
}

#[tokio::test]
async fn probe_limits_truncation_and_duplicate_paths_do_not_return_partial_plugins() {
    for kind in ["truncated", "plugins", "entries", "duplicate", "unsafe"] {
        let f = Fixture::new().await;
        let count = match kind {
            "plugins" => 11,
            "entries" => 20001,
            "duplicate" => 2,
            _ => 1,
        };
        let tree:Vec<_>=(0..count).map(|i|json!({"path":match kind{"plugins"=>format!("plugins/{i}/plugin.json"),"unsafe"=>"../escape".into(),"duplicate"=>"same/file".into(),_=>format!("file-{i}")},"type":"blob","mode":"100644","sha":"b".repeat(40),"size":2})).collect();
        f.server.expect(
            "https://api.github.com/repos/a/b/git/trees/HEAD?recursive=1",
            Reply::json(json!({"sha":"a".repeat(40),"truncated":kind=="truncated","tree":tree})),
            1,
        );
        assert!(f.core.probe_repo("a/b").await.is_err(), "{kind}");
        assert_eq!(f.server.count(), 1, "no unverified blob fetch after {kind}");
        assert!(f.core.state.lock().await.probes.is_empty());
        f.close().await;
    }
}

#[tokio::test]
async fn probe_cache_expires_and_evicts_the_oldest_entry() {
    let f = Fixture::new().await;
    for i in 0..11 {
        f.server.expect(
            &format!("https://api.github.com/repos/a/repo{i}/git/trees/HEAD?recursive=1"),
            Reply::json(json!({"sha":"a".repeat(40),"truncated":false,"tree":[]})),
            if i == 0 { 2 } else { 1 },
        );
        f.core.probe_repo(&format!("a/repo{i}")).await.unwrap();
    }
    assert_eq!(f.core.state.lock().await.probes.len(), 10);
    f.core.probe_repo("a/repo0").await.unwrap();
    f.core
        .state
        .lock()
        .await
        .probes
        .get_mut("a/repo0")
        .unwrap()
        .0 = std::time::Instant::now() - Duration::from_secs(301);
    f.server.expect_count(
        "https://api.github.com/repos/a/repo0/git/trees/HEAD?recursive=1",
        3,
    );
    f.core.probe_repo("a/repo0").await.unwrap();
    assert_eq!(f.server.count(), 13);
    f.close().await;
}

fn batch(f: &Fixture) {
    installed(&f.core.host.home, "other", "Other", "1.0.0", "other");
    write(
        f.core.host.home.join("plugins/other/package.json"),
        br#"{"name":"other","version":"1.0.0","repository":"c/d"}"#,
    );
    f.native.lock().unwrap()["snapshot"]["plugins"]
        .as_array_mut()
        .unwrap()
        .push(json!({"name":"Other","version":"1.0.0","load_type":0,"disabled":false}));
    let (url, bytes) = f.ordinary_release("v2.0.0", "2.0.0", "new");
    f.server.asset(&url, Reply::bytes(bytes), 1, 0);
    f.server.expect(
        "https://api.github.com/repos/c/d/releases?per_page=100&page=1",
        Reply::status(503),
        1,
    );
    f.server.expect(LATEST, Reply::status(404), 1);
}
fn assert_batch(value: &Value) {
    let items = value["items"].as_array().unwrap();
    assert_eq!(items.len(), 2);
    let example = items.iter().find(|row| row["name"] == "Example").unwrap();
    let other = items.iter().find(|row| row["name"] == "Other").unwrap();
    assert_eq!(example["status"], "update");
    assert_eq!(example["latest"], "2.0.0");
    assert_eq!(other["status"], "error");
    assert!(other["error"].is_string());
}
#[tokio::test]
async fn batch_check_persists_partial_failure_without_losing_successful_snapshot() {
    let f = Fixture::new().await;
    batch(&f);
    let result = f.core.check_updates("manual").await.unwrap();
    assert_batch(&result["last_check"]);
    let settings = load_settings(&f.core.host).unwrap();
    assert_eq!(settings["lastCheck"], result["last_check"]);
    assert!(settings["lastScheduledCheck"].is_null());
    assert_eq!(result["last_check"]["source"], "manual");
    assert!(!f.core.state.lock().await.checking);
    let before = f.server.count();
    let cached = f
        .core
        .get_plugin_releases("a/b", &json!("1.0.0"), "Example", &json!("example"), false)
        .await
        .unwrap();
    assert_eq!(cached["snapshot"]["releases"][0]["tag"], "v2.0.0");
    assert_eq!(f.server.count(), before);
    f.close().await;
}
async fn tick() {
    tokio::time::pause();
    tokio::time::advance(Duration::from_secs(61)).await;
    tokio::time::resume();
    tokio::time::sleep(Duration::from_millis(15)).await;
}
#[tokio::test]
async fn real_scheduler_obeys_disabled_busy_interval_and_emits_one_completed_report() {
    let f = Fixture::new().await;
    batch(&f);
    f.core.begin();
    tokio::task::yield_now().await;
    tick().await;
    assert_eq!(f.server.count(), 0, "disabled scheduler must not query");
    f.core.set_setting("autoCheck", json!(true)).await.unwrap();
    f.core.state.lock().await.self_preparing = true;
    tick().await;
    assert_eq!(
        f.server.count(),
        0,
        "self-update preparation must suppress scheduled checks"
    );
    f.core.state.lock().await.self_preparing = false;
    tick().await;
    tokio::time::timeout(Duration::from_secs(3), async {
        loop {
            if f.events
                .lock()
                .unwrap()
                .iter()
                .any(|event| event["event"] == "scheduled_check_completed")
            {
                break;
            }
            tokio::time::sleep(Duration::from_millis(5)).await;
        }
    })
    .await
    .unwrap();
    let completed: Vec<_> = f
        .events
        .lock()
        .unwrap()
        .iter()
        .filter(|event| event["event"] == "scheduled_check_completed")
        .cloned()
        .collect();
    assert_eq!(completed.len(), 1);
    assert_batch(&completed[0]["args"][0]["report"]);
    assert_eq!(completed[0]["args"][0]["checkNotifications"], true);
    let settings = load_settings(&f.core.host).unwrap();
    assert_eq!(settings["lastScheduledCheck"]["source"], "scheduled");
    assert_eq!(settings["lastCheck"], settings["lastScheduledCheck"]);
    let count = f.server.count();
    tick().await;
    assert_eq!(
        f.server.count(),
        count,
        "minimum interval must suppress another check"
    );
    assert_eq!(
        f.events
            .lock()
            .unwrap()
            .iter()
            .filter(|event| event["event"] == "scheduled_check_completed")
            .count(),
        1
    );
    f.close().await;
}
