# Contributing

Contributions should keep this repository tools-only and aligned with lawful Ford TSO processing workflows used alongside **FORScan Ford** diagnostics.

## Do Not Commit

- Source archive files or disc images
- Extracted service information
- Generated static sites
- Generated metadata such as `coverage.json`, `catalog.json`, `inventory.json`, `v1_names.json`, or `wiring_data.js`
- Screenshots or visual references from proprietary applications or documents
- PDFs, diagrams, database files, or other source-derived binary artifacts

## Expected Contributions

- Python tool fixes for extraction, catalog, wiring, or SVG repair stages
- Tests with synthetic fixtures only
- Documentation about running `tso_convert.bat` and the manual pipeline
- Ignore-rule or repository-hygiene improvements

Synthetic fixtures must be small and authored for this repository. Do not copy or paraphrase proprietary source material into tests, comments, issues, pull requests, or documentation.

## Before Opening a Pull Request

Run:

```
python -m pytest
git status --short
git ls-files
```

Confirm the tracked file list contains only tools, tests, launcher scripts, documentation, and repository metadata.
