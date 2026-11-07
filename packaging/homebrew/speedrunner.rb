# The formula of the anthonyGosme/homebrew-speedrunner tap: the release's
# static Linux binary, or its universal macOS one, as packaging/update.sh
# writes it.
class Speedrunner < Formula
  desc "Preloaded Python, Ruby, PHP and Node command-line tools, forked per call"
  homepage "https://github.com/anthonyGosme/speedrunner"
  version "0.4.9"
  license any_of: ["MIT", "Apache-2.0"]

  on_macos do
    on_intel do
      url "https://github.com/anthonyGosme/speedrunner/releases/download/v0.4.9/speedrunner-universal-apple-darwin.tar.gz"
      sha256 "72b04094ada41937e90a5669b0f47c2cb96c9866ec42dbb42a8cfc9b8d198ce7"
    end
    on_arm do
      url "https://github.com/anthonyGosme/speedrunner/releases/download/v0.4.9/speedrunner-universal-apple-darwin.tar.gz"
      sha256 "72b04094ada41937e90a5669b0f47c2cb96c9866ec42dbb42a8cfc9b8d198ce7"
    end
  end

  on_linux do
    on_intel do
      url "https://github.com/anthonyGosme/speedrunner/releases/download/v0.4.9/speedrunner-x86_64-unknown-linux-musl.tar.gz"
      sha256 "1cf8b3d2979ea8d7cff024817dc69cfca00f248dbaa3ff798b7eaa194a566e49"
    end
    on_arm do
      url "https://github.com/anthonyGosme/speedrunner/releases/download/v0.4.9/speedrunner-aarch64-unknown-linux-musl.tar.gz"
      sha256 "6d90bb5f6f533ad172a193a3396c207b219fcf437f24ce95b9c55d0cf5a76544"
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
