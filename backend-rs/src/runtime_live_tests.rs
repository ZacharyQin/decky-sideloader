use crate::{
    common::*,
    transport::{PROCESS_TEST_ORIGIN, Transport},
};
use serde_json::{Value, json};
use std::sync::{Arc, Mutex};
use tokio_util::sync::CancellationToken;

#[tokio::test]
#[ignore = "explicit public-network observation; not part of deterministic regression"]
async fn collect_public_service_transport_observations() {
    assert!(
        PROCESS_TEST_ORIGIN.get().is_none(),
        "live observation cannot use a fixture origin"
    );
    let spec: Value =
        serde_json::from_str(include_str!("../../e2e/fixtures/real-service.json")).unwrap();
    let output = std::path::PathBuf::from(
        std::env::var_os("SIDELOADER_REAL_SERVICE_REPORT").expect("explicit output path required"),
    );
    let directory = tempfile::tempdir().unwrap();
    let mut results = Vec::new();
    let started = now();
    let mut mismatched = false;
    for mode in ["direct", "gh-proxy", "ghfast", "auto"] {
        let attempts = Arc::new(Mutex::new(Vec::new()));
        let recorded = attempts.clone();
        let mut transport = Transport::new(mode, Arc::new(Mutex::new(None))).unwrap();
        assert!(transport.test_origin.is_none());
        transport.observer = Some(Arc::new(move |value| recorded.lock().unwrap().push(value)));
        let query = transport
            .json(text(&spec["api"]), 256 * 1024, &CancellationToken::new())
            .await;
        let query_attempts = attempts.lock().unwrap().clone();
        attempts.lock().unwrap().clear();
        let api = match query {
            Ok(value) => {
                let asset = array(&value["assets"])
                    .iter()
                    .find(|asset| asset["name"] == spec["name"]);
                let valid = value["tag_name"] == spec["tag"]
                    && asset.is_some_and(|asset| {
                        asset["browser_download_url"] == spec["asset"]
                            && asset["size"] == spec["bytes"]
                    });
                mismatched |= !valid;
                json!({"status":if valid{"passed"}else{"content_mismatch"},"attempts":query_attempts})
            }
            Err(error) => {
                json!({"status":"unavailable","errorCode":error.code,"attempts":query_attempts})
            }
        };
        let path = directory.path().join(format!("{mode}.zip"));
        let result = transport
            .download(
                text(&spec["asset"]),
                &path,
                1024 * 1024,
                3,
                &CancellationToken::new(),
                |_| {},
            )
            .await;
        let data = std::fs::read(&path).unwrap_or_default();
        let received = data.len();
        let hash = sha(&data);
        let download_attempts = attempts.lock().unwrap().clone();
        let download = match result {
            Ok(progress) => {
                let valid = spec["bytes"] == received && spec["sha256"] == hash;
                mismatched |= !valid;
                json!({"status":if valid{"passed"}else{"content_mismatch"},"bytes":received,"sha256":hash,"route":progress["route"],"attempt":progress["attempt"],"attempts":download_attempts})
            }
            Err(error) => {
                json!({"status":"unavailable","errorCode":error.code,"partialBytes":received,"attempts":download_attempts})
            }
        };
        results.push(json!({"mode":mode,"api":api,"download":download}));
    }
    let available = results
        .iter()
        .filter(|row| row["api"]["status"] == "passed" && row["download"]["status"] == "passed")
        .count();
    let report = json!({"startedAt":started,"finishedAt":now(),"transport":"actual production Transport/reqwest/TLS/URL/redirect/route rules; no test origin, no environment proxy","asset":spec,"modesAttempted":4,"availableModes":available,"outcome":if available==4{"all_available"}else{"observed_service_failures"},"contentMismatch":mismatched,"results":results});
    atomic_json(&output, &report).unwrap();
    println!("{}", serde_json::to_string(&report).unwrap());
    assert!(
        !mismatched,
        "a successful service response disagreed with independent fixed reference bytes/metadata"
    );
}
