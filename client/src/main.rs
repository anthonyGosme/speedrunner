//! speedrunner client: hands a CLI invocation to the tool's preloaded daemon,
//! or execs the real tool when the fast path is unavailable.
//!
//! Safety rule: fall back to the native tool only before the daemon's child
//! has been told to go (b"G"). After that, errors are reported, never retried,
//! so a command can never run twice. Wire format: see server/speedrunner_daemon.py.

mod script;

use std::env;
use std::ffi::{CString, OsStr, OsString};
use std::fs::File;
use std::io::{self, Read, Write};
use std::mem;
use std::os::unix::ffi::{OsStrExt, OsStringExt};
use std::os::unix::fs::MetadataExt;
use std::os::unix::io::{AsRawFd, RawFd};
use std::os::unix::net::UnixStream;
use std::path::{Path, PathBuf};
use std::process;
use std::ptr;
use std::sync::atomic::{AtomicI32, Ordering};
use std::time::{Duration, Instant};

const MAGIC: &[u8] = b"HS1";
const EXIT_NOT_FAST: i32 = 124;
const EXIT_LOST: i32 = 125;
const HANDSHAKE_TIMEOUT: Duration = Duration::from_secs(3);
// How long a would-be daemon waits for the startup lock that an exiting
// daemon (socket already gone) still holds.
const LOCK_WAIT: Duration = Duration::from_secs(3);
// How long the launcher waits for one daemon to be ready before the next.
const START_WAIT: Duration = Duration::from_secs(20);
const MAX_MSG: usize = 1 << 20;
// Relayed to the child. Terminal-generated ones go to its whole process group,
// as the terminal would have sent them to the native tool's group.
const RELAYED: [libc::c_int; 5] =
    [libc::SIGINT, libc::SIGQUIT, libc::SIGWINCH, libc::SIGHUP, libc::SIGTERM];

static CHILD_PID: AtomicI32 = AtomicI32::new(0);
static CHILD_PIDFD: AtomicI32 = AtomicI32::new(-1);

// The daemons and the zsh hook, built in: a release is this one file.
const EMBEDDED: [(&str, &[u8]); 3] = [
    ("speedrunner_daemon.py", include_bytes!("../../server/speedrunner_daemon.py")),
    ("speedrunner_daemon.rb", include_bytes!("../../server/speedrunner_daemon.rb")),
    ("speedrunner_daemon.php", include_bytes!("../../server/speedrunner_daemon.php")),
];
const ZSH_HOOK: &str = include_str!("../../shell/speedrunner.zsh");

fn main() {
    let args: Vec<OsString> = env::args_os().collect();
    let invoked = args.first().map(|a| basename(a.as_bytes()).to_vec()).unwrap_or_default();
    // Called as `srr` (a link to this binary): a prefix, as with time or
    // nice, that works for any tool without enrolling it. `srr aws s3 ls`
    // is `speedrunner run aws -- s3 ls`.
    if invoked == b"srr" {
        if args.len() < 2 {
            eprintln!("usage: srr <tool> [args...]");
            process::exit(2);
        }
        let rest: Vec<OsString> = [args[1].clone(), OsString::from("--")]
            .into_iter()
            .chain(args[2..].iter().cloned())
            .collect();
        process::exit(cmd_run(&rest));
    }
    // Called under a tool's name: one of the links `activate` puts first on
    // PATH. `aws s3 ls` is then `speedrunner run aws -- s3 ls`.
    if invoked != b"speedrunner" && !invoked.is_empty() {
        let lazy = lazy_counter(&invoked);
        let rest: Vec<OsString> = [OsString::from_vec(invoked), OsString::from("--")]
            .into_iter()
            .chain(args[1..].iter().cloned())
            .collect();
        process::exit(cmd_run_with(&rest, lazy.as_deref()));
    }
    let code = match args.get(1).map(|a| a.as_bytes()) {
        Some(b"run") => cmd_run(&args[2..]),
        Some(b"activate") => cmd_activate(&args[2..]),
        Some(b"exec") => cmd_exec(&args[2..]),
        Some(b"prewarm") => with_tool(&args[2..], cmd_prewarm),
        Some(b"enroll") => cmd_enroll(&args[2..]),
        Some(b"status") => with_tool(&args[2..], |t| control(t, b'P')),
        Some(b"stop") => with_tool(&args[2..], |t| control(t, b'S')),
        Some(b"init") => cmd_init(&args[2..]),
        Some(b"--version") | Some(b"version") => {
            println!("speedrunner {}", env!("CARGO_PKG_VERSION"));
            0
        }
        _ => {
            usage();
            2
        }
    };
    process::exit(code);
}

fn usage() {
    eprintln!(
        "usage: speedrunner run <tool> -- [args...]   (or: srr <tool> [args...])\n       \
         eval \"$(speedrunner activate [--eager] [<tool>...])\"     (in a script: no prefix needed)\n       \
         speedrunner exec [--eager] [-t <tool>]... [--] <command> [args...]\n       \
         speedrunner enroll [<tool>...]\n       \
         speedrunner prewarm|status|stop <tool>   (or: php <script>)\n       \
         eval \"$(speedrunner init zsh)\"     (in ~/.zshrc: wrappers, warm-up while typing)\n       \
         speedrunner --version"
    );
}

/// `eval "$(speedrunner init zsh)"` in ~/.zshrc: the built-in hook.
fn cmd_init(rest: &[OsString]) -> i32 {
    match rest.first().map(|a| a.as_bytes()) {
        Some(b"zsh") => {
            print!("{}", ZSH_HOOK);
            0
        }
        Some(other) => {
            eprintln!("speedrunner: init {}: only zsh is supported for now", String::from_utf8_lossy(other));
            2
        }
        None => {
            eprintln!("usage: eval \"$(speedrunner init zsh)\"   (in ~/.zshrc)");
            2
        }
    }
}

struct Tool<'a> {
    name: &'a OsStr,
    path: Vec<u8>, // the script the daemon preloads
    sock: PathBuf,
    dir: PathBuf,
    form: Option<ScriptForm>,
}

/// `php script args`: the interpreter on PATH, the script as an argument.
struct ScriptForm {
    interp: Vec<u8>,
    typed: OsString, // the script as typed, for argv and for the native run
}

impl<'a> Tool<'a> {
    fn resolve(name: &'a OsStr) -> Option<Tool<'a>> {
        let path = resolve_in_path(name.as_bytes())?;
        let dir = runtime_dir();
        let sock = dir.join(socket_name(name, &path));
        Some(Tool { name, path, sock, dir, form: None })
    }

    /// The tool as `php script args` runs it, when it is the PHP interpreter
    /// and its first argument a script file; None otherwise. Only PHP:
    /// its daemon resolves a script path relative to the client's cwd.
    fn script_form(&self, args: &[OsString]) -> Option<Tool<'a>> {
        if !matches!(Runtime::of(&self.path), Some(Runtime::Php)) || read_shebang(&self.path).is_ok() {
            return None;
        }
        let typed = args.first()?;
        if typed.as_bytes().starts_with(b"-") {
            return None; // php -r, php -d ...: run as is
        }
        let script = std::fs::canonicalize(typed).ok()?;
        if !script.is_file() {
            return None;
        }
        let path = script.into_os_string().into_vec();
        let dir = runtime_dir();
        let sock = dir.join(socket_name(OsStr::from_bytes(basename(&path)), &path));
        Some(Tool { name: self.name, path, sock, dir,
                    form: Some(ScriptForm { interp: self.path.clone(), typed: typed.clone() }) })
    }

    /// argv[0] the tool sees: the script as typed, or its resolved path.
    fn argv0(&self) -> &[u8] {
        self.form.as_ref().map_or(&self.path[..], |f| f.typed.as_bytes())
    }

    fn display(&self) -> String {
        self.name.to_string_lossy().into_owned()
    }
}

