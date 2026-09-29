# Design review — user questions and current answers

Screenshots of the current app are in `current/` (mock mode, 1440px wide). Mockups are in `mockups/`.
Design direction: minimal dark SaaS, inspired by neon.com (near-black ground, one green accent,
sans + mono type). A light variant of the documents page is included for comparison.

## The questions

| # | Question a user would ask | Current app (from screenshots) | Mockup answer |
|---|---|---|---|
| 1 | **Where am I, and what's the overall flow?** | Numbered tabs 01–04 suggest a linear flow, but Schema (04) is really a prerequisite for extraction, and Ask Genie sits before it. Every page repeats a large hero plus a Runtime/API panel that users don't need. | Left sidebar with plain nav. A "How it works" strip on Documents: Upload → Prepared automatically → Extract with a schema → Review, export or ask Genie. System status is hidden from end users. |
| 2 | **After I upload, do I know what happened?** | The two-step "Choose PDF files" then "Register documents" flow. Per-file outcomes exist, but "Register" and "Case ID" are unfamiliar terms. | One "Upload PDFs" button. An inline progress card shows counts, a progress bar and duplicate notices with a link to the existing file. |
| 3 | **Can I see status at a glance?** | Statuses mix raw codes (`UPLOADED`, `EXTRACT_FAILED`) with friendly labels ("Ready to extract"). `UPLOADED` and a failed run are both shown in green. No counts. | One plain-language set of statuses with consistent colours: Preparing (amber), Ready to extract (blue), Extracted (green), Needs review (violet), Failed (red). Filter tabs show a count for each. |
| 4 | **What do I need to do before I can extract?** | Not obvious. Uploaded docs show a disabled "Run extraction" button with small grey text. The bulk bar puts "Parse selected" and "Extract selected" at equal weight, and the schema defaults to the first one alphabetically (HUD voucher). | Parsing is automatic and called "Preparing". The next action appears on each row (Extract / View / Retry). The selection bar says how many docs are ready and queues the rest to extract when they're ready. The schema defaults to the latest one used. |
| 5 | **How do I see my results?** | A separate Results page lists runs and statuses but not the extracted values, so you open each one. Document detail and Result detail are two near-duplicate pages. | The Results table shows the key fields (invoice #, seller, total, validation) with summary totals. One document page combines the viewer, fields, line items, validation and history. |
| 6 | **Can I trust a value — where did it come from?** | Citations exist, but they're labelled "Visual debugger", "Element inspector" and "0 elements here". | Clicking a field highlights its source on the page. Confidence is shown per field. Validation warnings appear inline (for example, the due date is before the invoice date). |
| 7 | **What if something fails?** | The raw database error is shown to the user (`table invoice_candidates has no column named invoice_index`). The result detail says "No invoices stated in this document" on a failed run, which is misleading. | A plain-language failure message with Retry, and technical details collapsed. A failed run never shows an empty "no data" message. |
| 8 | **How do I talk to my documents (Genie)?** | A dead end when it isn't configured: one sentence and no guidance. Not linked from Documents or Results. | A Genie page with "what Genie can see" (sources and freshness), suggested questions and a chat. "Ask Genie" entry points on Results and Document pages. |
| 9 | **On Schemas, how do I view or edit one?** | Every version is labelled PRODUCTION, so it's unclear which is current. The only action is "Clone to a new draft". The page title says "Extraction contract" while the nav says "Schema". A full-width black "New schema" bar dominates the page. | A schema list with the current version, field count and usage. A clear "Edit (creates v5 draft)" button, plus a note explaining that published versions are locked so past results stay reproducible. Fields are shown as a table. |
| 10 | **Is there developer noise I don't need?** | The Runtime/API panel, "Retained parser contract 2.0", hashes, parse IDs, `profile invoice_v1` and "Depth 5/12". | Moved to a collapsed "Technical details" section or an admin view. |

## Open decisions for you
- Dark (primary mockups) or light (`01-documents-light.png`)?
- Should we merge Document detail and Result detail into one page? The mockups assume yes.
- Should users be able to pick a schema at upload time, so documents extract automatically after preparation?
  That would make it upload-and-done. The mockups show it as an optional "Extract when ready".
- **Genie constraint:** the current app embeds Databricks Genie in an iframe, so we can style the page *around* it
  (sources panel, suggested questions, entry points) but not the chat inside it. The native-looking chat in
  `04-ask-genie.png` would need the Genie Conversation API instead of the embed.

## Files
- `current/` — screenshots of today's app (01–07 routes, 08 schema selected, 09 rows selected)
- `mockups/blue/` — brand blue #0067B1 accent on a neutral dark ground
- `mockups/navy/` — navy ground built from #003463, #0067B1 accent
- `mockups/green/` — original Neon-style green (plus `01c-documents-light.png`)
- Each theme folder: 01 documents, 01b rows selected, 02/02b/02c document (extracted / preparing / failed),
  03 results, 04 Ask Genie (iframe), 05 schemas, 05b schema edit (draft v5)
- `mockups/src/` — HTML/CSS source; `python3 mockups/src/render.py [green|blue|navy] [page-name…]`
