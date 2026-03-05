---
name: pdf-processing
description: Extract text, tables, form fields, and markdown from PDF files in workspace/in/. Use when the user uploads a PDF, asks about a PDF document, wants to read a PDF, or mentions extracting content from a PDF.
metadata:
  author: beigebox
  version: "1.0"
  engine: pdf_oxide (Rust-backed, ~0.8ms/doc)
---

# PDF Processing Skill

Extracts content from PDFs using the `pdf_reader` operator tool, powered by
pdf_oxide — a Rust-backed library with 0.8ms mean processing time.

## Workflow

1. Confirm the PDF is in `workspace/in/` (user should drag/upload it via the Dashboard)
2. Call `pdf_reader` with the filename
3. The tool returns full markdown conversion of all pages
4. Summarize, answer questions, or extract specific sections as needed

## Example

User: "Can you read the report I uploaded?"

```json
{"thought": "User wants PDF content, calling pdf_reader", "tool": "pdf_reader", "input": "report.pdf"}
```

The tool returns markdown. Then answer the user's question based on that content.

## What pdf_reader returns

- Full document as markdown (headings detected, tables preserved)
- Page-by-page breakdown with separators
- Form field listing if the PDF contains fillable fields
- Error message if the file isn't found or can't be parsed

## Edge cases

- **File not found**: Ask the user to upload via the workspace drag-and-drop in the Dashboard
- **Scanned PDF (image-only)**: pdf_oxide extracts embedded text only — no OCR. Advise the user the PDF may be image-based if output is empty
- **Large PDFs**: The tool processes all pages. For very large documents, ask the user which pages or sections they care about before calling, to avoid overwhelming the context window
- **Password-protected PDFs**: Will return an error — ask user to provide an unlocked version

## Capabilities reference

See [references/pdf-oxide-api.md](references/pdf-oxide-api.md) for the full pdf_oxide API.
