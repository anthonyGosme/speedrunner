//! Which commands a shell script will call more than once, guessed
//! statically, so that `activate` can start their daemons before the first
//! call rather than at the second one.
//!
//! Repeated: a command inside a loop (its condition included), in a
//! function called more than once, from a loop or recursively, run by
//! xargs or find -exec, or written twice. Files read with `source` or `.`
//! are followed when their path is literal. Command substitutions count in
//! the context they appear in.
//!
//! A guess, simple on purpose: a lexer that knows quotes, comments,
//! substitutions and here-documents, and a parser that only tracks command
//! positions and nesting. Whatever it cannot see (eval, a command in a
//! variable) is still caught at run time by the second-call counter. Being
//! wrong only starts a daemon too early or too late, never changes output.

use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};

const MAX_INCLUDE_DEPTH: u32 = 8;

#[derive(Debug, PartialEq)]
enum Tok {
    Word(String),
    Sep,          // newline ; & && ||
    CaseSep,      // ;; between the branches of a case
    Pipe,         // |
    Open,         // (
    Close,        // )
    Redir,        // a redirection whose target is the next word
    Sub(String),  // the text of a $(...) or `...` substitution
}

#[derive(Clone, Default)]
struct Ctx {
    in_loop: bool,
    func: Option<String>,
}

struct Occ {
    name: String,
    repeated: bool,
    func: Option<String>,
}

/// The commands `text` (a script in directory `dir`) runs more than once.
#[cfg(test)]
fn repeated_commands(text: &str, dir: &Path) -> HashSet<String> {
    command_counts(text, dir).into_iter().filter(|(_, n)| *n >= 2).map(|(c, _)| c).collect()
}

/// Every command `text` may run, with how many times: 1, or 2 for more
/// than once; in the order the script first runs them, so that daemons can
/// start in that order. A command in a function nobody calls counts 0 and
/// is left out.
pub fn command_counts(text: &str, dir: &Path) -> Vec<(String, u32)> {
    let mut occs = Vec::new();
    let mut funcs = HashSet::new();
    let mut seen = HashSet::new();
    analyze(text, dir, &Ctx::default(), &mut occs, &mut funcs, &mut seen, 0);
    let mut memo = HashMap::new();
    let mut first_calls = HashMap::new();
    let mut totals: HashMap<&str, (u32, usize)> = HashMap::new();
    for (i, occ) in occs.iter().enumerate() {
        if funcs.contains(&occ.name) {
            continue;
        }
        let w = weight(occ, &occs, &funcs, &mut memo, &mut Vec::new());
        let first = first_use(i, &occs, &mut first_calls, &mut Vec::new()).unwrap_or(usize::MAX);
        let t = totals.entry(&occ.name).or_insert((0, usize::MAX));
        *t = ((t.0 + w).min(2), t.1.min(first));
    }
    let mut out: Vec<(String, u32, usize)> = totals.into_iter().filter(|(_, (t, _))| *t >= 1)
        .map(|(n, (t, first))| (n.to_string(), t, first)).collect();
    out.sort_by(|a, b| (a.2, &a.0).cmp(&(b.2, &b.0)));
    out.into_iter().map(|(n, t, _)| (n, t)).collect()
}

/// When occurrence `i` first runs, as the index of the top-level occurrence
/// that leads to it: its own, or the first call of its function. None if
/// its function is never called.
fn first_use(i: usize, occs: &[Occ], memo: &mut HashMap<String, Option<usize>>,
             stack: &mut Vec<String>) -> Option<usize> {
    let f = match &occs[i].func {
        None => return Some(i),
        Some(f) => f.clone(),
    };
    if let Some(first) = memo.get(&f) {
        return *first;
    }
    if stack.contains(&f) {
        return None; // recursion: the outer call decides
    }
    stack.push(f.clone());
    let mut first = None;
    for j in (0..occs.len()).filter(|&j| occs[j].name == f) {
        if let Some(k) = first_use(j, occs, memo, stack) {
            first = Some(first.map_or(k, |m: usize| m.min(k)));
        }
    }
    stack.pop();
    memo.insert(f, first);
    first
}

