# Ample final-bank Riffer retry

## Current recovery state (2026-10-23)

The terminal result from `phrase-duration-retest-02` is **0 passed / 2 failed**:
`8256c94444ba252a98278476` (Gotten(Fingerstyle)) and
`85c1f3d98a2a580ba7f13ccf` (80s Solo) rendered exact-zero first WAVs against
the installed AGLP bank. Their original retry3 staging-bank first and reopen
WAVs were nonzero. The E-only paired receipt is
`data/maintenance/instrument-intake/20261023-ample-shared-fix/riff-silent-lineage-01/lineage-comparison.json`
(SHA-256 `1467b8664eb7ac712a12c8f5c70e7abd03359b22c3900a538a2af93e8d044945`).
It directly compares the immutable native requests and WAVs: for each pair,
the full phrase/events, tempo/meter, block size, gain, factory preset bytes,
captured full state, all 2,298 parameter readbacks, plugin identity and all five
sample-family index contents match. The only semantic request-field difference
is `revision_id`, a job label; the preset path is the expected run-local copy.
The installed bank root/manifest changed, and the final-bank native call took
longer. No sample-ready timestamp is present, so bank/resource resolution and
an asynchronous readiness race remain hypotheses, not established causes.
Both strict failures remain failures until a controlled final-bank probe has
complete first/reopen evidence.

Retry06 is a **separate two-ID queue** for Sweet Child O Mine
(`d59c0b8032032c95ab193f55`) and Used to Love Her
(`dd3e3fb7a4ac5a4e88d4c4b7`); it must not be confused with the two LP2
duration-retest failures above. Its first outer attempt terminated at
`blocked_preparation_failed` before creating a probe queue: the read-only
preparer's own `--source-run` argument matched the source-run process scan.
The shared scan now excludes only the current exact preparer PID when its
executable is the expected preparer and its `--source-run` resolves to the
scanned source. Separate tests prove a distinct live supervisor still blocks.

The old LP2 checkpoint also contained two stale observed descendants whose
recorded creation times predate the runner: `tail.exe` PID 45464 and
`/silentConfig` PID 27344. They were not stopped. Shared process-birth lineage
validation now discards only exact, live PID/creation-time/command matches
that predate that runner; true observed children and unknown identities still
block. The E-only closure is
`phrase-duration-retest-02/supervision-02-lineage-assessment/assessment.json`.
Retry06 recovery uses an independent attempt plan, locator and checkpoint so
the old failed locator/checkpoint and all original reports remain intact.
At 11:39 UTC the recovery attempt was running under
`runtime/ample-final-bank-riff-retry-06-attempt-02-job.local.json` (frozen plan
SHA `ca8d79c38588267e5bb0b2001b1bd429584c06b942abc5f701254733e7cec70e`),
with its own `failed-only-final-bank-riff-retry-06-attempt-02` run and
`final-bank-riff-retry-06-supervision-attempt-02/control.json` checkpoint.
The live outer supervisor was verified as PID 53912,
creation ticks `639283523556278460`, command bound to that plan and locator;
it reached a stable AGLP config snapshot, then the read-only preparation child
failed before creating a queue. The retained `prepare.log` identifies the
guard `An Ample process still references the source run`, but that older
generic error did not include the matching PID/creation time/command, so the
specific process that triggered the refusal cannot be reconstructed. This is
not evidence that the preparer matched itself or that the source run changed.
The common guard now records bounded process identity evidence, redacting
credential-shaped command arguments, and a regression checks both the blocking
identity and redaction. Independent attempts03/04 reproduced the guard, but
their retained process rows do not contain enough parent/creation-time evidence
to distinguish the current preparer from its launcher. The earlier claim that
`sys.argv[0]` was the root cause was unsupported and is superseded. A new
no-write diagnostic showed the app Python launcher and parent PowerShell
command are distinct processes from the active interpreter; a source-path
substring or script name alone cannot identify either one as the current
invocation.

The shared process matcher now exempts only (a) the current process when its
PID, creation time, parent PID, and full command match the OS snapshot and the
command is the exact preparer with the exact source-run argument, or (b) an
exact invocation process proven to be on the current PID's parent chain. The
chain is checked edge by edge using PID, creation time, parent PID, and full
command, with parent creation time no later than its child; a reused PID,
cycle, unknown candidate, or depth limit leaves that candidate blocking.
Ancestry proof stops at the candidate, so a vanished unrelated grandparent
above an established launcher does not block preparation. A sibling running
the same preparer/source remains a conflict, as does a shell that merely
prints the script path and source flag. The E-only regressions cover these
cases. A direct no-write preflight reports zero matching processes and zero
queue/catalog/formal writes. Attempts02–04 and their reports remain intact;
the next recovery uses a separate attempt05.