/// `<tool>`, or `php <script>` for a script's own daemon.
fn with_tool(rest: &[OsString], f: impl Fn(&Tool) -> i32) -> i32 {
    if rest.is_empty() || rest.len() > 2 {
        usage();
        return 2;
    }
    let tool = match Tool::resolve(&rest[0]) {
        Some(tool) => tool,
        None => {
            eprintln!("speedrunner: {}: command not found", rest[0].to_string_lossy());
            return 127;
        }
    };
    if rest.len() == 1 {
        return f(&tool);
    }
    match tool.script_form(&rest[1..]) {
        Some(script) => f(&script),
        None => {
            eprintln!("speedrunner: {} {}: not an interpreter and its script",
                      rest[0].to_string_lossy(), rest[1].to_string_lossy());
            2
        }
    }
}

// --- run -------------------------------------------------------------------

fn cmd_run(rest: &[OsString]) -> i32 {
    cmd_run_with(rest, None)
}

/// `lazy`: the call counter of a link `activate` found by itself; with no
/// daemon up, only a tool's second call starts one, so that a tool called
/// once costs nothing.
fn cmd_run_with(rest: &[OsString], lazy: Option<&Path>) -> i32 {
    if rest.len() < 2 || rest[1] != "--" {
        usage();
        return 2;
    }
    let mut args = &rest[2..];
    let mut tool = match Tool::resolve(&rest[0]) {
        Some(t) => t,
        None => {
            eprintln!("speedrunner: {}: command not found", rest[0].to_string_lossy());
            return 127;
        }
    };
    if let Some(script) = tool.script_form(args) {
        tool = script;
        args = &args[1..];
    } else if read_shebang(&tool.path).is_err() {
        fall_back(&tool, args, "not a script of a runtime speedrunner serves");
    }
    let start = |tool: &Tool| {
        if lazy.map_or(true, called_again) {
            autostart(tool);
        }
    };
    if let Err(e) = check_runtime_dir(&tool.dir) {
        start(&tool);
        fall_back(&tool, args, &format!("runtime dir: {}", e));
    }
    let sock = match connect(&tool.sock) {
        Ok(s) => s,
        Err(e) => {
            if e.kind() != io::ErrorKind::PermissionDenied {
                start(&tool);
            }
            fall_back(&tool, args, &format!("connect: {}", e));
        }
    };
    let pid = match handshake(&sock, &tool, args) {
        Ok(pid) => pid,
        Err(Refused::Native(reason)) => {
            if reason.starts_with("stale") {
                autostart(&tool); // the old daemon is exiting; the new one waits for its lock
            }
            fall_back(&tool, args, &reason);
        }
        Err(Refused::Io(e)) => fall_back(&tool, args, &format!("handshake: {}", e)),
    };

    CHILD_PIDFD.store(pidfd_open(pid), Ordering::SeqCst);
    CHILD_PID.store(pid, Ordering::SeqCst);
    install_relays();
    // Sending G commits. If it cannot be sent, the child never runs the tool.
    let mut w: &UnixStream = &sock;
    if let Err(e) = w.write_all(b"G") {
        fall_back(&tool, args, &format!("go: {}", e));
    }
    wait_for_child(&sock, &tool, pid)
}

enum Refused {
    Native(String),
    Io(io::Error),
}

fn handshake(sock: &UnixStream, tool: &Tool, args: &[OsString]) -> Result<i32, Refused> {
    sock.set_read_timeout(Some(HANDSHAKE_TIMEOUT)).map_err(Refused::Io)?;
    for fd in 0..3 {
        if unsafe { libc::fcntl(fd, libc::F_GETFD) } < 0 {
            return Err(Refused::Native(format!("fd {} is closed", fd)));
        }
    }
    let cwd = unsafe {
        libc::open(b".\0".as_ptr() as *const libc::c_char,
                   libc::O_PATH | libc::O_DIRECTORY | libc::O_CLOEXEC)
    };
    if cwd < 0 {
        return Err(Refused::Io(io::Error::last_os_error()));
    }
    let msg = frame(b'R', &run_body(tool.argv0(), args));
    // Our controlling terminal, if any: the child has none, and tools open
    // /dev/tty for passwords.
    let tty = unsafe {
        libc::open(b"/dev/tty\0".as_ptr() as *const libc::c_char,
                   libc::O_RDWR | libc::O_NOCTTY | libc::O_CLOEXEC)
    };
    let mut fds = vec![0, 1, 2, cwd];
    if tty >= 0 {
        fds.push(tty);
    }
    let sent = send_with_fds(sock, &msg, &fds);
    unsafe {
        libc::close(cwd);
        if tty >= 0 {
            libc::close(tty);
        }
    }
    sent.map_err(Refused::Io)?;
    match read_msg(sock).map_err(Refused::Io)? {
        (b'N', reason) => Err(Refused::Native(String::from_utf8_lossy(&reason).into_owned())),
        (b'R', p) if p.len() == 4 => Ok(i32::from_le_bytes([p[0], p[1], p[2], p[3]])),
        _ => Err(Refused::Io(io::Error::new(io::ErrorKind::InvalidData, "unexpected reply"))),
    }
}

fn wait_for_child(sock: &UnixStream, tool: &Tool, pid: i32) -> i32 {
    let _ = sock.set_read_timeout(None);
    match read_msg(sock) {
        // Sent once nothing visible is left: the child tears down after we exit.
        Ok((b'X', p)) if p.len() == 4 => i32::from_le_bytes([p[0], p[1], p[2], p[3]]),
        // No X: the child died by a signal or its teardown decided the status.
        Ok((b'W', p)) if p.len() == 5 => {
            let value = i32::from_le_bytes([p[1], p[2], p[3], p[4]]);
            if p[0] == 1 {
                die_by_signal(value);
            }
            value
        }
        Ok(_) => {
            eprintln!("speedrunner: protocol error from {} daemon; not re-running it",
                      tool.display());
            EXIT_LOST
        }
        Err(_) => {
            eprintln!(
                "speedrunner: lost track of {} (pid {}) after it started; not re-running it",
                tool.display(), pid
            );
            EXIT_LOST
        }
    }
}

fn fall_back(tool: &Tool, args: &[OsString], reason: &str) -> ! {
    if env::var_os("SPEEDRUNNER_REQUIRE_FAST").map_or(false, |v| v == "1") {
        eprintln!("speedrunner: fast path unavailable: {}", reason);
        process::exit(EXIT_NOT_FAST);
    }
    if env::var_os("SPEEDRUNNER_DEBUG").is_some() {
        eprintln!("speedrunner: running {} natively: {}", tool.display(), reason);
    }
    exec_native(tool, args)
}

fn exec_native(tool: &Tool, args: &[OsString]) -> ! {
    // argv[0] as the shell would set it; the kernel hands the path to the interpreter.
    let typed = tool.form.as_ref().map(|f| f.typed.as_os_str());
    let argv: Vec<CString> = std::iter::once(tool.name)
        .chain(typed)
        .chain(args.iter().map(|a| a.as_os_str()))
        .map(|a| cstring(a.as_bytes()))
        .collect();
    let mut ptrs: Vec<*const libc::c_char> = argv.iter().map(|c| c.as_ptr()).collect();
    ptrs.push(ptr::null());
    let path = cstring(tool.form.as_ref().map_or(&tool.path[..], |f| &f.interp[..]));
    unsafe {
        // Rust ignores SIGPIPE at startup; the tool must inherit the default.
        libc::signal(libc::SIGPIPE, libc::SIG_DFL);
        libc::execv(path.as_ptr(), ptrs.as_ptr());
    }
    let err = io::Error::last_os_error();
    eprintln!("speedrunner: {}: {}", tool.display(), err);
    process::exit(if err.kind() == io::ErrorKind::NotFound { 127 } else { 126 });
}

