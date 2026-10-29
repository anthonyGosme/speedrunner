#!/bin/sh
# Install speedrunner, for the current user:
#
#   curl -fsSL https://raw.githubusercontent.com/anthonyGosme/speedrunner/main/install.sh | sh
#
# Downloads the static Linux binary of the latest release for this machine,
# checks its SHA-256 sum, installs it as ~/.local/bin/speedrunner with the
# `srr` link next to it, and prints what to add to ~/.zshrc. Needs no root.
#
# Env: SPEEDRUNNER_VERSION (a tag, default the latest release),
#      SPEEDRUNNER_BIN_DIR (default ~/.local/bin),
#      SPEEDRUNNER_BASE_URL (where the release files are, instead of GitHub).
set -eu

repo=anthonyGosme/speedrunner
version=${SPEEDRUNNER_VERSION:-latest}
bin_dir=${SPEEDRUNNER_BIN_DIR:-$HOME/.local/bin}

say() { printf 'speedrunner: %s\n' "$*"; }
die() { say "$*" >&2; exit 1; }

case $(uname -s) in
    Linux) ;;
    *) die "$(uname -s) is not supported yet: Linux only for now" ;;
esac
case $(uname -m) in
    x86_64 | amd64) arch=x86_64 ;;
    aarch64 | arm64) arch=aarch64 ;;
    *) die "no build for $(uname -m): x86_64 and aarch64 only" ;;
esac
asset=speedrunner-$arch-unknown-linux-musl.tar.gz

if [ -n "${SPEEDRUNNER_BASE_URL:-}" ]; then
    base=$SPEEDRUNNER_BASE_URL
elif [ "$version" = latest ]; then
    base=https://github.com/$repo/releases/latest/download
else
    base=https://github.com/$repo/releases/download/$version
fi

if command -v curl > /dev/null 2>&1; then
    fetch() { curl -fsSL -o "$2" "$1"; }
elif command -v wget > /dev/null 2>&1; then
    fetch() { wget -q -O "$2" "$1"; }
else
    die "needs curl or wget"
fi
if command -v sha256sum > /dev/null 2>&1; then
    sha256() { sha256sum "$1" | cut -d ' ' -f 1; }
elif command -v shasum > /dev/null 2>&1; then
    sha256() { shasum -a 256 "$1" | cut -d ' ' -f 1; }
else
    die "needs sha256sum or shasum to check the download"
fi

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

say "downloading $asset ($version)"
fetch "$base/$asset" "$tmp/$asset" || die "cannot download $base/$asset"
fetch "$base/$asset.sha256" "$tmp/$asset.sha256" || die "cannot download its checksum"
expected=$(cut -d ' ' -f 1 < "$tmp/$asset.sha256")
actual=$(sha256 "$tmp/$asset")
[ -n "$expected" ] && [ "$expected" = "$actual" ] || die "checksum mismatch for $asset: not installed"

tar xzf "$tmp/$asset" -C "$tmp"
mkdir -p "$bin_dir"
# Through a temporary name in the same directory: a running speedrunner
# keeps its file, and the new one appears whole.
cp "$tmp/speedrunner-$arch-unknown-linux-musl/speedrunner" "$bin_dir/.speedrunner.new"
chmod 755 "$bin_dir/.speedrunner.new"
mv -f "$bin_dir/.speedrunner.new" "$bin_dir/speedrunner"
ln -sf speedrunner "$bin_dir/srr"

say "installed $("$bin_dir/speedrunner" --version) in $bin_dir, with srr"
case ":$PATH:" in
    *":$bin_dir:"*) ;;
    *) say "$bin_dir is not on your PATH: add  export PATH=\"$bin_dir:\$PATH\"  to your shell's rc file" ;;
esac
cat << EOF

  srr aws s3 ls                      # any Python, Ruby or PHP tool, nothing to set up
  eval "\$(speedrunner activate)"     # at the top of a script: no prefix after

  For zsh (wrappers, and daemons warmed up while you type), add to ~/.zshrc:
    eval "\$(speedrunner init zsh)"

EOF
