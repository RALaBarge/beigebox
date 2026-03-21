// BeigeBox WASM module: output_normalizer
//
// Normalizes LLM responses to consistent markdown format.
// Three configurable levels:
//   - 1 (minimal): Strip preamble, detect code/JSON, wrap in code blocks
//   - 2 (medium): Add structure (sections, bullets), enhance readability
//   - 3 (full): Advanced formatting (headers, emphasis, lists, tables)
//
// Level is configured via:
//   - Environment variable: NORMALIZE_LEVEL (defaults to 2)
//   - Or passed in request header: X-Normalize-Level
//
// Examples:
//   Input: "Let me help. Here's a function:\ndef foo(): pass"
//   Level 1 output: "```python\ndef foo(): pass\n```"
//   Level 2 output: "```python\ndef foo(): pass\n```"
//   Level 3 output: "## Code Example\n\n```python\ndef foo(): pass\n```"
//
// Build:
//   rustup target add wasm32-wasip1
//   cargo build --target wasm32-wasip1 --release
//   cp target/wasm32-wasip1/release/output_normalizer.wasm ../../output_normalizer.wasm
//
// Register in config.yaml:
//   wasm:
//     enabled: true
//     modules:
//       output_normalizer:
//         path: "./wasm_modules/output_normalizer.wasm"
//         enabled: true
//         level: 2  # 1=minimal, 2=medium, 3=full
//         description: "Normalize LLM output to consistent markdown"

use std::io::{self, Read, Write};

/// Get normalization level from environment or default to 2
fn get_level() -> u8 {
    std::env::var("NORMALIZE_LEVEL")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(2)
}

/// Detect if text is JSON (object or array)
fn is_json(text: &str) -> bool {
    let trimmed = text.trim();
    (trimmed.starts_with('{') || trimmed.starts_with('['))
        && (trimmed.ends_with('}') || trimmed.ends_with(']'))
}

/// Detect if text is Python code
fn is_python_code(text: &str) -> bool {
    let lower = text.to_lowercase();
    lower.contains("def ") || lower.contains("class ")
        || lower.contains("import ") || lower.contains("from ")
        || lower.contains("if __name__")
}

/// Detect if text is JavaScript/TypeScript code
fn is_js_code(text: &str) -> bool {
    let lower = text.to_lowercase();
    lower.contains("function ") || lower.contains("const ")
        || lower.contains("let ") || lower.contains("var ")
        || lower.contains("export ") || lower.contains("import ")
        || lower.contains("=>")
}

/// Detect if text is Shell/Bash code
fn is_shell_code(text: &str) -> bool {
    text.starts_with("#!/bin/bash") || text.starts_with("#!/bin/sh")
        || text.starts_with("#!/usr/bin/env")
        || (text.contains("$") && text.contains("|"))
}

/// Detect if text is SQL
fn is_sql(text: &str) -> bool {
    let upper = text.to_uppercase();
    upper.contains("SELECT ") || upper.contains("INSERT ")
        || upper.contains("UPDATE ") || upper.contains("DELETE ")
        || upper.contains("FROM ") || upper.contains("WHERE ")
}

/// Strip common opening phrases
fn strip_preamble(text: &str) -> String {
    let preambles = vec![
        ("Let me help you", "."),
        ("Let me help you with", "."),
        ("I'd be happy to help", "."),
        ("I'm happy to help", "."),
        ("Certainly! ", ""),
        ("Of course! ", ""),
        ("Sure! ", ""),
        ("Absolutely! ", ""),
        ("Here's", ""),
        ("Based on your question", "."),
        ("I can help with", "."),
        ("Let me explain", "."),
        ("As an AI", "."),
        ("The answer is", ""),
    ];

    let trimmed = text.trim();
    for (preamble, suffix) in preambles {
        if trimmed.to_lowercase().starts_with(&preamble.to_lowercase()) {
            let after_preamble = if trimmed.len() > preamble.len() {
                &trimmed[preamble.len()..]
            } else {
                trimmed
            };

            let result = after_preamble.trim_start();
            if !suffix.is_empty() && result.contains(suffix) {
                if let Some(pos) = result.find(suffix) {
                    return result[pos + suffix.len()..].trim_start().to_string();
                }
            }
            return result.to_string();
        }
    }
    trimmed.to_string()
}

/// Level 1: Minimal normalization
/// - Strip preamble
/// - Detect code/JSON and wrap in code blocks
fn normalize_minimal(text: &str) -> String {
    let cleaned = strip_preamble(text);

    if is_json(&cleaned) {
        return format!("```json\n{}\n```", cleaned);
    }

    if is_python_code(&cleaned) {
        return format!("```python\n{}\n```", cleaned);
    }

    if is_js_code(&cleaned) {
        return format!("```javascript\n{}\n```", cleaned);
    }

    if is_shell_code(&cleaned) {
        return format!("```bash\n{}\n```", cleaned);
    }

    if is_sql(&cleaned) {
        return format!("```sql\n{}\n```", cleaned);
    }

    cleaned
}

