//! speedrunner client: hands a CLI invocation to the tool's preloaded daemon,
//! or execs the real tool when the fast path is unavailable.
//!
//! Safety rule: fall back to the native tool only before the daemon's child
//! has been told to go (b"G"), or when the child says it never started the tool
//! (N). Other errors are reported, never retried, so a command can never run
//! twice. Wire format: see server/speedrunner_daemon.py.

mod launcher;
mod platform;
mod script;

use std::env;
use std::ffi::{CString, OsStr, OsString};
use std::fs::File;
use std::io::{self, Read, Seek, SeekFrom, Write};
use std::mem;
use std::os::unix::ffi::{OsStrExt, OsStringExt};
use std::os::unix::fs::{DirBuilderExt, MetadataExt, OpenOptionsExt, PermissionsExt};
use std::os::unix::io::{AsRawFd, FromRawFd, RawFd};
use std::os::unix::net::UnixStream;
use std::path::{Path, PathBuf};
use std::process;
use std::ptr;
use std::sync::atomic::{AtomicI32, Ordering};
use std::time::{Duration, Instant};

const MAGIC: &[u8] = b"HS1";
// In each socket's name. The client waits for the child's hello before it hands
// over any file descriptor, so a client of an older protocol shares no daemon.
const PROTOCOL: &[u8] = b"hello";
const EXIT_NOT_FAST: i32 = 124;
const EXIT_LOST: i32 = 125;
const HANDSHAKE_TIMEOUT: Duration = Duration::from_secs(3);
// How long a would-be daemon waits for the startup lock that an exiting
// daemon (socket already gone) still holds.
const LOCK_WAIT: Duration = Duration::from_secs(3);
// How long the launcher waits for one daemon to be ready before the next.
const START_WAIT: Duration = Duration::from_secs(20);
// How long an interpreter may take to print its version, the first time.
const PROBE_WAIT: Duration = Duration::from_secs(10);
const MAX_MSG: usize = 1 << 20;
// Relayed to the child. Terminal-generated ones go to its whole process group,
// as the terminal would have sent them to the native tool's group.
const RELAYED: [libc::c_int; 5] =
    [libc::SIGINT, libc::SIGQUIT, libc::SIGWINCH, libc::SIGHUP, libc::SIGTERM];

static CHILD_PID: AtomicI32 = AtomicI32::new(0);
static NATIVE_PID: AtomicI32 = AtomicI32::new(0);
static CHILD_PIDFD: AtomicI32 = AtomicI32::new(-1);

// The daemons and the shell hooks, built in: a release is this one file. The
// Node daemon's addon comes per libc, as build.rs made it (empty if not).
const EMBEDDED: [(&str, &[u8]); 9] = [
    ("speedrunner_daemon.py", include_bytes!("../../server/speedrunner_daemon.py")),
    ("speedrunner_pyi_boot.py", include_bytes!("../../server/speedrunner_pyi_boot.py")),
    ("speedrunner_pyi", include_bytes!(concat!(env!("OUT_DIR"), "/speedrunner_pyi"))),
    ("speedrunner_daemon.rb", include_bytes!("../../server/speedrunner_daemon.rb")),
    ("speedrunner_daemon.php", include_bytes!("../../server/speedrunner_daemon.php")),
    ("speedrunner_daemon.mjs", include_bytes!("../../server/speedrunner_daemon.mjs")),
    ("speedrunner_node-gnu.node", include_bytes!(concat!(env!("OUT_DIR"), "/speedrunner_node-gnu.node"))),
    ("speedrunner_node-musl.node", include_bytes!(concat!(env!("OUT_DIR"), "/speedrunner_node-musl.node"))),
    ("speedrunner_node-darwin.node", include_bytes!(concat!(env!("OUT_DIR"), "/speedrunner_node-darwin.node"))),
];
const NATIVE_FLAG: &str = "--native";
const NATIVE_ENV: &str = "SPEEDRUNNER_NATIVE";
const ZSH_HOOK: &str = include_str!("../../shell/speedrunner.zsh");
const BASH_HOOK: &str = include_str!("../../shell/speedrunner.bash");

