# Python SDK / CLI

Implementation: `src/music_learning/sdk.py` and `cli.py`, currently run from the
source checkout with `PYTHONPATH=src`. They only use the HTTP API; there is no SDK-side project
writer. `Client.call`, `execute`, ordered `batch`, streaming `upload`,
`import_audio`, `wait`, cursor-based `events`, and `asset_bytes` cover all P1
transports. Capabilities and argument schemas come from `describe_capabilities`.

`batch` stops on the first error. Previous successful commands stay committed;
it is not a multi-command transaction. Save complete envelopes with stable
idempotency keys to safely replay a batch. A single `revisions.commit` atomically
publishes a validated snapshot and advances the authoritative revision.

P2 adds `import_audio(..., preprocess=True)`, `Client.preprocess`, and the shared
`jobs.stages`, `jobs.retry`, `preprocessing.get/adopt` operations. Wait for the
import job, then its `result.preprocess_job_id`; upload/import success alone is
not a ready project. Reanalysis creates a candidate when revisions already
contain music data. See `docs/14-project-service.md` and
`docs/15-preprocessing-pipeline.md` for runnable examples and phase boundaries.
