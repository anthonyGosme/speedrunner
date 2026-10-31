#!/bin/sh
# A .deb of the static client: /usr/bin/speedrunner, the srr link, the
# licenses. No dependency: the binary is static and carries its daemons.
#
#   packaging/deb.sh <speedrunner binary> <version> <x86_64|aarch64> <out dir>
set -eu
bin=$1 version=$2 arch=$3 out=$4
case $arch in
    x86_64) deb_arch=amd64 ;;
    aarch64) deb_arch=arm64 ;;
    *) echo "deb.sh: unknown architecture $arch" >&2; exit 2 ;;
esac
root=$(cd "$(dirname "$0")/.." && pwd)
maintainer="speedrunner contributors <39555936+anthonyGosme@users.noreply.github.com>"
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
mkdir -p "$stage/DEBIAN" "$stage/usr/bin" "$stage/usr/share/doc/speedrunner"
install -m 0755 "$bin" "$stage/usr/bin/speedrunner"
ln -s speedrunner "$stage/usr/bin/srr"
{
    echo "Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/"
    echo "Upstream-Name: speedrunner"
    echo "Source: https://github.com/anthonyGosme/speedrunner"
    echo
    echo "Files: *"
    echo "Copyright: 2026 The speedrunner contributors"
    echo "License: MIT or Apache-2.0"
    echo
    echo "License: MIT"
    sed -n '/^Permission/,$p' "$root/LICENSE-MIT" | sed 's/^$/./; s/^/ /'
    echo
    echo "License: Apache-2.0"
    echo " On Debian systems, the full text of the Apache License, Version 2.0, is"
    echo " in /usr/share/common-licenses/Apache-2.0."
} > "$stage/usr/share/doc/speedrunner/copyright"
# A native package's changelog: this version, pointing at the release notes.
{
    echo "speedrunner ($version) unstable; urgency=medium"
    echo
    echo "  * https://github.com/anthonyGosme/speedrunner/releases/tag/v$version"
    echo
    echo " -- $maintainer  $(date -R -u -d "@${SOURCE_DATE_EPOCH:-$(date +%s)}")"
} | gzip -9n > "$stage/usr/share/doc/speedrunner/changelog.gz"
size=$(du -sk "$stage/usr" | cut -f1)
cat > "$stage/DEBIAN/control" << CONTROL
Package: speedrunner
Version: $version
Architecture: $deb_arch
Maintainer: $maintainer
Installed-Size: $size
Section: utils
Priority: optional
Homepage: https://github.com/anthonyGosme/speedrunner
Description: Python, Ruby, PHP and Node command-line tools, preloaded
 speedrunner keeps each tool loaded in a daemon that forks it on every call:
 the interpreter start, the imports and the configuration are done once.
 Zero config, one static binary; a command never runs twice.
CONTROL
mkdir -p "$out"
dpkg-deb --root-owner-group --build "$stage" "$out/speedrunner_${version}_${deb_arch}.deb" > /dev/null
echo "$out/speedrunner_${version}_${deb_arch}.deb"
