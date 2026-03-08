// BeigeBox WASM module: pii_redactor
//
// Redacts common PII patterns from LLM responses:
//   - Email addresses         → [REDACTED_EMAIL]
//   - US phone numbers        → [REDACTED_PHONE]
//   - Social Security Numbers → [REDACTED_SSN]
//   - Credit card numbers     → [REDACTED_CC]
//
// Works in both JSON (transform_response) and plain text (transform_text) modes.
//
// Build:
//   rustup target add wasm32-wasip1
//   cargo build --target wasm32-wasip1 --release
//   cp target/wasm32-wasip1/release/pii_redactor.wasm ../../pii_redactor.wasm
//
// Register in config.yaml:
//   wasm:
//     modules:
//       pii_redactor:
//         path: "./wasm_modules/pii_redactor.wasm"
//         enabled: true
//         description: "Redact PII (emails, phones, SSNs, credit cards) from responses"

use std::io::{self, Read, Write};
use regex::Regex;

struct Redactor {
    // Order: CC before phone (phone patterns can partially match 16-digit strings)
    patterns: Vec<(Regex, &'static str)>,
}

impl Redactor {
    fn new() -> Self {
        let defs: &[(&str, &str)] = &[
            // Credit card: 4×4 digits separated by spaces or dashes
            (r"\b(?:\d{4}[-\s]){3}\d{4}\b", "[REDACTED_CC]"),
            // SSN: 3-2-4 format
            (r"\b\d{3}-\d{2}-\d{4}\b", "[REDACTED_SSN]"),
            // Email
            (r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b", "[REDACTED_EMAIL]"),
            // US phone: optional +1, then (NXX) or NXX, then NXX-XXXX variants
            (
                r"\b(?:\+1[-.\s]?)?(?:\(\d{3}\)|\d{3})[-.\s]?\d{3}[-.\s]?\d{4}\b",
                "[REDACTED_PHONE]",
            ),
        ];

        let patterns = defs
            .iter()
            .filter_map(|(pat, rep)| Regex::new(pat).ok().map(|re| (re, *rep)))
            .collect();

        Redactor { patterns }
    }

    fn redact(&self, text: &str) -> String {
        let mut result = text.to_string();
        for (re, replacement) in &self.patterns {
            result = re.replace_all(&result, *replacement).into_owned();
        }
        result
    }
}

fn process(input: &str, redactor: &Redactor) -> String {
    // Try JSON mode first (transform_response path)
    if let Ok(mut v) = serde_json::from_str::<serde_json::Value>(input) {
        if let Some(content) = v["choices"][0]["message"]["content"].as_str() {
            let redacted = redactor.redact(content);
            v["choices"][0]["message"]["content"] = serde_json::json!(redacted);
        }
        return serde_json::to_string(&v).unwrap_or_else(|_| input.to_string());
    }
    // Plain text mode (transform_text path)
    redactor.redact(input)
}

fn main() {
    let mut input = String::new();
    io::stdin()
        .read_to_string(&mut input)
        .expect("failed to read stdin");

    let redactor = Redactor::new();
    let result = process(&input, &redactor);

    io::stdout()
        .write_all(result.as_bytes())
        .expect("failed to write stdout");
}

#[cfg(test)]
mod tests {
    use super::*;

    fn r() -> Redactor {
        Redactor::new()
    }

    #[test]
    fn redacts_email() {
        assert_eq!(
            r().redact("Contact user@example.com today."),
            "Contact [REDACTED_EMAIL] today."
        );
    }

    #[test]
    fn redacts_email_with_plus() {
        assert_eq!(
            r().redact("My alias is user+tag@sub.example.org."),
            "My alias is [REDACTED_EMAIL]."
        );
    }

    #[test]
    fn redacts_ssn() {
        assert_eq!(r().redact("SSN: 123-45-6789."), "SSN: [REDACTED_SSN].");
    }

    #[test]
    fn redacts_phone_parens() {
        assert_eq!(
            r().redact("Call (555) 867-5309."),
            "Call [REDACTED_PHONE]."
        );
    }

    #[test]
    fn redacts_phone_dotted() {
        assert_eq!(
            r().redact("Phone: 555.867.5309"),
            "Phone: [REDACTED_PHONE]"
        );
    }

    #[test]
    fn redacts_phone_international() {
        assert_eq!(
            r().redact("+1-800-555-0100 is the number."),
            "[REDACTED_PHONE] is the number."
        );
    }

    #[test]
    fn redacts_cc() {
        assert_eq!(
            r().redact("Card: 4111 1111 1111 1111"),
            "Card: [REDACTED_CC]"
        );
    }

    #[test]
    fn redacts_cc_dashes() {
        assert_eq!(
            r().redact("Card: 4111-1111-1111-1111"),
            "Card: [REDACTED_CC]"
        );
    }

    #[test]
    fn passthrough_clean_text() {
        let s = "No PII here — just a normal sentence.";
        assert_eq!(r().redact(s), s);
    }

    #[test]
    fn json_mode_redacts_content() {
        let input = r#"{"choices":[{"message":{"content":"Email me at test@example.com."}}]}"#;
        let out = process(input, &r());
        let v: serde_json::Value = serde_json::from_str(&out).unwrap();
        assert_eq!(
            v["choices"][0]["message"]["content"].as_str().unwrap(),
            "Email me at [REDACTED_EMAIL]."
        );
    }
}
