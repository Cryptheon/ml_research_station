---
name: ocr_page
description: >
  Per-page extraction prompt for VisionLLMOCR. Sent once per PDF page alongside
  the JPEG image of that page.
variables:
  page:         1-based page number (integer)
  context_line: 'Optional "Paper context: <title — authors>" line, or empty string'
used_by: processing/pdf_ocr.py → VisionLLMOCR.extract_page()
notes: >
  Kept deliberately simple so general-purpose vision models (gemma4, llava, etc.)
  produce output rather than refusing due to overly strict formatting rules.
  Dollar signs in LaTeX use $$ escaping (string.Template). Curly braces pass through.
---

Transcribe every piece of text visible on page $page of this academic paper.

Include body text, headings, equations (in LaTeX), table contents, captions, footnotes, and reference entries exactly as they appear.

Output only the transcribed text. If a region is a pure figure with no readable text, write [figure]. If the page is blank, write [blank].
$context_line
