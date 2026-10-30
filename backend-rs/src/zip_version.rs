use crate::{common::*, transport::Transport};
use serde_json::{Value, json};
use std::io::Read;
use tokio_util::sync::CancellationToken;
fn u16_at(data: &[u8], i: usize) -> Result<u16> {
    let s = data
        .get(i..i + 2)
        .ok_or_else(|| Error::invalid("安装包目录不可读"))?;
    Ok(u16::from_le_bytes([s[0], s[1]]))
}
fn u32_at(data: &[u8], i: usize) -> Result<u32> {
    let s = data
        .get(i..i + 4)
        .ok_or_else(|| Error::invalid("安装包目录不可读"))?;
    Ok(u32::from_le_bytes(s.try_into().unwrap()))
}
#[derive(Clone)]
struct Entry {
    name: String,
    method: u16,
    compressed: u64,
    uncompressed: u64,
    offset: u64,
}
fn entry(directory: &[u8]) -> Result<Option<Entry>> {
    let (mut pos, mut found) = (0, None);
    let mut count = 0;
    let mut folders = std::collections::BTreeSet::new();
    while pos + 46 <= directory.len() {
        ensure(
            &directory[pos..pos + 4] == b"PK\x01\x02",
            "安装包目录不可读",
        )?;
        let flags = u16_at(directory, pos + 8)?;
        let compressed = u32_at(directory, pos + 20)?;
        let uncompressed = u32_at(directory, pos + 24)?;
        let offset = u32_at(directory, pos + 42)?;
        ensure(
            flags & 1 == 0
                && [compressed, uncompressed, offset]
                    .iter()
                    .all(|n| *n != u32::MAX),
            "安装包目录不可读",
        )?;
        let n = u16_at(directory, pos + 28)? as usize;
        let end = pos
            + 46
            + n
            + u16_at(directory, pos + 30)? as usize
            + u16_at(directory, pos + 32)? as usize;
        ensure(end <= directory.len(), "安装包目录不可读")?;
        let name = std::str::from_utf8(&directory[pos + 46..pos + 46 + n])
            .map_err(|_| Error::invalid("安装包目录不可读"))?;
        if safe_relative(name) && !name.ends_with('/') {
            let parts: Vec<_> = name.split('/').collect();
            if parts.len() >= 2 {
                folders.insert(parts[0].to_owned());
            }
            if parts.len() == 2 && parts[1] == "package.json" {
                count += 1;
                found = Some(Entry {
                    name: name.into(),
                    method: u16_at(directory, pos + 10)?,
                    compressed: compressed as u64,
                    uncompressed: uncompressed as u64,
                    offset: offset as u64,
                });
            }
        }
        pos = end;
    }
    ensure(pos == directory.len(), "安装包目录不可读")?;
    Ok(if folders.len() == 1 && count == 1 {
        found
    } else {
        None
    })
}
async fn package_version(
    transport: &Transport,
    url: &str,
    cancel: &CancellationToken,
) -> Result<Value> {
    let (tail, start, total) = transport.range(url, None, 22 + 65535, cancel).await?;
    ensure(
        tail.len() >= 22 && start + tail.len() as u64 == total,
        "安装包尾部不可读",
    )?;
    let end = (0..=tail.len() - 22)
        .rev()
        .find(|i| {
            &tail[*i..*i + 4] == b"PK\x05\x06"
                && u16_at(&tail, *i + 20).is_ok_and(|n| *i + 22 + n as usize == tail.len())
        })
        .ok_or_else(|| Error::invalid("安装包不是可读的 zip"))?;
    ensure(
        u16_at(&tail, end + 4)? == 0 && u16_at(&tail, end + 6)? == 0,
        "不读取分卷安装包",
    )?;
    let size = u32_at(&tail, end + 12)? as u64;
    let offset = u32_at(&tail, end + 16)? as u64;
    ensure(
        size <= 256 * 1024 && offset != u32::MAX as u64 && offset + size <= total,
        "安装包目录超出读取范围",
    )?;
    if size == 0 {
        return Ok(json!({"package_state":"absent","package_version":null,"git_version":null}));
    }
    let directory = if offset >= start && offset + size <= total {
        tail[(offset - start) as usize..(offset - start + size) as usize].to_vec()
    } else {
        let (data, at, _) = transport
            .range(url, Some(offset), offset + size - 1, cancel)
            .await?;
        ensure(
            at == offset && data.len() as u64 == size,
            "安装包目录不可读",
        )?;
        data
    };
    let Some(entry) = entry(&directory)? else {
        return Ok(json!({"package_state":"absent","package_version":null,"git_version":null}));
    };
    ensure(
        [0, 8].contains(&entry.method) && entry.compressed <= 65536 && entry.uncompressed <= 65536,
        "package.json 超出读取范围",
    )?;
    let (header, at, _) = transport
        .range(url, Some(entry.offset), entry.offset + 29, cancel)
        .await?;
    ensure(
        at == entry.offset
            && header.starts_with(b"PK\x03\x04")
            && header.len() == 30
            && u16_at(&header, 6)? & 1 == 0,
        "package.json 文件头不可读",
    )?;
    let name_len = u16_at(&header, 26)? as u64;
    let extra = u16_at(&header, 28)? as u64;
    ensure(
        name_len > 0 && name_len <= 512 && extra <= 4096,
        "package.json 文件头不可读",
    )?;
    let (name, _, _) = transport
        .range(
            url,
            Some(entry.offset + 30),
            entry.offset + 29 + name_len,
            cancel,
        )
        .await?;
    ensure(name == entry.name.as_bytes(), "package.json 文件头不可读")?;
    let offset = entry.offset + 30 + name_len + extra;
    let payload = if entry.compressed == 0 {
        Vec::new()
    } else {
        transport
            .range(url, Some(offset), offset + entry.compressed - 1, cancel)
            .await?
            .0
    };
    let raw = if entry.method == 0 {
        payload
    } else {
        let decoder = flate2::read::DeflateDecoder::new(payload.as_slice());
        let mut raw = Vec::new();
        decoder.take(65537).read_to_end(&mut raw)?;
        raw
    };
    ensure(
        raw.len() <= 65536 && raw.len() as u64 == entry.uncompressed,
        "package.json 不可读",
    )?;
    let package: Value = serde_json::from_slice(&raw).unwrap_or(Value::Null);
    let version = text(&package["version"]).trim();
    let (state, value) = if version.is_empty() || version.chars().count() > 128 {
        ("absent", None)
    } else if crate::metadata::compare(version, version).is_some() {
        ("semver", Some(version))
    } else {
        ("raw", Some(version))
    };
    Ok(json!({"package_state":state,"package_version":value,"git_version":null}))
}
pub async fn probe(transport: &Transport, url: &str, cancel: &CancellationToken) -> Value {
    package_version(transport, url, cancel)
        .await
        .unwrap_or_else(
            |_| json!({"package_state":"unknown","package_version":null,"git_version":null}),
        )
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn malformed_directory_is_bounded() {
        assert!(entry(b"not a zip").is_err());
        assert!(entry(&[]).unwrap().is_none());
    }
}
