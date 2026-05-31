---
name: ento-identification
description: "Use when: identifying an entomological specimen, running an identification session,
  working through a dichotomous key, choosing A or B at a couplet, resuming an identification,
  looking up morphological terms during identification, looking ahead in a key, confirming a
  terminal taxon, searching for taxa in the database."
argument-hint: "Optional: key name or taxon group, e.g. 'Ichneumonidae'"
---

# Ento-Assist Identification Workflow

Guides the agent through identifying a physical specimen using a dichotomous key stored in the
ento-assist database. The user makes every identification decision — the agent presents options
and waits.

**Never advance a couplet or conclude an identification without explicit user input.**

---

## Step 1 — Choose a key

Call `run_list_keys(db_path)` (optionally filtered by taxon group) and present the available
keys to the user.

Ask:
> "Which key would you like to use?"

Wait for the user to choose before proceeding.

---

## Step 2 — Start or resume a session

**New session:** Ask where to save the session file. Suggest a sensible default:
```
~/ento-sessions/<YYYY-MM-DD>-<key-name>.md
```

Call `run_start_session(db_path, key_id, output_path)` to create the session file and
retrieve the first couplet.

**Existing session:** Call `run_resume_session(session_path)` to reload state.
Report the current position and most recent choices to the user.

---

## Step 3 — Present a couplet

At each couplet, follow this sequence exactly:

### 3a. Display both legs in full

Show leg A and leg B with their complete text. Never summarize or truncate.

### 3b. Look up technical terms

For any morphological term that may be unfamiliar, call `run_lookup_term(db_path, term)`
and include the definition inline with the couplet text.

### 3c. Display figures

If either leg references a figure, call `run_get_figure(db_path, fig_id)` and display it.

### 3d. Ask for the user's choice

> "Which leg matches your specimen? **A** or **B**?"

**Wait for the user to answer. Do not guess, infer, or proceed without a response.**

---

## Step 4 — Advance the session

Only after the user has chosen A or B, call:

```
run_advance_session(session_path, leg_label="A")   # or "B"
```

The tool returns either:
- The **next couplet** — go back to Step 3
- A **terminal taxon** — proceed to Step 5

---

## Step 5 — Confirm the terminal taxon

When a terminal is reached:

1. Call `run_taxon_description(db_path, taxon_id)` and present the full description.
2. Ask the user:
   > "Does your specimen match this description? **Yes** or **No**?"
3. If **yes**: conclude the identification and report the taxon name.
4. If **no**: ask whether to go back one step or start over.
   - Back one step: call `run_resume_session` after manually editing the session file, or
     have the user restart from the point of uncertainty.
   - Start over: call `run_start_session` with the same key.

**Never conclude the identification before the user confirms the match.**

---

## Optional: Look ahead

If the user is uncertain about a couplet choice, offer to show what taxa are reachable via
each branch before they decide:

```
run_look_ahead(session_path, depth=2)
```

Present the candidate taxa for each branch. Then ask again: "A or B?"

---

## Optional: Search taxa

If the user wants to know whether a specific taxon is in the database:

```
run_search_taxa(db_path, query="<taxon name or keyword>")
```

---

## Session file

The session file is a plain markdown file with YAML frontmatter stored on the user's local
machine. It is **not** part of the shared database. Users can:
- Annotate it freely under `## Notes`
- Share it with colleagues
- Resume it at any time with `run_resume_session`

---

## Common Issues

| Problem | Approach |
|---------|----------|
| User is unsure about a couplet | Offer `run_look_ahead` to show reachable taxa for each branch |
| Unfamiliar morphological term | Call `run_lookup_term`; if not found, note it for glossary addition |
| Session file not found | Verify path; suggest starting a new session if file is missing |
| Terminal taxon doesn't match | Ask if user wants to back up one step or restart the session |
| Key has no description for terminal | Report "(no description stored)" and ask if they want to continue |
