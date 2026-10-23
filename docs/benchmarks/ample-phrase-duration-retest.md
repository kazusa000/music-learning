# Ample source-pinned phrase-duration retest

## Selection and prior evidence

The strict Ample preflight `strict-publication-preflight-03` identified exactly two queue-passed items whose existing first and reopen receipts fail only the source-pinned phrase-duration check:

- AGLP `80s Solo` — `85c1f3d98a2a580ba7f13ccf`
- AGLP `Gotten(Fingerstyle)` — `8256c94444ba252a98278476`

The prior queue rows and reports remain unchanged. The E-only planner verifies the frozen queue SHA, factory source SHA, source-derived Riffer profile and mapping, old first/reopen request/response/execution/state/WAV byte counts and SHA-256 values, evidence-index digest, and fixed host pin before creating any new queue. A non-duration failure, extra item, or changed evidence fails closed.

## New fixed queue

`scripts/instruments/prepare_ample_phrase_duration_retest.py` created an independent queue at:

`data/maintenance/instrument-intake/20261023-ample-shared-fix/phrase-duration-retest-01/`

It contains only those two AGLP IDs, starts both at `pending_staging`, and pins the existing fixed host SHA `d922eb9e89ba745f757aecf2b849c3d085a32f43456edfeae81a6bffa8587a0b`. The generated profile contract is source-derived from each original factory-state SHA and records the shared `trigger_phrase` generator, its pinned code files, 44.1 kHz sample rate, complete note/controller/tempo/meter data, exact phrase duration, and one-second tail. The old full-length requests were 40 seconds and about 30.967 seconds; the shared bounded source phrases are 28 seconds (`1,234,800` frames) and `1,194,968` frames respectively, each followed by `44,100` tail frames.

The planned queue targets the audited final bank path `H:\music\Sound Library\Ample Sound\AGLP 4.0.0`; preparation did not inspect H:. No plugin load has started. Execution remains gated until the root confirms all three final-bank control leases have been released and the final bank/config is ready. The root-owned runner then must complete first and clean reopen for both items before strict preflight can count them.

Queue SHA-256: `1ce355f8364877cfd7b8b1b7e2aa65d2f07c23ebbac792edfb787e47265fafd4`.

The prepared queue passed the existing Ample queue-shape validator, retry-supervisor state validator, and immutable pin verification. Four targeted E-only regression tests pass. The queue is not an admission or audio-pass result; formal catalog, project, and preview writes remain zero.

Run regression tests with the repository cleanup wrapper:

```powershell
& scripts/maintenance/run-tests.ps1 -PytestArgs @('tests/test_ample_phrase_retest.py','-q')
```
