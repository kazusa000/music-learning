# Admitted VST3 plugin allowlist preparation

`scripts/instruments/prepare_admitted_plugin_roots.py` prepares a **proposal**
to make already-admitted VST3 instruments available to the local application.
It does not edit `runtime/app.local.json`, restart the service, create a project,
or copy plugins or sample libraries.

The tool reads the active app configuration and the catalog at the declared
sound-library root. It considers only catalog entries with `backend` set to
`vst3-preset`, `audio_verified: true`, a complete `native_admission` record,
matching first/reopen state identity, and positive audio/restore metrics. It
rechecks the native admission bytes and complete parameter map, the installed
library manifest and referenced preset resources, and the actual VST3 module
SHA-256. Existing `verify_installed_library` behavior is preserved: unchanged
files are checked by stable metadata; changed files are freshly hashed and a
mismatch blocks the proposal.

Each new allowlist item is the exact `.vst3` bundle path named by a verified
catalog entry. The proposal records the single module file and SHA-256 behind
that bundle. It never adds the containing `Common Files\VST3` directory, a
wildcard, or an unverified plugin path. Existing configured roots remain
unchanged.

The CLI waits up to 24 hours for three consecutive idle observations from the
existing H: storage budget, polling every 20 seconds. It then verifies the
catalog once. Progress and the final proposal are written only below
`data/maintenance/instrument-intake/`; a content-addressed copy of the exact
original config bytes is saved there for compare-and-swap review. If the app
config changes before that backup is created, the writer refuses the stale
plan. Applying the change and restarting the local service remain a separate
explicit operation by the release owner.

Example:

```powershell
data/envs/app/Scripts/python.exe scripts/instruments/prepare_admitted_plugin_roots.py
```

Use the resulting `allowlist-plan.json` and its `config_backup` reference for
review. The file has `apply_mode: manual-cas-only` and
`active_config_modified: false`; it is not an installer or deployment command.
