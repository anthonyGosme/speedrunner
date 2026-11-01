# The formula of the anthonyGosme/homebrew-speedrunner tap: the release's
# static Linux binary, or its universal macOS one, as packaging/update.sh
# writes it.
class Speedrunner < Formula
  desc "Preloaded Python, Ruby, PHP and Node command-line tools, forked per call"
  homepage "https://github.com/anthonyGosme/speedrunner"
  version "0.4.2"
  license any_of: ["MIT", "Apache-2.0"]

  on_macos do
    on_intel do
      url "https://github.com/anthonyGosme/speedrunner/releases/download/v0.4.2/speedrunner-universal-apple-darwin.tar.gz"
      sha256 "2d5943f92a427185476316bf180a927c1933c5bf9f0a9dcda1d41951d204e018"
    end
    on_arm do
      url "https://github.com/anthonyGosme/speedrunner/releases/download/v0.4.2/speedrunner-universal-apple-darwin.tar.gz"
      sha256 "2d5943f92a427185476316bf180a927c1933c5bf9f0a9dcda1d41951d204e018"
    end
  end

  on_linux do
    on_intel do
      url "https://github.com/anthonyGosme/speedrunner/releases/download/v0.4.2/speedrunner-x86_64-unknown-linux-musl.tar.gz"
      sha256 "e9f8ffd7e7431e7f1d8079551057f2ab2c39e7d54927f5f2d1145cfe7bd7a4e0"
    end
    on_arm do
      url "https://github.com/anthonyGosme/speedrunner/releases/download/v0.4.2/speedrunner-aarch64-unknown-linux-musl.tar.gz"
      sha256 "ba2bc6e3621e27b87bce3c4820a6c4606f9b2c53e7d2985d99ec95b612927268"
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
