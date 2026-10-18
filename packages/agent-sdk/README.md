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

See `docs/14-project-service.md` for setup and runnable examples.
