// This Cargo test target is never a plugin binary. It shares the production
// entry point and Engine, with Transport's existing cfg(test) origin adapter.
#![cfg(test)]
#![allow(dead_code, unused_imports)]
include!("lib.rs");
extern crate self as sideloader_core;

mod production_entry {
    use crate as sideloader_core;
    pub fn launch() {
        main();
    }
    include!("main.rs");
}

fn main() {
    let user_home = std::path::PathBuf::from(
        std::env::var_os("HOME").expect("the private E2E driver requires a fixture home"),
    );
    let origin = std::fs::read_to_string(user_home.join(".sideloader-e2e-origin"))
        .expect("the private E2E driver requires a fixture origin");
    let parsed = url::Url::parse(&origin).expect("invalid fixture origin");
    assert!(
        parsed.scheme() == "http"
            && parsed.host_str() == Some("127.0.0.1")
            && parsed.port().is_some()
            && parsed.username().is_empty()
            && parsed.password().is_none()
            && parsed.path() == "/"
            && parsed.query().is_none()
            && parsed.fragment().is_none()
    );
    transport::PROCESS_TEST_ORIGIN
        .set(origin.trim_end_matches('/').to_owned())
        .expect("fixture origin already set");
    if std::env::args().nth(1).as_deref() == Some("--fixture-addresses") {
        println!("{}", serde_json::json!({"addresses": sharing::addresses()}));
        return;
    }
    if let Ok(value) = std::fs::read_to_string(user_home.join(".sideloader-e2e-share-addresses")) {
        let values: Vec<String> =
            serde_json::from_str(&value).expect("invalid fixture address snapshot");
        assert!(
            !values.is_empty()
                && values.len() <= 16
                && values.iter().all(|value| value
                    .parse::<std::net::Ipv4Addr>()
                    .is_ok_and(|address| address.is_private() && address.to_string() == *value))
        );
        sharing::PROCESS_TEST_ADDRESSES
            .set(values)
            .expect("fixture address snapshot already set");
    }
    if let Ok(value) = std::fs::read_to_string(user_home.join(".sideloader-e2e-backup-gate")) {
        let config: serde_json::Value =
            serde_json::from_str(&value).expect("checkpoint config JSON");
        let path =
            std::path::PathBuf::from(config["socket"].as_str().expect("checkpoint socket path"));
        let point = config["checkpoint"].as_str().expect("checkpoint name");
        assert!(["backup-first-chunk", "install-verified"].contains(&point));
        assert!(
            value.len() < 4096
                && path.is_absolute()
                && path.parent() == user_home.parent().and_then(|path| path.parent())
                && path
                    .file_name()
                    .is_some_and(|name| name == "backup-gate.sock")
        );
        backup_gate::PROCESS_POINT
            .set(point.to_owned())
            .expect("fixture checkpoint name already set");
        backup_gate::PROCESS_BACKUP_SOCKET
            .set(path)
            .expect("fixture backup checkpoint already set");
    }
    production_entry::launch();
}
