# Changelog

All notable changes to the research engine are documented here. Versions
follow [Semantic Versioning](https://semver.org/); while the project is in
`0.x`, a minor version may change methodology.

## [Unreleased]

### Fixed

- Dashboard: when most downloads fail (for example, Yahoo blocking a cloud
  server), it now shows the provider error instead of a "0 candidates"
  analysis, and the failure is no longer cached for 24 hours. Isolated
  failures show a warning with the count.

### Changed

- `historico.json` is written as compact JSON (no indentation): about 20 %
  smaller with identical content, including discarded companies and their
  rejection reasons.

### Internal

- `screener_value.diagnosticar_descarga()`: offline summary of failed
  downloads, used by the dashboard.
- README section on deploying the dashboard to Streamlit Community Cloud.
- Tests moved to `tests/`; a session-level fixture pins the working
  directory, so the suite also runs when launched from another folder.

## [0.2.0] - 2026-11-23

### Changed (methodology)

- **Signal entry price.** Signals issued from 2026-11-24 enter at the adjusted
  close of the trading session *after* the signal. Scheduled runs often finish
  after the European close, so the same-day close was already known when the
  signal was generated. Signals issued earlier keep their original rule, so
  existing tracking series do not change base (`seguimiento.fecha_inicio_precios`).
- Weekly schedule moved from `0 7 * * 1` to `17 7 * * 1` to reduce GitHub's
  delays for on-the-hour cron jobs.

### Fixed

- Dual-listing de-duplication stripped legal suffixes as substrings
  ("Banco Sabadell SA" became "BANCO BADELL"), which could merge distinct
  companies. Suffixes are now removed as whole words; groupings over all 710
  historical names are unchanged.
- The weekly run validates the control row before appending to the journal,
  so a failure can no longer leave a snapshot without a control record.
- A snapshot without a control record only counts as legacy if it predates
  the first control record. The current set of official snapshots is
  unchanged.
- `registrar_seguimiento` validates the CSV header before appending.
- Providers use per-instance caches instead of `lru_cache` on methods.
- The weekly workflow rebases and retries before pushing, so a concurrent
  push to the branch no longer discards the week's snapshot.

### Internal

- Single pipeline `screener_value.evaluar_fundamentales()` shared by the
  weekly run, `screener_api` and the dashboard. `screener_api` output and
  `API_SCHEMA_VERSION` (1.0) are unchanged.
- Exact dependency versions in `requirements.txt`; ruff configuration.
- New `CI` workflow (ruff + pytest) on pushes and pull requests, separate
  from the weekly data run.

## [0.1.1] - 2026-09-05

- Bilingual Streamlit research dashboard, TradingView chart, weekly
  signal-history visualization and documentation updates.

## [0.1.0] - 2026-09-05

- First stable research release: reproducible screening engine, versioned
  universes, append-only snapshots, signal tracking, SEC EDGAR verification
  and the side-effect-free Python API.

[0.2.0]: https://github.com/DiegoRecover8/screener-value-investing/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/DiegoRecover8/screener-value-investing/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/DiegoRecover8/screener-value-investing/releases/tag/v0.1.0
