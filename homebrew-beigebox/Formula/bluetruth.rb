class Bluetruth < Formula
  desc "Bluetooth diagnostic tool for device discovery and security assessment"
  homepage "https://github.com/RALaBarge/beigebox"
  url "https://files.pythonhosted.org/packages/source/b/bluetruth/bluetruth-0.2.0.tar.gz"
  sha256 "please_compute_from_pypi"  # Will be filled with actual SHA256
  license "MIT"

  depends_on "python@3.10"
  depends_on "dbus"

  def install
    python_exe = Formula["python@3.10"].opt_bin/"python3.10"
    ENV.prepend_create_path "PYTHONPATH", libexec/"lib/python3.10/site-packages"
    system python_exe, "-m", "pip", "install",
           "--quiet", "--no-deps", "--prefix", libexec,
           buildpath
    bin.install_symlink Dir[libexec/"bin/*"]
  end

  def caveats
    <<~EOS
      BlueTracker requires DBus support for Bluetooth access.
      On Linux, ensure you have system packages installed:
        Ubuntu/Debian: sudo apt-get install libdbus-1-dev
        Fedora: sudo dnf install dbus-devel
      On macOS, DBus is available via Homebrew (installed as dependency).
    EOS
  end

  test do
    system bin/"bluetruth", "--version"
    system bin/"bluetruth", "--help"
  end
end