// --- signals ---------------------------------------------------------------

extern "C" fn relay(sig: libc::c_int) {
    let pid = CHILD_PID.load(Ordering::SeqCst);
    if pid <= 0 {
        return;
    }
    unsafe {
        let errno = *libc::__errno_location();
        if sig == libc::SIGTERM {
            let fd = CHILD_PIDFD.load(Ordering::SeqCst);
            if fd >= 0 {
                libc::syscall(libc::SYS_pidfd_send_signal, fd, sig,
                              ptr::null::<libc::siginfo_t>(), 0);
            } else {
                libc::kill(pid, sig);
            }
        } else {
            libc::kill(-pid, sig);
        }
        *libc::__errno_location() = errno;
    }
}

fn install_relays() {
    for &sig in RELAYED.iter() {
        unsafe {
            let mut old: libc::sigaction = mem::zeroed();
            libc::sigaction(sig, ptr::null(), &mut old);
            if old.sa_sigaction == libc::SIG_IGN {
                continue; // inherited as ignored: the child ignores it too
            }
            let mut sa: libc::sigaction = mem::zeroed();
            sa.sa_sigaction = relay as extern "C" fn(libc::c_int) as usize;
            sa.sa_flags = libc::SA_RESTART;
            libc::sigemptyset(&mut sa.sa_mask);
            libc::sigaction(sig, &sa, ptr::null_mut());
        }
    }
}

fn ignored_signals() -> u64 {
    let mut mask = 0u64;
    for sig in 1..32 {
        // Rust itself sets SIGPIPE to SIG_IGN; CPython ignores it anyway.
        if sig == libc::SIGKILL || sig == libc::SIGSTOP || sig == libc::SIGPIPE {
            continue;
        }
        unsafe {
            let mut old: libc::sigaction = mem::zeroed();
            if libc::sigaction(sig, ptr::null(), &mut old) == 0 && old.sa_sigaction == libc::SIG_IGN {
                mask |= 1 << sig;
            }
        }
    }
    mask
}

fn die_by_signal(sig: i32) -> ! {
    unsafe {
        // Exit the way the child did, without dumping a core of our own.
        let mut lim: libc::rlimit = mem::zeroed();
        if libc::getrlimit(libc::RLIMIT_CORE, &mut lim) == 0 {
            lim.rlim_cur = 0;
            libc::setrlimit(libc::RLIMIT_CORE, &lim);
        }
        libc::signal(sig, libc::SIG_DFL);
        let mut set: libc::sigset_t = mem::zeroed();
        libc::sigemptyset(&mut set);
        libc::sigaddset(&mut set, sig);
        libc::sigprocmask(libc::SIG_UNBLOCK, &set, ptr::null_mut());
        libc::raise(sig);
    }
    process::exit(128 + sig);
}

fn pidfd_open(pid: i32) -> i32 {
    unsafe { libc::syscall(libc::SYS_pidfd_open, pid, 0) as i32 }
}

// --- wire format -----------------------------------------------------------

fn put_u32(buf: &mut Vec<u8>, v: u32) {
    buf.extend_from_slice(&v.to_le_bytes());
}

fn put_blob(buf: &mut Vec<u8>, b: &[u8]) {
    put_u32(buf, b.len() as u32);
    buf.extend_from_slice(b);
}

fn frame(kind: u8, body: &[u8]) -> Vec<u8> {
    let mut msg = Vec::with_capacity(MAGIC.len() + 5 + body.len());
    msg.extend_from_slice(MAGIC);
    msg.push(kind);
    put_u32(&mut msg, body.len() as u32);
    msg.extend_from_slice(body);
    msg
}

fn run_body(script: &[u8], args: &[OsString]) -> Vec<u8> {
    let mut b = Vec::with_capacity(8192);
    put_u32(&mut b, args.len() as u32 + 1);
    put_blob(&mut b, script);
    for a in args {
        put_blob(&mut b, a.as_bytes());
    }
    let env: Vec<Vec<u8>> = env::vars_os()
        .map(|(k, v)| {
            let mut e = k.into_vec();
            e.push(b'=');
            e.extend_from_slice(v.as_bytes());
            e
        })
        .collect();
    put_u32(&mut b, env.len() as u32);
    for e in &env {
        put_blob(&mut b, e);
    }
    let umask = unsafe {
        let m = libc::umask(0o022);
        libc::umask(m);
        m
    };
    put_u32(&mut b, umask as u32);
    b.extend_from_slice(&ignored_signals().to_le_bytes());
    let groups = unsafe {
        let n = libc::getgroups(0, ptr::null_mut()).max(0);
        let mut g = vec![0 as libc::gid_t; n as usize];
        let n = libc::getgroups(n, g.as_mut_ptr()).max(0);
        g.truncate(n as usize);
        g
    };
    put_u32(&mut b, groups.len() as u32);
    for g in groups {
        put_u32(&mut b, g);
    }
    b
}

fn send_with_fds(sock: &UnixStream, data: &[u8], fds: &[RawFd]) -> io::Result<()> {
    let fd_bytes = fds.len() * mem::size_of::<RawFd>();
    let sent = unsafe {
        let space = libc::CMSG_SPACE(fd_bytes as u32) as usize;
        let mut cbuf = vec![0u8; space];
        let mut iov = libc::iovec { iov_base: data.as_ptr() as *mut libc::c_void, iov_len: data.len() };
        let mut msg: libc::msghdr = mem::zeroed();
        msg.msg_iov = &mut iov;
        msg.msg_iovlen = 1;
        msg.msg_control = cbuf.as_mut_ptr() as *mut libc::c_void;
        msg.msg_controllen = space as _;
        let cmsg = libc::CMSG_FIRSTHDR(&msg);
        (*cmsg).cmsg_level = libc::SOL_SOCKET;
        (*cmsg).cmsg_type = libc::SCM_RIGHTS;
        (*cmsg).cmsg_len = libc::CMSG_LEN(fd_bytes as u32) as _;
        ptr::copy_nonoverlapping(fds.as_ptr() as *const u8, libc::CMSG_DATA(cmsg), fd_bytes);
        loop {
            let r = libc::sendmsg(sock.as_raw_fd(), &msg, libc::MSG_NOSIGNAL);
            if r < 0 && io::Error::last_os_error().kind() == io::ErrorKind::Interrupted {
                continue;
            }
            break r;
        }
    };
    if sent < 0 {
        return Err(io::Error::last_os_error());
    }
    let mut w: &UnixStream = sock;
    w.write_all(&data[sent as usize..])
}

fn read_msg(sock: &UnixStream) -> io::Result<(u8, Vec<u8>)> {
    let mut r: &UnixStream = sock;
    let mut header = [0u8; 5];
    r.read_exact(&mut header)?;
    let len = u32::from_le_bytes([header[1], header[2], header[3], header[4]]) as usize;
    if len > MAX_MSG {
        return Err(io::Error::new(io::ErrorKind::InvalidData, "message too large"));
    }
    let mut payload = vec![0u8; len];
    r.read_exact(&mut payload)?;
    Ok((header[0], payload))
}

// --- daemon discovery and startup ------------------------------------------

fn runtime_dir() -> PathBuf {
    match env::var_os("XDG_RUNTIME_DIR") {
        Some(d) if !d.is_empty() => Path::new(&d).join("speedrunner"),
        _ => PathBuf::from(format!("/tmp/speedrunner-{}", unsafe { libc::getuid() })),
    }
}