/// How many times an occurrence runs, capped at 2.
fn weight(occ: &Occ, occs: &[Occ], funcs: &HashSet<String>, memo: &mut HashMap<String, u32>,
          stack: &mut Vec<String>) -> u32 {
    if occ.repeated {
        return 2;
    }
    match &occ.func {
        None => 1,
        Some(f) => calls(f, occs, funcs, memo, stack),
    }
}

/// How many times a function is called, capped at 2; recursion counts as 2.
fn calls(f: &str, occs: &[Occ], funcs: &HashSet<String>, memo: &mut HashMap<String, u32>,
         stack: &mut Vec<String>) -> u32 {
    if let Some(n) = memo.get(f) {
        return *n;
    }
    if stack.iter().any(|s| s == f) {
        return 2;
    }
    stack.push(f.to_string());
    let mut n = 0;
    for occ in occs.iter().filter(|o| o.name == f) {
        n = (n + weight(occ, occs, funcs, memo, stack)).min(2);
    }
    stack.pop();
    memo.insert(f.to_string(), n);
    n
}

enum Frame {
    Loop,
    Func(String),
    Group,
}

fn analyze(text: &str, dir: &Path, base: &Ctx, occs: &mut Vec<Occ>, funcs: &mut HashSet<String>,
           seen: &mut HashSet<PathBuf>, depth: u32) {
    let toks = lex(text);
    let mut stack: Vec<Frame> = Vec::new();
    let ctx = |stack: &[Frame]| Ctx {
        in_loop: base.in_loop || stack.iter().any(|f| matches!(f, Frame::Loop)),
        func: stack.iter().rev().find_map(|f| match f {
            Frame::Func(n) => Some(n.clone()),
            _ => None,
        }).or_else(|| base.func.clone()),
    };
    let mut expect_cmd = true;
    let mut skip_until: Option<&str> = None; // after for/select: "do"; after case: "in"
    let mut in_case_pattern = false;
    let mut pending_func: Option<String> = None;
    let mut wrapper = false; // exec, env, nohup...: the next plain word is the command
    let mut repeat_next = false; // xargs: its command runs many times
    let mut include_next = false; // source / .
    let mut find_exec = false; // inside a find command
    let mut redir_target = false;
    let mut i = 0;
    while i < toks.len() {
        let tok = &toks[i];
        i += 1;
        if redir_target {
            redir_target = false;
            if matches!(tok, Tok::Word(_)) {
                continue;
            }
        }
        match tok {
            Tok::Sub(inner) => {
                analyze(inner, dir, &ctx(&stack), occs, funcs, seen, depth);
                continue;
            }
            Tok::Redir => {
                redir_target = true;
                continue;
            }
            Tok::CaseSep => {
                in_case_pattern = true; // the next branch's pattern
                expect_cmd = false;
                continue;
            }
            Tok::Sep | Tok::Pipe => {
                expect_cmd = !in_case_pattern;
                wrapper = false;
                repeat_next = false;
                include_next = false;
                find_exec = false;
                if let Some(until) = skip_until {
                    if until == "do" {
                        continue; // `for x in a b; do`
                    }
                }
                continue;
            }
            Tok::Open => {
                if expect_cmd {
                    stack.push(Frame::Group); // subshell
                }
                continue;
            }
            Tok::Close => {
                if in_case_pattern {
                    in_case_pattern = false;
                    expect_cmd = true;
                } else if matches!(stack.last(), Some(Frame::Group)) {
                    stack.pop();
                }
                continue;
            }
            Tok::Word(_) => {}
        }
        let w = match tok {
            Tok::Word(w) => w.as_str(),
            _ => unreachable!(),
        };
        if let Some(until) = skip_until {
            if w == until {
                skip_until = None;
                expect_cmd = until == "do";
                in_case_pattern = until == "in";
            }
            continue;
        }
        if include_next {
            include_next = false;
            if depth < MAX_INCLUDE_DEPTH && !w.contains('$') && !w.is_empty() {
                let path = if Path::new(w).is_absolute() { PathBuf::from(w) } else { dir.join(w) };
                if let Ok(canon) = path.canonicalize() {
                    if seen.insert(canon.clone()) {
                        if let Ok(inner) = std::fs::read_to_string(&canon) {
                            let inner_dir = canon.parent().unwrap_or(dir).to_path_buf();
                            analyze(&inner, &inner_dir, &ctx(&stack), occs, funcs, seen, depth + 1);
                        }
                    }
                }
            }
            continue;
        }
        if find_exec {
            if matches!(w, "-exec" | "-execdir" | "-ok" | "-okdir") {
                expect_cmd = true;
                repeat_next = true;
                find_exec = false;
            }
            continue;
        }
        if in_case_pattern {
            if w == "esac" {
                in_case_pattern = false;
                expect_cmd = false;
            }
            continue; // `pattern)` up to the ")"
        }
        if !expect_cmd {
            continue;
        }
        if wrapper && (w.starts_with('-') || is_assignment(w)) {
            continue;
        }
        if is_assignment(w) {
            continue; // VAR=value before the command
        }
        // A function definition: `name () {` or `function name {`.
        if toks.get(i) == Some(&Tok::Open) && toks.get(i + 1) == Some(&Tok::Close) {
            pending_func = Some(w.to_string());
            funcs.insert(w.to_string());
            i += 2;
            continue;
        }
        match w {
            "function" => {
                if let Some(Tok::Word(name)) = toks.get(i) {
                    pending_func = Some(name.clone());
                    funcs.insert(name.clone());
                    i += 1;
                    if toks.get(i) == Some(&Tok::Open) && toks.get(i + 1) == Some(&Tok::Close) {
                        i += 2;
                    }
                }
                continue;
            }
            "{" => {
                stack.push(match pending_func.take() {
                    Some(name) => Frame::Func(name),
                    None => Frame::Group,
                });
                continue;
            }
            "}" => {
                if matches!(stack.last(), Some(Frame::Func(_)) | Some(Frame::Group)) {
                    stack.pop();
                }
                expect_cmd = false;
                continue;
            }
            "for" | "select" => {
                stack.push(Frame::Loop);
                skip_until = Some("do");
                continue;
            }
            "while" | "until" => {
                stack.push(Frame::Loop); // the condition runs each time too
                continue;
            }
            "done" => {
                if matches!(stack.last(), Some(Frame::Loop)) {
                    stack.pop();
                }
                expect_cmd = false;
                continue;
            }
            "case" => {
                skip_until = Some("in");
                continue;
            }
            "esac" | "fi" => {
                expect_cmd = false;
                continue;
            }
            "do" | "then" | "else" | "elif" | "if" | "!" | "time" => continue,
            "exec" | "command" | "nohup" | "nice" | "env" | "srr" | "builtin" => {
                wrapper = true;
                continue;
            }
            "xargs" | "parallel" => {
                wrapper = true;
                repeat_next = true;
                continue;
            }
            "find" => {
                find_exec = true;
                expect_cmd = false;
                continue;
            }
            "source" | "." => {
                include_next = true;
                expect_cmd = false;
                continue;
            }
            _ => {}
        }
        // A path bypasses PATH, hence our links: nothing to start for it.
        if !w.contains('/') && !w.is_empty() {
            let c = ctx(&stack);
            occs.push(Occ { name: w.to_string(), repeated: repeat_next || c.in_loop, func: c.func });
        }
        expect_cmd = false;
        wrapper = false;
        repeat_next = false;
    }
}

