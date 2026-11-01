#!/bin/sh
# Builds the macOS client as one universal binary (x86_64 and arm64) at
# $1, for macOS 11 and later: release.yml publishes it, macos.yml tests it.
# $1 ends in speedrunner: under another name, the client serves that tool.
# Runs on either architecture: Apple's toolchain builds both.
set -eu

out=$1
here=$(cd "$(dirname "$0")" && pwd)
# rustc and cc (the Node addon, build.rs) both read it.
export MACOSX_DEPLOYMENT_TARGET="${MACOSX_DEPLOYMENT_TARGET:-11.0}"

for target in x86_64-apple-darwin aarch64-apple-darwin; do
    rustup target add "$target" > /dev/null
    cargo build --release --locked --manifest-path "$here/Cargo.toml" --target "$target"
done
mkdir -p "$(dirname "$out")"
lipo -create -output "$out" \
    "$here/target/x86_64-apple-darwin/release/speedrunner" \
    "$here/target/aarch64-apple-darwin/release/speedrunner"

lipo "$out" -verify_arch x86_64 arm64
# Apple Silicon runs only signed code: the linker signs arm64 ad hoc.
codesign --verify --arch arm64 "$out"
for arch in x86_64 arm64; do
    minos=$(otool -arch "$arch" -l "$out" | awk '/LC_BUILD_VERSION/ { b = 1 } b && $1 == "minos" { print $2; exit }')
    [ "$minos" = "$MACOSX_DEPLOYMENT_TARGET" ] || { echo "$arch: minos $minos" >&2; exit 1; }
done
"$out" --version