2026-10-23. This note tracks the two remaining AGLP Riffer items that may be
rechecked against the final installed bank. It distinguishes a changed sample
bank from changed musical input, and keeps the older failed evidence intact.

## Held five-item attempt

`data/maintenance/instrument-intake/20261023-ample-shared-fix/failed-only-output-contract-retry-05`
is prepared only. Its queue SHA is recorded in `review.json`; the status is
`prepared`, all five rows remain `pending_staging`, and no probe process was
started. The runner output-root contract fix is shared and tested, but does not
explain the actual failures recorded by retry3/retry4:

- `e52d95643c575172d4df66ad` (Stairway to Heaven),
  `fe844ddb31d05a36e9d16b83` (The Silence of Faling Flower(ARP)), and
  `551c6b809af39fe39df3be51` (Walk This Way) have the concrete
  `Pedal-HM2-Dist` readback mismatch. They remain with the shared HM2 diagnosis
  and are excluded from this retry.
- `d59c0b8032032c95ab193f55` (Sweet Child O Mine) and
  `dd3e3fb7a4ac5a4e88d4c4b7` (Used to Love Her) each reached the actual strict
  probe in retry3 and retry4 and failed as silent. Their source factory SHA,
  Riff profile, trigger phrase, host, and validator did not materially change
between those attempts. The final installed bank is the controlled difference.

The immutable per-item details and the held decision are in the E-only
`failed-only-output-contract-retry-05/review.json`. The original queues and
reports are not rewritten.

## Final-bank retry gate

The exact two-item plan is
`data/maintenance/instrument-intake/20261023-ample-shared-fix/failed-only-final-bank-riff-retry-06-plan.json`.
It binds the fresh AGLP resource-audit result SHA and the installed AGLP
manifest SHA `1e4de934fa7821334fcf1bb6689d8363c1ecd452c46c09e10828629426673a28`
for `Ample Sound/AGLP 4.0.0`. Both requests are already complete source-bound
phrases (15.61 s and 28.24 s at 44.1 kHz, plus a 1 s tail); this retry does not
claim a phrase-duration fix will change them. Host SHA and Riff-profile SHA
also remain fixed.

Do not create/start the new queue until the existing
`phrase-duration-retest-02` queue is terminal, its supervisor/runner/native
host process tree has exited, and the AGLP configuration lease is free. At
that point, read the current AGLP `InstDir` config and capture its SHA. The
queue builder must fail closed unless the current config points exactly to the
audited installed bank. Then use the normal supervisor with its three-sample
H: storage gate; do not override the gate. Keep the three HM2 IDs out of this
queue. This plan does not imply a pass, publication, or preview readiness.

## Shared admission binding

`apply_audited_installed_library` in
`src/music_learning/ample_terminal_retry.py` is the common queue-preparation
guard. It requires a completed full-manifest audit, validates the audited bank
stays below the selected Sound Library root, checks every selected item has
the audit's product, and verifies the current plugin config's unique `InstDir`
exactly equals that bank. It attaches the audit/result/resource/installed
manifest hashes and the current config SHA to the new queue and each item,
then directs only that new retry queue to the final bank. It never hashes or
copies sample files; the strict fixed validator still verifies the sample
inventory, complete state, first/reopen audio and identity. Tests run through
`scripts/maintenance/run-tests.ps1` and keep their temporary files under the
configured test root.

## Durable wait and execution entry

`scripts/instruments/supervise_ample_final_bank_riff_retry.py` is the durable
entry for retry06. It polls the frozen LP2 queue/checkpoint and actual Windows
PID, creation-time, and command identities. It does not read H: while LP2 is
nonterminal or any process holding the AGLP configuration lease remains. Once
the exact queue is terminal, its recorded process tree has exited, and the
single AGLP `InstDir` stays bound to the audited final bank for two consecutive
observations, it prepares the fixed two-ID queue with the current config SHA
and invokes the existing retry supervisor. That supervisor retains its normal
three-sample H: storage gate.

The outer entry is serialized by a dedicated lock. Its checkpoint and
`runtime/ample-final-bank-riff-retry-06-job.local.json` locator record the
plan/script hashes and PID/creation-time/command identity, with a 24-hour
overall wait budget. After an outer-process restart it adopts the exact
recorded child if still alive; otherwise it resumes the same fixed queue using
the existing terminal guard, so completed IDs are not run again. Retry05
remains held. A queue that reaches terminal failures is recorded as terminal
with unresolved items and is not automatically repeated.

