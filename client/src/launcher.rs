//! Launchers: scripts that set up an environment, then exec a Python or Ruby
//! interpreter on the real tool. gcloud is a shell script that ends in
//! `exec "$CLOUDSDK_PYTHON" ... lib/gcloud.py "$@"`; az, from pip, a Python
//! script that ends in `os.execl(sys.executable, ..., "-m", "azure.cli", ...)`;
//! brew, a bash script that runs `env -i ... bash -p brew.sh`, which ends in
//! `exec "$HOMEBREW_RUBY_PATH" ... brew.rb "$@"`.
//!
//! The launcher runs natively, under ptrace. When it execs the interpreter,
//! the kernel stops it before the interpreter runs a single instruction: its
//! command line and environment are read, then it is killed. What it would
//! have run is served by a daemon, or execed as is. The tool itself has not
//! started, so following a launcher never runs anything twice.
//!
//! Linux: PTRACE_O_TRACEEXEC, and /proc for what the stopped process
//! execs. macOS: PT_TRACE_ME stops the process with SIGTRAP at each exec,
//! and sysctl and libproc tell what it execs (platform.rs). There the
//! kernel hides the env of Apple's own binaries, and no process can read
//! another's umask: a stop whose env is hidden is followed to the next exec,
//! and the launcher, then every script a shell runs on the way, is read for
//! `umask` commands: one that may change ours makes the launcher run
//! natively. Elsewhere `trace` fails, and the launcher runs natively.
#![cfg_attr(not(any(target_os = "linux", target_os = "macos")), allow(dead_code, unused_imports))]

use std::ffi::{CString, OsStr};
use std::fs::{self, File};
use std::io::{self, Read, Seek, SeekFrom};
#[cfg(target_os = "linux")]
use std::mem;
use std::os::unix::ffi::OsStrExt;
#[cfg(target_os = "linux")]
use std::os::unix::fs::MetadataExt;
#[cfg(target_os = "linux")]
use std::path::Path;
use std::ptr;

// The shebang is in the head, the last command in the tail.
const HEAD: usize = 256;
const TAIL: u64 = 4096;
// Programs a launcher may exec on its way to the interpreter.
const SHELLS: [&[u8]; 6] = [b"sh", b"bash", b"dash", b"ksh", b"zsh", b"env"];

/// What the launcher execs: the program as it named it, argv and env, and
/// the umask and ignored signals (1 << signal) it leaves it.
pub struct Launch {
    pub exe: Vec<u8>,
    pub argv: Vec<Vec<u8>>,
    pub env: Vec<Vec<u8>>,
    pub umask: u32,
    pub ignored: u64,
}

pub enum Traced {
    /// Stopped as it execed a Python or Ruby interpreter, and killed.
    Interpreter(Launch),
    /// Exited while traced: its wait status.
    Done(i32),
    /// Left to run natively, untraced: its pid, ours to wait for.
    Native(i32),
}

/// Let the launcher run natively, untraced, and say why when asked to.
#[cfg(any(target_os = "linux", target_os = "macos"))]
fn let_go(pid: i32, name: &[u8], why: &str) -> Traced {
    if std::env::var_os("SPEEDRUNNER_DEBUG").is_some() {
        eprintln!("speedrunner: the launcher runs natively: at {}, {}", String::from_utf8_lossy(name), why);
    }
    detach(pid)
}

fn is_shell(name: &[u8]) -> bool {
    SHELLS.contains(&name)
}

/// Programs that are many: BusyBox's `env` and `sh` (Alpine), coreutils'
/// single binary (Fedora's containers). They are what argv[0] names.
#[cfg(target_os = "linux")]
const MULTICALL: [&[u8]; 3] = [b"busybox", b"coreutils", b"toybox"];

/// What a launcher may stop at: a Python or Ruby interpreter, by its file's
/// name (python3.12, ruby, Debian's ruby3.1). Fedora's /usr/bin/ruby is a
/// script, rubypick, that execs ruby-mri: that one is the interpreter.
fn is_interpreter(name: &[u8]) -> bool {
    name.starts_with(b"python")
        || name.strip_prefix(b"ruby").map_or(false, |v| {
            let v = v.strip_suffix(b"-mri").unwrap_or(v);
            v.iter().all(|c| c.is_ascii_digit() || *c == b'.')
        })
}

