# Test Fixtures

## sample_key.pdf (required for Phase 3 tests)

Place a real dichotomous key PDF here named **`sample_key.pdf`**.

Requirements:
- Text-based pages (not fully image-scanned) — at least 3 pages
- Contains at least one key with 3–7 couplets and 2+ terminal taxa (genus + species)
- PDF file must be readable by PyMuPDF (standard PDF format)

Once you have placed the file, update `tests/conftest.py` with the following constants
so that Phase 3 assertions can be exact rather than shape-only:

```python
# In conftest.py — fill these in after confirming with a first test run:
FIXTURE_PDF_PAGE_COUNT = ???       # total pages in sample_key.pdf
FIXTURE_PDF_KEY_PAGE = ???         # 1-indexed page number containing the key
FIXTURE_PDF_TERMINAL_TAXA = [???]  # e.g. ["Aedes aegypti", "Culex pipiens"]
FIXTURE_PDF_IMAGE_ONLY_PAGE = ???  # 1-indexed page that is image-only, or None
```

Tests that depend on this file are automatically skipped when the file is absent.
