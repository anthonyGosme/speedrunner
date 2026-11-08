//! `uv run <command> ...`: uv prepares the project's environment, then runs
//! the command. Put in front of uv, speedrunner only sees uv, a native
//! binary, and the command runs natively behind it. So the call becomes
//! `uv run ... <speedrunner> run <command> -- ...`: uv does everything it
//! would have done (sync, environment variables, PATH), then starts the
//! client in the command's place, which serves the command PATH now names.
//!
//! This module only reads the arguments. Whether the command is a script a
//! daemon serves is decided by the caller, which sees the filesystem.

/// A `uv run` call speedrunner can step into.
#[derive(Debug, PartialEq)]
pub struct Run {
    /// Index, in the arguments after `uv`, of the command uv will run.
    pub command: usize,
    /// `--no-project`: uv does not use the project's environment.
    pub no_project: bool,
}

/// Options of `uv run` and uv's global ones that take a value, as uv 0.13
/// documents them. Anything not listed here or in FLAGS leaves the call
/// alone: an option whose arity is unknown could hide the command.
const VALUED: &[&str] = &[
    "allow-insecure-host", "trusted-host", "cache-dir", "color", "config-file",
    "config-setting", "config-settings", "config-settings-package", "default-index",
    "env-file", "exclude-newer", "exclude-newer-package", "extra", "extra-index-url",
    "find-links", "fork-strategy", "group", "index", "index-strategy", "index-url",
    "keyring-provider", "link-mode", "no-binary-package", "no-build-isolation-package",
    "no-build-package", "no-editable-package", "no-extra", "no-group",
    "no-sources-package", "only-group", "package", "prerelease", "prerelease-package",
    "python", "python-platform", "refresh-package", "reinstall-package", "resolution",
    "upgrade-group", "upgrade-package", "with", "with-editable", "with-requirements",
];

const FLAGS: &[&str] = &[
    "active", "all-extras", "all-groups", "all-packages", "compile-bytecode", "compile",
    "exact", "frozen", "isolated", "locked", "managed-python", "no-binary", "no-build",
    "no-build-isolation", "no-cache", "no-cache-dir", "no-config", "no-default-groups",
    "no-dev", "no-editable", "no-env-file", "no-index", "no-managed-python", "no-progress",
    "no-python-downloads", "no-require-build-hashes", "no-sources", "no-sync", "offline",
    "only-dev", "quiet", "refresh", "reinstall", "force-reinstall",
    "require-build-hashes", "system-certs", "upgrade", "verbose",
    "no-project", "no_workspace", "native-tls", "preview", "no-preview",
];

/// Short options that take a value, and those that do not.
const SHORT_VALUED: &[u8] = b"pCfiPw";
const SHORT_FLAGS: &[u8] = b"qvnU";

