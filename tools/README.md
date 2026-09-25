# tools

## `md2docx.py`
Regenerates the Word twins of `docs/*_requirements.md` / `docs/*_analysis.md`.

The `.docx` files in `docs/` are **generated artefacts** — edit the Markdown, then
regenerate. It reproduces the house style of the original `python-docx` output:
Heading 1/2/3, Normal body, `Light Grid Accent 1` tables with a bold header row
(an all-empty header row is dropped), monospace code blocks, and inline
`**bold**` / `` `code` `` / `*italic*` / `~~strike~~` rendered as runs.

```bash
python3 -m pip install python-docx --break-system-packages   # not preinstalled
for b in availability_report_balancing_analysis crew_planning_analysis \
         rental_availability_requirements rental_purchase_requirements \
         rental_scanning_requirements rental_serial_log_requirements \
         sale_flow_return_demand_requirements; do
    python3 tools/md2docx.py "docs/$b.md" "docs/$b.docx"
done
```

`crew_planning_requirements.md`, `crew_portal_requirements.md`,
`rental_return_serial_requirements.md` and `serial_uniqueness_requirements.md`
are Markdown-only by design and have no Word twin.
