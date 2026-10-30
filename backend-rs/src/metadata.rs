use crate::{common::*, transport::Transport};
use base64::Engine;
use regex::Regex;
use serde_json::{Value, json};
use std::{
    collections::{BTreeSet, HashMap},
    sync::LazyLock,
};
use tokio_util::sync::CancellationToken;
static REPO_PART: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^[A-Za-z0-9_.-]+$").unwrap());
static VERSION: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"^[vV]?(0|[1-9][0-9]*)(?:\.(0|[1-9][0-9]*))?(?:\.(0|[1-9][0-9]*))?(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$").unwrap()
});
pub fn parse_repo(raw: &str) -> Option<String> {
    let mut s = raw
        .trim()
        .strip_prefix("git+")
        .unwrap_or(raw.trim())
        .to_owned();
    if let Some(rest) = s.strip_prefix("git@github.com:") {
        s = format!("https://github.com/{rest}");
    }
    if s.to_lowercase().starts_with("github.com/") {
        s = format!("https://{s}");
    }
    let parts: Vec<String> = if s.contains("://") {
        let url = url::Url::parse(&s).ok()?;
        if !["http", "https"].contains(&url.scheme())
            || url.host_str()? != "github.com"
            || !url.username().is_empty()
            || url.password().is_some()
            || url.port().is_some()
        {
            return None;
        }
        url.path()
            .trim_matches('/')
            .split('/')
            .take(2)
            .map(str::to_owned)
            .collect()
    } else {
        s.split('/').map(str::to_owned).collect()
    };
    if parts.len() != 2 {
        return None;
    }
    let owner = &parts[0];
    let repo = parts[1].strip_suffix(".git").unwrap_or(&parts[1]);
    if [owner.as_str(), repo]
        .iter()
        .any(|p| !REPO_PART.is_match(p) || [".", ".."].contains(p))
    {
        return None;
    }
    Some(format!("{owner}/{repo}"))
}
fn version(raw: &str) -> Option<([u128; 3], Option<Vec<String>>)> {
    let caps = VERSION.captures(raw.trim())?;
    let mut n = [0; 3];
    for (i, out) in n.iter_mut().enumerate() {
        *out = caps
            .get(i + 1)
            .map_or(Some(0), |v| v.as_str().parse().ok())?;
    }
    let pre = caps
        .get(4)
        .map(|v| v.as_str().split('.').map(str::to_owned).collect::<Vec<_>>());
    if pre.as_ref().is_some_and(|p| {
        p.iter()
            .any(|s| s.len() > 1 && s.starts_with('0') && s.bytes().all(|c| c.is_ascii_digit()))
    }) {
        return None;
    }
    Some((n, pre))
}
pub fn compare(remote: &str, local: &str) -> Option<i8> {
    let (a, ap) = version(remote)?;
    let (b, bp) = version(local)?;
    if a != b {
        return Some(if a > b { 1 } else { -1 });
    }
    if ap == bp {
        return Some(0);
    }
    let (ap, bp) = match (ap, bp) {
        (None, _) => return Some(1),
        (_, None) => return Some(-1),
        (Some(a), Some(b)) => (a, b),
    };
    for (x, y) in ap.iter().zip(bp.iter()) {
        if x == y {
            continue;
        }
        let xn = x.bytes().all(|c| c.is_ascii_digit());
        let yn = y.bytes().all(|c| c.is_ascii_digit());
        return Some(if xn && yn {
            let cmp = (x.len(), x.as_str()).cmp(&(y.len(), y.as_str()));
            if cmp.is_gt() { 1 } else { -1 }
        } else if xn != yn {
            if xn { -1 } else { 1 }
        } else if x > y {
            1
        } else {
            -1
        });
    }
    None
}
pub fn embedded(raw: &str) -> Option<String> {
    let chars: Vec<char> = raw.chars().collect();
    let mut found = Vec::new();
    let mut i = 0;
    while i < chars.len() {
        let c = chars[i];
        let starts = c.is_ascii_digit()
            || matches!(c, 'v' | 'V') && chars.get(i + 1).is_some_and(|v| v.is_ascii_digit());
        if starts && (i == 0 || !chars[i - 1].is_alphanumeric()) {
            let mut end = i + 1;
            let mut best = None;
            while end <= chars.len()
                && (chars[end - 1].is_alphanumeric() || ".+-".contains(chars[end - 1]))
            {
                let candidate: String = chars[i..end].iter().collect();
                if version(&candidate).is_some() {
                    best = Some(end);
                }
                end += 1;
            }
            if let Some(end) = best {
                let token: String = chars[i..end].iter().collect();
                let canonical = token.trim_start_matches(['v', 'V']).to_owned();
                if !found.contains(&canonical) {
                    found.push(canonical);
                }
                i = end;
                continue;
            }
        }
        i += 1;
    }
    if found.len() == 1 { found.pop() } else { None }
}
pub fn label(tag: &str, names: &[Value]) -> Option<String> {
    let tag = embedded(tag);
    let versions: BTreeSet<_> = names.iter().filter_map(|v| embedded(text(v))).collect();
    if let Some(tag) = tag {
        if versions.iter().any(|s| s != &tag) {
            None
        } else {
            Some(tag)
        }
    } else if versions.len() == 1 {
        versions.into_iter().next()
    } else {
        None
    }
}
pub fn parse_releases(payload: &Value, repo: &str) -> Result<Vec<Value>> {
    let items = payload
        .as_array()
        .ok_or_else(|| Error::new("invalid_response", "GitHub 发布列表格式异常"))?;
    let mut out = Vec::new();
    for item in items {
        if !item.is_object()
            || item["draft"].as_bool().unwrap_or(false)
            || text(&item["tag_name"]).trim().is_empty()
        {
            continue;
        }
        let mut zips: Vec<_> = array(&item["assets"])
            .iter()
            .filter(|a| {
                text(&a["name"]).to_lowercase().ends_with(".zip")
                    && a["browser_download_url"].is_string()
            })
            .map(|a| json!({"name":a["name"],"url":a["browser_download_url"]}))
            .collect();
        zips.sort_by_key(|a| {
            let name = text(&a["name"]).to_lowercase();
            (
                !name.contains(&repo.to_lowercase()) && !name.contains("plugin"),
                name,
            )
        });
        let count = item["assets"].as_array().and_then(|assets| {
            assets.iter().try_fold(0u64, |sum, a| {
                a["download_count"]
                    .as_u64()
                    .and_then(|n| sum.checked_add(n))
            })
        });
        out.push(json!({"tag":item["tag_name"],"title":if text(&item["name"]).trim().is_empty(){item["tag_name"].clone()}else{item["name"].clone()},"prerelease":item["prerelease"].as_bool().unwrap_or(false),"body":text(&item["body"]).chars().take(20000).collect::<String>(),"published_at":text(&item["published_at"]),"download_count":count,"zips":zips}));
    }
    out.sort_by(|a, b| text(&b["published_at"]).cmp(text(&a["published_at"])));
    Ok(out)
}
pub fn assess(releases: &[Value], current: &str, evidence: &Value) -> Value {
    if releases.is_empty() {
        return json!({"status":"no_release","latest":null,"notice":null});
    }
    let Some((index, release)) = releases
        .iter()
        .enumerate()
        .find(|(_, r)| r["has_zip"] == true)
    else {
        return json!({"status":"no_asset","latest":releases[0]["tag"],"notice":null});
    };
    let mut notice = if index > 0 {
        Some(format!("最新发布 {} 没有安装包", text(&releases[0]["tag"])))
    } else {
        None
    };
    let state = text(&evidence["package_state"]);
    let packaged = text(&evidence["package_version"]);
    let tag = text(&release["tag"]);
    let names: Vec<_> = array(&release["zips"])
        .iter()
        .map(|v| v["name"].clone())
        .collect();
    let label = string(&release["version"]).or_else(|| label(tag, &names));
    let git = text(&evidence["git_version"]);
    let mut extra = None;
    let chosen = if state == "semver" && version(packaged).is_some() {
        Some(packaged.trim().to_owned())
    } else if state == "raw" {
        return json!({"status":"uncomparable","latest":if packaged.trim().is_empty(){tag}else{packaged.trim()},"notice":notice});
    } else if state == "absent" {
        extra = Some("安装包没有可读取的版本");
        None
    } else if label.as_ref().is_some_and(|v| version(v).is_some()) {
        label
    } else if version(git).is_some() {
        extra = Some("版本来自仓库源文件，安装时会再核对安装包");
        Some(git.trim().to_owned())
    } else {
        None
    };
    if let Some(extra) = extra {
        notice = Some(notice.map_or_else(|| extra.into(), |s| format!("{s}，{extra}")));
    }
    if let Some(v) = chosen {
        if let Some(cmp) = compare(&v, current) {
            return json!({"status":match cmp{1=>"update",0=>"current",_=>"ahead"},"latest":v,"notice":notice});
        }
    }
    json!({"status":"uncomparable","latest":tag,"notice":notice})
}
pub async fn releases(
    transport: &Transport,
    raw: &str,
    current: &str,
    cancel: &CancellationToken,
) -> Result<Vec<Value>> {
    let repo = parse_repo(raw).ok_or_else(|| Error::invalid("GitHub 链接无效"))?;
    let mut rows = Vec::new();
    for page in 1..=10 {
        let payload = transport
            .json(
                &format!("https://api.github.com/repos/{repo}/releases?per_page=100&page={page}"),
                1024 * 1024,
                cancel,
            )
            .await?;
        let count = array(&payload).len();
        rows.extend(parse_releases(
            &payload,
            repo.split('/').nth(1).unwrap_or(""),
        )?);
        if count < 100 {
            break;
        }
        if page == 10 {
            return Err(Error::invalid(
                "仓库发布超过 1000 条，请使用发布较少的插件仓库",
            ));
        }
    }
    rows.sort_by(|a, b| text(&b["published_at"]).cmp(text(&a["published_at"])));
    for row in &mut rows {
        let names: Vec<_> = array(&row["zips"])
            .iter()
            .map(|v| v["name"].clone())
            .collect();
        let v = label(text(&row["tag"]), &names);
        row["repo"] = json!(repo);
        row["version"] = json!(v);
        row["comparison"] = json!(v.as_ref().and_then(|v| compare(v, current)));
        row["has_zip"] = json!(!array(&row["zips"]).is_empty());
    }
    Ok(rows)
}
pub async fn search(
    transport: &Transport,
    query: &str,
    cancel: &CancellationToken,
) -> Result<Value> {
    let query = query.trim();
    ensure(
        !query.is_empty() && query.chars().count() <= 100,
        "请输入 1 至 100 个字符的插件名或关键词",
    )?;
    let q = if query
        .split_whitespace()
        .any(|s| s.to_lowercase().starts_with("fork:"))
    {
        query.into()
    } else {
        format!("{query} fork:true")
    };
    let url = url::Url::parse_with_params(
        "https://api.github.com/search/repositories",
        &[("q", q.as_str()), ("per_page", "20")],
    )
    .unwrap();
    let payload = transport.json(url.as_str(), 1024 * 1024, cancel).await?;
    ensure(
        payload["items"].is_array(),
        "GitHub 搜索响应格式异常，请重试",
    )?;
    let mut seen = BTreeSet::new();
    let mut out = Vec::new();
    for item in array(&payload["items"]).iter().take(20) {
        let Some(repo) = parse_repo(text(&item["full_name"])) else {
            continue;
        };
        if repo != text(&item["full_name"]) || !seen.insert(repo.clone()) {
            continue;
        }
        out.push(json!({"repo":repo,"description":text(&item["description"]),"stars":item["stargazers_count"].as_u64().unwrap_or(0),"fork":item["fork"]==true,"archived":item["archived"]==true}));
    }
    Ok(json!(out))
}
async fn blob(
    transport: &Transport,
    base: &str,
    files: &HashMap<String, Value>,
    path: &str,
    cache: &mut HashMap<String, String>,
    cancel: &CancellationToken,
) -> Result<String> {
    let item = files
        .get(path)
        .ok_or_else(|| Error::invalid("仓库文件不存在"))?;
    let size = item["size"]
        .as_u64()
        .ok_or_else(|| Error::invalid("仓库元数据或入口过大/无效"))?;
    let hash = text(&item["sha"]);
    ensure(
        size <= 256 * 1024
            && [40, 64].contains(&hash.len())
            && hash.bytes().all(|c| c.is_ascii_hexdigit()),
        "仓库元数据或入口过大/无效",
    )?;
    if let Some(v) = cache.get(hash) {
        return Ok(v.clone());
    }
    ensure(
        cache.len() < 40,
        "仓库探查超过请求或时间预算，无法确认完整结果",
    )?;
    let payload = transport
        .json(&format!("{base}blobs/{hash}"), 1024 * 1024, cancel)
        .await?;
    ensure(
        payload["encoding"] == "base64" && payload["content"].is_string(),
        "GitHub 文件响应格式异常",
    )?;
    let encoded = text(&payload["content"])
        .split_whitespace()
        .collect::<String>();
    let data = base64::engine::general_purpose::STANDARD
        .decode(encoded)
        .map_err(|_| Error::invalid("GitHub 文件内容无效"))?;
    ensure(
        data.len() as u64 == size && data.len() <= 256 * 1024,
        "GitHub 文件内容无效",
    )?;
    let text = String::from_utf8(data)
        .map_err(|_| Error::invalid("GitHub 文件内容无效"))?
        .trim_start_matches('\u{feff}')
        .to_owned();
    cache.insert(hash.into(), text.clone());
    Ok(text)
}
pub async fn probe(transport: &Transport, raw: &str, cancel: &CancellationToken) -> Result<Value> {
    let repo = parse_repo(raw).ok_or_else(|| Error::invalid("GitHub 链接无效"))?;
    let base = format!("https://api.github.com/repos/{repo}/git/");
    let started = std::time::Instant::now();
    let tree = transport
        .json(
            &format!("{base}trees/HEAD?recursive=1"),
            8 * 1024 * 1024,
            cancel,
        )
        .await?;
    let hash = text(&tree["sha"]);
    ensure(
        tree["tree"].is_array()
            && [40, 64].contains(&hash.len())
            && hash.bytes().all(|c| c.is_ascii_hexdigit()),
        "GitHub 仓库目录响应格式异常",
    )?;
    ensure(
        tree["truncated"] == false && array(&tree["tree"]).len() <= 20000,
        "仓库目录过大，无法完成完整探查，请使用范围较小的仓库",
    )?;
    let mut files: HashMap<String, Value> = HashMap::new();
    for item in array(&tree["tree"]) {
        let path = text(&item["path"]);
        ensure(safe_relative(path), "GitHub 仓库包含不安全路径")?;
        if item["type"] == "blob" && ["100644", "100755"].contains(&text(&item["mode"])) {
            ensure(
                files.insert(path.into(), item.clone()).is_none(),
                "GitHub 仓库目录包含重复路径",
            )?;
        }
    }
    let mut dirs: Vec<_> = files
        .keys()
        .filter(|p| p.rsplit('/').next() == Some("plugin.json"))
        .map(|p| p.rsplit_once('/').map_or("", |v| v.0).to_owned())
        .collect();
    dirs.sort();
    ensure(
        dirs.len() <= 10,
        "仓库中 plugin.json 超过 10 个，无法在探查预算内确认所有插件",
    )?;
    let mut cache = HashMap::new();
    let mut out = Vec::new();
    let py =
        Regex::new(r"(?m)^\s*(?:from\s+decky(?:_plugin)?\b|import\s+decky(?:_plugin)?\b)").unwrap();
    let js = Regex::new(
        r#"(?:from\s*|import\s*|require\s*\()\s*['"](?:@decky/(?:api|ui)|decky-frontend-lib)['"]"#,
    )
    .unwrap();
    for dir in dirs {
        ensure(
            started.elapsed().as_secs() <= 120,
            "仓库探查超过请求或时间预算，无法确认完整结果",
        )?;
        let prefix = if dir.is_empty() {
            String::new()
        } else {
            format!("{dir}/")
        };
        if !files.contains_key(&format!("{prefix}package.json")) {
            continue;
        }
        let plugin: Value = serde_json::from_str(
            &blob(
                transport,
                &base,
                &files,
                &format!("{prefix}plugin.json"),
                &mut cache,
                cancel,
            )
            .await?,
        )?;
        let package: Value = serde_json::from_str(
            &blob(
                transport,
                &base,
                &files,
                &format!("{prefix}package.json"),
                &mut cache,
                cancel,
            )
            .await?,
        )?;
        let name = text(&plugin["name"]);
        if !valid_text(&plugin["name"], 128)
            || name.trim().is_empty()
            || name.contains(['/', '\\'])
            || !valid_text(&package["name"], 214)
            || !valid_text(&package["version"], 128)
        {
            continue;
        }
        let mut evidence = Vec::new();
        for dependency in [
            "@decky/api",
            "@decky/ui",
            "@decky/rollup",
            "decky-frontend-lib",
        ] {
            if [
                "dependencies",
                "devDependencies",
                "peerDependencies",
                "optionalDependencies",
            ]
            .iter()
            .any(|group| package[group][dependency].is_string())
            {
                evidence.push(format!("依赖 {dependency}"));
            }
        }
        let main = format!("{prefix}main.py");
        if evidence.is_empty()
            && files.contains_key(&main)
            && py.is_match(&blob(transport, &base, &files, &main, &mut cache, cancel).await?)
        {
            evidence.push("后端 main.py".into());
        }
        if evidence.is_empty() {
            for entry in [
                "src/index.tsx",
                "src/index.ts",
                "src/index.jsx",
                "src/index.js",
                "dist/index.js",
            ] {
                let path = format!("{prefix}{entry}");
                if files.contains_key(&path)
                    && js
                        .is_match(&blob(transport, &base, &files, &path, &mut cache, cancel).await?)
                {
                    evidence.push(format!("源码 {entry}"));
                    break;
                }
            }
        }
        if !evidence.is_empty() {
            out.push(json!({"path":dir,"name":name,"package_name":package["name"],"version":package["version"],"evidence":evidence,"requires_root":array(&plugin["flags"]).contains(&json!("root"))}));
        }
    }
    Ok(json!({"repo":repo,"tree_sha":tree["sha"],"plugins":out}))
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn repo_variants() {
        for s in [
            "a/b",
            "https://github.com/a/b/releases",
            "git@github.com:a/b.git",
            "git+https://github.com/a/b.git",
        ] {
            assert_eq!(parse_repo(s), Some("a/b".into()));
        }
        for s in [
            "https://evil.test/a/b",
            "a/../b",
            "https://user@github.com/a/b",
        ] {
            assert_eq!(parse_repo(s), None);
        }
    }
    #[test]
    fn precedence() {
        assert_eq!(compare("v1.2", "1.1.9"), Some(1));
        assert_eq!(compare("1.0.0-beta.2", "1.0.0-beta.10"), Some(-1));
        assert_eq!(compare("1.0.0", "1.0.0-rc"), Some(1));
        assert_eq!(compare("1.0.0-01", "1"), None);
    }
    #[test]
    fn version_labels() {
        assert_eq!(embedded("plugin-0.3.1.zip"), Some("0.3.1".into()));
        assert_eq!(label("v1.0", &[json!("plugin-2.0.zip")]), None);
    }
}
