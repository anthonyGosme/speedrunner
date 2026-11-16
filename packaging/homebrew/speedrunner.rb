# The formula of the anthonyGosme/homebrew-speedrunner tap: the release's
# static Linux binary, or its universal macOS one, as packaging/update.sh
# writes it.
class Speedrunner < Formula
  desc "Preloaded Python, Ruby, PHP and Node command-line tools, forked per call"
  homepage "https://github.com/anthonyGosme/speedrunner"
  version "1.1.0"
  license any_of: ["MIT", "Apache-2.0"]

  on_macos do
    on_intel do
      url "https://github.com/anthonyGosme/speedrunner/releases/download/v1.1.0/speedrunner-universal-apple-darwin.tar.gz"
      sha256 "e9090b1f912cb9a45269f31105a9ad9908952a92465d82aa3931a6a4ebaf806d"
    end
    on_arm do
      url "https://github.com/anthonyGosme/speedrunner/releases/download/v1.1.0/speedrunner-universal-apple-darwin.tar.gz"
      sha256 "e9090b1f912cb9a45269f31105a9ad9908952a92465d82aa3931a6a4ebaf806d"
    end
  end

  on_linux do
    on_intel do
      url "https://github.com/anthonyGosme/speedrunner/releases/download/v1.1.0/speedrunner-x86_64-unknown-linux-musl.tar.gz"
      sha256 "05f473fb317c84083f2c415a967f454cc56669d7eaeea582e4afd61578b605f1"
    end
    on_arm do
      url "https://github.com/anthonyGosme/speedrunner/releases/download/v1.1.0/speedrunner-aarch64-unknown-linux-musl.tar.gz"
      sha256 "10655adc4408ed292c04ae7025db9d19b47be70399008371dc7ee582a61af610"
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