## Attempt05 process-chain recovery (2026-10-23)

The durable attempt05 outer supervisor reached the stable AGLP config gate and
prepared the exact two-ID queue, but its child retry supervisor exited before
the first runner invocation. The child log records
`An unowned Ample process already references this pinned retry queue`; the
queue remains `prepared` with two `pending_staging` items and
`invocation_count=0`. The outer checkpoint confirms one child exit code 1 and
zero formal-project or catalog writes. No H-backed probe ran. Attempt05 and its
queue, pins, log, and checkpoint are preserved as an unresolved attempt.

The shared cause was the child supervisor's process matcher: it exempted any
Python command containing `supervise_ample_failed_retry.py` without proving
parentage, while it treated the real outer `supervise_ample_final_bank_riff_retry.py`
ancestor as an unowned process because that command carried the child run path.
The replacement matcher exempts a recognized Python supervisor only when a
single current process snapshot proves exact PID, creation time, PPID, command,
and birth-ordered ancestry to the current child supervisor. It stops at the
candidate; a missing unrelated grandparent does not block. Same-script siblings,
reused PID edges, unknown rows, and independent hosts remain conflicts. Conflict
logs now retain compact PID/PPID/creation-time and command-hash evidence without
printing command contents.

The shared matcher is
`process_row_matches_ancestor_snapshot` in
`src/music_learning/ample_retry_planning.py`, also used by the read-only
preparer ancestry check. Retry06 plans pin and self-check the SHA-256 of the
outer supervisor, child supervisor, and shared ancestry module before
preparation and again immediately before child launch. Regressions cover exact
parent lineage, missing irrelevant grandparents, independent sibling
processes, PID reuse, and shell text that merely mentions the script. The
three focused test files passed 37 tests through
`scripts/maintenance/run-tests.ps1`; Ruff and `git diff --check` passed.

The next execution is a separate attempt06 plan, run directory, checkpoint,
and locator. It inherits the same frozen two failed IDs, source queue/status,
audited AGLP bank/manifest, fixed host and strict first/reopen requirements.
Attempt05's prepared queue and original retry3/retry4 reports remain unchanged.

## Attempt06 durable recovery (2026-10-23)

Attempt06 is frozen at
`data/maintenance/instrument-intake/20261023-ample-shared-fix/failed-only-final-bank-riff-retry-06-attempt-06-plan.json`
(SHA-256 `bd4aa59d76995e14679b6df14248ef0167face51a1b2bed7fd50172a86d6089b`).
It supersedes only attempt05, whose preserved queue remains two `pending_staging`
rows with `invocation_count=0` and child return code 1. Attempt06 uses a new
run directory and checkpoint and still selects only the same two source-bound
IDs; it does not repeat any completed item from the original or prior retry
queues.

The no-write process preflight is
`final-bank-riff-retry-06-attempt-06-preflight/process-preflight.json`
(SHA-256 `b5dfcbf7259f1ea843bc4c1665f04cf4179ed93c8fd3ac5e3ee2ca1c2fda1609`),
with zero matching active processes and no queue/catalog/formal writes. Its
corrected manifest is `manifest-corrected.json` (SHA-256
`031af82a0356a8b824a1ede16159acbff79b51e1efdf3b6f8ff415907913566b`); the
initial manifest is retained as invalid evidence because PowerShell's reserved
`$Host` variable yielded a null host field. The plan pins the outer supervisor,
child supervisor and shared ancestry module. The outer process rechecks all
three hashes before preparation and immediately before child launch.

The durable locator is
`runtime/ample-final-bank-riff-retry-06-attempt-06-job.local.json`. Initial
identity verification found supervisor PID 44864, creation ticks
`639283542611493030`, with the command line bound to the frozen plan and
locator; its parent launcher PID 33292 was alive and born earlier. Initial
status is `waiting_for_config_stability`; no retry queue or probe has yet been
started. It retains the 24-hour bound and existing three-sample H: storage gate.

Attempt06's child then exited before starting a runner. Its preserved queue is
still `prepared`, with two `pending_staging` items and `invocation_count=0`;
the child log reports three Python process rows in the exact launcher → outer
supervisor → child-wrapper ancestry. The first shared fix accepted only
recognized supervisor-script rows, so it still rejected the exact Python
launcher/bootstrap ancestor. No audio probe ran and the original queue remains
unchanged.

