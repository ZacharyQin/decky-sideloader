use sha2::{Digest, Sha256};
use std::{
    env, fs,
    path::{Path, PathBuf},
};
fn collect(path: &Path, files: &mut Vec<PathBuf>) {
    for entry in fs::read_dir(path)
        .expect("Rust sources")
        .map(Result::unwrap)
    {
        let path = entry.path();
        if path.is_dir() {
            collect(&path, files);
        } else if path.extension().is_some_and(|v| v == "rs" || v == "json") {
            files.push(path);
        }
    }
}
fn main() {
    let root = PathBuf::from(env::var_os("CARGO_MANIFEST_DIR").unwrap());
    let mut files = vec![root.join("Cargo.toml"), root.join("build.rs")];
    if root.join("Cargo.lock").exists() {
        files.push(root.join("Cargo.lock"));
    }
    collect(&root.join("src"), &mut files);
    files.sort();
    let mut hash = Sha256::new();
    for path in files {
        println!("cargo:rerun-if-changed={}", path.display());
        hash.update(
            path.strip_prefix(&root)
                .unwrap()
                .to_string_lossy()
                .as_bytes(),
        );
        hash.update([0]);
        hash.update(fs::read(path).unwrap());
        hash.update([0]);
    }
    let fingerprint = format!("{:x}", hash.finalize());
    let target = env::var("TARGET").unwrap();
    let version = env::var("CARGO_PKG_VERSION").unwrap();
    let out = PathBuf::from(env::var_os("OUT_DIR").unwrap());
    fs::write(out.join("backend-build.json"), format!(r#"{{"version":"{version}","fingerprint":"{fingerprint}","target":"{target}","protocol":1}}"#)).unwrap();
    println!("cargo:rustc-env=SIDELOADER_BUILD={fingerprint}");
    println!("cargo:rustc-env=SIDELOADER_TARGET={target}");
}