fn main() {
    let args: Vec<OsString> = env::args_os().collect();
    let invoked = args.first().map(|a| basename(a.as_bytes()).to_vec()).unwrap_or_default();
    // Called as `srr` (a link to this binary): a prefix, as with time or
    // nice, that works for any tool without enrolling it. `srr aws s3 ls`
    // is `speedrunner run aws -- s3 ls`.
    if invoked == b"srr" {
        // No tool has these names, and they are what one tries first.
        if let Some(code) = args.get(1).and_then(|a| own_flag(a.as_bytes())) {
            process::exit(code);
        }
        let at = if args.get(1).map_or(false, |a| a == NATIVE_FLAG) { 2 } else { 1 };
        if args.len() <= at {
            eprintln!("usage: srr [{}] <tool> [args...]", NATIVE_FLAG);
            process::exit(2);
        }
        let rest: Vec<OsString> = args[1..=at].iter().cloned()
            .chain(std::iter::once(OsString::from("--")))
            .chain(args[at + 1..].iter().cloned())
            .collect();
        process::exit(cmd_run(&rest));
    }
    if invoked == b"bash" {
        process::exit(run_as_bash(&args));
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
    if let Some(code) = args.get(1).and_then(|a| own_flag(a.as_bytes())) {
        process::exit(code);
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
        Some(b"demo") => cmd_demo(&args[2..]),
        Some(b"version") => own_flag(b"--version").unwrap_or(0),
        Some(b"help") => own_flag(b"--help").unwrap_or(0),
        _ => {
            eprintln!("{}", USAGE);
            2
        }
    };
    process::exit(code);
}

/// What speedrunner and srr answer themselves: the exit code, or None.
fn own_flag(flag: &[u8]) -> Option<i32> {
    match flag {
        b"--version" | b"-V" => println!("speedrunner {}", env!("CARGO_PKG_VERSION")),
        b"--help" | b"-h" => println!("{}", USAGE),
        _ => return None,
    }
    Some(0)
}

const USAGE: &str =
    "usage: speedrunner run [--native] <tool> -- [args...]   (or: srr [--native] <tool> [args...])\n       \
     eval \"$(speedrunner activate [--eager] [<tool>...])\"     (in a script: no prefix needed)\n       \
     speedrunner exec [--eager] [-t <tool>]... [--] <command> [args...]\n       \
     speedrunner enroll [<tool>...]\n       \
     speedrunner prewarm|status|stop <tool>   (or: php <script>)\n       \
     eval \"$(speedrunner init zsh|bash)\"     (in ~/.zshrc or ~/.bashrc: wrappers, warm-up while typing)\n       \
     speedrunner demo [--runs N]     (the gain on this machine, with the tools it has)\n       \
     speedrunner --version|--help";

/// Well-known tools and a command of theirs with no side effect, for
/// `speedrunner demo`: only these run, never whatever else is on PATH.
const DEMO_TOOLS: &[&str] = &[
    "aws", "pip", "pip3", "ansible", "black", "poetry", "http", "mypy", "pytest", "flake8", "yamllint",
    "pre-commit", "tox", "pipx", "django-admin", "jupyter", "ipython", "sphinx-build",
    "rubocop", "rspec", "rake", "bundle", "gem", "rails", "solargraph", "pod", "jekyll",
    "composer", "phpunit", "phpstan", "psalm", "php-cs-fixer", "phpcs", "rector", "laravel",
    "npm", "eslint", "prettier", "jest", "webpack", "next", "firebase", "yarn",
];
const DEMO_ARGS: &[&str] = &["--version"];

/// `speedrunner demo`: for each of DEMO_TOOLS installed here, its median time
/// natively and through speedrunner (the fast path required: a native
/// fallback is reported, not counted), and whether both print the same.
/// Daemons it started are stopped after; those already running are left.
fn cmd_demo(rest: &[OsString]) -> i32 {
    // Asking for the gain is asking for the fast path, whatever the shell exports.
    env::remove_var(NATIVE_ENV);
    let runs = match rest {
        [] => Some(3),
        [flag, n] if flag == "--runs" => n.to_str().and_then(|n| n.parse::<usize>().ok()).filter(|n| *n > 0),
        _ => None,
    };
    let runs = match runs {
        Some(r) => r,
        None => {
            eprintln!("usage: speedrunner demo [--runs N]");
            return 2;
        }
    };
    let me = match env::current_exe() {
        Ok(p) => p,
        Err(e) => {
            eprintln!("speedrunner demo: {}", e);
            return 1;
        }
    };
    let mut seen: Vec<Vec<u8>> = Vec::new();
    let mut found: Vec<&str> = Vec::new();
    for name in DEMO_TOOLS {
        if let Some(tool) = Tool::resolve(OsStr::new(name)) {
            // A script some daemon can preload, and not one already listed (pip, pip3).
            if read_shebang(&tool.path).is_ok() && !seen.contains(&tool.path) {
                seen.push(tool.path.clone());
                found.push(*name);
            }
        }
    }
    if found.is_empty() {
        eprintln!("speedrunner demo: none of the tools it knows is installed here: {}", DEMO_TOOLS.join(", "));
        return 1;
    }
    println!("speedrunner demo: {} tool{} found here, {} runs each, median time\n", found.len(),
             if found.len() > 1 { "s" } else { "" }, runs);
    println!("  {:<24} {:>9} {:>12} {:>7}  {}", "command", "native", "speedrunner", "gain", "same output");
    let quiet = |mut c: process::Command| {
        c.stdin(process::Stdio::null()).stdout(process::Stdio::null()).stderr(process::Stdio::null());
        c
    };
    let mut gains = Vec::new();
    for name in &found {
        let tool = match Tool::resolve(OsStr::new(name)) {
            Some(t) => t,
            None => continue,
        };
        print!("  {:<24} ", format!("{} {}", name, DEMO_ARGS.join(" ")));
        let _ = io::stdout().flush();
        let native = || {
            let mut c = process::Command::new(OsStr::from_bytes(&tool.found));
            c.args(DEMO_ARGS);
            c
        };
        let fast = |required: bool| {
            let mut c = process::Command::new(&me);
            c.arg("run").arg(name).arg("--").args(DEMO_ARGS);
            if required {
                c.env("SPEEDRUNNER_REQUIRE_FAST", "1");
            }
            c
        };
        let was_running = matches!(request(&tool, b'P'), Ok((b'P', ref t)) if t.starts_with(b"ready"));
        let _ = quiet(native()).status(); // warm the page cache, as the daemon runs will be
        let native_ms = median_ms(runs, || quiet(native()).status().is_ok());
        if !was_running {
            let _ = spawn_daemon_with(&tool, "demo", "0"); // on /dev/null, as the runs below
        }
        wait_ready(&tool);
        for _ in 0..2 {
            // What the first calls import lazily, the daemon learns and preloads.
            let _ = quiet(fast(false)).status();
            std::thread::sleep(Duration::from_millis(100));
        }
        wait_learned(&tool);
        let a = native().stdin(process::Stdio::null()).output();
        let b = fast(true).stdin(process::Stdio::null()).output();
        let refused = matches!(&b, Ok(o) if o.status.code() == Some(EXIT_NOT_FAST));
        let same = match (&a, &b) {
            (Ok(a), Ok(b)) => a.status.code() == b.status.code() && a.stdout == b.stdout && a.stderr == b.stderr,
            _ => false,
        };
        if refused {
            println!("{:>6.0} ms {:>12}", native_ms, "runs natively here");
        } else {
            let fast_ms = median_ms(runs, || quiet(fast(true)).status().map_or(false, |s| s.success()));
            gains.push(native_ms / fast_ms);
            println!("{:>6.0} ms {:>9.0} ms {:>6.1}x  {}", native_ms, fast_ms, native_ms / fast_ms,
                     if same { "yes" } else { "NO" });
        }
        if !was_running {
            let _ = request(&tool, b'S');
        }
    }
    if !gains.is_empty() {
        gains.sort_by(|a, b| a.partial_cmp(b).unwrap());
        println!("\n  median gain x{:.1}, from x{:.1} to x{:.1}. Daemons the demo started are stopped.",
                 gains[gains.len() / 2], gains[0], gains[gains.len() - 1]);
        println!("  Try it on your own commands:  srr <tool> [args...]   or   speedrunner exec ./script.sh");
    }
    0
}

/// Median wall time of `runs` runs of `f`, in milliseconds.
fn median_ms(runs: usize, mut f: impl FnMut() -> bool) -> f64 {
    let mut times: Vec<f64> = (0..runs).map(|_| {
        let t0 = Instant::now();
        f();
        t0.elapsed().as_secs_f64() * 1000.0
    }).collect();
    times.sort_by(|a, b| a.partial_cmp(b).unwrap());
    times[times.len() / 2]
}

/// `eval "$(speedrunner init zsh)"` in ~/.zshrc, or `init bash` in
/// ~/.bashrc: the built-in hook.
fn cmd_init(rest: &[OsString]) -> i32 {
    match rest.first().map(|a| a.as_bytes()) {
        Some(b"zsh") => {
            print!("{}", ZSH_HOOK);
            0
        }
        Some(b"bash") => {
            print!("{}", BASH_HOOK);
            0
        }
        Some(other) => {
            eprintln!("speedrunner: init {}: only zsh and bash are supported", String::from_utf8_lossy(other));
            2
        }
        None => {
            eprintln!("usage: eval \"$(speedrunner init zsh)\"   (in ~/.zshrc; or init bash, in ~/.bashrc)");
            2
        }
    }
}

struct Tool<'a> {
    name: &'a OsStr,
    path: Vec<u8>,  // the script the daemon preloads: an absolute path
    found: Vec<u8>, // the script as execvp finds it, maybe relative: what runs natively
    sock: PathBuf,
    dir: PathBuf,
    form: Option<ScriptForm>,
    target: Option<Target>,
    learned: bool, // a launcher, seen as the daemon it last execed
}

/// `php script args`: the interpreter on PATH, the script as an argument.
struct ScriptForm {
    interp: Vec<u8>,
    typed: OsString, // the script as typed, for argv and for the native run
}

/// What a launcher execs, when a daemon can serve it: a Python interpreter,
/// with options of its own that change nothing per run, on a script or on
/// `-m module`.
struct Target {
    launch: launcher::Launch,
    flags: Vec<Vec<u8>>,
    module: Option<Vec<u8>>,
    argv0: Vec<u8>,      // of the request: the script as named, or -m
    args: Vec<OsString>, // the rest; for a module, its name first
}

impl Target {
    fn parse(launch: launcher::Launch) -> Result<Target, launcher::Launch> {
        let argv = &launch.argv;
        let mut i = 1;
        while i < argv.len() {
            let a = &argv[i][..];
            if a == b"-X" || a == b"-W" {
                i += 2;
            } else if a.len() > 1 && a[0] == b'-' && a[1..].iter().all(|c| b"BEIOPRSbqsu".contains(c)) {
                i += 1;
            } else {
                break;
            }
        }
        // The module, and where the tool's arguments start.
        let (module_name, next) = match argv.get(i) {
            Some(a) if &a[..] == b"-m" && i + 1 < argv.len() => (Some(argv[i + 1].clone()), i + 2),
            // -mpre_commit, as pre-commit's git hook writes it
            Some(a) if a.len() > 2 && a.starts_with(b"-m") => (Some(a[2..].to_vec()), i + 1),
            Some(a) if !a.starts_with(b"-") => (None, i + 1),
            _ => return Err(launch), // -c, -, or nothing to run: as is
        };
        let flags = argv[1.min(i)..i].to_vec();
        let rest = argv[next..].iter().map(|a| OsString::from_vec(a.clone()));
        let (argv0, args) = match &module_name {
            Some(m) => (b"-m".to_vec(), std::iter::once(OsString::from_vec(m.clone())).chain(rest).collect()),
            None => (argv[i].clone(), rest.collect()),
        };
        Ok(Target { flags, module: module_name, argv0, args, launch })
    }
}

