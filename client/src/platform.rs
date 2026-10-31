//! What differs between Linux and macOS: Linux has /proc, pidfds, O_PATH
//! and SO_PEERCRED; macOS has libproc, sysctl, O_SEARCH and getpeereid.

use std::io;
use std::path::PathBuf;

/// The calling thread's errno, for signal handlers that must keep it.
#[cfg(target_os = "linux")]
pub unsafe fn errno() -> *mut libc::c_int {
    libc::__errno_location()
}

#[cfg(target_os = "macos")]
pub unsafe fn errno() -> *mut libc::c_int {
    libc::__error()
}

/// How the cwd is opened to hand it to the child: without needing read
/// permission on it, as the tool needs none natively.
#[cfg(target_os = "linux")]
pub const CWD_FLAGS: libc::c_int = libc::O_PATH | libc::O_DIRECTORY | libc::O_CLOEXEC;
#[cfg(target_os = "macos")]
pub const CWD_FLAGS: libc::c_int = libc::O_SEARCH | libc::O_CLOEXEC;

/// Our controlling terminal, open for the child, or -1 if we have none.
/// On macOS /dev/tty stands for the terminal of whichever process uses it,
/// and the child has none: the terminal device itself is opened instead.
pub fn open_tty() -> libc::c_int {
    let flags = libc::O_RDWR | libc::O_NOCTTY | libc::O_CLOEXEC;
    let fd = unsafe { libc::open(b"/dev/tty\0".as_ptr() as *const libc::c_char, flags) };
    if cfg!(target_os = "linux") || fd < 0 {
        return fd;
    }
    unsafe { libc::close(fd) };
    controlling_tty().map_or(-1, |path| unsafe { libc::open(path.as_ptr(), flags) })
}

#[cfg(target_os = "linux")]
fn controlling_tty() -> Option<std::ffi::CString> {
    None
}

#[cfg(target_os = "macos")]
fn controlling_tty() -> Option<std::ffi::CString> {
    unsafe {
        let mut info: libc::proc_bsdinfo = std::mem::zeroed();
        let size = std::mem::size_of::<libc::proc_bsdinfo>() as libc::c_int;
        if libc::proc_pidinfo(libc::getpid(), libc::PROC_PIDTBSDINFO, 0,
                              &mut info as *mut _ as *mut libc::c_void, size) != size
            || info.e_tdev == u32::MAX {
            return None;
        }
        let name = libc::devname(info.e_tdev as libc::dev_t, libc::S_IFCHR);
        if name.is_null() {
            return None;
        }
        let mut path = b"/dev/".to_vec();
        path.extend_from_slice(std::ffi::CStr::from_ptr(name).to_bytes());
        std::ffi::CString::new(path).ok()
    }
}

/// A pidfd for `pid`, or -1: SIGTERM then goes by pid.
#[cfg(target_os = "linux")]
pub fn pidfd_open(pid: i32) -> i32 {
    unsafe { libc::syscall(libc::SYS_pidfd_open, pid, 0) as i32 }
}

#[cfg(not(target_os = "linux"))]
pub fn pidfd_open(_pid: i32) -> i32 {
    -1
}

/// Async-signal-safe.
#[cfg(target_os = "linux")]
pub unsafe fn pidfd_send_signal(fd: i32, sig: libc::c_int) {
    libc::syscall(libc::SYS_pidfd_send_signal, fd, sig, std::ptr::null::<libc::siginfo_t>(), 0);
}

#[cfg(not(target_os = "linux"))]
pub unsafe fn pidfd_send_signal(_fd: i32, _sig: libc::c_int) {}

/// The effective uid of the process at the other end of a Unix socket.
#[cfg(target_os = "linux")]
pub fn peer_uid(fd: libc::c_int) -> io::Result<libc::uid_t> {
    unsafe {
        let mut cred: libc::ucred = std::mem::zeroed();
        let mut len = std::mem::size_of::<libc::ucred>() as libc::socklen_t;
        if libc::getsockopt(fd, libc::SOL_SOCKET, libc::SO_PEERCRED,
                            &mut cred as *mut _ as *mut libc::c_void, &mut len) != 0 {
            return Err(io::Error::last_os_error());
        }
        Ok(cred.uid)
    }
}

#[cfg(target_os = "macos")]
pub fn peer_uid(fd: libc::c_int) -> io::Result<libc::uid_t> {
    let (mut uid, mut gid) = (0, 0);
    if unsafe { libc::getpeereid(fd, &mut uid, &mut gid) } != 0 {
        return Err(io::Error::last_os_error());
    }
    Ok(uid)
}

/// Our parent's command line and cwd, and the file it has open on `fd`
/// if any (bash keeps the script it runs on fd 255).
#[cfg(target_os = "linux")]
pub fn parent_process(fd: i32) -> Option<(Vec<Vec<u8>>, PathBuf, Option<PathBuf>)> {
    let proc_dir = PathBuf::from(format!("/proc/{}", unsafe { libc::getppid() }));
    let cmdline = std::fs::read(proc_dir.join("cmdline")).ok()?;
    let argv = cmdline.split(|b| *b == 0).filter(|a| !a.is_empty()).map(<[u8]>::to_vec).collect();
    let cwd = std::fs::read_link(proc_dir.join("cwd")).ok()?;
    Some((argv, cwd, Some(proc_dir.join(format!("fd/{}", fd)))))
}

