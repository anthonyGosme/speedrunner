//! Embeds builds of the Node addon (server/speedrunner_node.c), which the
//! daemon of Node tools loads: on Linux one per libc, as Node itself is
//! built against glibc or musl; on macOS one, for the target's architecture. Taken from SPEEDRUNNER_NODE_ADDONS, a directory holding
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
    // cc reads it, as rustc does: the addon runs wherever the client does.
    println!("cargo:rerun-if-env-changed=MACOSX_DEPLOYMENT_TARGET");
    let given = env::var_os("SPEEDRUNNER_NODE_ADDONS");
    let os = env::var("CARGO_CFG_TARGET_OS").unwrap_or_default();
    let linux = os == "linux";
    let musl_host = fs::read_dir("/lib").map_or(false, |d| {
        d.flatten().any(|e| e.file_name().to_string_lossy().starts_with("ld-musl"))
    });
    let darwin = Path::new(&out).join("speedrunner_node-darwin.node");
    let _ = fs::remove_file(&darwin);
    // Node-API's functions come from the node binary at load time.
    let arch = if env::var("CARGO_CFG_TARGET_ARCH").as_deref() == Ok("aarch64") { "arm64" } else { "x86_64" };
    let built = os == "macos" && Command::new(env::var("NODE_ADDON_CC").unwrap_or_else(|_| "cc".to_string()))
        .args(["-O2", "-shared", "-fPIC", "-undefined", "dynamic_lookup", "-arch", arch, "-o"])
        .arg(&darwin)
        .arg(src)
        .status()
        .map_or(false, |s| s.success());
    if os == "macos" && !built {
        println!("cargo:warning=no Node addon (cc failed): Node tools will run natively");
    }
    if !built {
        fs::write(&darwin, b"").unwrap();
    }
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
            None if linux && (libc == "musl") == musl_host => {
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
