// BeigeBox WASM module: json_extractor
//
// Extracts the first valid JSON object or array from an LLM response.
// Handles common LLM output patterns:
//   - Raw JSON (passes through)
//   - JSON in ```json...``` or ```...``` code fences
//   - JSON embedded in surrounding prose
//
// Use case: structured-extraction prompts where the model wraps JSON in
// explanation text. The extracted JSON replaces the full content field,
// making the response directly machine-parseable without further parsing.
//
// If no valid JSON is found, the response passes through unmodified.
//
// Build:
//   rustup target add wasm32-wasip1
//   cargo build --target wasm32-wasip1 --release
//   cp target/wasm32-wasip1/release/json_extractor.wasm ../../json_extractor.wasm
//
// Register in config.yaml:
//   wasm:
//     modules:
//       json_extractor:
//         path: "./wasm_modules/json_extractor.wasm"
//         enabled: true
//         description: "Extract first valid JSON from mixed prose+JSON responses"

use std::io::{self, Read, Write};

/// Find the closing sequence `seq` in `chars` starting at `from`.
/// Returns the index of the first character of the closing sequence.
fn find_seq(chars: &[char], from: usize, seq: &[char]) -> Option<usize> {
    let n = seq.len();
    if n == 0 {
        return Some(from);
    }
    for i in from..=chars.len().saturating_sub(n) {
        if &chars[i..i + n] == seq {
            return Some(i);
        }
    }
    None
}

/// Extract content between the first pair of matching code fences.
/// Handles ```json\n...\n``` and ```\n...\n``` patterns.
fn extract_from_fence(chars: &[char]) -> Option<String> {
    let fence = ['`', '`', '`'];
    let open_pos = find_seq(chars, 0, &fence)?;
    let after_fence = open_pos + 3;

    // Skip optional language tag to the first newline
    let content_start = find_seq(chars, after_fence, &['\n'])? + 1;

    let close_pos = find_seq(chars, content_start, &fence)?;

    Some(chars[content_start..close_pos].iter().collect())
}

/// Find a balanced JSON object `{...}` or array `[...]` starting at `from`.
/// Returns `(json_string, end_index)` where end_index is exclusive.
fn find_balanced_json(chars: &[char], from: usize) -> Option<String> {
    let first = *chars.get(from)?;
    let (open_ch, close_ch) = match first {
        '{' => ('{', '}'),
        '[' => ('[', ']'),
        _ => return None,
    };

    let mut depth: usize = 0;
    let mut in_string = false;
    let mut escaped = false;
    let mut end = 0;

    for (i, &c) in chars[from..].iter().enumerate() {
        let abs = from + i;
        if escaped {
            escaped = false;
            continue;
        }
        if in_string {
            match c {
                '\\' => escaped = true,
                '"' => in_string = false,
                _ => {}
            }
            continue;
        }
        match c {
            '"' => in_string = true,
            c if c == open_ch => depth += 1,
            c if c == close_ch => {
                depth -= 1;
                if depth == 0 {
                    end = abs + 1;
                    break;
                }
            }
            _ => {}
        }
    }

    if end == 0 {
        return None;
    }

    Some(chars[from..end].iter().collect())
}

/// Try to parse `s` as JSON. Returns pretty-printed version on success.
fn try_parse(s: &str) -> Option<String> {
    serde_json::from_str::<serde_json::Value>(s.trim())
        .ok()
        .and_then(|v| serde_json::to_string_pretty(&v).ok())
}

/// Main extraction logic: tries multiple strategies in order.
fn extract_json(text: &str) -> Option<String> {
    // 1. Whole text is valid JSON
    if let Some(s) = try_parse(text) {
        return Some(s);
    }

    let chars: Vec<char> = text.chars().collect();

    // 2. JSON inside a code fence
    if let Some(fenced) = extract_from_fence(&chars) {
        if let Some(s) = try_parse(&fenced) {
            return Some(s);
        }
    }

    // 3. Scan for first { or [ and try to parse a balanced block from there
    for (i, &c) in chars.iter().enumerate() {
        if c == '{' || c == '[' {
            if let Some(json_str) = find_balanced_json(&chars, i) {
                if let Some(s) = try_parse(&json_str) {
                    return Some(s);
                }
            }
        }
    }

    None
}

fn process(input: &str) -> String {
    // JSON (transform_response) mode: modify the content field
    if let Ok(mut v) = serde_json::from_str::<serde_json::Value>(input) {
        if let Some(content) = v["choices"][0]["message"]["content"].as_str() {
            if let Some(extracted) = extract_json(content) {
                v["choices"][0]["message"]["content"] = serde_json::json!(extracted);
                return serde_json::to_string(&v).unwrap_or_else(|_| input.to_string());
            }
        }
        // No JSON found in content — pass through unchanged
        return input.to_string();
    }

    // Plain text (transform_text) mode: return extracted JSON or passthrough
    extract_json(input).unwrap_or_else(|| input.to_string())
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
    fn passthrough_raw_json() {
        let s = r#"{"a":1}"#;
        let extracted = extract_json(s).unwrap();
        let v: serde_json::Value = serde_json::from_str(&extracted).unwrap();
        assert_eq!(v["a"], 1);
    }

    #[test]
    fn extracts_from_json_fence() {
        let text = "Here is the data:\n```json\n{\"a\":1}\n```\nDone.";
        let extracted = extract_json(text).unwrap();
        let v: serde_json::Value = serde_json::from_str(&extracted).unwrap();
        assert_eq!(v["a"], 1);
    }

    #[test]
    fn extracts_from_plain_fence() {
        let text = "Result:\n```\n{\"x\":42}\n```";
        let extracted = extract_json(text).unwrap();
        let v: serde_json::Value = serde_json::from_str(&extracted).unwrap();
        assert_eq!(v["x"], 42);
    }

    #[test]
    fn extracts_inline_json() {
        let text = "The answer is: {\"result\": \"yes\"} as you can see.";
        let extracted = extract_json(text).unwrap();
        let v: serde_json::Value = serde_json::from_str(&extracted).unwrap();
        assert_eq!(v["result"].as_str().unwrap(), "yes");
    }

    #[test]
    fn extracts_array() {
        let text = "Items: [1, 2, 3]";
        let extracted = extract_json(text).unwrap();
        let v: serde_json::Value = serde_json::from_str(&extracted).unwrap();
        assert_eq!(v[0], 1);
    }

    #[test]
    fn passthrough_when_no_json() {
        assert!(extract_json("No JSON here at all.").is_none());
    }

    #[test]
    fn nested_json() {
        let text = r#"Here: {"a":{"b":2}}"#;
        let extracted = extract_json(text).unwrap();
        let v: serde_json::Value = serde_json::from_str(&extracted).unwrap();
        assert_eq!(v["a"]["b"], 2);
    }

    #[test]
    fn json_with_string_containing_braces() {
        let text = r#"Data: {"msg": "use {} here"}"#;
        let extracted = extract_json(text).unwrap();
        let v: serde_json::Value = serde_json::from_str(&extracted).unwrap();
        assert_eq!(v["msg"].as_str().unwrap(), "use {} here");
    }
}
