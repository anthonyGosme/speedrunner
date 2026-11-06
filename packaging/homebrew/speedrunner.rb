# The formula of the anthonyGosme/homebrew-speedrunner tap: the release's
# static Linux binary, or its universal macOS one, as packaging/update.sh
# writes it.
class Speedrunner < Formula
  desc "Preloaded Python, Ruby, PHP and Node command-line tools, forked per call"
  homepage "https://github.com/anthonyGosme/speedrunner"
  version "0.4.7"
  license any_of: ["MIT", "Apache-2.0"]

  on_macos do
    on_intel do
      url "https://github.com/anthonyGosme/speedrunner/releases/download/v0.4.7/speedrunner-universal-apple-darwin.tar.gz"
      sha256 "5eb15f020d10dc685af327504b231ca57287044ace50465cd5124411e33ed22f"
    end
    on_arm do
      url "https://github.com/anthonyGosme/speedrunner/releases/download/v0.4.7/speedrunner-universal-apple-darwin.tar.gz"
      sha256 "5eb15f020d10dc685af327504b231ca57287044ace50465cd5124411e33ed22f"
    end
  end

  on_linux do
    on_intel do
      url "https://github.com/anthonyGosme/speedrunner/releases/download/v0.4.7/speedrunner-x86_64-unknown-linux-musl.tar.gz"
      sha256 "20d83a6ba1a032aee512fdd211ca556485065ada9722073662923a606991929b"
    end
    on_arm do
      url "https://github.com/anthonyGosme/speedrunner/releases/download/v0.4.7/speedrunner-aarch64-unknown-linux-musl.tar.gz"
      sha256 "b43bc0ffdd0a6e80fe0b9b696112e8342e863a77e2f3ad78a94b3b23af2a2a6e"
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