The shared matcher now exempts a Python process only when its exact PID,
creation time, PPID and command are proven on the current child supervisor's
birth-ordered parent chain. A Python shim need not itself contain a known
supervisor filename; unrelated siblings, same-script processes, reused PIDs,
unknown rows and shell text remain conflicts. The regression suite now has 38
focused passes, including a shim ancestor with an independent sibling still
blocking. Ruff and `git diff --check` pass. Attempt05 and attempt06 remain
preserved as separate no-probe attempts.

The next durable execution is independent attempt07. Its fresh process
preflight is
`final-bank-riff-retry-06-attempt-07-preflight/process-preflight.json`
(SHA-256 `75d12057c1554c9ea272abaad18ca9e46feaf2cf7f7a4f5f7a3d476a8c4d21f5`):
zero matching processes, no queue write, and zero formal/catalog writes. The
standalone manifest SHA is
`6586764a4438087fe51afcbbcf6a621903eef9ec00309102567551172f3ece05`.
The frozen plan SHA is
`4a8a7980cee193dc2987cdf1ebc1ee2706b121bcab9737fe1e32612e08063e13`; it pins
the current shared child/outer/helper code. Locator:
`runtime/ample-final-bank-riff-retry-06-attempt-07-job.local.json`.
Supervisor PID 17356, creation ticks `639283544157409930`, was verified with
the exact plan and locator command; its initial state is
`waiting_for_config_stability`. No attempt07 probe was reported as started in
the initial snapshot. The usual 24-hour bound and three-sample H: gate remain
in force.

## Attempt07 CIM snapshot diagnosis and attempt08 recovery

Attempt07 also terminated before any probe: its queue remains two
`pending_staging` rows and `invocation_count=0`; no H-backed host was started.
The direct process matcher showed the exact failure was not missing ancestry
but malformed unrelated rows in the Windows CIM inventory. The read-only
control at `process-ancestry-cim-control-01/assessment.json` captured 589
rows, including PID 0 and 195 processes with empty commands. The original
ancestry helper rejected the complete inventory before traversing the valid
target chain. Filtering only unrelated incomplete rows made the same exact
chain prove successfully; the report records zero H reads and zero formal
project writes.

Shared fix `c01f3b3` changes the ancestry map to ignore incomplete rows with
unrelated PIDs, while recording malformed or duplicate PIDs as ambiguous.
If such a PID is the target, current process, or a parent on the traversed
chain, proof still fails closed. Regression coverage includes PID 0,
protected/system rows with empty commands, incomplete target identities,
missing chain parents, PID reuse and independent siblings. The focused suite
passed 39 tests; Ruff passed. Attempt07 and its terminal artifacts remain
preserved.

Attempt08 uses a new fixed queue and checkpoint, based on a fresh process-only
preflight with zero active matches and no writes. Plan SHA-256 is
`8bbd2a7ebe63b0e1cac6ce8c85dbc070cc495759bdd232192bd609d55dfc07eb`;
preflight JSON SHA-256 is
`846f840aa154858734147b0543564acb5c9552e91e94408c6dccff8cc2af28a9`, and
its manifest SHA-256 is
`18d4cc853a05ad2304a5cc5320fd1cb1907edaa0d6a9d75e881bd712667dbb5b`.
The durable locator is
`runtime/ample-final-bank-riff-retry-06-attempt-08-job.local.json`. Initial
supervisor identity is PID 26884, creation ticks `639283545430409510`, with an
exact plan/locator command and status `waiting_for_config_stability`. The plan
pins the updated shared ancestry module and both supervisors; it still selects
only the two fixed failed IDs, preserves the 24-hour bound, and delegates the
three-sample H: budget gate unchanged.

Attempt08 reached the unchanged idle gate and ran both fixed IDs. The outer
supervisor and child exited naturally (child return code 0); the final queue is
`completed` with 2 failed / 0 passed, each with one invocation. Both native
responses and runner executions returned successfully, and each item's config
was restored. Neither item passed the required non-silent audio check:
`Sweet Child O Mine` rendered 732,490 frames at 44.1 kHz with peak/RMS/
non-silent/clipped all zero; `Used to Love Her` rendered 1,289,276 frames with
the same exact zero metrics. Their WAV SHA-256 values are respectively
`71b47395b5d9297c90241241e478a9daaaeef85c35378b5865c4e7a12e968cd0` and
`718c664e96db363e43361287749ecb35bf260a36e4c41624fb1a6e6039563f9b`.
Formal-project writes and catalog writes remained zero. The first WAV is
byte-identical to the same preset's earlier retry3 and retry4 silent outputs,
despite the independent retry and final-bank path; this does not support a
simple disk-timing explanation. The attempts and original failures remain
unchanged. Further retry needs a source-backed shared event/readiness diagnosis;
do not relabel either item or relax the audio threshold.
