# Ample factory validation chain

This maintenance chain continues the existing AGLP35 run and then uses only
the already-prepared AGM33 and AGSC45 fixed runs. It is serial: each run's
supervisor must exit and every queue item must have a terminal result before
the next fixed run can start. The instrument validator retains its own
three-sample H: idle gate before each item.

The local locator is `runtime/ample-factory-chain.local.json`. It names the
chain plan and the E: checkpoint at
`data/maintenance/instrument-intake/20261021-all/ample/factory-chain-20261022-a/`.
The checkpoint uses an OS byte-range lock, records the existing LP PID and
creation time, and stores each later child PID/creation time before waiting.
A second supervisor is rejected by that lock. If a PID changes, a child exits
with unfinished queue items, or a launch is ambiguous after interruption, the
chain records `blocked` and waits for diagnosis; it does not restart, resume,
or retry a run automatically.

The one planned resource handoff is an LP cooperative pause. After its current
item finishes, the fixed runner records `paused_at_boundary`; the chain records
`blocked` with `lp_exited_before_terminal_queue` and does not launch AGM/AGSC.
To resume that same chain, first remove the pause only through the fixed LP
runner's `resume` action, then update the local plan's LP PID, wrapper PID,
creation times, and exact command fragments to the newly started supervisor.
The plan pins the original 113-item source-queue SHA plus each product's sorted
ID set, count, and identity fingerprint. Run the chain with
`--resume-lp-handoff`; recovery is accepted only for that exact blocked reason,
with no progressed child, no remaining pause request, matching source/run
fingerprints, matching live process identities, and no other Ample run tree.
The complete prior blocked checkpoint is appended to `recovery_history` before
the chain returns to `waiting_for_lp`. Any mismatch remains blocked for review.

The chain verifies each prepared run's queue identity set and pinned source
and host hashes before use. It does not edit a fixed queue or its pin, modify
source libraries, touch formal projects/catalog data, or publish presets. A
failed or inconclusive preset remains in its original run report; the AGM
`Planet Caravan` item stays `blocked_missing_sample_group` because AGMPk is
absent. The AGM result does not prevent the serial AGSC run once the AGM queue
is fully terminal.

Start with the existing local locator after reviewing its plan:

```powershell
data/envs/app/Scripts/python.exe scripts/instruments/run_ample_factory_chain.py --plan runtime/ample-factory-chain.local.json
```

Validation covers the nonterminal-LP stop, continuing from a failed AGM queue
to AGSC, narrowly recovering the expected cooperative LP handoff, rejecting
changed IDs or process identities, and cross-process lock rejection. Real
queue counts remain derived from each `queue.json` item list, not copied from a
potentially stale status summary.
