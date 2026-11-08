# The formula of the anthonyGosme/homebrew-speedrunner tap: the release's
# static Linux binary, or its universal macOS one, as packaging/update.sh
# writes it.
class Speedrunner < Formula
  desc "Preloaded Python, Ruby, PHP and Node command-line tools, forked per call"
  homepage "https://github.com/anthonyGosme/speedrunner"
  version "0.4.10"
  license any_of: ["MIT", "Apache-2.0"]

  on_macos do
    on_intel do
      url "https://github.com/anthonyGosme/speedrunner/releases/download/v0.4.10/speedrunner-universal-apple-darwin.tar.gz"
      sha256 "3e1254120a1b4daf7151955e024265bc4853aaadae8da843d15fd9e664f81262"
    end
    on_arm do
      url "https://github.com/anthonyGosme/speedrunner/releases/download/v0.4.10/speedrunner-universal-apple-darwin.tar.gz"
      sha256 "3e1254120a1b4daf7151955e024265bc4853aaadae8da843d15fd9e664f81262"
    end
  end

  on_linux do
    on_intel do
      url "https://github.com/anthonyGosme/speedrunner/releases/download/v0.4.10/speedrunner-x86_64-unknown-linux-musl.tar.gz"
      sha256 "6921307ef333ca917ee56769da2fe673efd506da2f063538d9ae046ac08ae49a"
    end
    on_arm do
      url "https://github.com/anthonyGosme/speedrunner/releases/download/v0.4.10/speedrunner-aarch64-unknown-linux-musl.tar.gz"
      sha256 "f40a33a23a26c4600dbddfaabd4dabc1c191633d52494860941b5c141a8bcfc2"
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
