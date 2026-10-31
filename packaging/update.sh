#!/bin/sh
# Points the AUR package (aur/PKGBUILD, aur/.SRCINFO) and the Homebrew
# formula (homebrew/speedrunner.rb) at a published release: its version,
# its archives' SHA-256 sums.
#
#   packaging/update.sh 0.3.2
set -eu
new=$1
cd "$(dirname "$0")"
base=https://github.com/anthonyGosme/speedrunner/releases/download/v$new
sum() {
    curl -fsSL "$base/speedrunner-$1-unknown-linux-musl.tar.gz.sha256" | cut -d ' ' -f 1
}
x86=$(sum x86_64)
arm=$(sum aarch64)
if [ ${#x86} != 64 ] || [ ${#arm} != 64 ]; then
    echo "update.sh: no SHA-256 sums published for v$new" >&2
    exit 1
fi
old=$(sed -n 's/^pkgver=//p' aur/PKGBUILD)
old_x86=$(sed -n "s/^sha256sums_x86_64=('\(.*\)')$/\1/p" aur/PKGBUILD)
old_arm=$(sed -n "s/^sha256sums_aarch64=('\(.*\)')$/\1/p" aur/PKGBUILD)
old_re=$(printf %s "$old" | sed 's/\./\\./g')
for f in aur/PKGBUILD aur/.SRCINFO homebrew/speedrunner.rb; do
    sed -i.bak -e "s/$old_re/$new/g" -e "s/$old_x86/$x86/g" -e "s/$old_arm/$arm/g" \
        -e 's/^pkgrel=.*/pkgrel=1/' -e 's/^\tpkgrel = .*/\tpkgrel = 1/' "$f"
    rm -f "$f.bak"
done
echo "packaging: v$old -> v$new"