/// Whether every `umask` command in a shell script leaves `mask` as it is:
/// none at all, one that only prints, or one whose mode gives `mask` itself
/// (brew.sh's `umask go-w`, when go-w is masked already). Any other mode, one
/// in a variable included, may change it. Comments are skipped.
#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
pub fn umask_kept(text: &[u8], mask: u32) -> bool {
    let starts = |b: u8| b.is_ascii_whitespace() || b";&|({`".contains(&b);
    for line in text.split(|b| *b == b'\n') {
        let line = trim(line);
        if line.starts_with(b"#") {
            continue;
        }
        let mut i = 0;
        while let Some(off) = line[i..].windows(5).position(|w| w == b"umask") {
            let at = i + off;
            let after = &line[at + 5..];
            i = at + 5;
            let word_start = at == 0 || starts(line[at - 1]);
            let word_end = after.first().map_or(true, |b| b.is_ascii_whitespace() || b";&|)}".contains(b));
            if !(word_start && word_end) {
                continue;
            }
            let end = after.iter().position(|b| b";&|)}".contains(b)).unwrap_or(after.len());
            let mode = after[..end]
                .split(|b| b.is_ascii_whitespace())
                .find(|w| !w.is_empty() && !w.starts_with(b"-")); // -S, -p only print
            if let Some(mode) = mode {
                if umask_after(mode, mask) != Some(mask) {
                    return false;
                }
            }
        }
    }
    true
}

/// The mask `umask mode` leaves, from `mask`: octal, or symbolic as POSIX
/// has it (`u=rwx,g=rx,o=`, `go-w`). None for anything else.
fn umask_after(mode: &[u8], mask: u32) -> Option<u32> {
    let mode = match mode {
        [q, inner @ .., e] if (*q == b'\'' || *q == b'"') && q == e => inner,
        m => m,
    };
    if !mode.is_empty() && mode.len() <= 4 && mode.iter().all(|c| (b'0'..=b'7').contains(c)) {
        return u32::from_str_radix(std::str::from_utf8(mode).ok()?, 8).ok().map(|m| m & 0o777);
    }
    let mut allowed = !mask & 0o777;
    for clause in mode.split(|b| *b == b',') {
        let op_at = clause.iter().position(|b| b"+-=".contains(b))?;
        let mut who = 0;
        for c in &clause[..op_at] {
            who |= match c {
                b'u' => 0o700,
                b'g' => 0o070,
                b'o' => 0o007,
                b'a' => 0o777,
                _ => return None,
            };
        }
        if who == 0 {
            who = 0o777;
        }
        let mut perms = 0;
        for c in &clause[op_at + 1..] {
            perms |= match c {
                b'r' => 0o444,
                b'w' => 0o222,
                b'x' => 0o111,
                _ => return None,
            };
        }
        perms &= who;
        allowed = match clause[op_at] {
            b'+' => allowed | perms,
            b'-' => allowed & !perms,
            _ => (allowed & !who) | perms,
        };
    }
    Some(!allowed & 0o777)
}

/// Whether the script a shell runs, as `args` exec it (`bash -p file`, or
/// `sh -c 'text'`), keeps `mask`. A shell that reads its commands elsewhere
/// (stdin), or a file that cannot be read: it may not.
#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
fn shell_keeps_umask(args: &[Vec<u8>], mask: u32) -> bool {
    let mut it = args.iter().skip(1);
    while let Some(a) = it.next() {
        match &a[..] {
            b"-c" => return it.next().map_or(false, |text| umask_kept(text, mask)),
            b"-o" | b"+o" => {
                it.next();
            }
            b"--" => break,
            a if a.starts_with(b"-") || a.starts_with(b"+") => {}
            path => return fs::read(OsStr::from_bytes(path)).map_or(false, |text| umask_kept(&text, mask)),
        }
    }
    it.next().map_or(false, |path| fs::read(OsStr::from_bytes(path)).map_or(false, |t| umask_kept(&t, mask)))
}

fn basename(path: &[u8]) -> &[u8] {
    path.rsplit(|b| *b == b'/').next().unwrap_or(path)
}

fn trim(b: &[u8]) -> &[u8] {
    let start = b.iter().position(|c| !c.is_ascii_whitespace()).unwrap_or(b.len());
    let end = b.iter().rposition(|c| !c.is_ascii_whitespace()).map_or(start, |e| e + 1);
    &b[start..end]
}

/// The line pre-commit writes under the shebang of the git hooks it installs.
const PRECOMMIT_MARK: &[u8] = b"\n# File generated by pre-commit: https://pre-commit.com\n";

/// pre-commit's git hook (`pre-commit install`): a bash launcher whose tail
/// is `if [ -x "$INSTALL_PYTHON" ]; then exec "$INSTALL_PYTHON" -mpre_commit
/// ...; elif command -v pre-commit ...; then exec pre-commit ...; else ...
/// exit 1; fi`.
pub fn is_precommit_hook(script: &[u8]) -> bool {
    let marked = match head_and_tail(script) {
        Ok((head, _)) => head.windows(PRECOMMIT_MARK.len()).any(|w| w == PRECOMMIT_MARK),
        Err(_) => false,
    };
    marked && looks_like_launcher(script)
}

