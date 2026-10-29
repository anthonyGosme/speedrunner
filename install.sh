#!/bin/sh
# Install speedrunner:
#
#   curl -fsSL https://raw.githubusercontent.com/anthonyGosme/speedrunner/main/install.sh | sh
#
# Downloads the static Linux binary of the latest release for this machine,
# checks its SHA-256 sum, installs it as speedrunner with the `srr` link next
# to it. Needs no root, and no step after it: as root it installs in
# /usr/local/bin; otherwise in ~/.local/bin or ~/bin when one is on PATH, else
# in ~/.local/bin, added to PATH in ~/.profile, ~/.bashrc and ~/.zshrc.
#
# Env: SPEEDRUNNER_VERSION (a tag, default the latest release),
#      SPEEDRUNNER_BIN_DIR (where to install, instead),
#      SPEEDRUNNER_NO_MODIFY_PATH=1 (leave the shell rc files alone),
#      SPEEDRUNNER_BASE_URL (where the release files are, instead of GitHub).
set -eu

repo=anthonyGosme/speedrunner
version=${SPEEDRUNNER_VERSION:-latest}
on_path() {
    case ":$PATH:" in
        *":$1:"*) return 0 ;;
    esac
    return 1
}
if [ -n "${SPEEDRUNNER_BIN_DIR:-}" ]; then
    bin_dir=$SPEEDRUNNER_BIN_DIR
elif [ "$(id -u)" = 0 ]; then
    bin_dir=/usr/local/bin  # on every PATH, root's included
else
    bin_dir=$HOME/.local/bin
    for d in "$HOME/.local/bin" "$HOME/bin"; do
        if on_path "$d"; then
            bin_dir=$d  # works in this very shell
            break
        fi
    done
fi

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
# Not on PATH: put it there for every new shell. Once per file, marked.
if ! on_path "$bin_dir"; then
    case $bin_dir in
        "$HOME"/*) entry="\$HOME/${bin_dir#"$HOME"/}" ;;
        *) entry=$bin_dir ;;
    esac
    line="export PATH=\"$entry:\$PATH\"  # added by the speedrunner installer"
    if [ "${SPEEDRUNNER_NO_MODIFY_PATH:-}" = 1 ]; then
        say "$bin_dir is not on your PATH: add  $line"
    else
        changed=
        for rc in "$HOME/.profile" "$HOME/.bashrc" "$HOME/.zshrc"; do
            case $rc in
                */.profile) wanted=1 ;;  # login shells, sh included
                */.bashrc) [ -e "$rc" ] || [ "${SHELL##*/}" = bash ] && wanted=1 || wanted= ;;
                */.zshrc) [ -e "$rc" ] || [ "${SHELL##*/}" = zsh ] && wanted=1 || wanted= ;;
            esac
            [ -n "$wanted" ] || continue
            if ! grep -qs "added by the speedrunner installer" "$rc"; then
                printf '\n%s\n' "$line" >> "$rc"
                changed="$changed ${rc#"$HOME"/}"
            fi
        done
        [ -z "$changed" ] || say "added $bin_dir to PATH in:$changed"
        say "new shells find it; this one does once restarted:  exec \$SHELL"
    fi
fi
cat << EOF

  speedrunner demo                   # the gain on this machine, with the tools it has
  srr aws s3 ls                      # one command: any Python, Ruby or PHP tool
  speedrunner exec ./script.sh       # a whole script: every tool it calls, preloaded
  eval "\$(speedrunner activate)"     # or this line at the top of the script itself

  For zsh (wrappers, and daemons warmed up while you type), add to ~/.zshrc:
    eval "\$(speedrunner init zsh)"

EOF
