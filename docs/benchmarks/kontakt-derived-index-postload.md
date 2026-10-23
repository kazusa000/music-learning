# Kontakt derived-index post-load audit

The schema-1 installed-library manifest keeps every non-`.nkc` resource in its
immutable full-SHA list. Only `.nkc` files already present in the reviewed
source audit are recorded separately as Kontakt-generated indexes. The
partition must cover the original resource inventory exactly; it does not
allow arbitrary new cache files to be treated as trusted.

The fixed full-job validator remains unchanged. A separate E-side supervisor
provides an additional post-load gate for a Kontakt full run:

1. It pins the full-job run/plan and the derived-index revision report.
2. While the full job is active, it polls only E-side JSON progress, queue
   hashes, and process identities. It does not enumerate, read, or write `.nkc`
   files or other library resources on H.
3. After the full-job status is terminal, `current` is null, its conversion and
   validation queues contain only terminal item states, and the pinned wrapper,
   worker, and all Kontakt processes have exited, it captures stable terminal
   progress and queue SHA pins. Both queues must exactly cover the source paths
   pinned by the full-job plan, have unique IDs and paths, and agree on each
   ID-to-source mapping. Conversion items must be `failed` or
   `converted_pending_validation`; validation items must be `failed` or
   `passed`. A missing queue, `pending` item, duplicate, or mismatch stops the
   watcher in an E-only incomplete-queue state before it reads H.
4. Before each H read it waits for the existing three-sample
   `loading_budget("H:")` idle window, acquires the exact private-profile
   transaction lock, and checks that the default Multi still matches the
   previously captured baseline and that no profile journal/staging artifact
   exists. It rechecks the full-job pins and process boundary while the lock is
   held.
5. It verifies the installed static manifest and fully hashes every registered
   non-`.nkc` file against the original audit in two independent snapshots. The
   snapshots must agree on exact paths, sizes, modification times, and SHA-256.
   It also checks the exact registered `.nkc` path set and hashes each source /
   installed index pair twice. Source indexes must remain unchanged from their
   original audit; a final path audit must match the first.
6. It writes an independent E-side report. It never retries presets, changes
   the active full-job state, publishes catalog entries, or dispatches previews.

The first historical plan is
`data/maintenance/instrument-intake/kf22/derived-index-postload-full-g-01/plan.json`.
The first full-h follow-up plan was prepared before the queue-completeness gate
was added; it was never started and must remain a preserved superseded attempt.
The corrected full-h watcher has its own immutable plan and ignored runtime
locator:
`data/maintenance/instrument-intake/kf22/derived-index-postload-full-h-02/plan.json`
and `runtime/kontakt-derived-index-snapshot-full-h-02.local.json`. Its plan
SHA-256 is `8a881c235ac242c5c43509161ac088b561d1f3e27efac2dc4b49ffe7f730a116`.
At launch it entered `waiting_full_job_terminal` with
`no_h_access=true`; the full-h worker and wrapper were still alive in
`waiting_storage`, `current=null`, and no Kontakt executable was running.
Progress, logs, and the eventual report stay beside each plan. The supervisor has a
48-hour combined wait bound for Kontakt process exit and idle H storage. An
expired wait is inconclusive and does not trigger a retry; a new attempt uses a
new evidence directory and preserves the earlier plan and report.

The first CSS source, `Instruments/CSS 1st Violins.nki` (1,980,114 bytes,
SHA-256 `9ad172688a0cb695039257e46e4df2b034c99f36167e1f4fd195dfccba9b0fff`),
timed out at the fixed 180-second conversion limit in both full-g and full-h.
Each recorded `timeout_kill`, exit code 1, and no observed NKM or receipt; the
Lua log contains two unresolved-resource messages followed by the script-start
line. Full-g therefore had no successful conversion receipt for the seeder to
reuse. These records do not identify whether the timeout came from storage
pressure, runtime/resource initialization, or this source; they are not proof
that the NKI or sample library is corrupt. Full-h's original failure remains
unchanged, and its later queue progress must be assessed only after the frozen
batch reaches terminal state.

The live application config currently has no Kontakt bundle in `plugin_roots`.
The reviewed Kontakt runtime bundle is
`H:\music\Sound Library\Kontakt\Runtime\Kontakt 8\x64\VST3\Kontakt 8 Portable.vst3`.
Do not add it while the intake is running or before successful audio admission.
After admitted catalog entries exist, the product's additions-only plugin-root
delta can validate their strict receipt, native admission, state, library
manifest, and bundle identity and propose only that exact bundle path. Applying
the resulting config change remains a separate compare-and-swap/restart step;
the current post-load watcher never publishes catalog rows or changes app
configuration.

The separate CSS failure snapshot at
`data/maintenance/instrument-intake/kf22/full-g-diagnosis-01/css-terminal-failures-20261023-0950z/`
preserves the current three terminal failures without changing the active
full-g queue. It distinguishes one 180-second source-conversion timeout from
two completed conversions rejected at the pinned VST3 descriptor scan. None
of those three items reached the full-state, audition-controller, or
first/reopen audio checks. Any retry must wait for full-g terminal status and
use a new independent queue after the shared descriptor/loading causes are
reviewed.
