# The formula of the anthonyGosme/homebrew-speedrunner tap: the release's
# static Linux binary, or its universal macOS one, as packaging/update.sh
# writes it.
class Speedrunner < Formula
  desc "Preloaded Python, Ruby, PHP and Node command-line tools, forked per call"
  homepage "https://github.com/anthonyGosme/speedrunner"
  version "0.4.1"
  license any_of: ["MIT", "Apache-2.0"]

  on_macos do
    on_intel do
      url "https://github.com/anthonyGosme/speedrunner/releases/download/v0.4.1/speedrunner-universal-apple-darwin.tar.gz"
      sha256 "b27a4c8f3da710f26370a4865c59c785dc8c9a4a429a1ba262b8c8ea9af06545"
    end
    on_arm do
      url "https://github.com/anthonyGosme/speedrunner/releases/download/v0.4.1/speedrunner-universal-apple-darwin.tar.gz"
      sha256 "b27a4c8f3da710f26370a4865c59c785dc8c9a4a429a1ba262b8c8ea9af06545"
    end
  end

  on_linux do
    on_intel do
      url "https://github.com/anthonyGosme/speedrunner/releases/download/v0.4.1/speedrunner-x86_64-unknown-linux-musl.tar.gz"
      sha256 "bf18aa3eaaf6f5d31edbc32c8cbdc5dc62c55d06fb60b8ef99c0a739633f2488"
    end
    on_arm do
      url "https://github.com/anthonyGosme/speedrunner/releases/download/v0.4.1/speedrunner-aarch64-unknown-linux-musl.tar.gz"
      sha256 "af6e97a0dfc0363d446737245bf0bc80e4d1c27b4868d53e18daf773fd47b6df"
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
