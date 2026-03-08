// BeigeBox WASM module: markdown_stripper
//
// Strips Markdown formatting from LLM responses, producing plain text
// suitable for TTS (text-to-speech) or other contexts where Markdown
// syntax would be read aloud literally.
//
// Block-level (per line):
//   - ATX headers:       # Heading → Heading
//   - Horizontal rules:  ---, ***, ___ → (line removed)
//   - Code fences:       ``` lines → (removed, code content kept)
//   - Blockquotes:       > text → text
//   - List items:        - item / * item / 1. item → item
//
// Inline (within each line):
//   - Bold+italic:  ***text*** → text
//   - Bold:         **text** / __text__ → text
//   - Italic:       *text* / _text_ → text
//   - Inline code:  `code` / ``code`` → code
//   - Links:        [text](url) → text
//   - Images:       ![alt](url) → (removed)
//
// Build:
//   rustup target add wasm32-wasip1
//   cargo build --target wasm32-wasip1 --release
//   cp target/wasm32-wasip1/release/markdown_stripper.wasm ../../markdown_stripper.wasm
//
// Register in config.yaml:
//   wasm:
//     modules:
//       markdown_stripper:
//         path: "./wasm_modules/markdown_stripper.wasm"
//         enabled: true
//         description: "Strip Markdown formatting for plain-text / TTS output"

use std::io::{self, Read, Write};

// ── Helpers ──────────────────────────────────────────────────────────────────

/// Find `seq` in `chars` starting at `from`. Returns the start index of the match.
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

/// Check whether `chars[from..]` starts with `seq`.
fn starts_with_seq(chars: &[char], from: usize, seq: &[char]) -> bool {
    let n = seq.len();
    from + n <= chars.len() && &chars[from..from + n] == seq
}

// ── Inline stripping ─────────────────────────────────────────────────────────

/// Try to consume a Markdown link or image reference: `[text](url)` or `![alt](url)`.
/// Caller passes `chars` positioned at the `[`. Returns `(inner_text, chars_consumed)`.
/// Returns None if it doesn't look like a valid link.
fn try_link(chars: &[char], from: usize) -> Option<(String, usize)> {
    // chars[from] == '['
    let close_bracket = find_seq(chars, from + 1, &[']'])?;
    // Must be followed by '('
    if chars.get(close_bracket + 1) != Some(&'(') {
        return None;
    }
    let close_paren = find_seq(chars, close_bracket + 2, &[')'])?;
    let text: String = chars[from + 1..close_bracket].iter().collect();
    let consumed = close_paren + 1 - from;
    Some((text, consumed))
}

/// Strip inline Markdown from a line given as a char slice.
fn strip_inline(chars: &[char]) -> String {
    let mut result = String::with_capacity(chars.len());
    let mut i = 0;

    while i < chars.len() {
        // ── Images: ![alt](url) → removed ───────────────────────────────────
        if chars[i] == '!' && chars.get(i + 1) == Some(&'[') {
            if let Some((_text, consumed)) = try_link(chars, i + 1) {
                i += 1 + consumed; // skip '!' + link
                continue;
            }
        }

        // ── Links: [text](url) → text ────────────────────────────────────────
        if chars[i] == '[' {
            if let Some((text, consumed)) = try_link(chars, i) {
                result.push_str(&text);
                i += consumed;
                continue;
            }
        }

        // ── Inline code: `code` or ``code`` ─────────────────────────────────
        if chars[i] == '`' {
            let tick_count = chars[i..].iter().take_while(|&&c| c == '`').count();
            let close: Vec<char> = vec!['`'; tick_count];
            let content_start = i + tick_count;
            if let Some(close_pos) = find_seq(chars, content_start, &close) {
                let code: String = chars[content_start..close_pos].iter().collect();
                result.push_str(&code);
                i = close_pos + tick_count;
                continue;
            }
        }

        // ── Bold+italic: ***text*** ──────────────────────────────────────────
        if starts_with_seq(chars, i, &['*', '*', '*']) {
            let inner_start = i + 3;
            if let Some(end) = find_seq(chars, inner_start, &['*', '*', '*']) {
                let inner: String = chars[inner_start..end].iter().collect();
                result.push_str(&inner);
                i = end + 3;
                continue;
            }
        }

        // ── Bold: **text** ───────────────────────────────────────────────────
        if starts_with_seq(chars, i, &['*', '*']) {
            let inner_start = i + 2;
            if let Some(end) = find_seq(chars, inner_start, &['*', '*']) {
                let inner: String = chars[inner_start..end].iter().collect();
                result.push_str(&inner);
                i = end + 2;
                continue;
            }
        }

        // ── Bold: __text__ ───────────────────────────────────────────────────
        if starts_with_seq(chars, i, &['_', '_']) {
            let inner_start = i + 2;
            if let Some(end) = find_seq(chars, inner_start, &['_', '_']) {
                let inner: String = chars[inner_start..end].iter().collect();
                result.push_str(&inner);
                i = end + 2;
                continue;
            }
        }

        // ── Italic: *text* ───────────────────────────────────────────────────
        if chars[i] == '*' {
            let inner_start = i + 1;
            if let Some(end) = find_seq(chars, inner_start, &['*']) {
                let inner: String = chars[inner_start..end].iter().collect();
                result.push_str(&inner);
                i = end + 1;
                continue;
            }
        }

        // ── Italic: _text_ ───────────────────────────────────────────────────
        if chars[i] == '_' {
            let inner_start = i + 1;
            if let Some(end) = find_seq(chars, inner_start, &['_']) {
                let inner: String = chars[inner_start..end].iter().collect();
                result.push_str(&inner);
                i = end + 1;
                continue;
            }
        }

        result.push(chars[i]);
        i += 1;
    }

    result
}