fn check_runtime_dir(dir: &Path) -> io::Result<()> {
    let c = cstring(dir.as_os_str().as_bytes());
    unsafe {
        let fd = libc::open(c.as_ptr(), libc::O_RDONLY | libc::O_DIRECTORY | libc::O_NOFOLLOW | libc::O_CLOEXEC);
        if fd < 0 {
            return Err(io::Error::last_os_error());
        }
        let mut st: libc::stat = mem::zeroed();
        let r = libc::fstat(fd, &mut st);
        libc::close(fd);
        if r != 0 {
            return Err(io::Error::last_os_error());
        }
        if st.st_uid != libc::getuid() || st.st_mode & 0o7777 != 0o700 {
            return Err(io::Error::new(io::ErrorKind::PermissionDenied,
                                      "runtime dir must be ours with mode 0700"));
        }
    }
    Ok(())
}

fn ensure_runtime_dir(dir: &Path) -> io::Result<()> {
    let c = cstring(dir.as_os_str().as_bytes());
    if unsafe { libc::mkdir(c.as_ptr(), 0o700) } != 0 {
        let e = io::Error::last_os_error();
        if e.kind() != io::ErrorKind::AlreadyExists {
            return Err(e);
        }
    }
    check_runtime_dir(dir)
}

fn connect(sock: &Path) -> io::Result<UnixStream> {
    let s = UnixStream::connect(sock)?;
    unsafe {
        let mut cred: libc::ucred = mem::zeroed();
        let mut len = mem::size_of::<libc::ucred>() as libc::socklen_t;
        if libc::getsockopt(s.as_raw_fd(), libc::SOL_SOCKET, libc::SO_PEERCRED,
                            &mut cred as *mut _ as *mut libc::c_void, &mut len) != 0 {
            return Err(io::Error::last_os_error());
        }
        if cred.uid != libc::getuid() {
            return Err(io::Error::new(io::ErrorKind::PermissionDenied,
                                      "daemon runs as another user"));
        }
    }
    Ok(s)
}

fn socket_name(tool: &OsStr, path: &[u8]) -> String {
    let base: String = Path::new(tool)
        .file_name()
        .map(|n| n.to_string_lossy().into_owned())
        .unwrap_or_default()
        .chars()
        .take(32)
        .map(|c| if c.is_ascii_alphanumeric() || "._-".contains(c) { c } else { '_' })
        .collect();
    format!("{}-{:016x}.sock", base, fnv1a(path))
}

fn fnv1a(data: &[u8]) -> u64 {
    let mut h: u64 = 0xcbf2_9ce4_8422_2325;
    for b in data {
        h ^= *b as u64;
        h = h.wrapping_mul(0x0100_0000_01b3);
    }
    h
}

/// Start the tool's daemon in the background, unless one is running.
fn autostart(tool: &Tool) {
    if env::var_os("SPEEDRUNNER_NO_AUTOSTART").is_none() {
        if let Err(e) = spawn_daemon(tool, "run") {
            if env::var_os("SPEEDRUNNER_DEBUG").is_some() {
                eprintln!("speedrunner: cannot start {} daemon: {}", tool.display(), e);
            }
        }
    }
}

/// Make sure a daemon is up and still matches the installed tool, so that
/// the next run finds a spare ready. Called by the shell while typing.
fn cmd_prewarm(tool: &Tool) -> i32 {
    match request(tool, b'P') {
        // A stale daemon is exiting; its replacement waits for the lock.
        Ok((b'P', text)) if text.starts_with(b"stale") => {}
        Ok(_) => return 0, // ready, or disabled: a restart would not help
        Err(_) => {}
    }
    match spawn_daemon(tool, "prewarm") {
        Ok(()) => 0,
        Err(e) => {
            eprintln!("speedrunner: cannot start {} daemon: {}", tool.display(), e);
            1
        }
    }
}

/// One control request. A daemon that holds the lock but has no socket yet
/// is starting: wait for it rather than report it down.
fn request(tool: &Tool, kind: u8) -> io::Result<(u8, Vec<u8>)> {
    check_runtime_dir(&tool.dir)?;
    let deadline = Instant::now() + HANDSHAKE_TIMEOUT;
    let s = loop {
        match connect(&tool.sock) {
            Ok(s) => break s,
            Err(e) if lock_held(tool) && Instant::now() < deadline && e.kind() != io::ErrorKind::PermissionDenied => {
                std::thread::sleep(Duration::from_millis(10))
            }
            Err(e) => return Err(e),
        }
    };
    s.set_read_timeout(Some(HANDSHAKE_TIMEOUT))?;
    let mut w: &UnixStream = &s;
    w.write_all(&frame(kind, b""))?;
    read_msg(&s)
}

fn lock_path(tool: &Tool) -> CString {
    cstring(tool.sock.with_extension("sock.lock").as_os_str().as_bytes())
}

fn lock_held(tool: &Tool) -> bool {
    unsafe {
        let fd = libc::open(lock_path(tool).as_ptr(), libc::O_RDONLY | libc::O_CLOEXEC | libc::O_NOFOLLOW);
        if fd < 0 {
            return false;
        }
        let held = libc::flock(fd, libc::LOCK_SH | libc::LOCK_NB) != 0;
        libc::close(fd);
        held
    }
}

fn control(tool: &Tool, kind: u8) -> i32 {
    match request(tool, kind) {
        Ok((b'P', text)) => {
            let text = String::from_utf8_lossy(&text);
            println!("{}", text);
            if kind == b'S' || text.starts_with("ready") { 0 } else { 1 }
        }
        Ok((_, text)) => {
            println!("{}", String::from_utf8_lossy(&text));
            1
        }
        Err(_) => {
            println!("down");
            1
        }
    }
}

/// Double fork; the grandchild takes the startup lock, then becomes the
/// daemon and keeps it. However many clients race here, at most one daemon
/// starts. The lock file says what its holder does: "running <pid>", or
/// "exiting <pid>" once it lets go. Decided here, from what we see now:
/// a free lock, start; a daemon starting or running, nothing to do; one
/// exiting, the grandchild waits for that very one to let go, and gives
/// up on anything else. The caller waits for none of this.
/// `--tty MASK[:ROWSxCOLS]` for a daemon: which of our fds 0-2 are
/// terminals, and the terminal's size. The daemon imports the tool with a pty
/// on those, since some tools check for a terminal at import. A prewarm from
/// the shell hook has its fds on /dev/null, but warms up for the command
/// line being typed: on the terminal, when there is one.
fn tty_spec(origin: &str) -> String {
    let mut mask = 0u8;
    let mut size = None;
    let mut probe = |fd: libc::c_int| unsafe {
        let mut ws: libc::winsize = std::mem::zeroed();
        if size.is_none() && libc::ioctl(fd, libc::TIOCGWINSZ, &mut ws) == 0 && ws.ws_row > 0 {
            size = Some((ws.ws_row, ws.ws_col));
        }
    };
    if origin == "prewarm" {
        let fd = unsafe {
            libc::open(b"/dev/tty\0".as_ptr() as *const libc::c_char,
                       libc::O_RDWR | libc::O_NOCTTY | libc::O_CLOEXEC)
        };
        if fd >= 0 {
            mask = 7;
            probe(fd);
            unsafe { libc::close(fd) };
        }
    } else {
        for fd in 0..3 {
            if unsafe { libc::isatty(fd) } == 1 {
                mask |= 1 << fd;
                probe(fd);
            }
        }
    }
    match size {
        Some((rows, cols)) => format!("{}:{}x{}", mask, rows, cols),
        None => mask.to_string(),
    }
}

fn spawn_daemon(tool: &Tool, origin: &str) -> io::Result<()> {
    spawn_daemon_with(tool, origin, &tty_spec(origin))
}

/// How many daemons may preload at once: one per CPU we may run on (the
/// affinity mask, as nproc counts: a container's cpuset, not the host's
/// CPUs), so that they do not starve the script that waits for them.
/// SPEEDRUNNER_START_JOBS overrides.
fn start_jobs() -> usize {
    env::var("SPEEDRUNNER_START_JOBS").ok().and_then(|v| v.parse().ok())
        .unwrap_or_else(|| std::thread::available_parallelism().map_or(1, |n| n.get()))
        .max(1)
}

