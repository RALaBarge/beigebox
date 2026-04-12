class EmbeddingsGuardian < Formula
  desc "Security library for embedding-based content filtering and policy enforcement"
  homepage "https://github.com/RALaBarge/beigebox"
  url "https://files.pythonhosted.org/packages/source/e/embeddings-guardian/embeddings-guardian-0.1.0.tar.gz"
  sha256 "please_compute_from_pypi"  # Will be filled with actual SHA256
  license "MIT"

  depends_on "python@3.11"

  def install
    python_exe = Formula["python@3.11"].opt_bin/"python3.11"
    ENV.prepend_create_path "PYTHONPATH", libexec/"lib/python3.11/site-packages"
    system python_exe, "-m", "pip", "install",
           "--quiet", "--no-deps", "--prefix", libexec,
           buildpath
    bin.install_symlink Dir[libexec/"bin/*"]
  end

  def caveats
    <<~EOS
      embeddings-guardian is primarily a library for use in Python projects.
      To use it in your own code:
        pip install embeddings-guardian
      Or add to your project's requirements.txt:
        embeddings-guardian>=0.1.0
    EOS
  end

  test do
    system bin/"python3.11", "-c", "import embeddings_guardian; print(embeddings_guardian.__version__)"
  end
end
