use crate::{common::*, diagnostics, library_model as model, metadata};
use base64::Engine;
use serde_json::{Value, json};
fn run(case: &Value) -> Result<Value> {
    let args = array(&case["args"]);
    let a = |i: usize| args.get(i).unwrap_or(&Value::Null);
    let kwargs = &case["kwargs"];
    match text(&case["function"]) {
        "github_meta.parse_github_input" => Ok(json!(metadata::parse_repo(text(a(0))))),
        "github_meta.compare_versions" => Ok(json!(metadata::compare(text(a(0)), text(a(1))))),
        "github_meta.embedded_version" => Ok(json!(metadata::embedded(text(a(0))))),
        "github_meta.label_version" => Ok(json!(metadata::label(text(a(0)), array(a(1))))),
        "github_meta.pick_zip_assets" => {
            let rows =
                metadata::parse_releases(&json!([{"tag_name":"test","assets":a(0)}]), text(a(1)))?;
            Ok(rows[0]["zips"].clone())
        }
        "github_meta.assess_checked_release" => {
            Ok(metadata::assess(array(a(0)), text(a(1)), kwargs))
        }
        "github_meta.parse_releases" => Ok(json!(metadata::parse_releases(a(0), text(a(1)))?)),
        "scan.detect_github_repo" => Ok(json!(model::detect_repo(a(0)))),
        "persist.apply_bindings" => {
            let mut rows = array(a(0)).to_vec();
            model::apply_bindings(&mut rows, a(1));
            Ok(json!(rows))
        }
        "persist.skipped_tags" => Ok(json!(skipped_tags(a(0), text(a(1)), a(2)))),
        "catalog.parse_gitmodules" => Ok(json!(model::parse_registry(text(a(0)))?)),
        "repository_map.decode_mapping" => {
            let bytes = base64::engine::general_purpose::STANDARD
                .decode(text(&a(0)["$bytes"]))
                .map_err(|_| Error::invalid("fixture bytes"))?;
            model::decode_map(&bytes)
        }
        "repository_map.resolve_repository" => {
            let mut plugin = a(0).clone();
            model::resolve_repository(&mut plugin, a(1), a(2));
            Ok(json!([plugin["repo"], plugin["repo_source"]]))
        }
        "release_cache.valid_snapshot" => Ok(json!(model::valid_snapshot(a(0)))),
        "release_cache.release_identity" => Ok(model::identity(a(0))),
        "release_cache.matches" => Ok(json!(model::snapshot_matches(a(0), a(1)))),
        "diagnostics.sanitize" => {
            Ok(diagnostics::sanitize(text(a(0)), a(1)).unwrap_or(Value::Null))
        }
        "diagnostic_snapshot.sanitize_snapshot" => Ok(diagnostics::sanitize_snapshot(
            a(0),
            a(1).as_u64().or(kwargs["depth"].as_u64()).unwrap_or(0) as usize,
        )),
        other => Err(Error::invalid(format!("Unknown contract: {other}"))),
    }
}
#[test]
fn frozen_python_050_contracts() {
    let fixtures: Value =
        serde_json::from_str(include_str!("../tests/fixtures/python-050-contracts.json")).unwrap();
    let mut failures = Vec::new();
    for (index, case) in array(&fixtures["cases"]).iter().enumerate() {
        let actual = run(case);
        let matches = if case["ok"] == true {
            actual.as_ref().is_ok_and(|v| v == &case["value"])
        } else {
            actual.is_err()
        };
        if !matches {
            failures.push(format!(
                "case {index} {}\ninput={}\nexpected={} ok={}\nactual={actual:?}",
                text(&case["function"]),
                case["args"],
                case["value"],
                case["ok"]
            ));
        }
    }
    assert!(
        failures.is_empty(),
        "{} differential failures out of {} contracts:\n{}",
        failures.len(),
        array(&fixtures["cases"]).len(),
        failures
            .iter()
            .take(20)
            .cloned()
            .collect::<Vec<_>>()
            .join("\n\n")
    );
}
