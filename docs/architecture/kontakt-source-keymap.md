# Kontakt source key-map query

`scripts/instruments/kontakt_source_keymap.lua` is a metadata-only query for one instrument that an isolated Kontakt job has already loaded. It reads instrument-wide key/velocity options and keyswitch, group start conditions, zone ownership and geometry, sample presence, and visible script metadata/source. It does not load, save, edit, or play an instrument. Sample paths are reduced to present/absent; embedded script source is captured as hex only when the getter succeeds and the source is unprotected and within the 1 MiB bound.

## API provenance and unknown handling

The query is based on Native Instruments' primary Kontakt Lua API reference: [Instrument](https://docs.native-instruments.com/ni-tech-manuals/kontakt-api-reference-manual/en/instrument), [Group](https://docs.native-instruments.com/ni-tech-manuals/kontakt-api-reference-manual/en/group), and [Zone](https://docs.native-instruments.com/ni-tech-manuals/kontakt-api-reference-manual/en/zone). The instrument page documents `get_instrument_options` fields `key_switch`, `key_range_from/to`, and `velocity_range_from/to`, plus instrument script getters. The group page documents `get_num_groups` and `get_group_start_options`; the zone page documents `get_num_zones`, `get_zone_group`, `get_zone_geometry`, direct low/high key and velocity getters, root key, and sample lookup.

The script performs every API method lookup and call under `pcall`. Missing methods, exceptions, unexpected return types, count limits, protected scripts, and oversized script sources remain explicit `unknown`, `protected`, or `over_limit` results. Omitted entries in a successfully returned instrument options table are written as `omitted`; the query does not silently fill defaults. The cited reference does not supply a Kontakt build/version getter used here, so `runtime/kontakt_version` is always `unknown`; the external runner must pin the exact Kontakt executable SHA and report its version separately if available. A successful call does not prove that the API applies identically to every Kontakt version.

Group and zone enumeration uses indices from zero through `count - 1`, as required by the query's bounded enumeration loop. The cited signatures do not state the index base explicitly. The local Kontakt version is unknown, so these rows must be treated as unverified if the getters fail or produce inconsistent counts; the query never repairs or guesses an index.

## Input and invocation

Before execution, the external runner fills the eight `@@...@@` constants using Lua-string-safe quoting: output path, exact source NKI SHA-256, source IComponent SHA-256, complete restored-state SHA-256, plugin SHA-256, VST3 class ID, unique restore ID, and phase (`first-load` or `reopen`). These tags are runner assertions, not values calculated by Kontakt Lua. The runner separately verifies the source file SHA before and after loading, the executable/plugin/class identity, complete state bytes/hash and parameter IDs/values, and the output receipt.

The query body expects exactly one already-loaded instrument. The generated request must first establish an isolated empty rack, load the exact source NKI through the existing approved Kontakt loader, then append this query with the tags filled. The established process form is:

```text
<pinned-ml_kontakt_probe.exe> <new-unique-generated-request.lua>
```

Run that request only after the source-access and storage gates pass. Keep each generated request and output under a new E-side evidence directory. Repeat in a second independent empty-rack restore with a different restore ID/output path and phase `reopen`. This query is not an audio pass and does not validate the candidate three-window MIDI profile. Actual first-load/reopen audio and full-state checks must still use the existing strict thresholds in both restores.

## Output schema

The output is UTF-8 text with tab-separated records. Arbitrary strings are hex encoded; nested API tables use deterministic typed values: `s:<hex>`, `n:<number>`, `b:0/1`, `z`, or recursively `t{key=value,...}`. Record forms are:

- `schema<TAB>kontakt-source-keymap-v1`
- `identity<TAB>field<TAB>hex-value`
- `runtime<TAB>kontakt_version<TAB>unknown<TAB>hex-reason`
- `context`, `instrument`, `option`, `group`, and `zone_<index>` records with field status and encoded value
- `group_index<TAB>index` and `zone_index<TAB>index` markers
- `script_capacity`, `script`, and `script_source` records; source status is `known`, `empty`, `protected`, `over_limit`, or `unknown`
- `summary<TAB>metadata_complete|metadata_partial<TAB>getter_unknown_count<TAB>count`

The writer refuses to replace an existing output or temporary file, publishes by rename, caps output at 32 MiB, and caps each captured script at 1 MiB. The query's `metadata_complete` status covers getter results only; Kontakt version remains separately unknown. A complete metadata readout can narrow a source-derived event profile. If zone/script conditions remain opaque, use the separate all-128-key diagnostic plan in the current full-h failure audit; do not infer keys from preset labels or weaken the three-window validator.