/// Start the daemons of `tools` in that order (the order a script first
/// runs them), at most start_jobs() preloading at once: the next starts as
/// soon as one is ready. Beyond that many, a detached launcher does the
/// waiting, on fds of its own; the caller waits for none of it. A tool the
/// script reaches before its daemon has started runs natively, as it would
/// without the launcher, and the startup lock keeps it to one daemon.
fn start_daemons(tools: &[Tool], origin: &str) {
    let jobs = start_jobs();
    let tty = tty_spec(origin); // from our fds, before the launcher lets go of them
    if tools.len() <= jobs {
        for tool in tools {
            let _ = spawn_daemon_with(tool, origin, &tty);
        }
        return;
    }
    let devnull = cstring(b"/dev/null");
    unsafe {
        let pid = libc::fork();
        if pid < 0 {
            return;
        }
        if pid > 0 {
            libc::waitpid(pid, ptr::null_mut(), 0);
            return;
        }
        libc::setsid();
        if libc::fork() != 0 {
            libc::_exit(0);
        }
        let null = libc::open(devnull.as_ptr(), libc::O_RDWR);
        for fd in 0..3 {
            libc::dup2(null, fd);
        }
        for fd in 3..1024 {
            libc::close(fd);
        }
    }
    let mut queue = tools.iter();
    let mut running = 0;
    loop {
        while running < jobs {
            let tool = match queue.next() {
                Some(t) => t,
                None => break,
            };
            match unsafe { libc::fork() } {
                0 => {
                    let _ = spawn_daemon_with(tool, origin, &tty);
                    wait_ready(tool);
                    unsafe { libc::_exit(0) }
                }
                pid if pid > 0 => running += 1,
                _ => {}
            }
        }
        if running == 0 {
            break;
        }
        unsafe { libc::wait(ptr::null_mut()) };
        running -= 1;
    }
    unsafe { libc::_exit(0) }
}

/// Until the tool's daemon answers a ping, which its first spare does once
/// the preload is done; or until START_WAIT.
fn wait_ready(tool: &Tool) {
    let deadline = Instant::now() + START_WAIT;
    while Instant::now() < deadline {
        if request(tool, b'P').is_ok() {
            return;
        }
        std::thread::sleep(Duration::from_millis(20));
    }
}

fn spawn_daemon_with(tool: &Tool, origin: &str, tty: &str) -> io::Result<()> {
    ensure_runtime_dir(&tool.dir)?;
    let (interp, flag, runtime) = match &tool.form {
        Some(f) => (f.interp.clone(), None, Runtime::Php),
        None => read_shebang(&tool.path)?,
    };
    let daemon = locate_daemon(runtime)?;
    let lock = lock_path(tool);
    let awaited = match lock_state(&lock)? {
        LockState::Free => None,
        LockState::Exiting(holder) => Some(holder),
        LockState::Held => return Ok(()),
    };
    let mut argv = vec![cstring(&interp)];
    if let Some(f) = flag {
        argv.push(cstring(&f));
    }
    for a in runtime.daemon_args() {
        argv.push(cstring(a.as_bytes()));
    }
    argv.push(cstring(&daemon));
    // For `php script`, the daemon is the script's: name it after it.
    let tool_name = if tool.form.is_some() { basename(&tool.path) } else { tool.name.as_bytes() };
    for (k, v) in [("--tool", tool_name), ("--script", &tool.path[..]),
                   ("--socket", tool.sock.as_os_str().as_bytes()), ("--lock-fd", b"3"),
                   ("--origin", format!("{} pid={}", origin, process::id()).as_bytes()),
                   ("--tty", tty.as_bytes())] {
        argv.push(cstring(k.as_bytes()));
        argv.push(cstring(v));
    }
    let mut ptrs: Vec<*const libc::c_char> = argv.iter().map(|c| c.as_ptr()).collect();
    ptrs.push(ptr::null());
    let log = cstring(tool.sock.with_extension("log").as_os_str().as_bytes());
    let devnull = cstring(b"/dev/null");
    let polls = (LOCK_WAIT.as_millis() / 10) as u32;
    unsafe {
        // Double fork: the daemon is reparented to init, not left to the tool
        // we are about to exec.
        let pid = libc::fork();
        if pid < 0 {
            return Err(io::Error::last_os_error());
        }
        if pid == 0 {
            libc::setsid();
            if libc::fork() != 0 {
                libc::_exit(0);
            }
            // Let go of the caller's fds first: a pipeline it is part of must
            // not wait for us while we wait for the lock.
            let null = libc::open(devnull.as_ptr(), libc::O_RDWR);
            let out = libc::open(log.as_ptr(), libc::O_WRONLY | libc::O_CREAT | libc::O_APPEND, 0o600);
            let out = if out >= 0 { out } else { null };
            libc::dup2(null, 0);
            libc::dup2(out, 1);
            libc::dup2(out, 2);
            for fd in 3..1024 {
                libc::close(fd);
            }
            let fd = libc::open(lock.as_ptr(), libc::O_RDWR | libc::O_CREAT | libc::O_NOFOLLOW, 0o600);
            if fd < 0 {
                libc::_exit(1);
            }
            let mut tries = 0;
            while libc::flock(fd, libc::LOCK_EX | libc::LOCK_NB) != 0 {
                let mut buf = [0u8; 64];
                let n = libc::pread(fd, buf.as_mut_ptr() as *mut libc::c_void, buf.len(), 0);
                let still = n > 0 && awaited.as_deref() == Some(&buf[..n as usize]);
                tries += 1;
                if !still || tries > polls {
                    libc::_exit(0); // another daemon got there first
                }
                libc::usleep(10_000);
            }
            let mark = format!("running {}\n", libc::getpid());
            libc::ftruncate(fd, 0);
            libc::pwrite(fd, mark.as_ptr() as *const libc::c_void, mark.len(), 0);
            if fd != 3 {
                libc::dup2(fd, 3); // without FD_CLOEXEC: the daemon keeps the lock
                libc::close(fd);
            }
            libc::signal(libc::SIGPIPE, libc::SIG_DFL);
            libc::execv(argv[0].as_ptr(), ptrs.as_ptr());
            libc::_exit(127);
        }
        let mut status = 0;
        libc::waitpid(pid, &mut status, 0);
    }
    Ok(())
}

enum LockState {
    Free,
    Held,
    Exiting(Vec<u8>), // the holder's own words, to wait for it and no other
}

fn lock_state(lock: &CString) -> io::Result<LockState> {
    unsafe {
        let fd = libc::open(lock.as_ptr(), libc::O_RDWR | libc::O_CREAT | libc::O_CLOEXEC | libc::O_NOFOLLOW, 0o600);
        if fd < 0 {
            return Err(io::Error::last_os_error());
        }
        let state = if libc::flock(fd, libc::LOCK_EX | libc::LOCK_NB) == 0 {
            LockState::Free // released with the fd: the grandchild takes it again
        } else {
            let mut buf = [0u8; 64];
            let n = libc::pread(fd, buf.as_mut_ptr() as *mut libc::c_void, buf.len(), 0);
            if n > 0 && buf.starts_with(b"exiting ") {
                LockState::Exiting(buf[..n as usize].to_vec())
            } else {
                LockState::Held
            }
        };
        libc::close(fd);
        Ok(state)
    }
}

// --- scripts: activate and exec --------------------------------------------

/// Counts a call; true from the second one on.
fn called_again(counter: &Path) -> bool {
    std::fs::OpenOptions::new().create(true).append(true).open(counter)
        .and_then(|mut f| {
            f.write_all(b".")?;
            Ok(f.metadata()?.len() >= 2)
        })
        .unwrap_or(true)
}

