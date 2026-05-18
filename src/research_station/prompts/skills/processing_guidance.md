---
name: processing_guidance
description: >
  Guidance for summarisation patience, batch ordering, and error handling when processing papers.
triggers:
  - summarise
  - summarize
  - summary
  - ocr
  - extract text
  - processing
  - pipeline
---

## Summarisation — Critical Rules

Summarisation requires a multi-step pipeline managed by `processing_expert`. Always delegate:

> "Summarise {paper_id}. Extract text first if needed."

`processing_expert` handles the `extract_pdf_text → summarize_paper` chain internally.

---

### Patience — summarisation takes time

`summarize_paper()` blocks and polls until ready — up to **10 minutes** for long papers using map-reduce.

| Situation | What to do |
|---|---|
| Tool is running (no result yet) | Wait — do NOT retry |
| Tool returns "already in progress" | Stop. Do not call again. The first call is running. |
| Tool returns "timed out after Xs" | Job may still run — call `get_paper()` to check |
| Tool returns summary text | Done |

**NEVER ask `processing_expert` to call `summarize_paper()` more than once per paper.**
Duplicate calls stack and waste minutes.

---

### Batch summarisation

For multiple papers, instruct `processing_expert` to process them **sequentially**:

> "Summarise these papers one at a time: arxiv:XXXX, arxiv:YYYY, arxiv:ZZZZ.
> For each, extract text if needed, then summarise."

---

### Text extraction before summarisation

```
extract_pdf_text()  →  summarize_paper() or rag_query()
ocr_paper()         →  summarize_paper() or rag_query()  (scanned PDFs only)
```

- `extract_pdf_text()` is instant (PyMuPDF). Use for PDFs with a text layer.
- `ocr_paper()` is slow (~1–3 min). Use only when `extract_pdf_text` returns empty text.
- After either succeeds, `summarize_paper()` and `rag_query()` both work.

---

### Error reference

| Error | Cause | Fix |
|---|---|---|
| `"Error starting summarisation: timed out"` | Server busy, HTTP POST timed out | Retry once |
| `"appears to have stopped after Xs"` | Summarisation may have crashed | Check `get_paper()` — summary may still have completed |
| `"already in progress"` | Duplicate call | Do NOT retry |
| `"No text available"` | PDF not extracted | Delegate to `processing_expert` to extract text first |
