# Homebrew Tap for BeigeBox

Official Homebrew tap for BeigeBox security tools and LLM proxy.

## Installation

Add this tap to your Homebrew installation:

```bash
brew tap RALaBarge/homebrew-beigebox
```

Then install any of the following formulas.

## Formulas

### beigebox (LLM Proxy)

OpenAI-compatible LLM proxy middleware with routing, caching, observability, and policy decisions.

```bash
brew install beigebox
beigebox --version
beigebox dial  # start the server
```

**Requirements:** Python 3.11+

**License:** AGPL-3.0 (with dual-license commercial option)

### bluetruth (Bluetooth Diagnostics)

Bluetooth diagnostic tool for device discovery, security assessment, and threat detection.

```bash
brew install bluetruth
bluetruth --version
bluetruth --help
```

**Requirements:** Python 3.10+, DBus (Linux) or native Bluetooth (macOS)

**License:** MIT

### embeddings-guardian (Security Library)

Security library for embedding-based content filtering and policy enforcement.

```bash
brew install embeddings-guardian
```

**Requirements:** Python 3.11+

**License:** MIT

**Note:** This is primarily a Python library. For application developers, install via:
```bash
pip install embeddings-guardian
```

## Updating Formulas

To update all formulas to the latest versions:

```bash
brew upgrade beigebox bluetruth embeddings-guardian
```

Or update a specific formula:

```bash
brew upgrade beigebox
```

## Uninstallation

```bash
brew uninstall beigebox bluetruth embeddings-guardian
brew untap RALaBarge/homebrew-beigebox
```

## Docker Alternative

All tools are also available as Docker images:

```bash
# BeigeBox
docker pull ralabarge/beigebox:1.3.5
docker run -d -p 1337:1337 ralabarge/beigebox:1.3.5

# BlueTruth
docker pull ralabarge/bluetruth:0.2.0
docker run -d --privileged ralabarge/bluetruth:0.2.0

# Embeddings Guardian
docker pull ralabarge/embeddings-guardian:0.1.0
```

## PyPI Alternative

All packages are available on PyPI:

```bash
pip install beigebox
pip install bluetruth
pip install embeddings-guardian
```

## Documentation

- **BeigeBox:** https://github.com/RALaBarge/beigebox
- **BlueTruth:** https://github.com/RALaBarge/beigebox/tree/main/beigebox/tools
- **Embeddings Guardian:** https://github.com/RALaBarge/beigebox/tree/main/beigebox/security

## Issues and Support

For issues, feature requests, or support, please visit:
https://github.com/RALaBarge/beigebox/issues

## License

- **beigebox:** AGPL-3.0 (see [COMMERCIAL_LICENSE.md](https://github.com/RALaBarge/beigebox/blob/main/COMMERCIAL_LICENSE.md))
- **bluetruth:** MIT
- **embeddings-guardian:** MIT
