# The formula of the anthonyGosme/homebrew-speedrunner tap: the release's
# static Linux binary, or its universal macOS one, as packaging/update.sh
# writes it.
class Speedrunner < Formula
  desc "Preloaded Python, Ruby, PHP and Node command-line tools, forked per call"
  homepage "https://github.com/anthonyGosme/speedrunner"
  version "0.4.8"
  license any_of: ["MIT", "Apache-2.0"]

  on_macos do
    on_intel do
      url "https://github.com/anthonyGosme/speedrunner/releases/download/v0.4.8/speedrunner-universal-apple-darwin.tar.gz"
      sha256 "1b0b6e31d84ff71026b03e38941d9465482fb2a037c09636e882afa2d6c486b8"
    end
    on_arm do
      url "https://github.com/anthonyGosme/speedrunner/releases/download/v0.4.8/speedrunner-universal-apple-darwin.tar.gz"
      sha256 "1b0b6e31d84ff71026b03e38941d9465482fb2a037c09636e882afa2d6c486b8"
    end
  end

  on_linux do
    on_intel do
      url "https://github.com/anthonyGosme/speedrunner/releases/download/v0.4.8/speedrunner-x86_64-unknown-linux-musl.tar.gz"
      sha256 "9286736411737f75a0bfcccedc3b3c933f3008c31f3ba04b56bcdc2484fa45f8"
    end
    on_arm do
      url "https://github.com/anthonyGosme/speedrunner/releases/download/v0.4.8/speedrunner-aarch64-unknown-linux-musl.tar.gz"
      sha256 "227c2c38091a40c54d537ec6b4bc24d9587ef7576f6b2d70884accf289a06c1a"
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
