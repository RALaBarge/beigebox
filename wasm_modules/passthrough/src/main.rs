// BeigeBox WASM passthrough — reference implementation
//
// This is the simplest possible WASM transform module: it reads everything
// from stdin and writes it to stdout unchanged. Use it to verify your WASM
// pipeline is wired up correctly before writing a real transform.
//
// Build:
//   rustup target add wasm32-wasip1
//   cargo build --target wasm32-wasip1 --release
//   cp target/wasm32-wasip1/release/passthrough.wasm ../../passthrough.wasm
//
// Then in config.yaml set:
//   wasm:
//     enabled: true
//     modules:
//       passthrough:
//         path: "./wasm_modules/passthrough.wasm"
//         enabled: true

use std::io::{self, Read, Write};

fn main() {
    let mut input = Vec::new();
    io::stdin()
        .read_to_end(&mut input)
        .expect("failed to read stdin");
    io::stdout()
        .write_all(&input)
        .expect("failed to write stdout");
}
