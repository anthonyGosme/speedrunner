# The formula of the anthonyGosme/homebrew-speedrunner tap: the release's
# static Linux binary, or its universal macOS one, as packaging/update.sh
# writes it.
class Speedrunner < Formula
  desc "Preloaded Python, Ruby, PHP and Node command-line tools, forked per call"
  homepage "https://github.com/anthonyGosme/speedrunner"
  version "0.4.5"
  license any_of: ["MIT", "Apache-2.0"]

  on_macos do
    on_intel do
      url "https://github.com/anthonyGosme/speedrunner/releases/download/v0.4.5/speedrunner-universal-apple-darwin.tar.gz"
      sha256 "939e6df516bce3a3bc906140d297667b1c34f7732841baa361c53864cf24ea17"
    end
    on_arm do
      url "https://github.com/anthonyGosme/speedrunner/releases/download/v0.4.5/speedrunner-universal-apple-darwin.tar.gz"
      sha256 "939e6df516bce3a3bc906140d297667b1c34f7732841baa361c53864cf24ea17"
    end
  end

  on_linux do
    on_intel do
      url "https://github.com/anthonyGosme/speedrunner/releases/download/v0.4.5/speedrunner-x86_64-unknown-linux-musl.tar.gz"
      sha256 "e0231716712f5eabfab15ba5f16a04ee94d01e51d3dbced8cd0966084739e785"
    end
    on_arm do
      url "https://github.com/anthonyGosme/speedrunner/releases/download/v0.4.5/speedrunner-aarch64-unknown-linux-musl.tar.gz"
      sha256 "aa270fe4e09736f761e16a7a8cdd1094e67a89070d37b2959c0328d18b673ba1"
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
