# BeigeBox WASM module build
#
# Usage:
#   make              — build all Rust modules
#   make pdf_oxide    — copy pre-built pdf-oxide-wasi binary into place
#   make all          — build everything including pdf_oxide
#   make clean        — remove compiled outputs
#
# Prerequisites:
#   rustup target add wasm32-wasip1
#   (pdf-oxide-wasi must be built separately at ~/ai-stack/pdf-oxide-wasi/)

CARGO   = cargo
TARGET  = wasm32-wasip1
PROFILE = release

# Rust modules in this directory (each is a Cargo workspace member)
RUST_MODULES = \
	json_extractor \
	markdown_stripper \
	opener_strip \
	passthrough \
	pii_redactor

# External pre-built binary (Rust nightly required — build separately)
PDF_OXIDE_SRC = $(HOME)/ai-stack/pdf-oxide-wasi/target/wasm32-wasip1/release/pdf-oxide-wasi.wasm
PDF_OXIDE_DST = pdf_oxide/pdf-oxide-wasi.wasm

.PHONY: all build $(RUST_MODULES) pdf_oxide clean

all: build pdf_oxide

build: $(RUST_MODULES)

$(RUST_MODULES):
	@echo "==> Building $@"
	cd $@ && $(CARGO) build --$(PROFILE) --target $(TARGET)

pdf_oxide:
	@mkdir -p pdf_oxide
	@if [ -f "$(PDF_OXIDE_SRC)" ]; then \
		cp "$(PDF_OXIDE_SRC)" "$(PDF_OXIDE_DST)" && \
		echo "==> Copied pdf-oxide-wasi.wasm ($(shell du -sh $(PDF_OXIDE_DST) | cut -f1))"; \
	else \
		echo "WARN: $(PDF_OXIDE_SRC) not found."; \
		echo "      Build it first: cd ~/ai-stack/pdf-oxide-wasi && cargo +nightly build --release --target wasm32-wasip1"; \
	fi

clean:
	@for m in $(RUST_MODULES); do \
		echo "==> Cleaning $$m"; \
		cd $$m && $(CARGO) clean; cd ..; \
	done
	rm -f $(PDF_OXIDE_DST)
