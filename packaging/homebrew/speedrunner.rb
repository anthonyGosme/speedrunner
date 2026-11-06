# The formula of the anthonyGosme/homebrew-speedrunner tap: the release's
# static Linux binary, or its universal macOS one, as packaging/update.sh
# writes it.
class Speedrunner < Formula
  desc "Preloaded Python, Ruby, PHP and Node command-line tools, forked per call"
  homepage "https://github.com/anthonyGosme/speedrunner"
  version "0.4.6"
  license any_of: ["MIT", "Apache-2.0"]

  on_macos do
    on_intel do
      url "https://github.com/anthonyGosme/speedrunner/releases/download/v0.4.6/speedrunner-universal-apple-darwin.tar.gz"
      sha256 "b8795fec603161549e90ce4d21563c69800bd8919024b0832da7a41de4de804a"
    end
    on_arm do
      url "https://github.com/anthonyGosme/speedrunner/releases/download/v0.4.6/speedrunner-universal-apple-darwin.tar.gz"
      sha256 "b8795fec603161549e90ce4d21563c69800bd8919024b0832da7a41de4de804a"
    end
  end

  on_linux do
    on_intel do
      url "https://github.com/anthonyGosme/speedrunner/releases/download/v0.4.6/speedrunner-x86_64-unknown-linux-musl.tar.gz"
      sha256 "d18d2e8f5e8660277cf85066301add5e4e543e7a97dcf7331f93943f42c19e99"
    end
    on_arm do
      url "https://github.com/anthonyGosme/speedrunner/releases/download/v0.4.6/speedrunner-aarch64-unknown-linux-musl.tar.gz"
      sha256 "ca9a7dd250bdbdaf22396fcb82cecfc60b175d2e6ab8731869c02de97054555e"
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
