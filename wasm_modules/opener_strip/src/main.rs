// BeigeBox WASM module: opener_strip
//
// Strips sycophantic opener phrases from LLM responses.
// Works in both the non-streaming (JSON dict) and streaming (plain text) paths:
//   - If stdin is valid JSON, parses it, strips the opener from the content
//     field, and outputs the modified JSON. (used by transform_response)
//   - If stdin is plain text, strips the opener and outputs text.
//     (used by transform_text)
//
// Examples of openers removed:
//   "Certainly! Here is the answer…"   → "Here is the answer…"
//   "Of course! I'd be happy to…"      → "I'd be happy to…"
//   "Great question! The answer is…"   → "The answer is…"
//
// Build:
//   rustup target add wasm32-wasip1
//   cargo build --target wasm32-wasip1 --release
//   cp target/wasm32-wasip1/release/opener_strip.wasm ../../opener_strip.wasm
//
// Register in config.yaml:
//   wasm:
//     enabled: true
//     modules:
//       opener_strip:
//         path: "./wasm_modules/opener_strip.wasm"
//         enabled: true
//         description: "Strip sycophantic opener phrases from responses"

use std::io::{self, Read, Write};

/// Sycophantic openers to detect and remove.
/// Each entry is (prefix, whether it ends with a sentence boundary).
/// The match is case-sensitive on the first character.
const OPENERS: &[&str] = &[
    "Certainly! ",
    "Certainly, ",
    "Certainly!\n",
    "Of course! ",
    "Of course, ",
    "Of course!\n",
    "Sure! ",
    "Sure, ",
    "Sure!\n",
    "Absolutely! ",
    "Absolutely, ",
    "Absolutely!\n",
    "Great question! ",
    "Great question!\n",
    "That's a great question! ",
    "That's a great question!\n",
    "I'd be happy to help! ",
    "I'd be happy to help! ",
    "I'd be happy to help!\n",
    "I'm happy to help! ",
    "I'm happy to help!\n",
    "I would be happy to help! ",
    "I would be happy to help!\n",
    "Happy to help! ",
    "Happy to help!\n",
    "As an AI language model, ",
    "As an AI assistant, ",
    "As an AI, ",
];

/// Strip one leading opener from `text`, if present.
/// Returns the stripped string with the first letter capitalized if needed.
fn strip_opener(text: &str) -> String {
    for &opener in OPENERS {
        if text.starts_with(opener) {
            let rest = text[opener.len()..].trim_start();
            return capitalize_first(rest);
        }
    }
    text.to_string()
}

/// Capitalize the first Unicode character of a string.
fn capitalize_first(s: &str) -> String {
    let mut chars = s.chars();
    match chars.next() {
        None => String::new(),
        Some(c) => {
            let mut upper = c.to_uppercase().to_string();
            upper.push_str(chars.as_str());
            upper
        }
    }
}

/// Process input: try as JSON first, fall back to plain text.
fn process(input: &str) -> String {
    // Try to parse as an OpenAI-compatible response JSON
    if let Ok(mut v) = serde_json::from_str::<serde_json::Value>(input) {
        // Non-streaming: full response dict — modify choices[0].message.content
        if let Some(content) = v["choices"][0]["message"]["content"].as_str() {
            let stripped = strip_opener(content);
            v["choices"][0]["message"]["content"] = serde_json::json!(stripped);
        }
        return serde_json::to_string(&v).unwrap_or_else(|_| input.to_string());
    }
    // Streaming assembled text (or any plain text): strip and return
    strip_opener(input)
}

fn main() {
    let mut input = String::new();
    io::stdin()
        .read_to_string(&mut input)
        .expect("failed to read stdin");

    let result = process(&input);
    io::stdout()
        .write_all(result.as_bytes())
        .expect("failed to write stdout");
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn strips_certainly() {
        assert_eq!(strip_opener("Certainly! Here is the plan."), "Here is the plan.");
    }

    #[test]
    fn strips_of_course() {
        assert_eq!(strip_opener("Of course! I can help with that."), "I can help with that.");
    }

    #[test]
    fn no_match_passes_through() {
        let s = "The answer is 42.";
        assert_eq!(strip_opener(s), s);
    }

    #[test]
    fn capitalizes_after_strip() {
        assert_eq!(strip_opener("Sure! the answer is yes."), "The answer is yes.");
    }
}