/// A script whose last command execs: a shell script ending in `exec ...`
/// (or in an `if` whose every branch ends in `exec ...` or `exit`), or a
/// Python script whose last top-level line is `os.exec...(...)`.
/// Everything before it only prepares that exec.
pub fn looks_like_launcher(script: &[u8]) -> bool {
    let (head, tail) = match head_and_tail(script) {
        Ok(parts) => parts,
        Err(_) => return false,
    };
    if !head.starts_with(b"#!") {
        return false;
    }
    let first = match head.iter().position(|b| *b == b'\n') {
        Some(end) => &head[..end],
        None => return false,
    };
    let words: Vec<&[u8]> = first[2..]
        .split(|b| *b == b' ' || *b == b'\t')
        .filter(|w| !w.is_empty())
        .collect();
    let mut interp = match words.first() {
        Some(w) => basename(w),
        None => return false,
    };
    if interp == b"env" {
        interp = match words.get(1) {
            Some(w) => basename(w),
            None => return false,
        };
    }
    // Lines of the tail after the shebang's; the first may be cut short.
    let body = if tail.0 == 0 { &tail.1[first.len()..] } else { &tail.1[..] };
    let mut lines: Vec<&[u8]> = body.split(|b| *b == b'\n').collect();
    if tail.0 > 0 {
        lines.remove(0);
    }
    let lines: Vec<&[u8]> = lines
        .into_iter()
        .filter(|l| {
            let t = trim(l);
            !t.is_empty() && !t.starts_with(b"#")
        })
        .collect();
    let last = lines.last().copied().unwrap_or(b"");
    if is_shell(interp) && interp != b"env" {
        let commands: Vec<&[u8]> = lines.iter().map(|l| trim(l)).collect();
        (trim(last).starts_with(b"exec ") || ends_in_exec_chain(&commands))
            && !commands.iter().any(|c| execs_privileged(c))
    } else if interp.starts_with(b"python") {
        last.starts_with(b"os.exec")
    } else {
        false
    }
}

/// `exec sudo ...`, named as is: under ptrace the kernel drops a program's
/// setuid and setgid, and it would fail. Not a launcher, then. A program named
/// by a variable is not seen.
fn execs_privileged(command: &[u8]) -> bool {
    let word = match command.strip_prefix(b"exec ") {
        Some(rest) => trim(rest).split(|b| b.is_ascii_whitespace()).next().unwrap_or(b""),
        None => return false,
    };
    if word.iter().any(|b| b"$\"'`".contains(b)) {
        return false;
    }
    crate::resolve_in_path(word).map_or(false, |path| {
        use std::os::unix::fs::MetadataExt;
        fs::metadata(OsStr::from_bytes(&path)).map_or(false, |m| m.mode() & 0o6000 != 0)
    })
}

/// Commands (trimmed lines, comments out) ending in an `if ... fi` block,
/// one per line, whose every branch ends in `exec ...` or `exit`, one of
/// them at least in `exec`. A nested block ends no branch: not recognised.
fn ends_in_exec_chain(commands: &[&[u8]]) -> bool {
    if commands.last() != Some(&&b"fi"[..]) {
        return false;
    }
    let mut ends: Vec<&[u8]> = Vec::new(); // each branch's last command
    let mut last: Option<&[u8]> = None; // of the branch being read, backwards
    let mut depth = 0;
    for c in commands.iter().rev() {
        let header = c.starts_with(b"if ") || c.starts_with(b"elif ") || *c == b"else";
        if *c == b"fi" {
            depth += 1;
            if depth > 1 && last.is_none() {
                last = Some(*c);
            }
        } else if depth == 1 && header {
            match last.take() {
                Some(end) => ends.push(end),
                None => return false, // an empty branch
            }
            if c.starts_with(b"if ") {
                let exits = |e: &&[u8]| *e == b"exit" || e.starts_with(b"exit ");
                return ends.iter().all(|e| e.starts_with(b"exec ") || exits(e))
                    && ends.iter().any(|e| e.starts_with(b"exec "));
            }
        } else if c.starts_with(b"if ") {
            depth -= 1;
        } else if last.is_none() {
            last = Some(*c);
        }
    }
    false
}

/// The file's first HEAD bytes, and its last TAIL bytes with their offset.
fn head_and_tail(path: &[u8]) -> io::Result<(Vec<u8>, (u64, Vec<u8>))> {
    let mut f = File::open(OsStr::from_bytes(path))?;
    let mut head = vec![0u8; HEAD];
    let n = f.read(&mut head)?;
    head.truncate(n);
    let size = f.metadata()?.len();
    let from = size.saturating_sub(TAIL);
    f.seek(SeekFrom::Start(from))?;
    let mut tail = Vec::new();
    f.take(TAIL).read_to_end(&mut tail)?;
    Ok((head, (from, tail)))
}

#[cfg(target_os = "linux")]
fn wait(pid: i32) -> io::Result<i32> {
    let mut status = 0;
    loop {
        if unsafe { libc::waitpid(pid, &mut status, libc::__WALL) } == pid {
            return Ok(status);
        }
        let err = io::Error::last_os_error();
        if err.kind() != io::ErrorKind::Interrupted {
            return Err(err);
        }
    }
}

