# Packages

All of them ship the release's static binary, unchanged.

- **.deb** (Debian, Ubuntu…): `deb.sh` builds one per architecture in the
  release workflow, which installs and removes it once, signs it (build
  provenance) and attaches it to the release, with its SHA-256 sum.
  `sudo apt install ./speedrunner_<version>_amd64.deb`.
- **AUR** (`speedrunner-bin`): `aur/PKGBUILD` and `aur/.SRCINFO`.
- **Homebrew on Linux**: `homebrew/speedrunner.rb`, the formula of the tap
  `anthonyGosme/homebrew-speedrunner`: `brew install anthonyGosme/speedrunner/speedrunner`.

## After a release

```bash
packaging/update.sh 0.3.2      # version and sums of the AUR package and the formula
```

Then publish them (each needs the maintainer's own account):

```bash
# AUR: an account with an SSH key, then
git clone ssh://aur@aur.archlinux.org/speedrunner-bin.git
cp packaging/aur/PKGBUILD packaging/aur/.SRCINFO speedrunner-bin/
git -C speedrunner-bin commit -am "Update to 0.3.2" && git -C speedrunner-bin push

# Homebrew: the tap repository anthonyGosme/homebrew-speedrunner
cp packaging/homebrew/speedrunner.rb homebrew-speedrunner/Formula/
git -C homebrew-speedrunner commit -am "speedrunner 0.3.2" && git -C homebrew-speedrunner push
```

## Tested

- .deb: lintian (no error; warnings: no manual page, and a static binary
  taken for a shared library), installed, run and removed on Debian 12 and
  Ubuntu 24.04.
- AUR: `makepkg`, namcap, installed, run and removed on Arch Linux.
- Homebrew: installed from a local tap, `brew test`, `brew audit --strict`,
  `brew style`, uninstalled, with Linuxbrew.
