# pdf_oxide API Reference

## Extraction methods (per page, 0-indexed)

| Method | Returns |
|---|---|
| `to_markdown(page, detect_headings=True)` | Full page as markdown |
| `extract_text(page)` | Plain text string |
| `extract_text_lines(page)` | List of text lines |
| `extract_words(page)` | Word-level data |
| `extract_chars(page)` | Character-level data with positions |
| `extract_tables(page)` | Structured table data |
| `extract_images(page)` | Embedded images |

## Document-level

| Method | Returns |
|---|---|
| `page_count()` | Total page count |
| `get_form_fields()` | List of form field dicts |
| `set_form_field_value(name, value)` | Populate a form field |
| `version()` | PDF version string |

## Scoped extraction

```python
doc.within(page, bbox).extract_text()   # bbox = (x0, y0, x1, y1)
```

Useful for extracting only a specific region (header, footer, column).