// The request's type differs between the glibc and musl bindings.
#[cfg(all(target_os = "linux", target_env = "musl"))]
type Request = libc::c_int;
#[cfg(all(target_os = "linux", not(target_env = "musl")))]
type Request = libc::c_uint;

#[cfg(target_os = "linux")]
unsafe fn ptrace(request: Request, pid: i32, data: usize) -> libc::c_long {
    libc::ptrace(request, pid as libc::pid_t, ptr::null_mut::<libc::c_void>(), data as *mut libc::c_void)
}

#[cfg(target_os = "linux")]
fn kill(pid: i32) {
    unsafe {
        libc::kill(pid, libc::SIGKILL);
    }
    let _ = wait(pid);
}

#[cfg(target_os = "macos")]
fn wait(pid: i32) -> io::Result<i32> {
    let mut status = 0;
    loop {
        if unsafe { libc::waitpid(pid, &mut status, 0) } == pid {
            return Ok(status);
        }
        let err = io::Error::last_os_error();
        if err.kind() != io::ErrorKind::Interrupted {
            return Err(err);
        }
    }
}

/// The signal a traced process stopped on. macOS's WIFSTOPPED leaves out
/// SIGCONT, whose stop status is WIFCONTINUED's.
#[cfg(target_os = "macos")]
fn stop_signal(status: i32) -> Option<i32> {
    if status & 0x7f == 0x7f { Some((status >> 8) & 0xff) } else { None }
}

#[cfg(target_os = "macos")]
unsafe fn ptrace(request: libc::c_int, pid: i32, data: i32) -> libc::c_int {
    // waitpid reports a stop before the process has quite stopped: a request
    // made at once now and then leaves it frozen for good, with us waiting.
    // Without this pause, 13 calls in 1400 hung; with it, none.
    std::thread::sleep(std::time::Duration::from_micros(500));
    // addr 1: go on from where it stopped.
    libc::ptrace(request, pid, 1 as *mut libc::c_char, data)
}

/// A stopped traced process ignores SIGKILL until resumed: PT_KILL.
#[cfg(target_os = "macos")]
fn kill(pid: i32) {
    unsafe {
        if ptrace(libc::PT_KILL, pid, 0) != 0 {
            libc::kill(pid, libc::SIGKILL);
        }
    }
    let _ = wait(pid);
}

/// Run the launcher at `path` with `argv`, until it execs an interpreter.
/// `quiet`: a run of our own (to learn what it execs, for prewarm), with
/// /dev/null for fds 0-2, killed rather than left to run natively.
#[cfg(not(any(target_os = "linux", target_os = "macos")))]
pub fn trace(_path: &[u8], _argv: &[Vec<u8>], _quiet: bool) -> io::Result<Traced> {
    Err(io::Error::new(io::ErrorKind::Unsupported, "no ptrace on this platform"))
}

#[cfg(target_os = "linux")]
pub fn trace(path: &[u8], argv: &[Vec<u8>], quiet: bool) -> io::Result<Traced> {
    // Before anything runs: what the launcher execs is read from /proc, and a
    // run that cannot tell falls back to a native one, so its preamble would
    // run twice.
    fs::read_link("/proc/self/exe")?;
    let path_c = CString::new(path)?;
    let argv_c: Vec<CString> = argv.iter().map(|a| CString::new(&a[..])).collect::<Result<_, _>>()?;
    let mut ptrs: Vec<*const libc::c_char> = argv_c.iter().map(|c| c.as_ptr()).collect();
    ptrs.push(ptr::null());
    let pid = unsafe { libc::fork() };
    if pid < 0 {
        return Err(io::Error::last_os_error());
    }
    if pid == 0 {
        unsafe {
            if quiet {
                let null = libc::open(b"/dev/null\0".as_ptr() as *const libc::c_char, libc::O_RDWR);
                for fd in 0..3 {
                    libc::dup2(null, fd);
                }
            }
            if ptrace(libc::PTRACE_TRACEME, 0, 0) != 0 {
                libc::_exit(127); // seen before the stop below: not traceable
            }
            libc::raise(libc::SIGSTOP);
            // Rust ignores SIGPIPE at startup; the launcher must inherit the default.
            libc::signal(libc::SIGPIPE, libc::SIG_DFL);
            libc::execv(path_c.as_ptr(), ptrs.as_ptr());
            libc::_exit(127);
        }
    }
    let status = wait(pid)?;
    let unavailable = || io::Error::new(io::ErrorKind::Unsupported, "ptrace unavailable");
    if !libc::WIFSTOPPED(status) {
        return Err(unavailable());
    }
    let opts = libc::PTRACE_O_TRACEEXEC | libc::PTRACE_O_EXITKILL;
    if unsafe { ptrace(libc::PTRACE_SETOPTIONS, pid, opts as usize) } != 0 {
        kill(pid);
        return Err(unavailable());
    }
    let mut deliver = 0;
    let mut execs = 0;
    loop {
        unsafe {
            ptrace(libc::PTRACE_CONT, pid, deliver as usize);
        }
        deliver = 0;
        let status = wait(pid)?;
        if !libc::WIFSTOPPED(status) {
            return Ok(Traced::Done(status));
        }
        let sig = libc::WSTOPSIG(status);
        if sig != libc::SIGTRAP || status >> 16 != libc::PTRACE_EVENT_EXEC {
            if execs == 0 && sig == libc::SIGSTOP {
                continue; // our own stop, before the exec
            }
            deliver = sig; // a signal for the launcher: let it have it
            continue;
        }
        execs += 1;
        if execs == 1 {
            continue; // the launcher itself starting
        }
        let exe = fs::read_link(format!("/proc/{}/exe", pid))?;
        let file = basename(exe.as_os_str().as_bytes());
        let argv0 = if MULTICALL.contains(&file) {
            fs::read(format!("/proc/{}/cmdline", pid)).ok().and_then(|c| split0(c).into_iter().next())
        } else {
            None
        };
        let name = argv0.as_deref().map_or(file, basename);
        if is_interpreter(name) {
            let launch = read_launch(pid, &exe, !quiet);
            match launch {
                Some(launch) => {
                    kill(pid);
                    return Ok(Traced::Interpreter(launch));
                }
                None if quiet => {
                    kill(pid);
                    return Err(io::Error::new(io::ErrorKind::Other, "unreadable launch"));
                }
                None => return Ok(let_go(pid, name, "what it execs cannot be checked")),
            }
        }
        // A framework build's python3 execs its Python.app: still the same
        // program on its way.
        if !is_shell(name) && name != b"Python" {
            if quiet {
                kill(pid);
                return Err(io::Error::new(io::ErrorKind::Other, "execs no Python or Ruby interpreter"));
            }
            return Ok(let_go(pid, name, "it execs no Python or Ruby interpreter"));
        }
    }
}