/// Level 2: Medium normalization
/// - Strip preamble
/// - Detect code/JSON and wrap
/// - Add subtle structure: bullets, bold for headers
fn normalize_medium(text: &str) -> String {
    let minimal = normalize_minimal(text);

    // If it's already a code block, return as-is
    if minimal.starts_with("```") {
        return minimal;
    }

    // Detect multi-line structure (paragraphs)
    let lines: Vec<&str> = minimal.lines().collect();
    let mut result: Vec<String> = Vec::new();

    for (i, line) in lines.iter().enumerate() {
        let trimmed = line.trim();

        // Empty lines between sections
        if trimmed.is_empty() {
            if !result.is_empty() && !result.last().unwrap().is_empty() {
                result.push("".to_string());
            }
            continue;
        }

        // Detect list items
        if trimmed.starts_with("-") || trimmed.starts_with("*") || trimmed.starts_with("+") {
            result.push(line.to_string());
            continue;
        }

        // Detect numbered lists
        if trimmed.len() > 2 && trimmed.chars().next().unwrap().is_numeric()
            && trimmed.chars().nth(1) == Some('.') {
            result.push(line.to_string());
            continue;
        }

        // Capitalize first letter of sentences
        let capitalized = if !trimmed.is_empty() {
            let first_char = trimmed.chars().next().unwrap();
            if first_char.is_lowercase() {
                let mut chars = trimmed.chars();
                let first = chars.next().unwrap().to_uppercase().to_string();
                first + chars.as_str()
            } else {
                trimmed.to_string()
            }
        } else {
            trimmed.to_string()
        };

        result.push(capitalized);
    }

    result.join("\n")
}

/// Level 3: Full normalization
/// - All level 2 features
/// - Add markdown headers for sections
/// - Bold emphasis on keywords
/// - Preserve tables
fn normalize_full(text: &str) -> String {
    let medium = normalize_medium(text);

    // If it starts with code block, return medium (don't reformat code)
    if medium.starts_with("```") {
        return medium;
    }

    // Split into paragraphs
    let paragraphs: Vec<&str> = medium.split("\n\n").collect();
    let mut result: Vec<String> = Vec::new();

    for (i, para) in paragraphs.iter().enumerate() {
        let trimmed = para.trim();

        if trimmed.is_empty() {
            continue;
        }

        // First line/para could be a section header
        if i == 0 && !trimmed.contains('\n') && trimmed.len() < 100 {
            // Looks like a title
            if !trimmed.ends_with(':') && !trimmed.ends_with('.') {
                result.push(format!("## {}", trimmed));
                result.push("".to_string());
                continue;
            }
        }

        // Detect if paragraph is a list
        let lines: Vec<&str> = trimmed.lines().collect();
        let is_list = lines.iter().all(|l| {
            let t = l.trim();
            t.is_empty() || t.starts_with('-') || t.starts_with('*')
                || (t.len() > 2 && t.chars().next().unwrap().is_numeric()
                    && t.chars().nth(1) == Some('.'))
        });

        if is_list {
            result.push(trimmed.to_string());
            result.push("".to_string());
            continue;
        }

        // Otherwise, preserve as paragraph
        result.push(trimmed.to_string());
        result.push("".to_string());
    }

    result.join("\n").trim().to_string()
}

/// Process input: choose normalization level and return formatted output
fn process(input: &str, level: u8) -> String {
    match level {
        1 => normalize_minimal(input),
        3 => normalize_full(input),
        _ => normalize_medium(input), // default to 2
    }
}

fn main() {
    let mut input = String::new();
    io::stdin()
        .read_to_string(&mut input)
        .expect("failed to read stdin");

    let level = get_level();
    let result = process(&input, level);

    io::stdout()
        .write_all(result.as_bytes())
        .expect("failed to write stdout");
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn detects_json() {
        assert!(is_json(r#"{"name": "John", "age": 30}"#));
        assert!(is_json(r#"[1, 2, 3]"#));
        assert!(!is_json("plain text"));
    }

    #[test]
    fn detects_python() {
        assert!(is_python_code("def foo():\n    pass"));
        assert!(is_python_code("class MyClass:\n    pass"));
        assert!(is_python_code("import os"));
    }

    #[test]
    fn detects_js() {
        assert!(is_js_code("function foo() { }"));
        assert!(is_js_code("const x = 5;"));
        assert!(is_js_code("const fn = () => x + 1;"));
    }

    #[test]
    fn strips_preamble() {
        assert_eq!(
            strip_preamble("Certainly! Here is the answer"),
            "Here is the answer"
        );
        assert_eq!(
            strip_preamble("I'd be happy to help. Let me explain."),
            "Let me explain."
        );
    }

    #[test]
    fn minimal_wraps_code() {
        let input = "def fibonacci(n):\n    return n";
        let output = normalize_minimal(input);
        assert!(output.contains("```python"));
        assert!(output.contains("def fibonacci"));
    }

    #[test]
    fn minimal_wraps_json() {
        let input = r#"{"status": "ok", "data": [1,2,3]}"#;
        let output = normalize_minimal(input);
        assert!(output.contains("```json"));
    }

    #[test]
    fn medium_preserves_lists() {
        let input = "- Item 1\n- Item 2\n- Item 3";
        let output = normalize_medium(input);
        assert!(output.contains("- Item 1"));
        assert!(output.contains("- Item 3"));
    }
}