/// Where the command starts in `args` (what follows `uv`), or why the call
/// is left to uv as it is.
pub fn parse_run(args: &[&[u8]]) -> Result<Run, &'static str> {
    let mut i = 0;
    let mut seen_run = false;
    let mut no_project = false;
    while i < args.len() {
        let a = args[i];
        if a == b"--" {
            if !seen_run {
                return Err("not `uv run`");
            }
            i += 1;
            break;
        }
        if let Some(long) = a.strip_prefix(b"--") {
            let (name, inline) = match long.iter().position(|b| *b == b'=') {
                Some(eq) => (&long[..eq], true),
                None => (long, false),
            };
            let name = std::str::from_utf8(name).map_err(|_| "an option uv run does not document")?;
            match name {
                // Elsewhere than the cwd: the project, and so its environment, too.
                "directory" | "project" => return Err("--directory or --project"),
                // What runs is not a command looked up on PATH.
                "module" | "script" | "gui-script" => return Err("not a command"),
                _ => {}
            }
            if VALUED.contains(&name) {
                i += if inline { 1 } else { 2 };
            } else if FLAGS.contains(&name) && !inline {
                no_project |= name == "no-project" || name == "no_workspace";
                i += 1;
            } else {
                return Err("an option uv run does not document");
            }
            continue;
        }
        if a.len() > 1 && a[0] == b'-' {
            // A cluster: flags, then at most one option whose value is the
            // rest of the word, or the next word.
            let mut used = 1;
            for (k, c) in a[1..].iter().enumerate() {
                if SHORT_FLAGS.contains(c) {
                    continue;
                }
                if SHORT_VALUED.contains(c) {
                    if k + 2 == a.len() {
                        used = 2; // the value is the next word
                    }
                    break;
                }
                return Err(if *c == b'm' || *c == b's' { "not a command" }
                           else { "an option uv run does not document" });
            }
            i += used;
            continue;
        }
        if !seen_run {
            if a != b"run" {
                return Err("not `uv run`");
            }
            seen_run = true;
            i += 1;
            continue;
        }
        break; // the command
    }
    if !seen_run {
        return Err("not `uv run`");
    }
    let command = match args.get(i) {
        Some(c) => *c,
        None => return Err("no command"),
    };
    // uv runs these with Python itself, or reads them from stdin or the web.
    let script = command == b"-" || command.contains(&b'/')
        || command.ends_with(b".py") || command.ends_with(b".pyw")
        || command.starts_with(b"-") || command.is_empty();
    if script {
        return Err("not a command");
    }
    Ok(Run { command: i, no_project })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn parse(line: &str) -> Result<Run, &'static str> {
        let words: Vec<&[u8]> = line.split(' ').filter(|w| !w.is_empty()).map(str::as_bytes).collect();
        parse_run(&words)
    }

    fn command(line: &str) -> Option<usize> {
        parse(line).ok().map(|r| r.command)
    }

    #[test]
    fn finds_the_command() {
        assert_eq!(command("run black --version"), Some(1));
        assert_eq!(command("run --frozen black"), Some(2));
        assert_eq!(command("-q run black"), Some(2));
        assert_eq!(command("run --python 3.12 black ."), Some(3));
        assert_eq!(command("run --python=3.12 black"), Some(2));
        assert_eq!(command("run -p 3.12 black"), Some(3));
        assert_eq!(command("run -p3.12 black"), Some(2));
        assert_eq!(command("run -qv black"), Some(2));
        assert_eq!(command("run -qp 3.12 black"), Some(3));
        assert_eq!(command("run --with httpie --extra dev http"), Some(5));
        assert_eq!(command("run --python 3.12 -- black --check"), Some(4));
        assert_eq!(command("--color never run black"), Some(3));
    }

    #[test]
    fn what_follows_the_command_is_the_tool_s() {
        // `--directory` after the command is black's, not uv's.
        assert_eq!(command("run black --directory x -m y"), Some(1));
    }

    #[test]
    fn no_project_is_noted() {
        assert_eq!(parse("run --no-project black"), Ok(Run { command: 2, no_project: true }));
        assert_eq!(parse("run black"), Ok(Run { command: 1, no_project: false }));
    }

    #[test]
    fn leaves_other_calls_alone() {
        assert_eq!(parse("sync"), Err("not `uv run`"));
        assert_eq!(parse("pip install black"), Err("not `uv run`"));
        assert_eq!(parse("tool run black"), Err("not `uv run`"));
        assert_eq!(parse("run"), Err("no command"));
        assert_eq!(parse("run --frozen"), Err("no command"));
        assert_eq!(parse("run script.py"), Err("not a command"));
        assert_eq!(parse("run ./bin/tool"), Err("not a command"));
        assert_eq!(parse("run https://example.com/x.py"), Err("not a command"));
        assert_eq!(parse("run -"), Err("not a command"));
        assert_eq!(parse("run -m http.server"), Err("not a command"));
        assert_eq!(parse("run --module http.server"), Err("not a command"));
        assert_eq!(parse("run --script tool"), Err("not a command"));
        assert_eq!(parse("run -qm x"), Err("not a command"));
        assert_eq!(parse("run --directory sub black"), Err("--directory or --project"));
        assert_eq!(parse("--project sub run black"), Err("--directory or --project"));
        assert_eq!(parse("run --some-new-option black"), Err("an option uv run does not document"));
        assert_eq!(parse("run --frozen=yes black"), Err("an option uv run does not document"));
        assert_eq!(parse("run -x black"), Err("an option uv run does not document"));
        assert_eq!(parse("-- run black"), Err("not `uv run`"));
    }
}
