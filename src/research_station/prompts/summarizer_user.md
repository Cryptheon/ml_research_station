---
name: summarizer_user
description: >
  User-turn prompt for PaperSummarizer. Injects paper metadata and either the
  abstract or the OCR-extracted full text, then requests a JSON object matching
  the schema injected at runtime.
variables:
  content_label:   '"FULL TEXT" or "ABSTRACT ONLY" — tells the model what it is reading'
  full_text_note:  'Appended hint when full text is provided (e.g. " — use the full text to give richer analysis."), empty otherwise'
  title:           Paper title
  author_names:    Formatted author list (up to 6, with et al.)
  venue:           Conference or journal name (falls back to source name)
  categories:      arXiv / subject categories, comma-separated
  published:       ISO date string (YYYY-MM-DD)
  tldr_hint:       'Semantic Scholar TLDR prefixed with a newline, or empty string'
  content_block:   'Either "Abstract:\n{text}" or "Full paper text (OCR extracted):\n{text}"'
  schema_json:     JSON schema object serialised to a string (injected by PaperSummarizer)
used_by: processing/summarizer.py → _build_prompt()
---

Analyse the following paper and respond with a JSON object that EXACTLY matches
the schema below. Use null for any field you cannot infer.
Input quality: $content_label$full_text_note

=== PAPER ===
Title:      $title
Authors:    $author_names
Venue:      $venue
Categories: $categories
Published:  $published$tldr_hint

$content_block

=== REQUIRED JSON SCHEMA ===
$schema_json

Respond with ONLY the JSON object, no other text.