fn is_assignment(w: &str) -> bool {
    match w.find('=') {
        Some(eq) if eq > 0 => {
            let name = &w[..eq].trim_end_matches('+');
            name.chars().next().map_or(false, |c| c.is_ascii_alphabetic() || c == '_')
                && name.chars().all(|c| c.is_ascii_alphanumeric() || c == '_')
        }
        _ => false,
    }
}

/// Shell tokens, enough to find command positions.
fn lex(text: &str) -> Vec<Tok> {
    let chars: Vec<char> = text.chars().collect();
    let mut toks = Vec::new();
    let mut word = String::new();
    let mut in_word = false;
    let mut heredocs: Vec<(String, bool)> = Vec::new(); // delimiter, strip tabs
    let mut i = 0;
    macro_rules! end_word {
        () => {
            if in_word {
                toks.push(Tok::Word(std::mem::take(&mut word)));
                in_word = false;
            }
        };
    }
    while i < chars.len() {
        let c = chars[i];
        match c {
            ' ' | '\t' => {
                end_word!();
                i += 1;
            }
            '\n' => {
                end_word!();
                toks.push(Tok::Sep);
                i += 1;
                // Here-document bodies are data, not commands.
                for (delim, strip) in std::mem::take(&mut heredocs) {
                    while i < chars.len() {
                        let end = chars[i..].iter().position(|&c| c == '\n').map_or(chars.len(), |p| i + p);
                        let line: String = chars[i..end].iter().collect();
                        i = (end + 1).min(chars.len());
                        let line = if strip { line.trim_start_matches('\t').to_string() } else { line };
                        if line == delim {
                            break;
                        }
                    }
                }
            }
            '#' if !in_word => {
                while i < chars.len() && chars[i] != '\n' {
                    i += 1;
                }
            }
            '\\' => {
                if i + 1 < chars.len() && chars[i + 1] == '\n' {
                    i += 2; // line continuation
                } else {
                    in_word = true;
                    if i + 1 < chars.len() {
                        word.push(chars[i + 1]);
                    }
                    i += 2;
                }
            }
            '\'' => {
                in_word = true;
                i += 1;
                while i < chars.len() && chars[i] != '\'' {
                    word.push(chars[i]);
                    i += 1;
                }
                i += 1;
            }
            '"' => {
                in_word = true;
                i += 1;
                while i < chars.len() && chars[i] != '"' {
                    if chars[i] == '\\' && i + 1 < chars.len() {
                        word.push(chars[i + 1]);
                        i += 2;
                    } else if chars[i] == '$' && chars.get(i + 1) == Some(&'(') {
                        let (inner, next) = substitution(&chars, i);
                        if let Some(inner) = inner {
                            toks.push(Tok::Sub(inner));
                        }
                        i = next;
                    } else if chars[i] == '`' {
                        let (inner, next) = backtick(&chars, i);
                        toks.push(Tok::Sub(inner));
                        i = next;
                    } else {
                        word.push(chars[i]);
                        i += 1;
                    }
                }
                i += 1;
            }
            '`' => {
                in_word = true;
                let (inner, next) = backtick(&chars, i);
                toks.push(Tok::Sub(inner));
                i = next;
            }
            '$' if chars.get(i + 1) == Some(&'(') => {
                in_word = true;
                let (inner, next) = substitution(&chars, i);
                if let Some(inner) = inner {
                    toks.push(Tok::Sub(inner));
                }
                i = next;
            }
            '$' if chars.get(i + 1) == Some(&'{') => {
                in_word = true;
                let mut depth = 0;
                while i < chars.len() {
                    if chars[i] == '{' {
                        depth += 1;
                    } else if chars[i] == '}' {
                        depth -= 1;
                        if depth == 0 {
                            i += 1;
                            break;
                        }
                    }
                    word.push(chars[i]);
                    i += 1;
                }
            }
            ';' | '&' | '|' => {
                end_word!();
                let doubled = chars.get(i + 1) == Some(&c);
                if c == '|' && !doubled {
                    toks.push(Tok::Pipe);
                    i += if chars.get(i + 1) == Some(&'&') { 2 } else { 1 };
                } else if c == '&' && chars.get(i + 1) == Some(&'>') {
                    toks.push(Tok::Redir); // &> file
                    i += if chars.get(i + 2) == Some(&'>') { 3 } else { 2 };
                } else {
                    toks.push(if c == ';' && doubled { Tok::CaseSep } else { Tok::Sep });
                    i += if doubled { 2 } else { 1 };
                }
            }
            '(' | ')' => {
                end_word!();
                toks.push(if c == '(' { Tok::Open } else { Tok::Close });
                i += 1;
            }
            '<' | '>' => {
                // Digits just before belong to the redirection (2>&1).
                if in_word && word.chars().all(|c| c.is_ascii_digit()) {
                    word.clear();
                    in_word = false;
                } else {
                    end_word!();
                }
                if c == '<' && chars.get(i + 1) == Some(&'<') && chars.get(i + 2) != Some(&'<') {
                    // Here-document: the delimiter is the next word.
                    let strip = chars.get(i + 2) == Some(&'-');
                    i += if strip { 3 } else { 2 };
                    while i < chars.len() && (chars[i] == ' ' || chars[i] == '\t') {
                        i += 1;
                    }
                    let mut delim = String::new();
                    while i < chars.len() && !" \t\n;&|<>()".contains(chars[i]) {
                        if chars[i] != '\'' && chars[i] != '"' && chars[i] != '\\' {
                            delim.push(chars[i]);
                        }
                        i += 1;
                    }
                    heredocs.push((delim, strip));
                    continue;
                }
                while i < chars.len() && (chars[i] == '<' || chars[i] == '>') {
                    i += 1;
                }
                if chars.get(i) == Some(&'&') {
                    i += 1; // >&2
                    while i < chars.len() && (chars[i].is_ascii_digit() || chars[i] == '-') {
                        i += 1;
                    }
                    continue;
                }
                if chars.get(i) == Some(&'(') {
                    continue; // process substitution <(...): lexed as a group
                }
                toks.push(Tok::Redir);
            }
            _ => {
                in_word = true;
                word.push(c);
                i += 1;
            }
        }
    }
    if in_word {
        toks.push(Tok::Word(word));
    }
    toks
}

