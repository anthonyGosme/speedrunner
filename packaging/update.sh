#!/bin/sh
# Points the AUR package (aur/PKGBUILD, aur/.SRCINFO) and the Homebrew
# formula (homebrew/speedrunner.rb) at a published release: its version,
# its archives' SHA-256 sums. The formula is written whole: for macOS too
# when the release has the universal macOS archive (releases after 0.3.2),
# else for Linux only.
#
#   packaging/update.sh 0.3.2
#
# SPEEDRUNNER_BASE_URL: where the release files are, instead of GitHub (tests).
set -eu
new=$1
cd "$(dirname "$0")"
repo=https://github.com/anthonyGosme/speedrunner
base=${SPEEDRUNNER_BASE_URL:-$repo/releases/download/v$new}
sum() {
    curl -fsSL "$base/speedrunner-$1.tar.gz.sha256" 2> /dev/null | cut -d ' ' -f 1
}
x86=$(sum x86_64-unknown-linux-musl)
arm=$(sum aarch64-unknown-linux-musl)
mac=$(sum universal-apple-darwin) || mac=
if [ ${#x86} != 64 ] || [ ${#arm} != 64 ]; then
    echo "update.sh: no SHA-256 sums published for v$new" >&2
    exit 1
fi
old=$(sed -n 's/^pkgver=//p' aur/PKGBUILD)
old_x86=$(sed -n "s/^sha256sums_x86_64=('\(.*\)')$/\1/p" aur/PKGBUILD)
old_arm=$(sed -n "s/^sha256sums_aarch64=('\(.*\)')$/\1/p" aur/PKGBUILD)
old_re=$(printf %s "$old" | sed 's/\./\\./g')
for f in aur/PKGBUILD aur/.SRCINFO; do
    sed -i.bak -e "s/$old_re/$new/g" -e "s/$old_x86/$x86/g" -e "s/$old_arm/$arm/g" \
        -e 's/^pkgrel=.*/pkgrel=1/' -e 's/^\tpkgrel = .*/\tpkgrel = 1/' "$f"
    rm -f "$f.bak"
done

url=$repo/releases/download/v$new
{
    cat << EOF
# The formula of the anthonyGosme/homebrew-speedrunner tap: the release's
# static Linux binary, or its universal macOS one, as packaging/update.sh
# writes it.
class Speedrunner < Formula
  desc "Preloaded Python, Ruby, PHP and Node command-line tools, forked per call"
  homepage "$repo"
  version "$new"
  license any_of: ["MIT", "Apache-2.0"]

EOF
    if [ ${#mac} = 64 ]; then
        cat << EOF
  on_macos do
    on_intel do
      url "$url/speedrunner-universal-apple-darwin.tar.gz"
      sha256 "$mac"
    end
    on_arm do
      url "$url/speedrunner-universal-apple-darwin.tar.gz"
      sha256 "$mac"
    end
  end

EOF
    else
        printf '  depends_on :linux\n\n'
    fi
    cat << EOF
  on_linux do
    on_intel do
      url "$url/speedrunner-x86_64-unknown-linux-musl.tar.gz"
      sha256 "$x86"
    end
    on_arm do
      url "$url/speedrunner-aarch64-unknown-linux-musl.tar.gz"
      sha256 "$arm"
    end
  end

  def install
    bin.install "speedrunner"
    bin.install_symlink "speedrunner" => "srr"
  end

  test do
    assert_match version.to_s, shell_output("#{bin}/speedrunner --version")
    assert_match "speedrunner for zsh", shell_output("#{bin}/speedrunner init zsh")
  end
end
EOF
} > homebrew/speedrunner.rb.new
mv homebrew/speedrunner.rb.new homebrew/speedrunner.rb
echo "packaging: v$old -> v$new${mac:+, macOS included}"