/// The directory holding the link through which we were called, if
/// `activate` made it by itself, and the tool is not enrolled.
fn lazy_counter(name: &[u8]) -> Option<PathBuf> {
    let me = env::current_exe().and_then(|p| p.canonicalize()).ok()?;
    let path = env::var_os("PATH")?;
    let dir = env::split_paths(&path)
        .find(|d| std::fs::canonicalize(d.join(OsStr::from_bytes(name))).ok().as_ref() == Some(&me))?;
    if !dir.join(".auto").exists() || enrolled().iter().any(|t| t.as_bytes() == name) {
        return None;
    }
    let calls = dir.join(".calls");
    let _ = std::fs::create_dir(&calls);
    Some(calls.join(OsStr::from_bytes(name)))
}

/// Python and Ruby tools, as PATH lookup finds them: the first entry of a
/// name wins, and our own link directories are skipped.
fn path_tools(path: &OsStr, ours: &Path) -> Vec<OsString> {
    let mut seen: Vec<OsString> = Vec::new();
    let mut tools = Vec::new();
    for dir in env::split_paths(path) {
        if dir.starts_with(ours) {
            continue;
        }
        let entries = match std::fs::read_dir(if dir.as_os_str().is_empty() { Path::new(".") } else { &dir }) {
            Ok(e) => e,
            Err(_) => continue,
        };
        let mut names: Vec<OsString> = entries.filter_map(|e| e.ok().map(|e| e.file_name())).collect();
        names.sort();
        for name in names {
            if seen.contains(&name) {
                continue;
            }
            let full = dir.join(&name);
            if !is_executable(full.as_os_str().as_bytes()) {
                continue;
            }
            seen.push(name.clone());
            let text = name.to_string_lossy();
            let plain = text.chars().all(|c| c.is_ascii_alphanumeric() || "._-".contains(c))
                && !text.starts_with('-') && text != "speedrunner" && text != "srr";
            // The PHP interpreter too (php, php8.2), for `php script` calls.
            let php = text.strip_prefix("php").map_or(false, |v| v.chars().all(|c| c.is_ascii_digit() || c == '.'));
            if plain && (read_shebang(full.as_os_str().as_bytes()).is_ok() || php) {
                tools.push(name);
            }
        }
    }
    tools
}

const SHELLS: [&[u8]; 6] = [b"bash", b"sh", b"dash", b"zsh", b"ksh", b"mksh"];

/// The text and directory of the script a shell command line runs: `sh -c
/// TEXT`, or `sh [options] FILE` with FILE relative to `cwd`.
fn script_of(argv: &[Vec<u8>], cwd: &Path) -> Option<(String, PathBuf)> {
    let shell = argv.first()?;
    if !SHELLS.contains(&basename(shell)) {
        return None;
    }
    let mut it = argv[1..].iter();
    while let Some(arg) = it.next() {
        if arg == b"-c" {
            return Some((String::from_utf8_lossy(it.next()?).into_owned(), cwd.to_path_buf()));
        }
        if arg.starts_with(b"-") || arg.starts_with(b"+") {
            if arg == b"-o" || arg == b"+o" {
                it.next();
            }
            continue;
        }
        let path = cwd.join(OsStr::from_bytes(arg));
        let text = std::fs::read_to_string(&path).ok()?;
        return Some((text, path.parent().unwrap_or(cwd).to_path_buf()));
    }
    None
}

/// The script whose `eval "$(speedrunner activate)"` runs us: our parent is
/// that shell (or a subshell of it, with the same command line). bash also
/// keeps the script open on fd 255.
fn calling_script() -> Option<(String, PathBuf)> {
    let proc_dir = PathBuf::from(format!("/proc/{}", unsafe { libc::getppid() }));
    let cmdline = std::fs::read(proc_dir.join("cmdline")).ok()?;
    let argv: Vec<Vec<u8>> = cmdline.split(|b| *b == 0).filter(|a| !a.is_empty()).map(<[u8]>::to_vec).collect();
    let cwd = std::fs::read_link(proc_dir.join("cwd")).ok()?;
    script_of(&argv, &cwd).or_else(|| {
        let text = std::fs::read_to_string(proc_dir.join("fd/255")).ok()?;
        Some((text, cwd))
    })
}

/// The script `speedrunner exec COMMAND ARGS` runs: COMMAND is a shell, or
/// a file whose shebang names one.
fn exec_script(command: &OsStr, args: &[OsString]) -> Option<(String, PathBuf)> {
    let cwd = env::current_dir().ok()?;
    let mut argv = vec![command.as_bytes().to_vec()];
    argv.extend(args.iter().map(|a| a.as_bytes().to_vec()));
    if let Some(found) = script_of(&argv, &cwd) {
        return Some(found);
    }
    let path = resolve_in_path(command.as_bytes())?;
    let text = std::fs::read_to_string(OsStr::from_bytes(&path)).ok()?;
    let first = text.lines().next()?.strip_prefix("#!")?;
    if !first.split_whitespace().any(|w| SHELLS.contains(&basename(w.as_bytes()))) {
        return None;
    }
    let dir = Path::new(OsStr::from_bytes(&path)).parent()?.to_path_buf();
    Some((text, dir))
}

/// `activate` or `exec` with no tool named: links for every Python and
/// Ruby tool on PATH. Daemons start at once for enrolled tools and for the
/// tools `script` calls more than once (script::command_counts), or with
/// `eager` for every tool it calls at all; otherwise at a tool's second
/// call (see lazy_counter). Rescanned when a PATH directory changes.
/// Returns the directory.
fn prepare_auto_links(origin: &str, script: Option<(String, PathBuf)>, eager_all: bool)
                      -> io::Result<PathBuf> {
    let runtime = runtime_dir();
    ensure_runtime_dir(&runtime)?;
    let path = env::var_os("PATH").unwrap_or_default();
    let dirs: Vec<PathBuf> = env::split_paths(&path).filter(|d| !d.starts_with(&runtime)).collect();
    let mut stamp = Vec::new();
    for d in &dirs {
        stamp.extend_from_slice(d.as_os_str().as_bytes());
        if let Ok(m) = std::fs::metadata(d) {
            stamp.extend_from_slice(format!(" {}.{}", m.mtime(), m.mtime_nsec()).as_bytes());
        }
        stamp.push(b'\n');
    }
    // Named after the PATH; the stamp (with the directories' mtimes) says
    // when to look again.
    let key = env::join_paths(&dirs).unwrap_or_default();
    let dir = runtime.join(format!("bin-auto-{:016x}", fnv1a(key.as_bytes())));
    let marker = dir.join(".auto");
    if std::fs::read(&marker).ok().as_deref() != Some(&stamp[..]) {
        let _ = std::fs::create_dir(&dir);
        if let Ok(entries) = std::fs::read_dir(&dir) {
            for e in entries.flatten() {
                if !e.file_name().as_bytes().starts_with(b".") {
                    let _ = std::fs::remove_file(e.path());
                }
            }
        }
        let me = env::current_exe()?.canonicalize()?;
        let joined = env::join_paths(&dirs).unwrap_or_default();
        for name in path_tools(&joined, &runtime) {
            std::os::unix::fs::symlink(&me, dir.join(&name))?;
        }
        std::fs::write(&marker, &stamp)?;
    }
    if env::var_os("SPEEDRUNNER_NO_AUTOSTART").is_none() {
        // The script's tools first, in the order it first runs them.
        let mut eager: Vec<OsString> = Vec::new();
        if let Some((text, script_dir)) = script {
            let wanted = if eager_all { 1 } else { 2 };
            let counts = script::command_counts(&text, &script_dir);
            for (name, _) in counts.into_iter().filter(|(_, n)| *n >= wanted) {
                let name = OsString::from(name);
                if dir.join(&name).exists() && !eager.contains(&name) {
                    eager.push(name);
                }
            }
        }
        for name in enrolled().into_iter().map(OsString::from) {
            if !eager.contains(&name) {
                eager.push(name);
            }
        }
        let tools: Vec<Tool> = eager.iter().filter_map(|n| Tool::resolve(n)).collect();
        start_daemons(&tools, origin);
    }
    Ok(dir)
}

