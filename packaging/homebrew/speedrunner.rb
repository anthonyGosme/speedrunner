# The formula of the anthonyGosme/homebrew-speedrunner tap: the release's
# static Linux binary, or its universal macOS one, as packaging/update.sh
# writes it.
class Speedrunner < Formula
  desc "Preloaded Python, Ruby, PHP and Node command-line tools, forked per call"
  homepage "https://github.com/anthonyGosme/speedrunner"
  version "0.4.4"
  license any_of: ["MIT", "Apache-2.0"]

  on_macos do
    on_intel do
      url "https://github.com/anthonyGosme/speedrunner/releases/download/v0.4.4/speedrunner-universal-apple-darwin.tar.gz"
      sha256 "03dec5552624c5efc0e1264635f7212a322ac3ac11bfd4fc06f729e7fc773f11"
    end
    on_arm do
      url "https://github.com/anthonyGosme/speedrunner/releases/download/v0.4.4/speedrunner-universal-apple-darwin.tar.gz"
      sha256 "03dec5552624c5efc0e1264635f7212a322ac3ac11bfd4fc06f729e7fc773f11"
    end
  end

  on_linux do
    on_intel do
      url "https://github.com/anthonyGosme/speedrunner/releases/download/v0.4.4/speedrunner-x86_64-unknown-linux-musl.tar.gz"
      sha256 "fe7e758f9f2fb23d872aa1a311662da7ff96856180d6b44eb22d913561c8da10"
    end
    on_arm do
      url "https://github.com/anthonyGosme/speedrunner/releases/download/v0.4.4/speedrunner-aarch64-unknown-linux-musl.tar.gz"
      sha256 "91a365179f9ff729376853c04ceda33562bfa18b452ca6d046f515df22bb04d8"
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