#[cfg(target_os = "macos")]
pub fn trace(path: &[u8], argv: &[Vec<u8>], quiet: bool) -> io::Result<Traced> {
    let path_c = CString::new(path)?;
    let argv_c: Vec<CString> = argv.iter().map(|a| CString::new(&a[..])).collect::<Result<_, _>>()?;
    let mut ptrs: Vec<*const libc::c_char> = argv_c.iter().map(|c| c.as_ptr()).collect();
    ptrs.push(ptr::null());
    // No process can read another's umask here: the launcher must keep ours,
    // and so must every script a shell runs on the way (brew.sh, for brew).
    let mask = unsafe {
        let m = libc::umask(0o022);
        libc::umask(m);
        m as u32
    };
    let mut umask_known = umask_kept(&fs::read(OsStr::from_bytes(path))?, mask);
    let pid = unsafe { libc::fork() };
    if pid < 0 {
        return Err(io::Error::last_os_error());
    }
    if pid == 0 {
        unsafe {
            if quiet {
                let null = libc::open(b"/dev/null\0".as_ptr() as *const libc::c_char, libc::O_RDWR);
                for fd in 0..3 {
                    libc::dup2(null, fd);
                }
            }
            if libc::ptrace(libc::PT_TRACE_ME, 0, ptr::null_mut(), 0) != 0 {
                libc::_exit(127); // seen before the stop below: not traceable
            }
            libc::raise(libc::SIGSTOP);
            libc::signal(libc::SIGPIPE, libc::SIG_DFL);
            libc::execv(path_c.as_ptr(), ptrs.as_ptr());
            libc::_exit(127);
        }
    }
    if stop_signal(wait(pid)?).is_none() {
        return Err(io::Error::new(io::ErrorKind::Unsupported, "ptrace unavailable"));
    }
    // A tracer's exit kills what it traces: no PTRACE_O_EXITKILL to ask for.
    let mut deliver = 0;
    let mut execs = 0;
    let mut last: Option<(Vec<u8>, Vec<Vec<u8>>)> = None;
    loop {
        unsafe {
            ptrace(libc::PT_CONTINUE, pid, deliver);
        }
        deliver = 0;
        let status = wait(pid)?;
        let sig = match stop_signal(status) {
            Some(sig) => sig,
            None => return Ok(Traced::Done(status)),
        };
        if sig != libc::SIGTRAP {
            if execs == 0 && sig == libc::SIGSTOP {
                continue; // our own stop, before the exec
            }
            deliver = sig; // a signal for the launcher: let it have it
            continue;
        }
        // A SIGTRAP stop is an exec, unless the process still runs what it
        // ran at the last one: then a SIGTRAP of its own, delivered.
        let (exe, args, env) = match crate::platform::process_exec(pid) {
            Some(exec) => exec,
            None if quiet => {
                kill(pid);
                return Err(io::Error::new(io::ErrorKind::Other, "unreadable exec"));
            }
            None => return Ok(detach(pid)),
        };
        if last.as_ref().map_or(false, |(e, a)| *e == exe && *a == args) {
            deliver = sig;
            continue;
        }
        last = Some((exe.clone(), args.clone()));
        execs += 1;
        if execs == 1 {
            continue; // the launcher itself starting
        }
        let name = basename(&exe);
        if is_shell(name) && name != b"env" && !shell_keeps_umask(&args, mask) {
            umask_known = false;
        }
        if is_interpreter(name) {
            if env.is_empty() {
                continue; // Apple's /usr/bin/python3, which execs the real one
            }
            let launch = if umask_known { read_launch(pid, &exe, args, env, !quiet) } else { None };
            match launch {
                Some(launch) => {
                    kill(pid);
                    return Ok(Traced::Interpreter(launch));
                }
                None if quiet => {
                    kill(pid);
                    return Err(io::Error::new(io::ErrorKind::Other, "unreadable launch"));
                }
                None => return Ok(let_go(pid, name, "what it execs cannot be checked")),
            }
        }
        if !is_shell(name) {
            if quiet {
                kill(pid);
                return Err(io::Error::new(io::ErrorKind::Other, "execs no Python or Ruby interpreter"));
            }
            return Ok(let_go(pid, name, "it execs no Python or Ruby interpreter"));
        }
    }
}