/// Links named after each tool, all pointing to this binary, in a private
/// directory of the runtime dir; and their daemons started meanwhile. The
/// tools: those enrolled, plus `extra`. Returns the directory.
fn prepare_links(extra: &[OsString], origin: &str, script: Option<(String, PathBuf)>,
                 eager_all: bool) -> io::Result<PathBuf> {
    if extra.is_empty() {
        return prepare_auto_links(origin, script, eager_all);
    }
    let mut names: Vec<OsString> = enrolled().into_iter().map(OsString::from).collect();
    for e in extra {
        if !names.contains(e) {
            names.push(e.clone());
        }
    }
    let mut good: Vec<OsString> = Vec::new();
    let mut to_start: Vec<Tool> = Vec::new();
    for name in &names {
        let text = name.to_string_lossy();
        let valid = !text.is_empty() && !text.starts_with('-')
            && text.chars().all(|c| c.is_ascii_alphanumeric() || "._-".contains(c))
            && text != "speedrunner" && text != "srr";
        if !valid {
            eprintln!("speedrunner: {}: not a plain command name", text);
            continue;
        }
        let tool = match Tool::resolve(name) {
            Some(t) => t,
            None => {
                eprintln!("speedrunner: {}: command not found", text);
                continue;
            }
        };
        if let Err(e) = read_shebang(&tool.path) {
            eprintln!("speedrunner: {}: cannot be preloaded ({}); left native", text, e);
            continue;
        }
        good.push(name.clone());
        to_start.push(tool);
    }
    if env::var_os("SPEEDRUNNER_NO_AUTOSTART").is_none() {
        start_daemons(&to_start, origin); // in the background: the script goes on
    }
    let runtime = runtime_dir();
    ensure_runtime_dir(&runtime)?;
    let mut key: Vec<u8> = Vec::new();
    let mut sorted = good.clone();
    sorted.sort();
    for n in &sorted {
        key.extend_from_slice(n.as_bytes());
        key.push(0);
    }
    // One directory per set of tools: a script gets the links it asked for, no more.
    let dir = runtime.join(format!("bin-{:016x}", fnv1a(&key)));
    if let Err(e) = std::fs::create_dir(&dir) {
        if e.kind() != io::ErrorKind::AlreadyExists {
            return Err(e);
        }
    }
    let me = env::current_exe()?.canonicalize()?;
    for name in &good {
        let link = dir.join(name);
        if std::fs::read_link(&link).ok().as_deref() != Some(me.as_path()) {
            let _ = std::fs::remove_file(&link);
            std::os::unix::fs::symlink(&me, &link)?;
        }
    }
    Ok(dir)
}

fn sh_quote(b: &[u8]) -> String {
    format!("'{}'", String::from_utf8_lossy(b).replace('\'', "'\\''"))
}

/// Prints the shell code that puts the links first on PATH, once:
/// `eval "$(speedrunner activate aws)"` at the top of a script.
fn cmd_activate(rest: &[OsString]) -> i32 {
    let eager_all = rest.iter().any(|a| a == "--eager");
    let rest: Vec<OsString> = rest.iter().filter(|a| *a != "--eager").cloned().collect();
    let script = if rest.is_empty() { calling_script() } else { None };
    match prepare_links(&rest, "activate", script, eager_all) {
        Ok(dir) => {
            let d = sh_quote(dir.as_os_str().as_bytes());
            println!("case \":$PATH:\" in *:{d}:*) ;; *) PATH={d}:\"$PATH\"; export PATH ;; esac", d = d);
            0
        }
        Err(e) => {
            eprintln!("speedrunner: cannot activate: {}", e);
            1
        }
    }
}

/// `speedrunner exec [-t tool]... [--] command args`: run a command, a
/// script for instance, with the links first on PATH. Also as a shebang:
/// `#!/usr/bin/env -S speedrunner exec -t aws bash`.
fn cmd_exec(rest: &[OsString]) -> i32 {
    let mut tools: Vec<OsString> = Vec::new();
    let mut eager_all = false;
    let mut i = 0;
    while i < rest.len() {
        match rest[i].as_bytes() {
            b"--eager" => {
                eager_all = true;
                i += 1;
            }
            b"-t" if i + 1 < rest.len() => {
                tools.push(rest[i + 1].clone());
                i += 2;
            }
            b"--" => {
                i += 1;
                break;
            }
            _ => break,
        }
    }
    let command = match rest.get(i) {
        Some(c) => c,
        None => {
            usage();
            return 2;
        }
    };
    let script = if tools.is_empty() { exec_script(command, &rest[i + 1..]) } else { None };
    match prepare_links(&tools, "exec", script, eager_all) {
        Ok(dir) => {
            let path = env::var_os("PATH").unwrap_or_default();
            let on_path = env::split_paths(&path).any(|p| p == dir);
            if !on_path {
                let mut joined = dir.into_os_string();
                joined.push(":");
                joined.push(&path);
                env::set_var("PATH", joined);
            }
        }
        Err(e) => eprintln!("speedrunner: cannot activate, running as is: {}", e),
    }
    unsafe {
        // Rust ignores SIGPIPE at startup; the command must inherit the default.
        libc::signal(libc::SIGPIPE, libc::SIG_DFL);
    }
    let err = std::os::unix::process::CommandExt::exec(
        process::Command::new(command).args(&rest[i + 1..]));
    eprintln!("speedrunner: {}: {}", command.to_string_lossy(), err);
    if err.kind() == io::ErrorKind::NotFound { 127 } else { 126 }
}

// --- enroll ----------------------------------------------------------------

fn tools_file() -> PathBuf {
    let base = match env::var_os("XDG_CONFIG_HOME") {
        Some(d) if !d.is_empty() => PathBuf::from(d),
        _ => Path::new(&env::var_os("HOME").unwrap_or_default()).join(".config"),
    };
    base.join("speedrunner").join("tools")
}

fn enrolled() -> Vec<String> {
    std::fs::read_to_string(tools_file())
        .unwrap_or_default()
        .lines()
        .map(str::trim)
        .filter(|l| !l.is_empty())
        .map(String::from)
        .collect()
}

/// Add tools to the list shell/speedrunner.zsh wraps and warms up. Only
/// Python scripts are accepted: a native binary has nothing to gain.
fn cmd_enroll(rest: &[OsString]) -> i32 {
    let mut tools = enrolled();
    if rest.is_empty() {
        for t in &tools {
            println!("{}", t);
        }
        return 0;
    }
    for arg in rest {
        let name = arg.to_string_lossy();
        let valid = !name.is_empty() && !name.starts_with('-')
            && name.chars().all(|c| c.is_ascii_alphanumeric() || "._-".contains(c));
        if !valid {
            eprintln!("speedrunner: {}: not a plain command name", name);
            return 2;
        }
        let tool = match Tool::resolve(arg) {
            Some(t) => t,
            None => {
                eprintln!("speedrunner: {}: command not found", name);
                return 127;
            }
        };
        if let Err(e) = read_shebang(&tool.path) {
            eprintln!("speedrunner: {}: cannot be preloaded ({}); left native", name, e);
            return 1;
        }
        if !tools.iter().any(|t| *t == name) {
            tools.push(name.into_owned());
        }
    }
    let path = tools_file();
    let written = path.parent().map_or(Ok(()), std::fs::create_dir_all)
        .and_then(|_| std::fs::write(&path, tools.join("\n") + "\n"));
    if let Err(e) = written {
        eprintln!("speedrunner: {}: {}", path.display(), e);
        return 1;
    }
    for arg in rest {
        println!("speedrunner: enrolled {}", arg.to_string_lossy());
    }
    0
}

