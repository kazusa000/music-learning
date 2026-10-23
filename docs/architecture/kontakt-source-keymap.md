# Kontakt source key-map query

[`scripts/instruments/kontakt_source_keymap.lua`](../../scripts/instruments/kontakt_source_keymap.lua) is a read-only metadata query for exactly one instrument already loaded by an isolated Kontakt job. It reads instrument key/velocity options and keyswitch, group start conditions, zone ownership and geometry, sample-presence state, and visible script metadata/source. It does not load, save, edit, or play an instrument. Sample paths are reduced to presence state; script source is captured only when the source getter succeeds, the slot is unprotected, and the text stays within the 1 MiB bound.

## API provenance and unknown handling

The query uses Native Instruments' primary Kontakt Lua API reference: [Instrument](https://docs.native-instruments.com/ni-tech-manuals/kontakt-api-reference-manual/en/instrument), [Multi](https://docs.native-instruments.com/ni-tech-manuals/kontakt-api-reference-manual/en/multi), [Group](https://docs.native-instruments.com/ni-tech-manuals/kontakt-api-reference-manual/en/group), and [Zone](https://docs.native-instruments.com/ni-tech-manuals/kontakt-api-reference-manual/en/zone). These pages document the option fields, group/zone getters, start conditions, zone geometry, script getters, and their parameter types. The Multi page explicitly calls instrument indexes zero-based. The Group and Zone pages list integer `group_idx` and `zone_idx` parameters but do not specify their bases. The script therefore does not treat those pages as proof of zero-based indexing.

For each nonempty group or zone collection, the query probes API indices `0` and `count`. It reports `inferred zero_based` only when both type-checked marker getters work at index `0` and both fail at `count`; it reports `inferred one_based` only for the reverse pattern. Groups use `get_group_name` and `get_group_start_options` as markers; zones use `get_zone_group` and `get_zone_geometry`. Script slots use `get_instrument_script_name` and `is_instrument_script_linked` at the same boundaries. The query enumerates a collection only after this boundary pattern is unambiguous. Otherwise it emits an `unknown` index-base row and suppresses that collection's indexed rows. A zero count is reported as `empty`; no base is inferred for an empty collection. These runtime inferences apply to the queried Kontakt build and source, not every Kontakt version. Individual getter failures within the inferred range remain unknown or unsupported.

Every member lookup and API call runs under `pcall`. A missing or non-function member is `unsupported`; a lookup exception, call exception, or invalid return is `unknown`. This also handles a Kontakt object exposed through a Lua userdata `__index` metamethod, provided it yields ordinary Lua functions. A callable non-function value is left `unsupported` rather than invoked. Omitted entries in a successfully returned instrument options table remain `omitted`; they are not filled with defaults. Known `nil` and empty-string returns stay distinct (`z` versus `s:`); zone sample presence uses `absent_nil`, `absent_empty`, or `present` without writing sample paths.

The cited API pages do not provide a Kontakt build-version getter used here, so `runtime/kontakt_version` is always `unknown`. The external runner must pin the exact Kontakt executable and plug-in hashes and report any version information separately. The Lua code's identity rows are caller-declared assertions, not values verified by Kontakt. The runner must independently verify the source file before and after load, the complete restored-state bytes/hash and parameter IDs/values, the plug-in/class identity, and the output receipt.

## Input and invocation

Before execution, the external runner fills the eight `@@...@@` constants with Lua-string-safe quoting: output path, exact source NKI SHA-256, source IComponent SHA-256, complete restored-state SHA-256, plug-in SHA-256, VST3 class ID, unique restore ID, and phase (`first-load` or `reopen`). The query expects exactly one already-loaded instrument. The generated request must establish an isolated empty rack, load the exact source NKI through the existing approved Kontakt loader, then append this query with the tags filled. The established process form is:

```text
<pinned-ml_kontakt_probe.exe> <new-unique-generated-request.lua>
```

Run only after the source-access and storage gates pass. Keep each generated request and output under a new E-side evidence directory. Repeat in a second independent empty-rack restore with a different restore ID/output path and phase `reopen`. This query is not an audio pass and does not validate the candidate three-window MIDI profile. Actual first-load/reopen audio and full-state checks must still use the existing strict thresholds in both restores.

## Output schema

Schema `kontakt-source-keymap-v2` is UTF-8 TSV. Arbitrary strings are UTF-8 hex encoded; nested API tables use deterministic typed values: `s:<hex>`, `n:<number>`, `b:0/1`, `z`, or recursively `t{key=value,...}`. The rows include:

- `schema`, caller-declared `identity`, and always-unknown `runtime/kontakt_version` rows.
- `context`, `instrument`, and `option` rows for the loaded-instrument inventory, MIDI channel, instrument name, and key/velocity options.
- `enumeration<TAB>{group|zone|script}_index_base<TAB>{inferred|empty|unknown}<TAB>{zero_based|one_based|hex-reason}` rows. `index_boundary` rows preserve the two marker-result statuses for each probed boundary.
- `group_index` / `group` and `zone_index` / `zone_<api-index>` rows only when that collection's base was inferred. Script capacity and `script` / `script_source` rows follow the same rule.
- `summary<TAB>metadata_complete|metadata_partial<TAB>metadata_unknown_count<TAB>count`. Unknown and unsupported metadata fields, protected or over-limit source text, and unproved nonempty index bases contribute to the count; the deliberately unknown Kontakt version does not.

The writer refuses to replace an existing output or `.tmp` file, publishes by rename, caps output at 32 MiB, and caps each captured script at 1 MiB. A consumer must require matching source/state identity across the two independent restores and inferred group/zone bases before interpreting zones as a source key map. If the API does not prove a base or source conditions remain opaque, use the separate all-128-key diagnostic; do not infer keys from preset labels or weaken the three-window validator.

## Bounded Python consumer

[`src/music_learning/kontakt_source_keymap.py`](../../src/music_learning/kontakt_source_keymap.py) provides `parse_file(path, expected_tags)` and `validate_restore_pair(first, reopen)`. The expected tag mapping must contain exactly `output_path`, `source_nki_sha256`, `source_component_sha256`, `restored_state_sha256`, `plugin_sha256`, `vst3_class_id`, `restore_id`, and `phase`. The parser compares the seven identity rows emitted in TSV with the expected values. Since `output_path` is not present in TSV, `parse_file` requires its exact text to match the caller's `output_path` tag; the caller must still verify the tag against the generated request and output receipt separately.

The consumer caps input at 32 MiB, each line at 2 MiB plus 4 KiB, rows at 2,000,000, typed-value nesting at 16 levels, and nodes in each typed value at 200,000. It rejects duplicate fields, invalid/odd hex, inconsistent summary counts, malformed boundaries, and reversed known key or velocity ranges. It preserves raw statuses and payloads. `zones` is `None` when the zone base is unknown; otherwise it contains only known numeric key/velocity values, group association only when the group base is inferred, and explicit sample-presence state. Group start conditions and script conditions remain opaque; each projection reports `conditions_status=unknown`. No candidate MIDI notes or audio profile is produced.

Example consumer call:

```python
from music_learning.kontakt_source_keymap import parse_file, validate_restore_pair

first = parse_file(first_tsv, first_expected_tags)
reopen = parse_file(reopen_tsv, reopen_expected_tags)
pair = validate_restore_pair(first, reopen)
if first.zones is None or reopen.zones is None:
    raise ValueError("Kontakt did not prove the source zone index base")
```

The pair helper checks only caller-declared tags: matching source/component/restored-state/plugin/class values, the requested phase names, and distinct declared restore IDs and output paths. It sets `identity_attested_by_host=False`, `independent_host_restores_verified=False`, and `external_host_evidence_required=True`. It does not prove which source or state Kontakt actually loaded, that the request produced the file at the claimed output path, or that two independent host restores occurred. The caller still needs to join the parsed declarations to saved request bytes, source-before/after hashes, complete restored-state and parameter evidence, pinned host/plugin process receipts, and distinct restore identities before using this metadata beside strict first-load/reopen validation.

The parser fixtures under `tests/service/fixtures/kontakt_source_keymap_v2_mock_*.tsv` are exact saved outputs from the Lua 5.4 contract mock, not Kontakt captures. The zero-based, one-based, and ambiguous-base fixture SHA-256 values are `65b5318a7b8d35fe11eaaa74893ab77f5e7ab17346bd8df024ffd4f116154427`, `69894ba39ef2ae7d950ac8c8105f4fb2fab97383eb2496cb8e3e224c3df96c27`, and `53ee40ca17b2d0ae900f0579571de029ea384022e100c10cfa2cc34b2f0ab031`.

## Contract validation provenance

The checked-in contract test can execute against a local Lua runtime at `runtime/build/kontakt-lua-contract/bin/lua.exe`. That ignored build target was produced from Lua.org's official Lua 5.4.9 source archive ([download area](https://www.lua.org/ftp/), SHA-256 `2335b6c582a52654f94612bf10d2f4672805d05329aa6568b1d8cd9e5c6fb8e6`) using the repository-owned E-side MSVC toolchain. The executable checks Lua 5.4 syntax and exercises a mocked `Kontakt` userdata/API for known, unknown, unsupported, boundary, output-limit, quoting/path, and no-mutation behavior. It is contract evidence only; it does not show that any Kontakt version exposes these methods or returns the mock values.