// ── Block stripping ───────────────────────────────────────────────────────────

/// Returns true if the line is a horizontal rule (3+ of -, *, or _ with optional spaces).
fn is_hr(line: &str) -> bool {
    let trimmed = line.trim();
    if trimmed.len() < 3 {
        return false;
    }
    let first = trimmed.chars().next().unwrap();
    if !matches!(first, '-' | '*' | '_') {
        return false;
    }
    trimmed.chars().all(|c| c == first || c == ' ')
}

/// Strip block-level and inline Markdown from a full text string.
fn strip_markdown(text: &str) -> String {
    let mut output: Vec<String> = Vec::new();
    let mut in_fence = false;

    for line in text.lines() {
        // ── Code fence boundaries ────────────────────────────────────────────
        let ltrim = line.trim_start();
        if ltrim.starts_with("```") {
            in_fence = !in_fence;
            // Remove the fence line itself; code content is kept as-is
            continue;
        }

        // Inside a fence: pass content through without inline processing
        if in_fence {
            output.push(line.to_string());
            continue;
        }

        // ── Horizontal rules → removed ───────────────────────────────────────
        if is_hr(line) {
            continue;
        }

        // ── ATX headers: # text → text ──────────────────────────────────────
        if ltrim.starts_with('#') {
            let stripped = ltrim.trim_start_matches('#').trim_start();
            // Apply inline stripping to the header text
            let chars: Vec<char> = stripped.chars().collect();
            output.push(strip_inline(&chars));
            continue;
        }

        // ── Blockquotes: > text → text ───────────────────────────────────────
        if ltrim.starts_with("> ") {
            let rest = &ltrim[2..];
            let chars: Vec<char> = rest.chars().collect();
            output.push(strip_inline(&chars));
            continue;
        }

        // ── Unordered list: - item / * item / + item → item ─────────────────
        {
            let trimmed = line.trim_start();
            let indent: String = line.chars().take_while(|c| c.is_whitespace()).collect();
            if let Some(rest) = trimmed
                .strip_prefix("- ")
                .or_else(|| trimmed.strip_prefix("* "))
                .or_else(|| trimmed.strip_prefix("+ "))
            {
                let chars: Vec<char> = rest.chars().collect();
                output.push(format!("{}{}", indent, strip_inline(&chars)));
                continue;
            }
        }

        // ── Ordered list: 1. item → item ────────────────────────────────────
        {
            let trimmed = line.trim_start();
            let indent: String = line.chars().take_while(|c| c.is_whitespace()).collect();
            // Find "digits." prefix
            let digits_end = trimmed
                .char_indices()
                .take_while(|(_, c)| c.is_ascii_digit())
                .last()
                .map(|(i, c)| i + c.len_utf8());
            if let Some(d) = digits_end {
                if trimmed.get(d..) == Some(". ") || trimmed.get(d..) == Some(".") {
                    let rest = &trimmed[d..].trim_start_matches(". ").trim_start_matches('.');
                    let chars: Vec<char> = rest.chars().collect();
                    output.push(format!("{}{}", indent, strip_inline(&chars)));
                    continue;
                }
            }
        }

        // ── Regular line: inline processing only ────────────────────────────
        let chars: Vec<char> = line.chars().collect();
        output.push(strip_inline(&chars));
    }

    output.join("\n")
}

