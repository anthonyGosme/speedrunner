# The formula of the anthonyGosme/homebrew-speedrunner tap: the release's
# static Linux binary, as packaging/update.sh sets version and sums.
class Speedrunner < Formula
  desc "Preloaded Python, Ruby, PHP and Node command-line tools, forked per call"
  homepage "https://github.com/anthonyGosme/speedrunner"
  version "0.3.1"
  license any_of: ["MIT", "Apache-2.0"]

  depends_on :linux

  on_linux do
    on_intel do
      url "https://github.com/anthonyGosme/speedrunner/releases/download/v0.3.1/speedrunner-x86_64-unknown-linux-musl.tar.gz"
      sha256 "45d82ef103a1b411a5841d451ac1b54addf22b8e5f323b900e0d42ff47da586b"
    end
    on_arm do
      url "https://github.com/anthonyGosme/speedrunner/releases/download/v0.3.1/speedrunner-aarch64-unknown-linux-musl.tar.gz"
      sha256 "763bd67544b57641f99b75c6a813482d58ab119ea6841d8671fcc97fb26845df"
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
