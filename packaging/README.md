# Packages

All of them ship the release's static binary, unchanged.

- **.deb** (Debian, Ubuntu…): `deb.sh` builds one per architecture in the
  release workflow, which installs and removes it once, signs it (build
  provenance) and attaches it to the release, with its SHA-256 sum.
  `sudo apt install ./speedrunner_<version>_amd64.deb`.
- **APT repository**: `apt.sh` builds a signed repository of the .deb files
  of every release, which `.github/workflows/apt.yml` publishes on this
  repository's GitHub Pages, https://anthonygosme.github.io/speedrunner/apt.
  One suite, `stable`, for every Debian and Ubuntu release (the binary is
  static), amd64 and arm64; older versions stay installable
  (`apt install speedrunner=0.3.2`). Installing it: the README.
- **AUR** (`speedrunner-bin`): `aur/PKGBUILD` and `aur/.SRCINFO`.
- **Homebrew, on macOS and Linux**: `homebrew/speedrunner.rb`, the formula of the tap
  [anthonyGosme/homebrew-speedrunner](https://github.com/anthonyGosme/homebrew-speedrunner)
  (published): `brew install anthonyGosme/speedrunner/speedrunner`.
  `update.sh` writes it whole: with the universal macOS archive when the
  release has one (after 0.3.2), else for Linux only.

## At each release

After `publish`, `release.yml` calls two workflows; each can also be run by
hand from the Actions tab or with `gh workflow run`.

- **`packages.yml`** (`-f version=0.3.2`): `update.sh <version>`, then
  pushes the formula to the tap if it changed, checks
  `brew install anthonyGosme/speedrunner/speedrunner` in Linuxbrew
  (`homebrew/brew`) and on macOS (Intel and Apple Silicon runners), with
  `brew test`, and opens a pull request that brings
  `packaging/` (the formula, the AUR files) to that version on main, rather
  than committing to main itself.
- **`apt.yml`**: rebuilds the APT repository from the .deb files of every
  published release (checked against their `.sha256`), so it never depends
  on a previous deployment, deploys it to Pages, then `apt install`s it in
  Debian 12 and Ubuntu 24.04. Pages' CDN may serve the previous index for
  up to 10 minutes: the check waits for the new version.

Only the AUR stays by hand, since it needs the maintainer's SSH key:

```bash
git clone ssh://aur@aur.archlinux.org/speedrunner-bin.git
cp packaging/aur/PKGBUILD packaging/aur/.SRCINFO speedrunner-bin/   # once the pull request is merged
git -C speedrunner-bin commit -am "Update to 0.3.3" && git -C speedrunner-bin push
```

`packaging/update.sh <version>` still does by hand what the pull request
does.

## Setup, once

Two secrets and three settings, on `anthonyGosme/speedrunner`.

**1. `HOMEBREW_TAP_TOKEN`**, a fine-grained personal access token that can
write to the tap and nothing else. On
https://github.com/settings/personal-access-tokens/new: resource owner
anthonyGosme, an expiration (a year, then renew it), Repository access
"Only select repositories": `anthonyGosme/homebrew-speedrunner`;
Permissions, Repository, Contents: "Read and write" (Metadata: read, added
by itself). Generate, copy, then:

```bash
gh secret set HOMEBREW_TAP_TOKEN -R anthonyGosme/speedrunner   # paste the token
```

**2. Pull requests from GitHub Actions** (for the `packaging/` pull
request; without it, the branch is pushed and the run gives the link to open
it):

```bash
gh api -X PUT repos/anthonyGosme/speedrunner/actions/permissions/workflow \
  -f default_workflow_permissions=read -F can_approve_pull_request_reviews=true
```

**3. `APT_SIGNING_KEY`**, the repository's own signing key (Ed25519, no
passphrase: the secret is its protection), made in a throwaway GnuPG home:

```bash
export GNUPGHOME="$(mktemp -d)"
gpg --batch --passphrase '' --quick-generate-key \
  "speedrunner APT repository <39555936+anthonyGosme@users.noreply.github.com>" ed25519 sign never
gpg --armor --export-secret-keys | gh secret set APT_SIGNING_KEY -R anthonyGosme/speedrunner
gpg --armor --export-secret-keys > speedrunner-apt-secret.asc   # into a password manager, then delete it
cp "$GNUPGHOME"/openpgp-revocs.d/*.rev speedrunner-apt-revoke.rev   # likewise
gpg --fingerprint
rm -rf "$GNUPGHOME"; unset GNUPGHOME
```

A secret cannot be read back: without the saved copy, a lost key means a
new one, and every user fetching it again. The workflow publishes the
public key (`speedrunner.asc`) and prints its fingerprint in the log and on
the repository's page.

**4. GitHub Pages built by Actions, deployable from the tags**: the release
runs on a `v*` tag, and the `github-pages` environment only allows `main`
by default.

```bash
gh api -X POST repos/anthonyGosme/speedrunner/pages -f build_type=workflow
gh api -X PUT repos/anthonyGosme/speedrunner/environments/github-pages \
  -F 'deployment_branch_policy[protected_branches]=false' \
  -F 'deployment_branch_policy[custom_branch_policies]=true'
gh api -X POST repos/anthonyGosme/speedrunner/environments/github-pages/deployment-branch-policies \
  -f name=main -f type=branch
gh api -X POST repos/anthonyGosme/speedrunner/environments/github-pages/deployment-branch-policies \
  -f name='v*' -f type=tag
```

(If enabling Pages already allowed `main`, the third call says so: harmless.)

**5. Once merged**, without waiting for a release:

```bash
gh workflow run apt.yml -R anthonyGosme/speedrunner                     # the APT repository, with 0.3.2
gh workflow run packages.yml -R anthonyGosme/speedrunner -f version=0.3.2   # the tap's token; nothing to push
```

## Tested

- .deb: lintian (no error; warnings: no manual page, and a static binary
  taken for a shared library), installed, run and removed on Debian 12 and
  Ubuntu 24.04.
- APT repository: built by `apt.sh` from a 0.3.1 .deb (made by `deb.sh`
  from the v0.3.1 archive) and from the v0.3.2 .deb files, served over HTTP;
  installed with the README's lines, then `apt upgrade` from 0.3.1 to
  0.3.2, on Debian 12, Ubuntu 24.04 and Ubuntu 24.04 arm64 (emulated). An
  index changed after signing is refused (hash mismatch), and so is the
  repository under another key (`NO_PUBKEY`).
- AUR: `makepkg`, namcap, installed, run and removed on Arch Linux.
- Homebrew: installed from a local tap, `brew test`, `brew audit --strict`,
  `brew style`, uninstalled, with Linuxbrew. `packages.yml`'s steps against
  a local copy of the tap: 0.3.2 left unchanged, 0.3.1 pushed,
  `brew install`, then 0.3.2 pushed and `brew upgrade`; its `brew` check
  as written, against the published tap. On macOS (Intel, macOS 26): the
  formula `update.sh` writes from a local universal archive, installed from
  a local tap, `brew test`, `brew audit --strict`, `brew style`,
  uninstalled; for 0.3.2 (no macOS archive) it writes the Linux formula
  unchanged.