// ── Entry points ─────────────────────────────────────────────────────────────

fn process(input: &str) -> String {
    // JSON (transform_response) mode
    if let Ok(mut v) = serde_json::from_str::<serde_json::Value>(input) {
        if let Some(content) = v["choices"][0]["message"]["content"].as_str() {
            let stripped = strip_markdown(content);
            v["choices"][0]["message"]["content"] = serde_json::json!(stripped);
        }
        return serde_json::to_string(&v).unwrap_or_else(|_| input.to_string());
    }
    // Plain text (transform_text) mode
    strip_markdown(input)
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
    fn strips_atx_headers() {
        assert_eq!(strip_markdown("# Hello"), "Hello");
        assert_eq!(strip_markdown("## World"), "World");
        assert_eq!(strip_markdown("### Three"), "Three");
    }

    #[test]
    fn strips_bold() {
        let chars: Vec<char> = "This is **bold** text.".chars().collect();
        assert_eq!(strip_inline(&chars), "This is bold text.");
    }

    #[test]
    fn strips_bold_underscores() {
        let chars: Vec<char> = "__also bold__".chars().collect();
        assert_eq!(strip_inline(&chars), "also bold");
    }

    #[test]
    fn strips_italic() {
        let chars: Vec<char> = "This is *italic* text.".chars().collect();
        assert_eq!(strip_inline(&chars), "This is italic text.");
    }

    #[test]
    fn strips_bold_italic() {
        let chars: Vec<char> = "***very important***".chars().collect();
        assert_eq!(strip_inline(&chars), "very important");
    }

    #[test]
    fn strips_inline_code() {
        let chars: Vec<char> = "Run `cargo build` now.".chars().collect();
        assert_eq!(strip_inline(&chars), "Run cargo build now.");
    }

    #[test]
    fn strips_link() {
        let chars: Vec<char> = "See [the docs](https://example.com) for more.".chars().collect();
        assert_eq!(strip_inline(&chars), "See the docs for more.");
    }

    #[test]
    fn removes_image() {
        let chars: Vec<char> = "Before ![alt text](img.png) after.".chars().collect();
        assert_eq!(strip_inline(&chars), "Before  after.");
    }

    #[test]
    fn removes_hr() {
        assert_eq!(strip_markdown("---"), "");
        assert_eq!(strip_markdown("***"), "");
        assert_eq!(strip_markdown("___"), "");
    }

    #[test]
    fn strips_list_items() {
        assert_eq!(strip_markdown("- item one"), "item one");
        assert_eq!(strip_markdown("* item two"), "item two");
        assert_eq!(strip_markdown("+ item three"), "item three");
    }

    #[test]
    fn strips_ordered_list() {
        assert_eq!(strip_markdown("1. First"), "First");
        assert_eq!(strip_markdown("10. Tenth"), "Tenth");
    }

    #[test]
    fn strips_blockquote() {
        assert_eq!(strip_markdown("> A quote"), "A quote");
    }

    #[test]
    fn preserves_code_fence_content() {
        let input = "Text\n```\nlet x = 1;\n```\nAfter";
        let out = strip_markdown(input);
        assert!(out.contains("let x = 1;"));
        assert!(!out.contains("```"));
    }

    #[test]
    fn passthrough_plain_text() {
        let s = "This is already plain text.";
        assert_eq!(strip_markdown(s), s);
    }

    #[test]
    fn json_mode() {
        let input = r##"{"choices":[{"message":{"content":"# Title\n\n**bold** and *italic*."}}]}"##;
        let out = process(input);
        let v: serde_json::Value = serde_json::from_str(&out).unwrap();
        let content = v["choices"][0]["message"]["content"].as_str().unwrap();
        assert!(!content.contains('#'));
        assert!(!content.contains("**"));
        assert_eq!(content, "Title\n\nbold and italic.");
    }
}