/// `$(...)` at chars[start]: its text and the index after it; None for an
/// arithmetic `$((...))`.
fn substitution(chars: &[char], start: usize) -> (Option<String>, usize) {
    let arithmetic = chars.get(start + 2) == Some(&'(');
    let mut depth = 0;
    let mut i = start + 1;
    let mut quote: Option<char> = None;
    while i < chars.len() {
        let c = chars[i];
        match quote {
            Some(q) => {
                if c == '\\' && q == '"' {
                    i += 1;
                } else if c == q {
                    quote = None;
                }
            }
            None => match c {
                '\'' | '"' => quote = Some(c),
                '\\' => i += 1,
                '(' => depth += 1,
                ')' => {
                    depth -= 1;
                    if depth == 0 {
                        let inner: String = chars[start + 2..i].iter().collect();
                        return (if arithmetic { None } else { Some(inner) }, i + 1);
                    }
                }
                _ => {}
            },
        }
        i += 1;
    }
    (None, chars.len())
}

fn backtick(chars: &[char], start: usize) -> (String, usize) {
    let mut i = start + 1;
    let mut inner = String::new();
    while i < chars.len() && chars[i] != '`' {
        if chars[i] == '\\' && i + 1 < chars.len() {
            i += 1;
        }
        inner.push(chars[i]);
        i += 1;
    }
    (inner, i + 1)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn rep(script: &str) -> Vec<String> {
        let mut v: Vec<String> = repeated_commands(script, Path::new("/nonexistent")).into_iter().collect();
        v.sort();
        v
    }

    #[test]
    fn counts_include_single_calls() {
        let counts: HashMap<String, u32> =
            command_counts("aws a\nfor i in 1; do pip x; done\nf() { gem; }\n", Path::new("/")).into_iter().collect();
        assert_eq!(counts.get("aws"), Some(&1));
        assert_eq!(counts.get("pip"), Some(&2));
        assert_eq!(counts.get("gem"), None); // in a function never called
    }

    #[test]
    fn in_the_order_first_run() {
        // rubocop is written first, but runs when f is first called, after pip.
        let script = "f() { rubocop x; }\naws a\nfor i in 1 2; do pip x; done\nf\nblack y\nf\naws b\n";
        let names: Vec<String> = command_counts(script, Path::new("/")).into_iter().map(|(n, _)| n).collect();
        assert_eq!(names, ["aws", "pip", "rubocop", "black"]);
    }

    #[test]
    fn single_calls_are_not_repeated() {
        assert_eq!(rep("aws s3 ls\nrubocop file.rb | grep x\n"), Vec::<String>::new());
    }

    #[test]
    fn written_twice() {
        assert_eq!(rep("aws s3 ls; aws s3 cp a b\n"), ["aws"]);
    }

    #[test]
    fn loops_and_their_conditions() {
        assert_eq!(rep("for f in a b; do aws s3 cp $f x; done"), ["aws"]);
        assert_eq!(rep("while pip check; do sleep 1; done"), ["pip", "sleep"]);
        assert_eq!(rep("for aws in x; do echo; done"), ["echo"]);
        assert_eq!(rep("until\n  rake test\ndo :\ndone"), [":", "rake"]);
    }

    #[test]
    fn functions_count_their_calls() {
        assert_eq!(rep("f() { aws s3 ls; }\nf\n"), Vec::<String>::new());
        assert_eq!(rep("f() { aws s3 ls; }\nf\nf\n"), ["aws"]);
        assert_eq!(rep("function g { rake; }\nfor i in 1; do g; done\n"), ["rake"]);
        assert_eq!(rep("h() {\n  pip list\n  h\n}\nh\n"), ["pip"]); // recursive
        assert_eq!(rep("a() { gem list; }\nb() { a; }\nb; b\n"), ["gem"]);
        assert_eq!(rep("unused() { aws s3 ls; }\n"), Vec::<String>::new());
    }

    #[test]
    fn xargs_and_find_exec() {
        assert_eq!(rep("ls | xargs -n1 aws s3 rm"), ["aws"]);
        assert_eq!(rep("find . -name '*.rb' -exec rubocop {} \\;"), ["rubocop"]);
    }

    #[test]
    fn wrappers_assignments_and_paths() {
        assert_eq!(rep("A=1 B=2 exec env C=3 aws x; command aws y"), ["aws"]);
        assert_eq!(rep("/usr/bin/aws a; /usr/bin/aws b"), Vec::<String>::new());
        assert_eq!(rep("srr aws a; srr aws b"), ["aws"]);
    }

    #[test]
    fn substitutions_quotes_comments_heredocs() {
        assert_eq!(rep("for i in 1; do x=$(aws sts get-caller-identity); done"), ["aws"]);
        assert_eq!(rep("echo \"$(pip --version) `gem -v`\"; echo"), ["echo"]);
        assert_eq!(rep("# aws\n# aws\necho 'aws; aws'\n"), Vec::<String>::new());
        assert_eq!(rep("cat <<EOF\naws\naws\nEOF\naws x\n"), Vec::<String>::new());
        assert_eq!(rep("echo $((1 + 2)); echo ${x:-aws}; x=1"), ["echo"]);
        assert_eq!(rep("aws a 2>&1 >/dev/null; aws b > out"), ["aws"]);
    }

    #[test]
    fn case_and_if() {
        assert_eq!(rep("case $1 in a) aws x ;; b) aws y ;; esac"), ["aws"]);
        assert_eq!(rep("if aws a; then echo; else echo; fi"), ["echo"]);
    }

    #[test]
    fn sourced_files() {
        let dir = std::env::temp_dir().join(format!("srscript-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join("lib.sh"), "for i in 1; do pip list; done\n").unwrap();
        std::fs::write(dir.join("loop.sh"), ". ./loop.sh\nrake\n").unwrap(); // includes itself
        let mut got: Vec<String> = repeated_commands(". ./lib.sh\nsource loop.sh\n", &dir).into_iter().collect();
        got.sort();
        assert_eq!(got, ["pip"]);
        std::fs::remove_dir_all(&dir).unwrap();
    }
}
