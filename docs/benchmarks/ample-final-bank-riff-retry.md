# Ample final-bank Riffer retry

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
