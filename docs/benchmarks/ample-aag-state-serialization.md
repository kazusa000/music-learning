# Ample Guitar M component-state numeric serialization

## Result

The strict Ample publication preflight now accounts for a verified wire-format rule in Ample Guitar M 3.1.0. Its `AAG_STATE` component-state numeric `Parameter/@Value` strings use C++ `defaultfloat` with the MSVC runtime's default precision of six significant digits. Admission formats each pinned source float32 through that exact representation, parses it back to float32, and compares the resulting typed values exactly. This is a serialization canonicalization, not an epsilon or a preset exception.

The rule is scoped to `AAG_STATE` component XML for Ample Guitar M 3.1.0. Factory XML bytes and the complete source typed map stay SHA-pinned at their original values. Host parameter IDs/readbacks, text values, keys, names, root identity, documented defaults, first/reopen complete typed maps, unknown-extra rejection, and all other Ample products remain strict. Signed zero is compared by its float32 bit pattern.

## Evidence

Static analysis of the installed `AGM.vst3` and the current `msvcp140.dll` traced the AAG_STATE builder through the float-to-string helper and the MSVC `basic_ostream` initializer. The builder supplies precision zero; the helper leaves the stream default alone; the runtime initializes precision to six with the default floatfield. The plugin then serializes that tree into component state. Exact binary hashes and REA evidence identifiers are preserved in the E-only record:

`data/maintenance/instrument-intake/20261023-ample-shared-fix/ample-state-serialization-analysis-01/shipped-artifact-static-proof.json`

The independent offline comparison covered 81 queue-passed factory items, 162 first/reopen captures, and 70,576 numeric pairs. Of those, 70,560 already matched native float32 exactly; all 16 mismatches across six factory IDs matched the emitted six-significant-digit strings and parsed back to the exact captured float32. The complete machine report is:

`data/maintenance/instrument-intake/20261023-ample-shared-fix/ample-state-serialization-analysis-02/report.json`

These E maintenance records are local verification evidence and remain under the repository's ignored `data/` tree; they are not product catalog data.

## Strict preflight

The follow-up strict publication preflight replayed the shared full receipt validator and consumed only the completed E-side fresh-resource audit records. It found 79 of 81 queue-passed items ready with strict evidence and resource audits. Exactly two remain for first/reopen phrase-duration retest: AGLP `80s Solo` (`85c1f3d98a2a580ba7f13ccf`) and `Gotten(Fingerstyle)` (`8256c94444ba252a98278476`). Their existing queue passes and reports remain unchanged. The other 31 source-queue nonpasses and the one missing `AGMPk` sample group remain excluded.

Preflight report and follow-up plan:

- `data/maintenance/instrument-intake/20261023-ample-shared-fix/strict-publication-preflight-03/report.json`
- `data/maintenance/instrument-intake/20261023-ample-shared-fix/strict-publication-preflight-03/retest-plan.json`

The preflight was E-only: it did not launch plugins, write the formal catalog or project database, or modify the source package/NAS. Final catalog publication still requires the full receipt verification against the materialized and freshly rehashed H library, plus the separate API/release compatibility gate.

## Regression coverage

The publication adapter tests cover representative positive and negative values, fixed/scientific formatting thresholds, rounding boundaries, negative zero, wrong component roots, unchanged exact comparisons for LP/SC, and strict rejection of changed defaults and extra state. The admission preflight test asserts that the comparison scope documents this exact wire rule.

Run with the repository cleanup wrapper:

```powershell
& scripts/maintenance/run-tests.ps1 -PytestArgs @('tests/service/test_ample_publication_adapter.py','tests/service/test_ample_admission_preflight.py','-q')
```

Latest targeted result: 32 passed.