/// Lets it run natively, untraced. PT_DETACH at an exec's stop would hand it
/// that SIGTRAP, which kills it: it goes on, and is detached at the stop of
/// a SIGCONT sent for that, which the detach drops.
#[cfg(target_os = "macos")]
fn detach(pid: i32) -> Traced {
    unsafe {
        ptrace(libc::PT_CONTINUE, pid, 0);
        libc::kill(pid, libc::SIGCONT);
    }
    loop {
        let status = match wait(pid) {
            Ok(status) => status,
            Err(_) => return Traced::Native(pid),
        };
        let sig = match stop_signal(status) {
            Some(sig) => sig,
            None => return Traced::Done(status),
        };
        if sig == libc::SIGCONT {
            unsafe {
                ptrace(libc::PT_DETACH, pid, 0);
            }
            return Traced::Native(pid);
        }
        // Another exec on the way, or a signal for it.
        unsafe {
            ptrace(libc::PT_CONTINUE, pid, if sig == libc::SIGTRAP { 0 } else { sig });
        }
    }
}

/// The stopped process's command line and env (read already), with our
/// umask (theirs cannot be read: the caller refuses a launcher that may set
/// one) and its ignored signals. With `ours`, None unless it has our cwd
/// and fds 0-2, which a request passes from us.
#[cfg(target_os = "macos")]
fn read_launch(pid: i32, exe: &[u8], argv: Vec<Vec<u8>>, env: Vec<Vec<u8>>, ours: bool) -> Option<Launch> {
    use crate::platform::{fd_identity, ignored_signals, process_cwd};
    let exe_real = fs::canonicalize(OsStr::from_bytes(exe)).ok()?;
    let named = argv.first()?.clone();
    let program = if named.contains(&b'/') {
        named
    } else {
        let path = env.iter().find(|e| e.starts_with(b"PATH=")).map(|e| e[5..].to_vec())?;
        path.split(|b| *b == b':').filter(|d| !d.is_empty()).find_map(|d| {
            let mut cand = d.to_vec();
            cand.push(b'/');
            cand.extend_from_slice(&named);
            fs::canonicalize(OsStr::from_bytes(&cand)).ok().filter(|r| *r == exe_real).map(|_| cand)
        })?
    };
    if fs::canonicalize(OsStr::from_bytes(&program)).ok()? != exe_real {
        return None;
    }
    let umask = unsafe {
        let m = libc::umask(0o022);
        libc::umask(m);
        m as u32
    };
    let ignored = ignored_signals(pid)?;
    if ours {
        if process_cwd(pid)? != std::env::current_dir().ok()? {
            return None;
        }
        let me = unsafe { libc::getpid() };
        for fd in 0..3 {
            if fd_identity(pid, fd)? != fd_identity(me, fd)? {
                return None;
            }
        }
    }
    Some(Launch { exe: program, argv, env, umask, ignored })
}

#[cfg(target_os = "linux")]
fn detach(pid: i32) -> Traced {
    unsafe {
        ptrace(libc::PTRACE_DETACH, pid, 0);
    }
    Traced::Native(pid)
}

#[cfg(target_os = "linux")]
fn split0(data: Vec<u8>) -> Vec<Vec<u8>> {
    let mut items: Vec<Vec<u8>> = data.split(|b| *b == 0).map(|s| s.to_vec()).collect();
    if items.last().map_or(false, |l| l.is_empty()) {
        items.pop();
    }
    items
}

