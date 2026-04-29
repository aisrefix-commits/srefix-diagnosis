# fix_maps/

Per-tech YAML files holding **human-reviewed** metric-name fixes for
`agents/*.md`.

## Why this directory exists

The applier (`srefix-fix apply`) is **deterministic and LLM-free**. It only
trusts what's checked into this directory. The split is:

| Stage | Tool | Trust level |
|-------|------|-------------|
| Propose fixes | `srefix-fix propose <tech>` (Claude headless, read-only tools) | LOW — LLM may hallucinate |
| Review | Human edits the draft YAML | gate |
| Apply | `srefix-fix apply <yaml>` (pure regex sed) | HIGH — same input, same output forever |

Putting the YAML in git means: every change to a manual is preceded by a
diff in this directory that names the human reviewer (`confirmed_by`) and
cites the source of truth (`authority`). LLM mistakes get rejected at the
review step; they never reach `agents/`.

## Workflow

```bash
# 1. Auto-draft (dispatch Claude headless against the manual + real exporter)
srefix-fix propose vitess
# → writes fix_maps/vitess.draft.yaml

# 2. Review: drop entries you're not sure about, fix `new` if proposer got
#    it wrong, add `confirmed_by: <your-name>` to entries you trust
$EDITOR fix_maps/vitess.draft.yaml
mv fix_maps/vitess.draft.yaml fix_maps/vitess.yaml
git add fix_maps/vitess.yaml

# 3. Dry-run to see exactly what will change
srefix-fix apply fix_maps/vitess.yaml --dry-run

# 4. Real run; manual is rewritten in place
srefix-fix apply fix_maps/vitess.yaml

# 5. Inspect, commit
git diff agents/vitess-agent.md
git commit -am "fix(vitess): apply confirmed metric-name corrections"
```

## Schema

```yaml
tech: <short-name>          # matches agents/<tech>-agent.md or <tech>.md
proposed_at: <iso-date>
proposed_by: <string>       # e.g. "claude --print (claude-opus-4-7)"
authority: <string>         # required: source of truth (URL + version)
notes: <string>             # optional

fixes:
  - old: <hallucinated_name>
    new: <correct_name>
    rationale: <one-line>            # why this is right
    confirmed_by: <reviewer-name>    # ← REQUIRED for apply; empty = skip
    occurrences_expected: <int>      # safety check; warning on mismatch
```

See `_example.yaml` for a worked Vitess example covering the
`vtgate_queries_error → vtgate_api_error_counts` headline fix.

## Naming convention

- `<tech>.draft.yaml` — proposer output, before human review (do NOT commit
  unless intentional).
- `<tech>.yaml` — reviewed and committed; ready for `apply`.
- `_example.yaml` / `_*.yaml` (leading underscore) — illustrative files,
  not auto-applied.

## Safety properties

1. **Word-boundary substitution**: `vtgate_queries_error` won't match
   inside `vtgate_queries_error_total`.
2. **Confirmed-only**: entries with empty `confirmed_by` are skipped, so
   half-reviewed YAMLs are safe to commit (or to keep around as drafts).
3. **Occurrence counter**: if `occurrences_expected` doesn't match the
   actual replacement count, apply prints a WARNING (non-zero exit) so
   surprise matches don't slip through CI.
4. **No LLM at apply time**: same YAML always produces the same diff,
   regardless of model availability.
