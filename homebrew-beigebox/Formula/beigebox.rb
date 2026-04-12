class Beigebox < Formula
  desc "OpenAI-compatible LLM proxy middleware with routing, caching, and observability"
  homepage "https://github.com/RALaBarge/beigebox"
  url "https://files.pythonhosted.org/packages/source/b/beigebox/beigebox-1.3.5.tar.gz"
  sha256 "please_compute_from_pypi"  # Will be filled with actual SHA256
  license "AGPL-3.0-only"

  depends_on "python@3.11"

  def install
    python_exe = Formula["python@3.11"].opt_bin/"python3.11"
    ENV.prepend_create_path "PYTHONPATH", libexec/"lib/python3.11/site-packages"
    system python_exe, "-m", "pip", "install",
           "--quiet", "--no-deps", "--prefix", libexec,
           buildpath
    bin.install_symlink Dir[libexec/"bin/*"]
  end

  test do
    system bin/"beigebox", "--version"
    system bin/"beigebox", "--help"
  end
end
