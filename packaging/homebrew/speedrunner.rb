# The formula of the anthonyGosme/homebrew-speedrunner tap: the release's
# static Linux binary, or its universal macOS one, as packaging/update.sh
# writes it.
class Speedrunner < Formula
  desc "Preloaded Python, Ruby, PHP and Node command-line tools, forked per call"
  homepage "https://github.com/anthonyGosme/speedrunner"
  version "1.0.0"
  license any_of: ["MIT", "Apache-2.0"]

  on_macos do
    on_intel do
      url "https://github.com/anthonyGosme/speedrunner/releases/download/v1.0.0/speedrunner-universal-apple-darwin.tar.gz"
      sha256 "ba568b4a63814928abab5e205eb2dce74241bed72b041b1d4ee719a49cb2e324"
    end
    on_arm do
      url "https://github.com/anthonyGosme/speedrunner/releases/download/v1.0.0/speedrunner-universal-apple-darwin.tar.gz"
      sha256 "ba568b4a63814928abab5e205eb2dce74241bed72b041b1d4ee719a49cb2e324"
    end
  end

  on_linux do
    on_intel do
      url "https://github.com/anthonyGosme/speedrunner/releases/download/v1.0.0/speedrunner-x86_64-unknown-linux-musl.tar.gz"
      sha256 "0b6d8e29603fe9a2a993dc8208d34fab09aae41fd0412273745aa2d9989638bf"
    end
    on_arm do
      url "https://github.com/anthonyGosme/speedrunner/releases/download/v1.0.0/speedrunner-aarch64-unknown-linux-musl.tar.gz"
      sha256 "d01d79cca35773933e79ac1ffb2e495582bb3bfdddb00c74b62f6ba82f736063"
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
