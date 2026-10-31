# The formula of the anthonyGosme/homebrew-speedrunner tap: the release's
# static Linux binary, as packaging/update.sh sets version and sums.
class Speedrunner < Formula
  desc "Preloaded Python, Ruby, PHP and Node command-line tools, forked per call"
  homepage "https://github.com/anthonyGosme/speedrunner"
  version "0.3.2"
  license any_of: ["MIT", "Apache-2.0"]

  depends_on :linux

  on_linux do
    on_intel do
      url "https://github.com/anthonyGosme/speedrunner/releases/download/v0.3.2/speedrunner-x86_64-unknown-linux-musl.tar.gz"
      sha256 "4e53c4ba37f2e89ab770968bed7d13d5ff9900088748f6295b2499efc543271f"
    end
    on_arm do
      url "https://github.com/anthonyGosme/speedrunner/releases/download/v0.3.2/speedrunner-aarch64-unknown-linux-musl.tar.gz"
      sha256 "2a2dc4ec7aaa1da287f026f4d00d78eb508b29c03d4c8014392e0ed8b6109c37"
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