fn unsupported(why: &str) -> io::Error {
    io::Error::new(io::ErrorKind::Unsupported, why.to_string())
}

/// A runtime speedrunner has a daemon for: the daemon is a script run by the
/// tool's own interpreter, and they all speak the same protocol.
#[derive(Clone, Copy)]
enum Runtime {
    Python,
    Ruby,
    Php,
}

impl Runtime {
    fn of(interp: &[u8]) -> Option<Runtime> {
        let name = basename(interp);
        if name.starts_with(b"python") {
            Some(Runtime::Python)
        } else if name.starts_with(b"ruby") {
            Some(Runtime::Ruby)
        } else if name.starts_with(b"php") {
            Some(Runtime::Php)
        } else {
            None
        }
    }

    fn daemon_file(self) -> &'static str {
        match self {
            Runtime::Python => "speedrunner_daemon.py",
            Runtime::Ruby => "speedrunner_daemon.rb",
            Runtime::Php => "speedrunner_daemon.php",
        }
    }

    /// Interpreter options for the daemon. PHP: OPcache on, its shared
    /// memory inherited by the children, revalidated on every include.
    fn daemon_args(self) -> &'static [&'static str] {
        match self {
            // register_argc_argv=0: getopt() falls back to the $argv the
            // child sets, instead of the daemon's own arguments.
            Runtime::Php => &["-d", "opcache.enable_cli=1", "-d", "opcache.validate_timestamps=1",
                              "-d", "opcache.revalidate_freq=0", "-d", "register_argc_argv=0"],
            _ => &[],
        }
    }
}

/// Interpreter and optional flag from the script's shebang, as the kernel
/// would run it, and the runtime that interpreter belongs to.
fn read_shebang(script: &[u8]) -> io::Result<(Vec<u8>, Option<Vec<u8>>, Runtime)> {
    let mut buf = [0u8; 256];
    let n = File::open(OsStr::from_bytes(script))?.read(&mut buf)?;
    let head = &buf[..n];
    if !head.starts_with(b"#!") {
        return Err(unsupported("no shebang"));
    }
    let end = head.iter().position(|b| *b == b'\n').ok_or_else(|| unsupported("shebang too long"))?;
    let line = trim(&head[2..end]);
    let split = line.iter().position(|b| *b == b' ' || *b == b'\t').unwrap_or(line.len());
    let mut interp = line[..split].to_vec();
    let rest = trim(&line[split..]);
    let mut flag = if rest.is_empty() { None } else { Some(rest.to_vec()) };
    if basename(&interp) == b"env" {
        let prog = flag.take().ok_or_else(|| unsupported("bare env shebang"))?;
        if prog.starts_with(b"-") || prog.contains(&b' ') {
            return Err(unsupported("env options in shebang"));
        }
        interp = resolve_in_path(&prog).ok_or_else(|| unsupported("interpreter not in PATH"))?;
    }
    let runtime = Runtime::of(&interp).ok_or_else(|| unsupported("not a Python, Ruby or PHP script"))?;
    Ok((interp, flag, runtime))
}

/// The runtime's daemon script: next to SPEEDRUNNER_DAEMON (a daemon of any
/// runtime, for tests), else next to this binary, in ../share/speedrunner, or
/// in the source tree's server/ when run from client/target/release.
fn locate_daemon(runtime: Runtime) -> io::Result<Vec<u8>> {
    let file = runtime.daemon_file();
    let dirs: Vec<PathBuf> = match env::var_os("SPEEDRUNNER_DAEMON") {
        Some(p) => vec![Path::new(&p).parent().map(Path::to_path_buf).unwrap_or_default()],
        None => {
            let exe = env::current_exe()?;
            let dir = exe.parent().ok_or_else(|| unsupported("no exe dir"))?;
            ["", "../share/speedrunner", "../../../server"].iter().map(|r| dir.join(r)).collect()
        }
    };
    let pinned = env::var_os("SPEEDRUNNER_DAEMON").is_some();
    for dir in dirs {
        let cand = dir.join(file);
        if cand.is_file() {
            return Ok(cand.canonicalize()?.into_os_string().into_vec());
        }
    }
    if pinned {
        return Err(io::Error::new(io::ErrorKind::NotFound, format!("{} not found", file)));
    }
    Ok(extract_daemon(file)?.into_os_string().into_vec())
}

/// The built-in copy of a daemon file, written out once per build to
/// $XDG_DATA_HOME/speedrunner/daemons/<hash of the three>/, so that an
/// upgrade never runs another build's daemon. Written under a temporary
/// name, then renamed; a copy already there is used only if its bytes match.
fn extract_daemon(file: &str) -> io::Result<PathBuf> {
    let bytes = EMBEDDED.iter().find(|(n, _)| *n == file).map(|(_, b)| *b)
        .ok_or_else(|| unsupported("no built-in daemon"))?;
    let mut key = Vec::new();
    for (name, content) in EMBEDDED {
        key.extend_from_slice(name.as_bytes());
        key.extend_from_slice(content);
    }
    let base = match env::var_os("XDG_DATA_HOME") {
        Some(d) if !d.is_empty() => PathBuf::from(d),
        _ => PathBuf::from(env::var_os("HOME").ok_or_else(|| unsupported("no HOME"))?).join(".local/share"),
    };
    let dir = base.join("speedrunner/daemons").join(format!("{:016x}", fnv1a(&key)));
    let path = dir.join(file);
    if std::fs::read(&path).ok().as_deref() == Some(bytes) {
        return Ok(path);
    }
    std::fs::create_dir_all(&dir)?;
    let tmp = dir.join(format!(".{}.{}", file, process::id()));
    std::fs::write(&tmp, bytes)?;
    std::fs::rename(&tmp, &path)?;
    Ok(path)
}

// --- small helpers ---------------------------------------------------------

fn resolve_in_path(name: &[u8]) -> Option<Vec<u8>> {
    if name.is_empty() {
        return None;
    }
    if name.contains(&b'/') {
        return if is_executable(name) { Some(name.to_vec()) } else { None };
    }
    let path = env::var_os("PATH")
        .map(|p| p.into_vec())
        .unwrap_or_else(|| b"/usr/local/bin:/usr/bin:/bin".to_vec());
    let me = env::current_exe().and_then(|p| p.canonicalize()).ok();
    for dir in path.split(|b| *b == b':') {
        // As execvp: an empty entry is the current directory.
        let mut cand = if dir.is_empty() { b".".to_vec() } else { dir.to_vec() };
        cand.push(b'/');
        cand.extend_from_slice(name);
        if is_executable(&cand) {
            // A link to ourselves, put there by `activate`: the tool is further on.
            let is_me = me.is_some()
                && std::fs::canonicalize(OsStr::from_bytes(&cand)).ok() == me;
            if !is_me {
                return Some(cand);
            }
        }
    }
    None
}

fn is_executable(path: &[u8]) -> bool {
    let c = match CString::new(path) {
        Ok(c) => c,
        Err(_) => return false,
    };
    unsafe {
        let mut st: libc::stat = mem::zeroed();
        libc::stat(c.as_ptr(), &mut st) == 0
            && st.st_mode & libc::S_IFMT == libc::S_IFREG
            && libc::access(c.as_ptr(), libc::X_OK) == 0
    }
}

fn cstring(b: &[u8]) -> CString {
    // OS strings (argv, env, paths) cannot contain NUL.
    CString::new(b).expect("NUL in OS string")
}

fn trim(b: &[u8]) -> &[u8] {
    let start = b.iter().position(|c| !c.is_ascii_whitespace()).unwrap_or(b.len());
    let end = b.iter().rposition(|c| !c.is_ascii_whitespace()).map_or(start, |e| e + 1);
    &b[start..end]
}

fn basename(path: &[u8]) -> &[u8] {
    path.rsplit(|b| *b == b'/').next().unwrap_or(path)
}