impl<'a> Tool<'a> {
    fn resolve(name: &'a OsStr) -> Option<Tool<'a>> {
        let found = resolve_in_path(name.as_bytes())?;
        // `./manage.py` names a different script in each project: the
        // daemon, which runs elsewhere, gets it relative to no directory.
        let path = absolute(&found)?;
        // `#!/usr/bin/env python`: the interpreter is PATH's, venv or version
        // manager: another one is another daemon.
        let mut identity = path.clone();
        if let Ok((interp, _, _)) = read_shebang(&found) {
            identity.push(0);
            identity.extend_from_slice(&absolute(&interp).unwrap_or(interp));
        }
        let (dir, sock) = socket_at(socket_name(name, &identity, None).as_ref());
        Some(Tool { name, path, found, sock, dir, form: None, target: None, learned: false })
    }

    /// The tool a launcher named `name` execs. Its daemon is the target's:
    /// another interpreter, options or script is another daemon.
    fn for_target(name: &'a OsStr, target: Target) -> Tool<'a> {
        let mut identity = target.launch.exe.clone();
        for f in &target.flags {
            identity.push(0);
            identity.extend_from_slice(f);
        }
        identity.push(0);
        let (path, found) = match &target.module {
            Some(m) => {
                identity.extend_from_slice(b"-m\0");
                (m.clone(), m.clone())
            }
            None => {
                // Relative to the launcher's cwd, which is ours (read_launch).
                (absolute(&target.argv0).unwrap_or_else(|| target.argv0.clone()), target.argv0.clone())
            }
        };
        identity.extend_from_slice(&path);
        let (dir, sock) = socket_at(socket_name(name, &identity, Some(&target.launch.env)).as_ref());
        Tool { name, path, found, sock, dir, form: None, target: Some(target), learned: false }
    }

    fn is_launcher(&self) -> bool {
        self.form.is_none() && self.target.is_none() && !self.learned
            && launcher::looks_like_launcher(&self.path)
    }

    /// Where a launcher keeps the socket name of the daemon it was last
    /// seen to exec, for status, stop and prewarm.
    fn learned_path(&self) -> PathBuf {
        self.sock.with_extension("target")
    }

    fn remember(&self, target: &Tool) {
        let name = match target.sock.file_name() {
            Some(n) => n.as_bytes(),
            None => return,
        };
        let path = self.learned_path();
        if std::fs::read(&path).ok().as_deref() == Some(name) {
            return;
        }
        let tmp = path.with_extension(format!("target.{}", process::id()));
        if std::fs::write(&tmp, name).and_then(|_| std::fs::rename(&tmp, &path)).is_err() {
            let _ = std::fs::remove_file(&tmp);
        }
    }

    /// The launcher as status and stop see it: the daemon it last execed.
    fn learned(&self) -> Option<Tool<'a>> {
        let name = std::fs::read(self.learned_path()).ok()?;
        let plain = !name.is_empty() && name.ends_with(b".sock")
            && name.iter().all(|c| c.is_ascii_alphanumeric() || b"._-".contains(c));
        if !plain {
            return None;
        }
        let (dir, sock) = socket_at(OsStr::from_bytes(&name));
        Some(Tool { name: self.name, path: self.path.clone(), found: self.found.clone(),
                    sock, dir,
                    form: None, target: None, learned: true })
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
        let mut identity = path.clone();
        identity.push(0);
        identity.extend_from_slice(&self.path); // the interpreter PATH found
        let (dir, sock) = socket_at(socket_name(OsStr::from_bytes(basename(&path)), &identity, None).as_ref());
        Some(Tool { name: self.name, found: path.clone(), path, sock, dir,
                    form: Some(ScriptForm { interp: self.path.clone(), typed: typed.clone() }),
                    target: None, learned: false })
    }

    /// argv[0] the tool sees: the script as typed, or as execvp found it.
    fn argv0(&self) -> &[u8] {
        if let Some(t) = &self.target {
            return &t.argv0;
        }
        self.form.as_ref().map_or(&self.found[..], |f| f.typed.as_bytes())
    }

    fn display(&self) -> String {
        self.name.to_string_lossy().into_owned()
    }
}

/// `<tool>`, or `php <script>` for a script's own daemon.
fn with_tool(rest: &[OsString], f: impl Fn(&Tool) -> i32) -> i32 {
    if rest.is_empty() || rest.len() > 2 {
        eprintln!("{}", USAGE);
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
        if tool.is_launcher() {
            return match tool.learned() {
                Some(target) => f(&target),
                None => f(&tool), // never seen to exec: nothing of its own
            };
        }
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
    let flagged = rest.first().map_or(false, |a| a == NATIVE_FLAG);
    let rest = if flagged { &rest[1..] } else { rest };
    if rest.len() < 2 || rest[1] != "--" {
        eprintln!("{}", USAGE);
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
    if flagged || native_asked() {
        // Not fall_back: the caller's choice outranks SPEEDRUNNER_REQUIRE_FAST.
        if env::var_os("SPEEDRUNNER_DEBUG").is_some() {
            eprintln!("speedrunner: running {} natively: asked to", tool.display());
        }
        exec_native(&tool, args);
    }
    if let Some(reason) = only_native() {
        fall_back(&tool, args, &reason);
    }
    if let Some(script) = tool.script_form(args) {
        tool = script;
        args = &args[1..];
    } else if tool.is_launcher() {
        return run_launcher(&tool, tool.name, args, lazy);
    } else if read_shebang(&tool.path).is_err() {
        fall_back(&tool, args, "not a script of a runtime speedrunner serves");
    }
    serve(&tool, args, lazy)
}

/// SPEEDRUNNER_NATIVE: everything run under it is the tool itself, sub-processes
/// included, and starts no daemon. Its forms that read as "no" do not count.
fn native_asked() -> bool {
    env::var_os(NATIVE_ENV).map_or(false, |v| !v.is_empty() && v != "0")
}

/// Why this very call must run natively, and start no daemon either.
fn only_native() -> Option<String> {
    untrusted_context().or_else(|| {
        if let Some(fd) = inherited_fd() {
            return Some(format!("fd {} is open, which a child could not have as its own", fd));
        }
        unsafe {
            let group = if libc::isatty(0) == 1 { libc::tcgetpgrp(0) } else { -1 };
            if group > 0 && group != libc::getpgrp() {
                // The child has no terminal: it would read it, not stop as a job does.
                return Some("a background job on a terminal".to_string());
            }
        }
        None
    })
}

/// Why no daemon may serve or be started in this context: what makes the
/// process differ is not something a daemon started elsewhere could have (code
/// preloaded into it, a sandbox), or the files a daemon runs on are not ours.
fn untrusted_context() -> Option<String> {
    for name in ["LD_PRELOAD", "LD_AUDIT", "DYLD_INSERT_LIBRARIES"] {
        if env::var_os(name).map_or(false, |v| !v.is_empty()) {
            return Some(format!("{} is set", name));
        }
    }
    if platform::sandboxed() {
        return Some("in a sandbox".to_string());
    }
    let euid = unsafe { libc::geteuid() };
    if euid != unsafe { libc::getuid() } || unsafe { libc::getegid() != libc::getgid() } {
        return Some("setuid or setgid".to_string());
    }
    // sudo keeps HOME on some systems: root would run the user's daemon files.
    for name in ["HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_CONFIG_HOME"] {
        if let Some(dir) = env::var_os(name) {
            if let Ok(meta) = std::fs::metadata(&dir) {
                if !owned_by_us_or_root(&meta, euid) {
                    return Some(format!("{} belongs to another user", name));
                }
            }
        }
    }
    None
}

/// Ours, or root's: root trusts only its own.
fn owned_by_us_or_root(meta: &std::fs::Metadata, euid: libc::uid_t) -> bool {
    meta.uid() == euid || meta.uid() == 0
}

/// An fd above 2 that the tool would inherit natively: `<(...)`, `3>file`. Only
/// 0, 1 and 2 are handed over, and the child's own low fds are not the caller's.
fn inherited_fd() -> Option<RawFd> {
    let jobserver = legacy_jobserver();
    let inherited = |fd: RawFd| fd > 2 && {
        let flags = unsafe { libc::fcntl(fd, libc::F_GETFD) };
        flags >= 0 && flags & libc::FD_CLOEXEC == 0 && !jobserver.contains(&pipe_id(fd))
    };
    match std::fs::read_dir("/dev/fd") {
        // The directory's own fd is close-on-exec.
        Ok(entries) => entries.flatten()
            .filter_map(|e| e.file_name().to_str().and_then(|n| n.parse().ok()))
            .find(|fd| inherited(*fd)),
        Err(_) => (3..256).find(|fd| inherited(*fd)),
    }
}

/// The pipes of a make that hands its jobserver to every recipe under -j, whether
/// the recipe runs a sub-make or not: `--jobserver-fds=R,W` in MAKEFLAGS, as
/// macOS's 3.81 does. Not an fd the tool asked for, unlike `--jobserver-auth=R,W`,
/// which make 4.3 gives only to a recipe marked as recursive: that one stays
/// native. Nothing here unless both are pipes.
fn legacy_jobserver() -> Vec<Option<(u64, u64)>> {
    let flags = env::var_os("MAKEFLAGS").unwrap_or_default();
    let named = flags.as_bytes().split(|b| *b == b' ')
        .find_map(|word| word.strip_prefix(&b"--jobserver-fds="[..]))
        .and_then(|fds| std::str::from_utf8(fds).ok())
        .and_then(|fds| fds.split_once(','))
        .and_then(|(r, w)| Some((r.parse::<RawFd>().ok()?, w.parse::<RawFd>().ok()?)));
    match named {
        Some((r, w)) if pipe_id(r).is_some() && pipe_id(w).is_some() => vec![pipe_id(r), pipe_id(w)],
        _ => Vec::new(),
    }
}

/// Which pipe an fd is, if it is one: make keeps a dup of the read end too.
fn pipe_id(fd: RawFd) -> Option<(u64, u64)> {
    let mut st: libc::stat = unsafe { std::mem::zeroed() };
    if unsafe { libc::fstat(fd, &mut st) } != 0 || st.st_mode & libc::S_IFMT != libc::S_IFIFO {
        return None;
    }
    Some((st.st_dev as u64, st.st_ino as u64))
}

/// Run a launcher natively until it execs the interpreter, then serve that,
/// by a daemon named `name`.
fn run_launcher(tool: &Tool, name: &OsStr, args: &[OsString], lazy: Option<&Path>) -> i32 {
    let argv: Vec<Vec<u8>> = std::iter::once(tool.name.as_bytes().to_vec())
        .chain(args.iter().map(|a| a.as_bytes().to_vec()))
        .collect();
    match launcher::trace(&tool.found, &argv, false) {
        Err(e) => fall_back(tool, args, &format!("cannot follow the launcher: {}", e)),
        Ok(launcher::Traced::Done(status)) => exit_like(status),
        Ok(launcher::Traced::Native(pid)) => wait_native(pid),
        Ok(launcher::Traced::Python(launch)) => match Target::parse(launch) {
            Err(launch) => fall_back_launch(tool, &launch, "the launcher execs no script or module"),
            Ok(target) => {
                let args = target.args.clone();
                let target = Tool::for_target(name, target);
                if name == tool.name {
                    tool.remember(&target);
                }
                serve(&target, &args, lazy)
            }
        },
    }
}

/// Called as `bash`, through the link `activate` and `exec` put next to
/// pre-commit's: git runs pre-commit's hook by its path, and the hook
/// starts Python by its absolute path, but its `#!/usr/bin/env bash` looks
/// bash up on PATH. That hook is followed as a launcher, served by a
/// `pre-commit` daemon; anything else is the real bash, at once.
fn run_as_bash(args: &[OsString]) -> i32 {
    let name = OsStr::new("bash");
    let bash = match Tool::resolve(name) {
        Some(t) => t,
        None => {
            eprintln!("speedrunner: bash: command not found");
            return 127;
        }
    };
    let args = &args[1..];
    // As the kernel runs a shebang: `bash /path/to/hook hook-args`.
    let hook = args.first().map_or(false, |a| {
        !a.as_bytes().starts_with(b"-") && launcher::is_precommit_hook(a.as_bytes())
    });
    if !hook || native_asked() {
        exec_native(&bash, args);
    }
    run_launcher(&bash, OsStr::new("pre-commit"), args, lazy_counter(b"pre-commit").as_deref())
}

fn serve(tool: &Tool, args: &[OsString], lazy: Option<&Path>) -> i32 {
    let start = |tool: &Tool| {
        if lazy.map_or(true, called_again) {
            autostart(tool);
        }
    };
    if let Err(e) = check_runtime_dir(&tool.dir) {
        start(tool);
        fall_back(tool, args, &format!("runtime dir: {}", e));
    }
    let sock = match connect(&tool.sock) {
        Ok(s) => s,
        Err(e) => {
            if e.kind() != io::ErrorKind::PermissionDenied {
                start(tool);
            }
            fall_back(tool, args, &format!("connect: {}", e));
        }
    };
    let pid = match handshake(&sock, tool, args) {
        Ok(pid) => pid,
        Err(Refused::Native(reason)) => {
            if reason.starts_with("stale") {
                autostart(tool); // the old daemon is exiting; the new one waits for its lock
            }
            fall_back(tool, args, &reason);
        }
        Err(Refused::Io(e)) => fall_back(tool, args, &format!("handshake: {}", e)),
    };

    CHILD_PIDFD.store(platform::pidfd_open(pid), Ordering::SeqCst);
    CHILD_PID.store(pid, Ordering::SeqCst);
    install_relays();
    // Sending G commits. If it cannot be sent, the child never runs the tool.
    let mut w: &UnixStream = &sock;
    if let Err(e) = w.write_all(b"G") {
        fall_back(tool, args, &format!("go: {}", e));
    }
    wait_for_child(&sock, tool, args, pid)
}

enum Refused {
    Native(String),
    Io(io::Error),
}

fn handshake(sock: &UnixStream, tool: &Tool, args: &[OsString]) -> Result<i32, Refused> {
    sock.set_read_timeout(Some(HANDSHAKE_TIMEOUT)).map_err(Refused::Io)?;
    sock.set_write_timeout(Some(HANDSHAKE_TIMEOUT)).map_err(Refused::Io)?;
    // Only then the request. What is sent stays in the socket until someone
    // reads it, whether or not we are still there, and with it our stdout: a
    // pipeline would wait for a daemon that is busy loading.
    read_hello(sock).map_err(Refused::Io)?;
    for fd in 0..3 {
        if unsafe { libc::fcntl(fd, libc::F_GETFD) } < 0 {
            return Err(Refused::Native(format!("fd {} is closed", fd)));
        }
    }
    let cwd = unsafe {
        libc::open(b".\0".as_ptr() as *const libc::c_char, platform::CWD_FLAGS)
    };
    if cwd < 0 {
        return Err(Refused::Io(io::Error::last_os_error()));
    }
    let msg = frame(b'R', &run_body(tool.argv0(), args, tool.target.as_ref().map(|t| &t.launch)));
    // Our controlling terminal, if any: the child has none, and tools open
    // /dev/tty for passwords.
    let tty = platform::open_tty();
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

fn read_hello(sock: &UnixStream) -> io::Result<()> {
    match read_msg(sock)? {
        (b'H', _) => Ok(()),
        _ => Err(io::Error::new(io::ErrorKind::InvalidData, "no hello")),
    }
}

fn wait_for_child(sock: &UnixStream, tool: &Tool, args: &[OsString], pid: i32) -> i32 {
    let _ = sock.set_read_timeout(None);
    let mut in_teardown = None;
    loop {
        return match read_msg(sock) {
            // The child failed before starting the tool: nothing can run twice.
            Ok((b'N', reason)) => fall_back(tool, args, &String::from_utf8_lossy(&reason)),
            // Sent once nothing visible is left: the child tears down after we exit.
            Ok((b'X', p)) if p.len() == 4 => i32::from_le_bytes([p[0], p[1], p[2], p[3]]),
            // The daemon checks we are here: being able to send it is the answer.
            Ok((b'C', _)) => continue,
            // The tool's code still runs in its teardown: wait for it to end.
            Ok((b'T', p)) if p.len() == 4 => {
                in_teardown = Some(i32::from_le_bytes([p[0], p[1], p[2], p[3]]));
                continue;
            }
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
            Err(_) => match in_teardown {
                Some(code) => code, // the child is done, and the daemon gone (kill -9)
                None => {
                    eprintln!(
                        "speedrunner: lost track of {} (pid {}) after it started; not re-running it",
                        tool.display(), pid
                    );
                    EXIT_LOST
                }
            },
        };
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

/// What the launcher execed, which we stopped before it ran: exec it.
fn fall_back_launch(tool: &Tool, launch: &launcher::Launch, reason: &str) -> ! {
    if env::var_os("SPEEDRUNNER_REQUIRE_FAST").map_or(false, |v| v == "1") {
        eprintln!("speedrunner: fast path unavailable: {}", reason);
        process::exit(EXIT_NOT_FAST);
    }
    if env::var_os("SPEEDRUNNER_DEBUG").is_some() {
        eprintln!("speedrunner: running {} natively: {}", tool.display(), reason);
    }
    exec_launch(tool, launch)
}

fn exec_launch(tool: &Tool, launch: &launcher::Launch) -> ! {
    let argv: Vec<CString> = launch.argv.iter().map(|a| cstring(a)).collect();
    let envp: Vec<CString> = launch.env.iter().map(|e| cstring(e)).collect();
    let mut argv_p: Vec<*const libc::c_char> = argv.iter().map(|c| c.as_ptr()).collect();
    argv_p.push(ptr::null());
    let mut envp_p: Vec<*const libc::c_char> = envp.iter().map(|c| c.as_ptr()).collect();
    envp_p.push(ptr::null());
    let path = cstring(&launch.exe);
    unsafe {
        libc::signal(libc::SIGPIPE, libc::SIG_DFL);
        libc::execve(path.as_ptr(), argv_p.as_ptr(), envp_p.as_ptr());
    }
    let err = io::Error::last_os_error();
    eprintln!("speedrunner: {}: {}", tool.display(), err);
    process::exit(if err.kind() == io::ErrorKind::NotFound { 127 } else { 126 });
}

/// Exit as a process with wait status `status` did.
fn exit_like(status: i32) -> i32 {
    if libc::WIFSIGNALED(status) {
        die_by_signal(libc::WTERMSIG(status));
    }
    libc::WEXITSTATUS(status)
}

extern "C" fn relay_native(sig: libc::c_int) {
    let pid = NATIVE_PID.load(Ordering::SeqCst);
    if pid > 0 {
        unsafe {
            let errno = *platform::errno();
            libc::kill(pid, sig);
            *platform::errno() = errno;
        }
    }
}

/// The launcher went on natively, as our child, in our process group: the
/// terminal's signals reach it as they would have; others sent to us are
/// passed on. Exit as it does.
fn wait_native(pid: i32) -> i32 {
    NATIVE_PID.store(pid, Ordering::SeqCst);
    unsafe {
        libc::signal(libc::SIGINT, libc::SIG_IGN);
        libc::signal(libc::SIGQUIT, libc::SIG_IGN);
        for sig in [libc::SIGTERM, libc::SIGHUP] {
            let mut sa: libc::sigaction = mem::zeroed();
            sa.sa_sigaction = relay_native as extern "C" fn(libc::c_int) as usize;
            sa.sa_flags = libc::SA_RESTART;
            libc::sigemptyset(&mut sa.sa_mask);
            libc::sigaction(sig, &sa, ptr::null_mut());
        }
    }
    let mut status = 0;
    loop {
        if unsafe { libc::waitpid(pid, &mut status, 0) } == pid {
            return exit_like(status);
        }
        if io::Error::last_os_error().kind() != io::ErrorKind::Interrupted {
            return EXIT_LOST;
        }
    }
}

fn exec_native(tool: &Tool, args: &[OsString]) -> ! {
    if let Some(t) = &tool.target {
        exec_launch(tool, &t.launch);
    }
    // argv[0] as the shell would set it; the kernel hands the path to the interpreter.
    let typed = tool.form.as_ref().map(|f| f.typed.as_os_str());
    let argv: Vec<CString> = std::iter::once(tool.name)
        .chain(typed)
        .chain(args.iter().map(|a| a.as_os_str()))
        .map(|a| cstring(a.as_bytes()))
        .collect();
    let mut ptrs: Vec<*const libc::c_char> = argv.iter().map(|c| c.as_ptr()).collect();
    ptrs.push(ptr::null());
    let path = cstring(tool.form.as_ref().map_or(&tool.found[..], |f| &f.interp[..]));
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
        let errno = *platform::errno();
        if sig == libc::SIGTERM {
            let fd = CHILD_PIDFD.load(Ordering::SeqCst);
            if fd >= 0 {
                platform::pidfd_send_signal(fd, sig);
            } else {
                libc::kill(pid, sig);
            }
        } else {
            libc::kill(-pid, sig);
        }
        *platform::errno() = errno;
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

/// `launch`: what a launcher execed, whose env, umask and ignored signals
/// the tool has instead of ours.
fn run_body(script: &[u8], args: &[OsString], launch: Option<&launcher::Launch>) -> Vec<u8> {
    let mut b = Vec::with_capacity(8192);
    put_u32(&mut b, args.len() as u32 + 1);
    put_blob(&mut b, script);
    for a in args {
        put_blob(&mut b, a.as_bytes());
    }
    let env: Vec<Vec<u8>> = match launch {
        Some(l) => l.env.clone(),
        None => environ(),
    };
    put_u32(&mut b, env.len() as u32);
    for e in &env {
        put_blob(&mut b, e);
    }
    let umask = launch.map_or_else(|| unsafe {
        let m = libc::umask(0o022);
        libc::umask(m);
        m as u32
    }, |l| l.umask);
    put_u32(&mut b, umask);
    b.extend_from_slice(&launch.map_or_else(ignored_signals, |l| l.ignored).to_le_bytes());
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

/// Our env, as KEY=VALUE entries.
fn environ() -> Vec<Vec<u8>> {
    env::vars_os()
        .map(|(k, v)| {
            let mut e = k.into_vec();
            e.push(b'=');
            e.extend_from_slice(v.as_bytes());
            e
        })
        .collect()
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
        _ => tmp_runtime_dir(),
    }
}

fn tmp_runtime_dir() -> PathBuf {
    PathBuf::from(format!("/tmp/speedrunner-{}", unsafe { libc::getuid() }))
}

/// The directory and path of the socket named `name`: in the runtime dir,
/// or in /tmp/speedrunner-<uid> when the path would not fit a socket address
/// (104 bytes on macOS, 108 on Linux). A daemon could still bind it,
/// relative to its directory, but no client could connect to it.
fn socket_at(name: &OsStr) -> (PathBuf, PathBuf) {
    let dir = runtime_dir();
    let room = unsafe { mem::zeroed::<libc::sockaddr_un>() }.sun_path.len();
    // The directory, a slash, the name and the final NUL.
    let dir = if dir.as_os_str().len() + 1 + name.len() + 1 > room { tmp_runtime_dir() } else { dir };
    let sock = dir.join(name);
    (dir, sock)
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

/// connect() blocks for as long as the daemon's queue is full, and a daemon
/// that is stuck never empties it: non-blocking, for a while.
fn connect_with_timeout(sock: &Path) -> io::Result<UnixStream> {
    let mut addr: libc::sockaddr_un = unsafe { mem::zeroed() };
    addr.sun_family = libc::AF_UNIX as _;
    let path = sock.as_os_str().as_bytes();
    if path.len() >= addr.sun_path.len() {
        return Err(io::Error::new(io::ErrorKind::InvalidInput, "socket path too long"));
    }
    for (to, from) in addr.sun_path.iter_mut().zip(path) {
        *to = *from as libc::c_char;
    }
    let size = mem::size_of::<libc::sockaddr_un>() as libc::socklen_t;
    let deadline = Instant::now() + HANDSHAKE_TIMEOUT;
    unsafe {
        let fd = libc::socket(libc::AF_UNIX, libc::SOCK_STREAM, 0);
        if fd < 0 {
            return Err(io::Error::last_os_error());
        }
        let stream = UnixStream::from_raw_fd(fd); // closes it, however we leave
        libc::fcntl(fd, libc::F_SETFD, libc::FD_CLOEXEC);
        stream.set_nonblocking(true)?;
        loop {
            if libc::connect(fd, &addr as *const _ as *const libc::sockaddr, size) == 0 {
                stream.set_nonblocking(false)?;
                return Ok(stream);
            }
            let err = io::Error::last_os_error();
            let full = err.raw_os_error() == Some(libc::EAGAIN);
            if !(full || err.kind() == io::ErrorKind::Interrupted) || Instant::now() >= deadline {
                return Err(err);
            }
            libc::usleep(2_000);
        }
    }
}

fn connect(sock: &Path) -> io::Result<UnixStream> {
    let s = connect_with_timeout(sock)?;
    if platform::peer_uid(s.as_raw_fd())? != unsafe { libc::getuid() } {
        return Err(io::Error::new(io::ErrorKind::PermissionDenied, "daemon runs as another user"));
    }
    Ok(s)
}

/// What a daemon has for good from the process that starts it, and a child
/// could not take from its client. Each different one is a daemon of its own,
/// started in that very context: limits, priority, namespaces, and so on
/// (platform::context), library paths, and the cwd when the interpreter finds
/// modules relative to it. `env`: what a launcher gives the tool instead of ours.
fn context_key(env: Option<&[Vec<u8>]>) -> Vec<u8> {
    let mut key = PROTOCOL.to_vec();
    platform::context(&mut key);
    let ours;
    let env = match env {
        Some(env) => env,
        None => {
            ours = environ();
            &ours
        }
    };
    let mut relative = false;
    for entry in env {
        let eq = entry.iter().position(|b| *b == b'=').unwrap_or(entry.len());
        let (name, value) = (&entry[..eq], entry.get(eq + 1..).unwrap_or(b""));
        if LIBRARY_PATHS.contains(&name) {
            key.push(0);
            key.extend_from_slice(entry);
        }
        relative = relative || lookup_is_relative(name, value);
    }
    if relative {
        if let Ok(cwd) = env::current_dir() {
            key.push(0);
            key.extend_from_slice(cwd.as_os_str().as_bytes());
        }
    }
    key
}

/// Read as the interpreter starts, and they name files: another value, another
/// set of libraries for the daemon's whole life.
const LIBRARY_PATHS: [&[u8]; 4] =
    [b"LD_LIBRARY_PATH", b"DYLD_LIBRARY_PATH", b"DYLD_FALLBACK_LIBRARY_PATH", b"DYLD_FRAMEWORK_PATH"];

/// Whether this variable makes an interpreter find code relative to the cwd.
/// Resolved once, in the daemon: `PYTHONPATH=.` would be the first project's in
/// all of them. Options are not parsed: an option that loads a file counts,
/// even with an absolute path, which only costs a daemon per directory.
fn lookup_is_relative(name: &[u8], value: &[u8]) -> bool {
    match name {
        b"PYTHONPATH" | b"RUBYLIB" | b"NODE_PATH" => {
            !value.is_empty() && value.split(|b| *b == b':').any(|e| !e.starts_with(b"/"))
        }
        b"NODE_OPTIONS" => value.split(|b| b.is_ascii_whitespace()).any(|o| {
            o == b"-r" || o.starts_with(b"--require") || o.starts_with(b"--import")
                || o.starts_with(b"--loader") || o.starts_with(b"--experimental-loader")
        }),
        b"RUBYOPT" => value.split(|b| b.is_ascii_whitespace()).any(|o| o.starts_with(b"-I") || o.starts_with(b"-r")),
        _ => false,
    }
}

fn socket_name(tool: &OsStr, path: &[u8], env: Option<&[Vec<u8>]>) -> String {
    let mut identity = path.to_vec();
    identity.push(0);
    identity.extend_from_slice(&context_key(env));
    let base: String = Path::new(tool)
        .file_name()
        .map(|n| n.to_string_lossy().into_owned())
        .unwrap_or_default()
        .chars()
        .take(32)
        .map(|c| if c.is_ascii_alphanumeric() || "._-".contains(c) { c } else { '_' })
        .collect();
    format!("{}-{:016x}.sock", base, fnv1a(&identity))
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
    if tool.is_launcher() {
        return 0; // never seen to exec a daemon's tool: its first run will tell
    }
    if tool.learned {
        return prewarm_launcher(tool, "prewarm");
    }
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

/// A launcher's daemon, as its last run found it: up, or started from what
/// the launcher execs when run without arguments, if that is the same.
fn prewarm_launcher(learned: &Tool, origin: &str) -> i32 {
    match request(learned, b'P') {
        Ok((b'P', text)) if !text.starts_with(b"stale") => return 0,
        _ => {}
    }
    let launcher = match Tool::resolve(learned.name) {
        Some(t) => t,
        None => return 1,
    };
    let argv = vec![learned.name.as_bytes().to_vec()];
    let target = match launcher::trace(&launcher.found, &argv, true) {
        Ok(launcher::Traced::Python(launch)) => match Target::parse(launch) {
            Ok(target) => Tool::for_target(learned.name, target),
            Err(_) => return 1,
        },
        _ => return 1,
    };
    if target.sock != learned.sock {
        return 0; // execs another daemon's tool without arguments: leave it to the run
    }
    match spawn_daemon(&target, origin) {
        Ok(()) => 0,
        Err(e) => {
            eprintln!("speedrunner: cannot start {} daemon: {}", target.display(), e);
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
    s.set_write_timeout(Some(HANDSHAKE_TIMEOUT))?;
    read_hello(&s)?;
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
            match known_too_old(tool) {
                Some(why) => println!("down: {}", why),
                None => println!("down"),
            }
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

/// Until the daemon has loaded what its children reported (no `learning=`
/// in its status), for at most 30 s: a Node daemon vets modules in probes.
fn wait_learned(tool: &Tool) {
    let deadline = Instant::now() + Duration::from_secs(30);
    while Instant::now() < deadline {
        match request(tool, b'P') {
            Ok((b'P', text)) if !text.windows(10).any(|w| w == b" learning=") => return,
            _ => std::thread::sleep(Duration::from_millis(50)),
        }
    }
}

/// The interpreter of a tool's daemon, as named (argv[0]), the file to exec,
/// its options, and its runtime.
fn interpreter_of(tool: &Tool) -> io::Result<(Vec<u8>, Vec<u8>, Vec<Vec<u8>>, Runtime)> {
    Ok(match (&tool.form, &tool.target) {
        (Some(f), _) => (f.interp.clone(), f.interp.clone(), vec![], Runtime::Php),
        (None, Some(t)) => (t.launch.argv[0].clone(), t.launch.exe.clone(), t.flags.clone(),
                            Runtime::Python),
        (None, None) => {
            let (interp, flag, runtime) = read_shebang(&tool.path)?;
            (interp.clone(), interp, flag.into_iter().collect(), runtime)
        }
    })
}

/// Why the tool's interpreter cannot run its daemon, if a first start found so.
fn known_too_old(tool: &Tool) -> Option<String> {
    let (_, interp, _, runtime) = interpreter_of(tool).ok()?;
    runtime.too_old(&noted_version(&interp_identity(&interp)?)?)
}

fn spawn_daemon_with(tool: &Tool, origin: &str, tty: &str) -> io::Result<()> {
    if native_asked() {
        return Err(unsupported(&format!("{} is set", NATIVE_ENV)));
    }
    if let Some(reason) = untrusted_context() {
        return Err(unsupported(&reason));
    }
    ensure_runtime_dir(&tool.dir)?;
    let (named, interp, flags, runtime) = interpreter_of(tool)?;
    // An interpreter too old for its daemon starts nothing, once we know it.
    let identity = runtime.oldest().and_then(|_| interp_identity(&interp));
    let noted = identity.as_deref().and_then(noted_version);
    if let Some(why) = noted.as_deref().and_then(|v| runtime.too_old(v)) {
        return Err(unsupported(&why));
    }
    let probe = identity.filter(|_| noted.is_none()); // first start on it: ask
    let daemon = locate_daemon(runtime, &interp)?;
    // A bundle has no interpreter to run: the launcher, the bundle, then its first script.
    let (named, interp, flags) = if matches!(runtime, Runtime::Bundle) {
        let launcher = extract_daemon("speedrunner_pyi")?;
        std::fs::set_permissions(&launcher, std::fs::Permissions::from_mode(0o700))?;
        let boot = extract_daemon("speedrunner_pyi_boot.py")?.into_os_string().into_vec();
        let launcher = launcher.into_os_string().into_vec();
        (launcher.clone(), launcher, vec![tool.path.clone(), boot])
    } else {
        (named, interp, flags)
    };
    let lock = lock_path(tool);
    let awaited = match lock_state(&lock)? {
        LockState::Free => None,
        LockState::Exiting(holder) => Some(holder),
        LockState::Held => return Ok(()),
    };
    let mut argv = vec![cstring(&named)];
    for f in &flags {
        argv.push(cstring(f));
    }
    let lazy = matches!(runtime, Runtime::Node) && node_without_code_cache(&interp);
    for a in runtime.daemon_args() {
        if !(lazy && *a == "--no-lazy") {
            argv.push(cstring(a.as_bytes()));
        }
    }
    argv.push(cstring(&daemon));
    // For `php script`, the daemon is the script's: name it after it. A tool
    // called by its path (`srr ./bin/aws`) is named after the file: the
    // daemons build their cache files from this name.
    let tool_name = basename(if tool.form.is_some() { &tool.path } else { tool.name.as_bytes() });
    let entry = if tool.target.as_ref().map_or(false, |t| t.module.is_some()) { "--module" } else { "--script" };
    for (k, v) in [("--tool", tool_name), (entry, &tool.path[..]),
                   ("--socket", tool.sock.as_os_str().as_bytes()), ("--lock-fd", b"3"),
                   ("--origin", format!("{} pid={}", origin, process::id()).as_bytes()),
                   ("--tty", tty.as_bytes())] {
        argv.push(cstring(k.as_bytes()));
        argv.push(cstring(v));
    }
    let mut ptrs: Vec<*const libc::c_char> = argv.iter().map(|c| c.as_ptr()).collect();
    ptrs.push(ptr::null());
    let interp = cstring(&interp);
    // A launcher's tool runs with the env the launcher made: so does its daemon.
    let mut env: Option<Vec<Vec<u8>>> = tool.target.as_ref().map(|t| t.launch.env.clone());
    if cfg!(target_os = "macos") {
        let mut given = env.take().unwrap_or_else(environ);
        let extra = match runtime {
            Runtime::Python | Runtime::Bundle => platform::launch_env_var(&given).into_iter().collect(),
            Runtime::Ruby | Runtime::Node => platform::objc_fork_env(&given),
            _ => Vec::new(),
        };
        given.extend(extra);
        env = Some(given);
    }
    let envp: Option<Vec<CString>> = env.map(|env| env.iter().map(|e| cstring(e)).collect());
    let mut envp_p: Vec<*const libc::c_char> = envp.iter().flatten().map(|c| c.as_ptr()).collect();
    envp_p.push(ptr::null());
    let log = cstring(tool.sock.with_extension("log").as_os_str().as_bytes());
    let devnull = cstring(b"/dev/null");
    let polls = (LOCK_WAIT.as_millis() / 10) as u32;
    let probe_argv: Vec<CString> = std::iter::once(cstring(&named))
        .chain(runtime.version_probe().iter().map(|a| cstring(a.as_bytes()))).collect();
    let mut probe_p: Vec<*const libc::c_char> = probe_argv.iter().map(|c| c.as_ptr()).collect();
    probe_p.push(ptr::null());
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
            // First start on this interpreter: its version, noted for every
            // later client, before it is asked to compile a daemon it may not
            // parse. Under the lock: one probe however many clients race.
            if let Some(identity) = &probe {
                let envp = envp.as_ref().map(|_| &envp_p[..]);
                if let Some(version) = ask_version(&interp, &probe_p, envp) {
                    note_version(identity, &version);
                    if let Some(why) = runtime.too_old(&version) {
                        eprintln!("speedrunner[{}] {}: not started, {} runs natively",
                                  libc::getpid(), why, tool.display());
                        libc::_exit(0);
                    }
                }
            }
            if envp.is_some() {
                libc::execve(interp.as_ptr(), ptrs.as_ptr(), envp_p.as_ptr());
            } else {
                libc::execv(interp.as_ptr(), ptrs.as_ptr());
            }
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
    let (argv, cwd, fd255) = platform::parent_process(255)?;
    script_of(&argv, &cwd).or_else(|| {
        let text = std::fs::read_to_string(fd255?).ok()?;
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
    // Outside the rescan: a directory made by an older client gets it too.
    link_bash_for_precommit(&dir)?;
    if env::var_os("SPEEDRUNNER_NO_AUTOSTART").is_none() {
        // The script's tools first, in the order it first runs them.
        let mut eager: Vec<OsString> = Vec::new();
        if let Some((text, script_dir)) = script {
            let wanted = if eager_all { 1 } else { 2 };
            let counts = script::command_counts(&text, &script_dir);
            for (name, _) in counts.into_iter().filter(|(_, n)| *n >= wanted) {
                let name = OsString::from(name);
                if dir.join(&name).exists() && name != "bash" && !eager.contains(&name) {
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
    let mut launchers: Vec<Tool> = Vec::new();
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
            if !tool.is_launcher() {
                eprintln!("speedrunner: {}: cannot be preloaded ({}); left native", text, e);
                continue;
            }
            // Its daemon is the one of what it execs, which its first run
            // through the link finds out; once known, it is started here.
            if let Some(learned) = tool.learned() {
                launchers.push(learned);
            }
            good.push(name.clone());
            continue;
        }
        good.push(name.clone());
        to_start.push(tool);
    }
    if env::var_os("SPEEDRUNNER_NO_AUTOSTART").is_none() {
        start_daemons(&to_start, origin); // in the background: the script goes on
        for learned in &launchers {
            prewarm_launcher(learned, origin); // runs its preamble: a few ms each
        }
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
    link_bash_for_precommit(&dir)?;
    Ok(dir)
}

/// A `bash` link too when pre-commit has one: git runs pre-commit's hook by
/// its path, and only the hook's `#!/usr/bin/env bash` looks a program up
/// on PATH (see run_as_bash). Without pre-commit, bash is left alone.
fn link_bash_for_precommit(dir: &Path) -> io::Result<()> {
    let me = match std::fs::read_link(dir.join("pre-commit")) {
        Ok(me) => me,
        Err(_) => return Ok(()),
    };
    let link = dir.join("bash");
    if std::fs::read_link(&link).ok() != Some(me.clone()) && resolve_in_path(b"bash").is_some() {
        let _ = std::fs::remove_file(&link);
        std::os::unix::fs::symlink(&me, &link)?;
    }
    Ok(())
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
            eprintln!("{}", USAGE);
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

/// Whether the Node daemon found this interpreter built without the code
/// cache of its internals (speedrunner_daemon.mjs notes it, one line per
/// binary: real path, size, mtime): its daemon then starts without
/// --no-lazy, which would rename frames in its stacks.
fn node_without_code_cache(interp: &[u8]) -> bool {
    match (std::fs::read(cache_dir().join("node-no-code-cache")), interp_identity(interp)) {
        (Ok(noted), Some(line)) => noted.split(|b| *b == b'\n').any(|l| l == &line[..]),
        _ => false,
    }
}

/// $XDG_CACHE_HOME/speedrunner, where the daemons keep what they learned.
fn cache_dir() -> PathBuf {
    let base = match env::var_os("XDG_CACHE_HOME") {
        Some(d) if !d.is_empty() => PathBuf::from(d),
        _ => Path::new(&env::var_os("HOME").unwrap_or_default()).join(".cache"),
    };
    base.join("speedrunner")
}

/// An interpreter as the cache files name it: real path, size, mtime. An
/// upgrade in place (a venv recreated, a new Ruby) is another interpreter.
fn interp_identity(interp: &[u8]) -> Option<Vec<u8>> {
    let real = std::fs::canonicalize(OsStr::from_bytes(interp)).ok()?;
    let meta = std::fs::metadata(&real).ok()?;
    Some([real.as_os_str().as_bytes(), format!("\t{}\t{}", meta.len(), meta.mtime()).as_bytes()].concat())
}

/// The version an interpreter gave when its first daemon was about to start
/// ($XDG_CACHE_HOME/speedrunner/interpreters, one line each: identity, version).
fn noted_version(identity: &[u8]) -> Option<Vec<u8>> {
    let noted = std::fs::read(cache_dir().join("interpreters")).ok()?;
    noted.split(|b| *b == b'\n')
        .filter_map(|l| l.strip_prefix(identity)?.strip_prefix(b"\t"))
        .last().map(<[u8]>::to_vec)
}

/// One line, appended in one write: clients racing to note never mix lines.
fn note_version(identity: &[u8], version: &[u8]) {
    let dir = cache_dir();
    let _ = std::fs::DirBuilder::new().recursive(true).mode(0o700).create(&dir);
    let line = [identity, b"\t", version, b"\n"].concat();
    if let Ok(mut f) = std::fs::OpenOptions::new().append(true).create(true).mode(0o600)
        .open(dir.join("interpreters")) {
        let _ = f.write_all(&line);
    }
}

/// Run `interp` with its runtime's version probe (argv), with the env the
/// daemon would get: what it prints, if it exits with 0 within PROBE_WAIT.
/// In the grandchild: fd 3 is the startup lock, which the probe must not keep.
unsafe fn ask_version(interp: &CString, argv: &[*const libc::c_char],
                      envp: Option<&[*const libc::c_char]>) -> Option<Vec<u8>> {
    let mut fds = [0 as libc::c_int; 2];
    if libc::pipe(fds.as_mut_ptr()) != 0 {
        return None;
    }
    let pid = libc::fork();
    if pid == 0 {
        libc::dup2(fds[1], 1);
        libc::close(fds[0]);
        libc::close(fds[1]);
        libc::close(3);
        match envp {
            Some(envp) => libc::execve(interp.as_ptr(), argv.as_ptr(), envp.as_ptr()),
            None => libc::execv(interp.as_ptr(), argv.as_ptr()),
        };
        libc::_exit(127);
    }
    libc::close(fds[1]);
    let deadline = Instant::now() + PROBE_WAIT;
    let mut out = Vec::new();
    let mut whole = false;
    while pid > 0 {
        let left = deadline.saturating_duration_since(Instant::now()).as_millis() as libc::c_int;
        let mut pfd = libc::pollfd { fd: fds[0], events: libc::POLLIN, revents: 0 };
        if left == 0 || libc::poll(&mut pfd, 1, left) != 1 {
            break;
        }
        let mut buf = [0u8; 64];
        let n = libc::read(fds[0], buf.as_mut_ptr() as *mut libc::c_void, buf.len());
        if n <= 0 {
            whole = n == 0;
            break;
        }
        out.extend_from_slice(&buf[..n as usize]);
        if out.len() > 256 {
            break;
        }
    }
    libc::close(fds[0]);
    if pid < 0 {
        return None;
    }
    if !whole {
        libc::kill(pid, libc::SIGKILL);
    }
    let mut status = 0;
    libc::waitpid(pid, &mut status, 0);
    let version = trim(&out);
    let fine = whole && libc::WIFEXITED(status) && libc::WEXITSTATUS(status) == 0;
    (fine && !version.is_empty() && !version.contains(&b'\n') && !version.contains(&b'\t'))
        .then(|| version.to_vec())
}

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

/// Add tools to the list shell/speedrunner.zsh and .bash wrap and warm up. Only
/// scripts of a served runtime and launchers are accepted: a native binary
/// has nothing to gain.
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
            if !tool.is_launcher() {
                eprintln!("speedrunner: {}: cannot be preloaded ({}); left native", name, e);
                return 1;
            }
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
    /// A PyInstaller bundle (aws v2): Python, started by speedrunner_pyi.
    Bundle,
    Ruby,
    Php,
    Node,
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
        } else if name == b"nodejs" || (name.starts_with(b"node") && name[4..].iter().all(|c| c.is_ascii_digit())) {
            Some(Runtime::Node)
        } else {
            None
        }
    }

    fn name(self) -> &'static str {
        match self {
            Runtime::Python | Runtime::Bundle => "python",
            Runtime::Ruby => "ruby",
            Runtime::Php => "php",
            Runtime::Node => "node",
        }
    }

    /// The oldest version its daemon runs on, measured: older ones cannot
    /// even parse it (Python 3.8, Ruby 2.7, PHP 8.0), and on Node 18 and 20
    /// served calls hang. A bundle brings its own Python, checked by its builder.
    fn oldest(self) -> Option<(u32, u32)> {
        match self {
            Runtime::Python => Some((3, 9)),
            Runtime::Bundle => None,
            Runtime::Ruby => Some((3, 0)),
            Runtime::Php => Some((8, 1)),
            Runtime::Node => Some((22, 0)),
        }
    }

    /// Interpreter arguments that print its version, quickly: no site,
    /// gems or ini files to load (none of them changes the version).
    fn version_probe(self) -> &'static [&'static str] {
        match self {
            Runtime::Python | Runtime::Bundle => &["-S", "-c", "import sys; print('%d.%d' % sys.version_info[:2])"],
            Runtime::Ruby => &["--disable-gems", "-e", "print RUBY_VERSION"],
            Runtime::Php => &["-n", "-r", "echo PHP_VERSION;"],
            Runtime::Node => &["-e", "process.stdout.write(process.versions.node)"],
        }
    }

    /// Why an interpreter of `version` (as its probe printed it) cannot run
    /// this runtime's daemon, if it cannot. A version we cannot read: try.
    fn too_old(self, version: &[u8]) -> Option<String> {
        let oldest = self.oldest()?;
        let text = std::str::from_utf8(version).ok()?;
        let mut parts = text.split('.');
        let number = |p: Option<&str>| -> Option<u32> {
            let p = p?;
            p[..p.find(|c: char| !c.is_ascii_digit()).unwrap_or(p.len())].parse().ok()
        };
        let found = (number(parts.next())?, number(parts.next())?);
        (found < oldest).then(|| format!("{} {} is older than {}.{}, the oldest speedrunner serves",
                                         self.name(), text, oldest.0, oldest.1))
    }

    fn daemon_file(self) -> &'static str {
        match self {
            Runtime::Python | Runtime::Bundle => "speedrunner_daemon.py",
            Runtime::Ruby => "speedrunner_daemon.rb",
            Runtime::Php => "speedrunner_daemon.php",
            Runtime::Node => "speedrunner_daemon.mjs",
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
            // V8 hands no work to the worker threads a forked child lacks,
            // and compiles in the parent what children would otherwise
            // compile on each run.
            Runtime::Node => &["--single-threaded", "--v8-pool-size=1", "--no-lazy"],
            _ => &[],
        }
    }
}

/// aws v2: a PyInstaller bundle in one directory, with the library the launcher
/// loads beside it. By name for now: nothing else has been compared to its native run.
/// Linux only, where this build has a launcher.
fn is_bundle(script: &[u8]) -> bool {
    if !cfg!(target_os = "linux") || !EMBEDDED.iter().any(|(n, b)| *n == "speedrunner_pyi" && !b.is_empty()) {
        return false;
    }
    bundle_library(script).map_or(false, |lib| lib.is_file())
}

/// The library named in the archive's cookie, beside the executable, if `script` is an
/// executable called aws with such an archive at its end.
fn bundle_library(script: &[u8]) -> Option<PathBuf> {
    const COOKIE: &[u8] = b"MEI\x0c\x0b\x0a\x0b\x0e";
    let real = std::fs::canonicalize(OsStr::from_bytes(script)).ok()?;
    if real.file_name()? != "aws" {
        return None;
    }
    let mut file = File::open(&real).ok()?;
    let size = file.seek(SeekFrom::End(0)).ok()?;
    file.seek(SeekFrom::Start(size.saturating_sub(8192))).ok()?;
    let mut tail = Vec::new();
    file.read_to_end(&mut tail).ok()?;
    let at = tail.windows(COOKIE.len()).rposition(|w| w == COOKIE)?;
    let name = tail.get(at + 24..at + 88)?;
    let name = &name[..name.iter().position(|b| *b == 0).unwrap_or(name.len())];
    Some(real.parent()?.join(OsStr::from_bytes(name)))
}

/// Interpreter and optional flag from the script's shebang, as the kernel
/// would run it, and the runtime that interpreter belongs to.
fn read_shebang(script: &[u8]) -> io::Result<(Vec<u8>, Option<Vec<u8>>, Runtime)> {
    let mut buf = [0u8; 256];
    let n = File::open(OsStr::from_bytes(script))?.read(&mut buf)?;
    let head = &buf[..n];
    if !head.starts_with(b"#!") {
        if is_bundle(script) {
            return Ok((script.to_vec(), None, Runtime::Bundle));
        }
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
    let runtime = Runtime::of(&interp).ok_or_else(|| unsupported("not a Python, Ruby, PHP or Node script"))?;
    Ok((interp, flag, runtime))
}

/// A daemon file found by its place relative to our binary: `../../../server`
/// from ~/.local/bin is another account's home, /home/server. Ours or root's,
/// and its directory too, and neither writable by anyone else.
fn safe_to_run(file: &Path) -> bool {
    let euid = unsafe { libc::geteuid() };
    let safe = |path: &Path| std::fs::metadata(path).map_or(false, |meta| {
        owned_by_us_or_root(&meta, euid) && meta.mode() & 0o022 == 0
    });
    match file.canonicalize() {
        Ok(real) => safe(&real) && real.parent().map_or(false, safe),
        Err(_) => false,
    }
}

/// The runtime's daemon script: next to SPEEDRUNNER_DAEMON (a daemon of any
/// runtime, for tests), else next to this binary, in ../share/speedrunner, or
/// in the source tree's server/ when run from client/target/release; only if
/// safe_to_run. Otherwise the built-in copy.
fn locate_daemon(runtime: Runtime, interp: &[u8]) -> io::Result<Vec<u8>> {
    let file = runtime.daemon_file();
    // The Node daemon loads the addon built for its interpreter's libc.
    let addon = match runtime {
        Runtime::Node => Some(node_addon(interp)?),
        _ => None,
    };
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
        let found = cand.is_file() && addon.map_or(true, |a| dir.join(a).is_file());
        if found && (pinned || (safe_to_run(&cand) && addon.map_or(true, |a| safe_to_run(&dir.join(a))))) {
            return Ok(cand.canonicalize()?.into_os_string().into_vec());
        }
    }
    if pinned {
        return Err(io::Error::new(io::ErrorKind::NotFound, format!("{} not found", file)));
    }
    if let Some(a) = addon {
        extract_daemon(a)?;
    }
    Ok(extract_daemon(file)?.into_os_string().into_vec())
}

/// The addon file for a Node interpreter: on Linux glibc or musl, from its
/// ELF interpreter (ld-musl-*); on macOS the one there is. An error if this
/// build has none for it.
fn node_addon(interp: &[u8]) -> io::Result<&'static str> {
    let name = if cfg!(target_os = "macos") {
        "speedrunner_node-darwin.node"
    } else {
        let mut head = vec![0u8; 4096];
        let n = File::open(OsStr::from_bytes(interp))?.read(&mut head)?;
        let musl = head[..n].windows(7).any(|w| w == b"ld-musl");
        if musl { "speedrunner_node-musl.node" } else { "speedrunner_node-gnu.node" }
    };
    match EMBEDDED.iter().find(|(f, _)| *f == name) {
        Some((_, bytes)) if !bytes.is_empty() => Ok(name),
        _ => Err(unsupported("this build has no Node addon for this libc")),
    }
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
    // Created with 0700, whatever the umask: what runs is what we wrote, and
    // nobody else could have put anything there since.
    let parent = dir.parent().ok_or_else(|| unsupported("no daemons dir"))?;
    std::fs::DirBuilder::new().recursive(true).create(parent)?;
    for d in [parent, &dir] {
        if let Err(e) = std::fs::DirBuilder::new().mode(0o700).create(d) {
            if e.kind() != io::ErrorKind::AlreadyExists {
                return Err(e);
            }
        }
    }
    let euid = unsafe { libc::geteuid() };
    for d in [parent, &dir] {
        let meta = std::fs::symlink_metadata(d)?;
        if !meta.is_dir() || meta.uid() != euid {
            return Err(unsupported("the daemons directory is not ours"));
        }
        if meta.mode() & 0o022 != 0 {
            std::fs::set_permissions(d, std::fs::Permissions::from_mode(0o700))?;
        }
    }
    if std::fs::read(&path).ok().as_deref() == Some(bytes) {
        return Ok(path);
    }
    let tmp = dir.join(format!(".{}.{}", file, process::id()));
    std::fs::write(&tmp, bytes)?;
    std::fs::rename(&tmp, &path)?;
    Ok(path)
}

// --- small helpers ---------------------------------------------------------

/// `path` made absolute against the cwd, `.` components dropped: no
/// symlink resolved, so that it still names what the shell would run.
fn absolute(path: &[u8]) -> Option<Vec<u8>> {
    let path = Path::new(OsStr::from_bytes(path));
    if path.is_absolute() {
        return Some(path.as_os_str().as_bytes().to_vec());
    }
    let full: PathBuf = env::current_dir().ok()?.join(path).components().collect();
    Some(full.into_os_string().into_vec())
}

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

#[cfg(test)]
mod tests {
    use super::Runtime;

    #[test]
    fn too_old_reads_what_each_probe_prints() {
        let old = |rt: Runtime, v: &str| rt.too_old(v.as_bytes()).is_some();
        assert!(old(Runtime::Python, "3.7"));
        assert!(old(Runtime::Python, "3.8"));
        assert!(!old(Runtime::Python, "3.9"));
        assert!(!old(Runtime::Python, "3.14"));
        assert!(old(Runtime::Python, "2.7"));
        assert!(old(Runtime::Ruby, "2.6.10"));
        assert!(!old(Runtime::Ruby, "3.0.7"));
        assert!(!old(Runtime::Ruby, "3.5.0preview1"));
        assert!(old(Runtime::Php, "8.0.30"));
        assert!(!old(Runtime::Php, "8.2.29-1~deb12u1"));
        assert!(!old(Runtime::Php, "8.5.0RC2"));
        assert!(old(Runtime::Node, "20.19.5"));
        assert!(!old(Runtime::Node, "22.15.0"));
        assert!(!old(Runtime::Bundle, "2.7"));
        // Unreadable: no verdict, the daemon starts as before.
        assert!(!old(Runtime::Python, "Python"));
        assert!(!old(Runtime::Python, "3"));
        assert_eq!(Runtime::Python.too_old(b"3.7").unwrap(),
                   "python 3.7 is older than 3.9, the oldest speedrunner serves");
    }
}
