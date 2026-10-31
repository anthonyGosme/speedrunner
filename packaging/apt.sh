#!/bin/sh
# A signed APT repository of the given .deb files, in <out dir>: the files
# themselves in pool/, one suite (stable, component main) for amd64 and
# arm64, signed by the only secret key of the GnuPG home (InRelease and
# Release.gpg), plus the public key and a .sources entry to install.
# The binary is static: one suite serves every Debian and Ubuntu release.
#
#   APT_URL=https://anthonygosme.github.io/speedrunner/apt \
#       packaging/apt.sh <out dir> <deb>...
#
# Needs apt-ftparchive (apt-utils) and gpg.
set -eu
url=${APT_URL:-https://anthonygosme.github.io/speedrunner/apt}
out=$1
shift
if [ $# = 0 ]; then
    echo "apt.sh: no .deb given" >&2
    exit 2
fi
fpr=$(gpg --batch --with-colons --list-secret-keys | awk -F: '$1 == "fpr" { print $10; exit }')
if [ -z "$fpr" ] || [ "$(gpg --batch --with-colons --list-secret-keys | grep -c '^sec:')" != 1 ]; then
    echo "apt.sh: the GnuPG home must hold exactly one secret key" >&2
    exit 1
fi
# SHA-256 and SHA-512 only: apt has stopped trusting MD5 and SHA-1.
ftparchive() {
    apt-ftparchive -o APT::FTPArchive::MD5=false -o APT::FTPArchive::SHA1=false "$@"
}
rm -rf "$out"
mkdir -p "$out/pool/main"
cp "$@" "$out/pool/main/"
cd "$out"
for arch in amd64 arm64; do
    dir=dists/stable/main/binary-$arch
    mkdir -p "$dir"
    ftparchive --arch "$arch" packages pool > "$dir/Packages"
    gzip -9nk "$dir/Packages"
done
ftparchive \
    -o APT::FTPArchive::Release::Origin=speedrunner \
    -o APT::FTPArchive::Release::Label=speedrunner \
    -o APT::FTPArchive::Release::Suite=stable \
    -o APT::FTPArchive::Release::Codename=stable \
    -o APT::FTPArchive::Release::Architectures="amd64 arm64" \
    -o APT::FTPArchive::Release::Components=main \
    -o APT::FTPArchive::Release::Description="speedrunner, https://github.com/anthonyGosme/speedrunner" \
    release dists/stable > Release
mv Release dists/stable/Release
gpg --batch --yes --local-user "$fpr" --clearsign -o dists/stable/InRelease dists/stable/Release
gpg --batch --yes --local-user "$fpr" --armor --detach-sign -o dists/stable/Release.gpg dists/stable/Release
gpg --batch --armor --export "$fpr" > speedrunner.asc
cat > speedrunner.sources << SOURCES
Types: deb
URIs: $url
Suites: stable
Components: main
Signed-By: /etc/apt/keyrings/speedrunner.asc
SOURCES
cat > index.html << HTML
<!doctype html>
<meta charset="utf-8">
<title>speedrunner APT repository</title>
<p>The APT repository of <a href="https://github.com/anthonyGosme/speedrunner">speedrunner</a>
(Debian, Ubuntu; amd64, arm64). Signing key: <code>$fpr</code>.</p>
<pre>
sudo install -d -m 0755 /etc/apt/keyrings
sudo curl -fsSLo /etc/apt/keyrings/speedrunner.asc $url/speedrunner.asc
sudo curl -fsSLo /etc/apt/sources.list.d/speedrunner.sources $url/speedrunner.sources
sudo apt update &amp;&amp; sudo apt install speedrunner
</pre>
HTML
echo "apt.sh: $(find pool -name '*.deb' | wc -l | tr -d ' ') packages, signed by $fpr"
