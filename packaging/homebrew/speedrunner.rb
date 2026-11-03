# The formula of the anthonyGosme/homebrew-speedrunner tap: the release's
# static Linux binary, or its universal macOS one, as packaging/update.sh
# writes it.
class Speedrunner < Formula
  desc "Preloaded Python, Ruby, PHP and Node command-line tools, forked per call"
  homepage "https://github.com/anthonyGosme/speedrunner"
  version "0.4.3"
  license any_of: ["MIT", "Apache-2.0"]

  on_macos do
    on_intel do
      url "https://github.com/anthonyGosme/speedrunner/releases/download/v0.4.3/speedrunner-universal-apple-darwin.tar.gz"
      sha256 "7cb9f91f8344ed7d485e0625492019e2b4d12e11f62013a3301db14a6944dbe9"
    end
    on_arm do
      url "https://github.com/anthonyGosme/speedrunner/releases/download/v0.4.3/speedrunner-universal-apple-darwin.tar.gz"
      sha256 "7cb9f91f8344ed7d485e0625492019e2b4d12e11f62013a3301db14a6944dbe9"
    end
  end

  on_linux do
    on_intel do
      url "https://github.com/anthonyGosme/speedrunner/releases/download/v0.4.3/speedrunner-x86_64-unknown-linux-musl.tar.gz"
      sha256 "6c2f2141cb0e98a1058ed64e049d7dc87e0fbe351f637b3495f40e9fe41ec418"
    end
    on_arm do
      url "https://github.com/anthonyGosme/speedrunner/releases/download/v0.4.3/speedrunner-aarch64-unknown-linux-musl.tar.gz"
      sha256 "539a365e49c4bfe19fe8b932e973082ac95d7f22f8c5f8e59d154383b8d8d61f"
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