/// The stopped process's command line, environment, umask and ignored
/// signals. With `ours`, None unless it has our cwd and fds 0-2, which a
/// request passes from us.
#[cfg(target_os = "linux")]
fn read_launch(pid: i32, exe: &Path, ours: bool) -> Option<Launch> {
    let proc = format!("/proc/{}", pid);
    let argv = split0(fs::read(format!("{}/cmdline", proc)).ok()?);
    let env = split0(fs::read(format!("{}/environ", proc)).ok()?);
    // The program as the launcher named it, searched on its PATH if bare:
    // a venv's python is found by that name, not by its resolved file.
    let named = argv.first()?.clone();
    let program = if named.contains(&b'/') {
        named
    } else {
        let path = env.iter().find(|e| e.starts_with(b"PATH=")).map(|e| e[5..].to_vec())?;
        path.split(|b| *b == b':').filter(|d| !d.is_empty()).find_map(|d| {
            let mut cand = d.to_vec();
            cand.push(b'/');
            cand.extend_from_slice(&named);
            fs::canonicalize(OsStr::from_bytes(&cand)).ok().filter(|r| r == exe).map(|_| cand)
        })?
    };
    if fs::canonicalize(OsStr::from_bytes(&program)).ok()? != exe {
        return None;
    }
    let status = fs::read_to_string(format!("{}/status", proc)).ok()?;
    let field = |key: &str| {
        status.lines().find_map(|l| l.strip_prefix(key)).map(|v| v.trim().to_string())
    };
    let umask = u32::from_str_radix(&field("Umask:")?, 8).ok()?;
    // Bit n-1 for signal n. A Python launcher leaves SIGPIPE and SIGXFSZ
    // ignored, as CPython does anyway.
    let ignored = u64::from_str_radix(&field("SigIgn:")?, 16).ok()? << 1;
    if ours {
        if fs::read_link(format!("{}/cwd", proc)).ok()? != std::env::current_dir().ok()? {
            return None;
        }
        for fd in 0..3 {
            let theirs = fs::metadata(format!("{}/fd/{}", proc, fd)).ok()?;
            let mine = unsafe {
                let mut st: libc::stat = mem::zeroed();
                if libc::fstat(fd, &mut st) != 0 {
                    return None;
                }
                st
            };
            if theirs.dev() != mine.st_dev as u64 || theirs.ino() != mine.st_ino as u64 {
                return None;
            }
        }
    }
    Some(Launch { exe: program, argv, env, umask, ignored })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    fn launcher(text: &str) -> bool {
        // Tests run in parallel: a file of its own for each call.
        static N: std::sync::atomic::AtomicUsize = std::sync::atomic::AtomicUsize::new(0);
        let n = N.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
        let dir = std::env::temp_dir().join(format!("sr-launcher-{}-{}", std::process::id(), n));
        fs::create_dir_all(&dir).unwrap();
        let path = dir.join("tool");
        File::create(&path).unwrap().write_all(text.as_bytes()).unwrap();
        let found = looks_like_launcher(path.as_os_str().as_bytes());
        fs::remove_dir_all(&dir).unwrap();
        found
    }

    #[test]
    fn shell_launchers() {
        assert!(launcher("#!/bin/sh\nX=1\nexport X\nexec \"$PY\" lib/tool.py \"$@\"\n"));
        assert!(launcher("#!/usr/bin/env bash\nexec python3 -m tool \"$@\"   \n# end\n\n"));
        assert!(!launcher("#!/bin/sh\nexec python3 tool.py \"$@\"\necho done\n"));
        assert!(!launcher("#!/bin/sh\npython3 -m tool \"$@\"\n"));
    }

    // pre-commit 4's resources/hook-tmpl, as `pre-commit install` fills it in.
    const HOOK: &str = "#!/usr/bin/env bash\n\
        # File generated by pre-commit: https://pre-commit.com\n\
        # ID: 138fd403232d2ddd5efb44317e38bf03\n\n\
        # start templated\n\
        INSTALL_PYTHON=/usr/local/bin/python3.11\n\
        ARGS=(hook-impl --config=.pre-commit-config.yaml --hook-type=pre-commit)\n\
        # end templated\n\n\
        HERE=\"$(cd \"$(dirname \"$0\")\" && pwd)\"\n\
        ARGS+=(--hook-dir \"$HERE\" -- \"$@\")\n\n\
        if [ -x \"$INSTALL_PYTHON\" ]; then\n\
        \x20   exec \"$INSTALL_PYTHON\" -mpre_commit \"${ARGS[@]}\"\n\
        elif command -v pre-commit > /dev/null; then\n\
        \x20   exec pre-commit \"${ARGS[@]}\"\n\
        else\n\
        \x20   echo '`pre-commit` not found.  Did you forget to activate your virtualenv?' 1>&2\n\
        \x20   exit 1\n\
        fi\n";

    fn hook(text: &str) -> bool {
        static N: std::sync::atomic::AtomicUsize = std::sync::atomic::AtomicUsize::new(0);
        let n = N.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
        let path = std::env::temp_dir().join(format!("sr-hook-{}-{}", std::process::id(), n));
        File::create(&path).unwrap().write_all(text.as_bytes()).unwrap();
        let found = is_precommit_hook(path.as_os_str().as_bytes());
        fs::remove_file(&path).unwrap();
        found
    }

    #[test]
    fn exec_chain_launchers() {
        assert!(launcher(HOOK));
        assert!(launcher("#!/bin/sh\nif [ -n \"$A\" ]; then\n  exec a\nfi\n"));
        assert!(launcher("#!/bin/sh\nif a\nthen\n  exec a\nelse\n  exit\nfi\n"));
        // A branch that goes on, an empty one, or a nested block: not a launcher.
        assert!(!launcher("#!/bin/sh\nif a; then\n  exec a\nelse\n  b\nfi\n"));
        assert!(!launcher("#!/bin/sh\nif a; then\n  exit 1\nelse\n  exit 2\nfi\n"));
        assert!(!launcher("#!/bin/sh\nif a; then\nelse\n  exec b\nfi\n"));
        assert!(!launcher("#!/bin/sh\nif a; then\n  if b; then\n    exec b\n  fi\nelse\n  exec c\nfi\n"));
        assert!(!launcher("#!/bin/sh\n  exec a\nfi\n"));
    }

    #[test]
    fn precommit_hooks() {
        assert!(hook(HOOK));
        assert!(!hook(&HOOK.replace("pre-commit.com", "example.com")));
        assert!(!hook(&HOOK.replace("    exit 1\nfi\n", "    exit 1\nfi\necho done\n")));
        assert!(!hook("#!/usr/bin/env bash\nexec true\n"));
        assert!(!is_precommit_hook(b"/nonexistent/hook"));
    }

    #[test]
    fn interpreters() {
        for name in ["python3", "python3.12", "ruby", "ruby3.1", "ruby3.4.1", "ruby-mri"] {
            assert!(is_interpreter(name.as_bytes()), "{}", name);
        }
        for name in ["rubocop", "ruby-build", "ruby-mri-x", "bash", "env", "node"] {
            assert!(!is_interpreter(name.as_bytes()), "{}", name);
        }
    }

    #[test]
    fn umask_commands() {
        let kept = |text: &str, mask| umask_kept(text.as_bytes(), mask);
        // brew.sh's, which only adds go-w
        let brew = "if [[ -n \"${HOMEBREW_MACOS}\" ]]\nthen\n  umask go-w\nfi\n";
        assert!(kept(brew, 0o022));
        assert!(kept(brew, 0o077));
        assert!(!kept(brew, 0o002));
        assert!(kept("umask 022", 0o022) && kept("umask 0022", 0o022) && kept("umask '022'", 0o022));
        assert!(!kept("umask 077", 0o022));
        assert!(kept("umask u=rwx,g=rx,o=rx", 0o022));
        assert!(!kept("umask u=rwx,g=,o=", 0o022));
        assert!(kept("umask a+r", 0o022) && !kept("umask a+w", 0o022));
        assert!(kept("umask", 0o002) && kept("umask -S", 0o002) && kept("echo $(umask)", 0o002));
        assert!(!kept("umask \"$mode\"", 0o022) && !kept("umask -S u=rwx,go=", 0o022));
        assert!(!kept("cd /x; umask 027; exec ruby t.rb", 0o022));
        assert!(kept("# umask 077 would be too strict\nexec ruby t.rb", 0o022));
        assert!(kept("HOMEBREW_UMASK=1 umasking=2", 0o002));
    }

    #[test]
    fn shell_scripts_read_for_umask() {
        let args = |a: &[&str]| a.iter().map(|s| s.as_bytes().to_vec()).collect::<Vec<_>>();
        assert!(!shell_keeps_umask(&args(&["sh", "-c", "umask 077; exec ruby t.rb"]), 0o022));
        assert!(shell_keeps_umask(&args(&["sh", "-c", "exec ruby t.rb"]), 0o022));
        assert!(!shell_keeps_umask(&args(&["bash"]), 0o022)); // reads stdin
        assert!(!shell_keeps_umask(&args(&["bash", "-p", "/nonexistent/brew.sh"]), 0o022));
        let dir = std::env::temp_dir().join(format!("sr-umask-{}", std::process::id()));
        fs::create_dir_all(&dir).unwrap();
        let script = dir.join("inner.sh");
        fs::write(&script, "umask go-w\nexec ruby t.rb \"$@\"\n").unwrap();
        let path = script.to_str().unwrap();
        assert!(shell_keeps_umask(&args(&["/bin/bash", "-p", path, "info"]), 0o022));
        assert!(!shell_keeps_umask(&args(&["/bin/bash", "-o", "posix", path]), 0o002));
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn python_launchers() {
        assert!(launcher("#!/usr/bin/python3\nimport os, sys\nos.execl(sys.executable, sys.executable, '-m', 'tool', *sys.argv[1:])\n"));
        assert!(!launcher("#!/usr/bin/python3\nimport os\nif True:\n    os.execv('/bin/true', ['true'])\n"));
        assert!(!launcher("#!/usr/bin/python3\nimport tool\ntool.main()\n"));
        assert!(!launcher("#!/usr/bin/ruby\nexec 'python3'\n"));
    }
}
