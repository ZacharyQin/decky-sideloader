use crate::common::*;
use std::{
    ffi::CString,
    fs::File,
    io::Read,
    os::{
        fd::{AsRawFd, FromRawFd},
        unix::fs::MetadataExt,
    },
    path::Path,
};
/// Directory-relative access keeps checks and I/O anchored across path renames.
pub struct Dir(File);
fn name(value: &str) -> Result<CString> {
    ensure(safe_component(value), "文件名无效")?;
    CString::new(value).map_err(|_| Error::invalid("文件名无效"))
}
fn opened(fd: libc::c_int) -> Result<File> {
    if fd < 0 {
        return Err(std::io::Error::last_os_error().into());
    } // Ownership transfers only after a successful open/openat.
    Ok(unsafe { File::from_raw_fd(fd) })
}
impl Dir {
    pub fn open(path: &Path) -> Result<Self> {
        use std::os::unix::ffi::OsStrExt;
        let path =
            CString::new(path.as_os_str().as_bytes()).map_err(|_| Error::invalid("目录无效"))?;
        let fd = unsafe {
            libc::open(
                path.as_ptr(),
                libc::O_RDONLY | libc::O_DIRECTORY | libc::O_NOFOLLOW | libc::O_CLOEXEC,
            )
        };
        Ok(Self(opened(fd)?))
    }
    pub fn child(&self, value: &str) -> Result<Self> {
        let value = name(value)?;
        let fd = unsafe {
            libc::openat(
                self.0.as_raw_fd(),
                value.as_ptr(),
                libc::O_RDONLY | libc::O_DIRECTORY | libc::O_NOFOLLOW | libc::O_CLOEXEC,
            )
        };
        Ok(Self(opened(fd)?))
    }
    pub fn file(&self, value: &str) -> Result<File> {
        let value = name(value)?;
        let fd = unsafe {
            libc::openat(
                self.0.as_raw_fd(),
                value.as_ptr(),
                libc::O_RDONLY | libc::O_NOFOLLOW | libc::O_NONBLOCK | libc::O_CLOEXEC,
            )
        };
        let file = opened(fd)?;
        ensure(file.metadata()?.is_file(), "文件不是普通文件")?;
        Ok(file)
    }
    pub fn beneath(&self, relative: &str) -> Result<File> {
        ensure(safe_relative(relative), "路径不安全")?;
        let mut parts = relative.split('/').peekable();
        let mut current = Self(self.0.try_clone()?);
        while let Some(part) = parts.next() {
            if parts.peek().is_none() {
                return current.file(part);
            }
            current = current.child(part)?;
        }
        Err(Error::invalid("路径无效"))
    }
    pub fn create(&self, value: &str) -> Result<File> {
        let value = name(value)?;
        let fd = unsafe {
            libc::openat(
                self.0.as_raw_fd(),
                value.as_ptr(),
                libc::O_WRONLY | libc::O_CREAT | libc::O_EXCL | libc::O_NOFOLLOW | libc::O_CLOEXEC,
                0o600,
            )
        };
        opened(fd)
    }
    pub fn append(&self, value: &str, expected: &std::fs::Metadata) -> Result<File> {
        let value = name(value)?;
        let fd = unsafe {
            libc::openat(
                self.0.as_raw_fd(),
                value.as_ptr(),
                libc::O_WRONLY | libc::O_APPEND | libc::O_NOFOLLOW | libc::O_CLOEXEC,
            )
        };
        let file = opened(fd)?;
        let current = file.metadata()?;
        ensure(
            current.ino() == expected.ino()
                && current.dev() == expected.dev()
                && current.nlink() == 1,
            "日志文件已变化",
        )?;
        Ok(file)
    }
    pub fn rename(&self, source: &str, target: &str) -> Result<()> {
        let source = name(source)?;
        let target = name(target)?;
        let result = unsafe {
            libc::renameat(
                self.0.as_raw_fd(),
                source.as_ptr(),
                self.0.as_raw_fd(),
                target.as_ptr(),
            )
        };
        if result < 0 {
            Err(std::io::Error::last_os_error().into())
        } else {
            self.0.sync_all()?;
            Ok(())
        }
    }
    pub fn entries(&self) -> Result<Vec<String>> {
        let mut names = Vec::new();
        for entry in std::fs::read_dir(format!("/proc/self/fd/{}", self.0.as_raw_fd()))? {
            let entry = entry?;
            if let Some(name) = entry.file_name().to_str() {
                names.push(name.to_owned());
            }
        }
        names.sort();
        Ok(names)
    }
    pub fn metadata(&self) -> Result<std::fs::Metadata> {
        Ok(self.0.metadata()?)
    }
    pub fn unlink(&self, value: &str, expected: &std::fs::Metadata) -> Result<()> {
        let file = self.file(value)?;
        let current = file.metadata()?;
        ensure(
            current.dev() == expected.dev()
                && current.ino() == expected.ino()
                && current.nlink() == 1
                && current.uid() == unsafe { libc::getuid() },
            "清理目标已变化",
        )?;
        let value = name(value)?;
        let result = unsafe { libc::unlinkat(self.0.as_raw_fd(), value.as_ptr(), 0) };
        if result < 0 {
            Err(std::io::Error::last_os_error().into())
        } else {
            self.0.sync_all()?;
            Ok(())
        }
    }
    pub fn bytes(&self, value: &str, max: u64) -> Result<Vec<u8>> {
        let file = self.file(value)?;
        ensure(file.metadata()?.len() <= max, "文件超过读取上限")?;
        let mut data = Vec::new();
        file.take(max + 1).read_to_end(&mut data)?;
        ensure(data.len() as u64 <= max, "文件超过读取上限")?;
        Ok(data)
    }
}
