---
name: ento-ingestion
description: "Use when: ingesting a PDF dichotomous key into the ento-assist database,
  encoding couplets from a printed key, registering a new document, scanning key structure,
  adding couplets one at a time, finalizing a key, adding figures, glossary terms, or
  taxon descriptions to the entomology database."
argument-hint: "PDF path and title, e.g. /books/Townes1969.pdf 'Townes 1969 Ichneumonidae vol 1'"
---

# Ento-Assist Ingestion Workflow

Guides the agent through encoding a printed dichotomous key from a PDF into the ento-assist
database. Every couplet is processed interactively — one at a time — with user confirmation
before it is written to the database.

**This workflow is always human-supervised. Each couplet is confirmed before committing.**

---

## Step 1 — Register the document

Call `build_register_document(path, title)`.

Report the returned `doc_id` and confirm registration.

---

## Step 2 — Scan for key regions

Call `build_scan_document(doc_id)`. Present the full list of detected regions
(page ranges, titles) to the user.

Ask:
> "Do these page boundaries look correct? Should any ranges be adjusted or excluded?"

**Wait for confirmation before proceeding.**

---

## Step 3 — Ask about couplet format and key identity

Ask the user:

> 1. How are couplets numbered in this key? (e.g. paired "1."/"1." lines,
>    lettered "1a."/"1b.", "A."/"B." bullets, or another format)
> 2. What is the base taxon this key covers? (scientific name + rank,
>    e.g. "Ichneumonidae" / "family")
> 3. What rank do the terminal leads identify to? (e.g. "subfamily", "genus")

Do not proceed until you have answers to all three questions.

---

## Step 4 — Create the key record

Call `build_create_key(doc_id, base_taxon_name, base_taxon_rank, leaf_taxon_rank)`.

The key title is auto-generated as `"<base_taxon_name> → <leaf_taxon_rank>"`.
Report the `key_id` and title to the user and confirm.

---

## Step 5 — Couplet loop (repeat for EVERY couplet, in source order)

Process **exactly one couplet per iteration**. Do not move to the next couplet
until the current one is confirmed and committed.

### 5a — Read the page

Call `build_get_page_text(doc_id, page_num)` for the page containing this couplet.
If the page is image-only, call `build_ocr_page(doc_id, page_num)` instead.

### 5b — Parse and present

Read the text and identify the single next couplet. Present your interpretation:

```
Couplet [N]:
  Leg A: [full condition text] → goto [M]  OR  terminal: [Taxon name]
  Leg B: [full condition text] → goto [P]  OR  terminal: [Taxon name]
```

Each leg must have **either** a goto (couplet number) **or** a terminal (taxon name),
never both.

### 5c — Confirm with user

Ask:
> "Does this look correct? (yes / describe what needs changing)"

**WAIT for the user's reply.** Apply any corrections, re-present, and re-confirm.
Do NOT proceed until the user explicitly says the interpretation is correct.

### 5d — Figures

Ask:
> "Is there an illustration on this page associated with this couplet?"

If YES:
1. Call `build_get_page_image(doc_id, page_num)` to display the page to the user.
2. Ask for the bounding box: `"Please give me x0, y0, x1, y1 coordinates for the figure."`
3. Call `build_crop_figure(doc_id, page_num, x0, y0, x1, y1, caption)`.
   Hold the returned `fig_id` — you will link it after committing the couplet.

Do NOT call `build_link_figure` yet (you need `leg_a_id`/`leg_b_id` from step 5f first).

### 5e — Glossary check

For each technical morphological term in leg A or leg B text that may be
unfamiliar to a student, ask:
> "Do you have a definition for '<term>'?"

If the user provides one, call `build_add_term(term, definition)`.

### 5f — Commit the couplet

Call `build_add_couplet` with the confirmed interpretation:

```
build_add_couplet(
    key_id=...,
    number="N",
    page_ref=...,
    leg_a_text="...",
    leg_b_text="...",
    leg_a_goto="M",      # or None
    leg_a_terminal=None, # or "Taxon name"
    leg_b_goto="P",      # or None
    leg_b_terminal=None, # or "Taxon name"
)
```

If a figure was captured in step 5d, call `build_link_figure(fig_id, leg_id, ref_text)`
using the `leg_a_id` or `leg_b_id` returned above.

### 5g — Advance

Confirm: `"Couplet [N] committed."` then move immediately to the next couplet
(return to step 5a).

---

## Step 6 — Finalize the key

After the **last** couplet is committed, call `build_finalize_key(key_id)`.

This resolves all forward-reference numbers to UUIDs and sets `start_couplet_id`.

If any unresolved gotos are reported, show them to the user:
> "The following goto references couldn't be matched: [list]. Would you like to
> add the missing couplets or leave them as stubs?"

---

## Step 7 — Taxon descriptions (optional, recommended)

For each terminal taxon, if the source document contains a diagnosis or description,
call `build_add_taxon(taxon_name, rank, description, doc_id, page_ref)`.

These descriptions are shown to users when they reach a terminal during identification.

---

## Tool Reference

| Tool | When to use |
|------|-------------|
| `build_register_document` | Step 1 — register a PDF |
| `build_scan_document` | Step 2 — detect key regions |
| `build_get_page_text` | Step 5a — read a page's text layer |
| `build_ocr_page` | Step 5a — OCR fallback for image-only pages |
| `build_get_page_image` | Step 5d — display a page for figure inspection |
| `build_create_key` | Step 4 — create the key record |
| `build_add_couplet` | Step 5f — commit one confirmed couplet |
| `build_finalize_key` | Step 6 — resolve forward refs, set start couplet |
| `build_crop_figure` | Step 5d — crop and store a figure |
| `build_link_figure` | Step 5f — associate a figure with a couplet leg |
| `build_add_term` | Step 5e — add a glossary term |
| `build_add_taxon` | Step 7 — add/update a taxon description |

---

## Common Issues

| Problem | Approach |
|---------|----------|
| Image-only pages | Call `build_ocr_page` instead of `build_get_page_text` |
| Non-standard couplet numbering | Ask the user in Step 3; adjust parsing accordingly |
| Couplet spans two pages | Read both pages before presenting interpretation |
| A leg has neither goto nor terminal | `build_add_couplet` will warn; ask user for the missing value |
| Server restart mid-ingestion | Couplets already committed are safe in the DB; resume from the last uncommitted couplet |
| Unresolved goto after finalization | Ask user whether to add missing couplets or treat as stubs |


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
