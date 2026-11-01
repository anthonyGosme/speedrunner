# The formula of the anthonyGosme/homebrew-speedrunner tap: the release's
# static Linux binary, or its universal macOS one, as packaging/update.sh
# writes it.
class Speedrunner < Formula
  desc "Preloaded Python, Ruby, PHP and Node command-line tools, forked per call"
  homepage "https://github.com/anthonyGosme/speedrunner"
  version "0.4.0"
  license any_of: ["MIT", "Apache-2.0"]

  on_macos do
    on_intel do
      url "https://github.com/anthonyGosme/speedrunner/releases/download/v0.4.0/speedrunner-universal-apple-darwin.tar.gz"
      sha256 "a19d8adff7cf7e77e368bc5760e2419dfa603d1bf7bf9e5b96969ec4c5034475"
    end
    on_arm do
      url "https://github.com/anthonyGosme/speedrunner/releases/download/v0.4.0/speedrunner-universal-apple-darwin.tar.gz"
      sha256 "a19d8adff7cf7e77e368bc5760e2419dfa603d1bf7bf9e5b96969ec4c5034475"
    end
  end

  on_linux do
    on_intel do
      url "https://github.com/anthonyGosme/speedrunner/releases/download/v0.4.0/speedrunner-x86_64-unknown-linux-musl.tar.gz"
      sha256 "48fb0bdc9b1373cd9cedd6d25f475400dc1fa51f4d0592687e86a5a503490336"
    end
    on_arm do
      url "https://github.com/anthonyGosme/speedrunner/releases/download/v0.4.0/speedrunner-aarch64-unknown-linux-musl.tar.gz"
      sha256 "19ea13ad37d2aacc00bde909424a7965f19b0126f0f89142e1b0a7b8c35f8c4d"
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
