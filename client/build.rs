//! Embeds builds of the Node addon (server/speedrunner_node.c), which the
//! daemon of Node tools loads: one per libc, as Node itself is built against
//! glibc or musl. Taken from SPEEDRUNNER_NODE_ADDONS, a directory holding
//! speedrunner_node-gnu.node and speedrunner_node-musl.node (CI builds each
//! on its own libc), else compiled here with cc (or NODE_ADDON_CC) for the
//! host's libc only. A missing one is embedded empty: Node tools of that
//! libc then run natively.
use std::env;
use std::fs;
use std::path::Path;
use std::process::Command;

fn main() {
    let out = env::var("OUT_DIR").unwrap();
    let src = "../server/speedrunner_node.c";
    println!("cargo:rerun-if-changed={}", src);
    println!("cargo:rerun-if-env-changed=SPEEDRUNNER_NODE_ADDONS");
    println!("cargo:rerun-if-env-changed=NODE_ADDON_CC");
    let given = env::var_os("SPEEDRUNNER_NODE_ADDONS");
    let musl_host = fs::read_dir("/lib").map_or(false, |d| {
        d.flatten().any(|e| e.file_name().to_string_lossy().starts_with("ld-musl"))
    });
    for libc in ["gnu", "musl"] {
        let name = format!("speedrunner_node-{}.node", libc);
        let dest = Path::new(&out).join(&name);
        let _ = fs::remove_file(&dest);
        let built = match &given {
            Some(dir) => {
                let from = Path::new(dir).join(&name);
                println!("cargo:rerun-if-changed={}", from.display());
                fs::copy(&from, &dest).is_ok()
            }
            None if (libc == "musl") == musl_host => {
                let cc = env::var("NODE_ADDON_CC").unwrap_or_else(|_| "cc".to_string());
                let ok = Command::new(&cc)
                    .args(["-O2", "-shared", "-fPIC", "-o"])
                    .arg(&dest)
                    .arg(src)
                    .status()
                    .map_or(false, |s| s.success());
                if !ok {
                    println!("cargo:warning=no Node addon ({} failed): Node tools will run natively", cc);
                }
                ok
            }
            None => false,
        };
        if !built {
            fs::write(&dest, b"").unwrap();
        }
    }
}