#[cfg(target_os = "macos")]
pub fn parent_process(fd: i32) -> Option<(Vec<Vec<u8>>, PathBuf, Option<PathBuf>)> {
    use std::ffi::OsStr;
    use std::os::unix::ffi::OsStrExt;
    let pid = unsafe { libc::getppid() };
    let (argv, _env) = process_args(pid)?;
    let path = |buf: &[u8]| {
        let end = buf.iter().position(|b| *b == 0).unwrap_or(buf.len());
        PathBuf::from(OsStr::from_bytes(&buf[..end]))
    };
    let cwd = unsafe {
        let mut info: libc::proc_vnodepathinfo = std::mem::zeroed();
        let size = std::mem::size_of::<libc::proc_vnodepathinfo>() as libc::c_int;
        if libc::proc_pidinfo(pid, libc::PROC_PIDVNODEPATHINFO, 0,
                              &mut info as *mut _ as *mut libc::c_void, size) != size {
            return None;
        }
        let bytes: &[u8; 1024] = &*(info.pvi_cdir.vip_path.as_ptr() as *const [u8; 1024]);
        path(bytes)
    };
    // struct vnode_fdinfowithpath: proc_fileinfo (24 bytes), then
    // vnode_info_path, whose path follows a vnode_info (152 bytes).
    const PROC_PIDFDVNODEPATHINFO: libc::c_int = 2;
    let mut info = [0u8; 24 + 152 + 1024];
    let n = unsafe {
        libc::proc_pidfdinfo(pid, fd, PROC_PIDFDVNODEPATHINFO,
                             info.as_mut_ptr() as *mut libc::c_void, info.len() as libc::c_int)
    };
    let file = if n as usize == info.len() { Some(path(&info[24 + 152..])) } else { None };
    Some((argv, cwd, file))
}

/// A process's argv and env as exec gave them (sysctl KERN_PROCARGS2):
/// argc, the executable path and padding, argv, then env up to an empty
/// string.
#[cfg(target_os = "macos")]
fn process_args(pid: i32) -> Option<(Vec<Vec<u8>>, Vec<Vec<u8>>)> {
    let mut mib = [libc::CTL_KERN, libc::KERN_PROCARGS2, pid];
    let mut size: libc::size_t = 0;
    unsafe {
        if libc::sysctl(mib.as_mut_ptr(), 3, std::ptr::null_mut(), &mut size, std::ptr::null_mut(), 0) != 0 {
            return None;
        }
    }
    let mut buf = vec![0u8; size];
    unsafe {
        if libc::sysctl(mib.as_mut_ptr(), 3, buf.as_mut_ptr() as *mut libc::c_void, &mut size,
                        std::ptr::null_mut(), 0) != 0 {
            return None;
        }
    }
    buf.truncate(size);
    if buf.len() < 4 {
        return None;
    }
    let argc = i32::from_ne_bytes([buf[0], buf[1], buf[2], buf[3]]).max(0) as usize;
    let rest = &buf[4..];
    let after_path = rest.iter().position(|b| *b == 0)?;
    let rest = &rest[after_path..];
    let start = rest.iter().position(|b| *b != 0)?;
    let mut items = rest[start..].split(|b| *b == 0);
    let argv: Vec<Vec<u8>> = items.by_ref().take(argc).map(<[u8]>::to_vec).collect();
    let env = items.take_while(|e| !e.is_empty()).map(<[u8]>::to_vec).collect();
    Some((argv, env))
}

/// What the daemon must be told of the env it is started with, as
/// (name, value) for its own env: nothing on Linux, where it reads
/// /proc/self/environ. On macOS a launcher stub may change the env between
/// its exec and the interpreter's (python.org's adds
/// __CF_USER_TEXT_ENCODING), and the daemon could not tell what came from
/// us: a FNV-1a 64 digest of each entry, then its name, joined by '='.
#[cfg(target_os = "linux")]
pub fn launch_env_var(_env: &[Vec<u8>]) -> Option<Vec<u8>> {
    None
}

#[cfg(target_os = "macos")]
pub fn launch_env_var(env: &[Vec<u8>]) -> Option<Vec<u8>> {
    let mut out = b"SPEEDRUNNER_LAUNCH_ENV=".to_vec();
    let mut first = true;
    for e in env {
        let eq = match e.iter().position(|b| *b == b'=') {
            Some(i) if i > 0 => i,
            _ => continue,
        };
        let mut h: u64 = 0xcbf29ce484222325;
        for b in e {
            h = (h ^ *b as u64).wrapping_mul(0x100000001b3);
        }
        if !first {
            out.push(b'=');
        }
        first = false;
        out.extend_from_slice(format!("{:016x}", h).as_bytes());
        out.extend_from_slice(&e[..eq]);
    }
    Some(out)
}
