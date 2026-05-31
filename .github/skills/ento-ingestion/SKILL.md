---
name: ento-ingestion
description: "Use when: ingesting a PDF dichotomous key into the ento-assist database,
  encoding couplets from a printed key, registering a new document, scanning key structure,
  submitting key extraction, previewing or correcting parsed couplets, committing key data,
  adding figures, glossary terms, or taxon descriptions to the entomology database."
argument-hint: "PDF path and title, e.g. /books/Townes1969.pdf 'Townes 1969 Ichneumonidae vol 1'"
---

# Ento-Assist Ingestion Workflow

Guides the agent through encoding a printed dichotomous key from a PDF into the ento-assist
database. Every step requires human confirmation before proceeding.

**This workflow is always human-supervised. Data is never committed without explicit user approval.**

---

## Step 1 — Register the document

Call `build_register_document` with the PDF path and a descriptive title.

```
build_register_document(path="<absolute path to PDF>", title="<title>")
```

Report the returned `doc_id` to the user and confirm registration.

---

## Step 2 — Scan for key regions

Call `build_scan_document(doc_id)` to detect regions of the PDF that look like
dichotomous keys. Present the full list of detected regions (page ranges, titles) to the user.

Ask the user:
> "Do these page boundaries look correct? Should any regions be adjusted or excluded?"

Wait for confirmation before proceeding.

---

## Step 3 — Read the page text

For each confirmed region, call `build_get_page_text` for relevant pages to retrieve raw text.
Display the relevant portions to the user so they can see the actual key content.

If a page appears to be image-only (no extracted text), call `build_ocr_page` to run OCR.

---

## Step 4 — Ask about couplet format

Before interpreting the key, ask the user:

> - How are couplets numbered? (e.g. paired "1."/"1." entries, lettered "1a."/"1b." suffixes,
>   "A."/"B." bullets, or something else?)
> - Are there inline figures that should be captured?

Do not proceed to Step 5 without answers to both questions.

---

## Step 5 — Build and submit the key structure

Based on the user's answers, interpret the couplet structure yourself and call `build_submit_key`.

Each couplet must have:
- A unique couplet number or label
- Two legs (A and B), each with either a `goto` (next couplet number) or a `terminal`
  (taxon name), but NOT both

```
build_submit_key(doc_id, title, couplets=[...])
```

This stores the proposed extraction in memory (not yet in the database). Report the returned
`extraction_id`.

---

## Step 6 — Preview the extraction

Call `build_preview_extraction(extraction_id)` and present the complete couplet list to the user.

Highlight any couplets flagged with confidence < 0.7 — these need special attention.

Ask the user to verify each couplet matches the source text.

---

## Step 7 — Apply corrections

If the user reports errors, apply them using `build_correct_extraction(extraction_id, corrections)`.

Call `build_preview_extraction` again after corrections and show the updated result.

Repeat until the user is satisfied.

---

## Step 8 — Get explicit approval and commit

Ask the user:
> "Are you satisfied with this extraction? Type **yes** to commit it to the database, or
> describe any remaining corrections."

**Do NOT call `build_commit_extraction` until the user answers "yes".**

On approval, call `build_commit_extraction(extraction_id)` and confirm the key was saved.

---

## Step 9 — Figures (optional)

If the key contains illustrations:

1. Call `build_get_page_image(doc_id, page_num)` to display the page.
2. Ask the user to describe the bounding box (x0, y0, x1, y1) of each figure.
3. Call `build_crop_figure(doc_id, page_num, x0, y0, x1, y1, caption)`.
4. Call `build_link_figure(fig_id, leg_id, ref_text)` to associate with a couplet leg.

---

## Step 10 — Glossary terms (optional)

When encountering technical morphological terms in the key text, ask the user if they want
to add a definition. If yes, call `build_add_term(term, definition, doc_id, page_ref)`.

---

## Step 11 — Taxon descriptions (optional)

To store full taxon descriptions for terminal taxa, call `build_add_taxon`. These descriptions
will be shown to users when they reach a terminal in the identification workflow.

---

## Common Issues

| Problem | Approach |
|---------|----------|
| Confidence < 0.7 on a couplet | Show the original text; ask user to manually correct the couplet |
| Image-only pages with no text | Use `build_ocr_page` for OCR extraction |
| Non-standard couplet numbering | Ask the user about the format in Step 4; build the structure manually |
| Server restart mid-ingestion | The pending extraction is lost; re-run from Step 5 |
