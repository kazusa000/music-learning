# Owned process inventory trace

`music_learning.owned_process_inventory_trace.OwnedProcessInventoryTrace` is an
opt-in observer for a future caller that already owns a process launch and
injects its inventory callback. It records only rows whose PID matches the
launcher's `launch.json` PID, or a `missing` sample. It does not enumerate
processes itself, launch or stop a process, or decide whether a row passes the
caller's identity check.

The observer returns the same inventory list and leaves every row untouched.
It verifies that the caller's host, request, and response paths match both
`launch.json` fields and the three exact `argv` elements. Optional expected host
and request SHA-256 values further bind the launch metadata. Those caller paths
and launch fields are evidence bindings, not observed attestations.

Raw `command_line` is retained only after the selected row matches the caller's
expected host name and executable path, the caller-supplied coordinator PPID,
the launch PID, a usable creation time, and all three expected command tokens.
That first qualifying identity pins the PID lifetime. Later rows retain raw
command lines only while PID, creation time, name, executable, and parent remain
the same. Other rows for the selected PID are represented by PID, creation
time, name, parent, executable path, command-line character/UTF-8 lengths and
SHA-256, expected-token presence, and a redaction reason. Rows for other PIDs
are never copied to the trace. The trace also records UTC and monotonic
timestamps, sample sequence, the last stored sample sequence, and bounded
errors. It refuses to overwrite its destination and enforces sample, row, and
complete-file byte limits.

For a caller wrapping the existing Ample diagnostic call, construct the observer
in the unique attempt directory before dispatch and place the call inside
`capture_call_errors()`:

```python
trace = OwnedProcessInventoryTrace(
    process_inventory,
    launch_path=request_path.parent / "launch.json",
    host_path=record["host_path"],
    request_path=request_path.resolve(strict=True),
    response_path=record["response_path"],
    trace_path=request_path.parent / "inventory-observer.json",
    owned_parent_pid=os.getpid(),
    expected_host_sha256=record["host_sha256"],
    expected_request_sha256=record["request_sha256"],
)
with trace.capture_call_errors():
    result = _host_call(..., inventory_fn=trace)
```

The outer context records an exception raised by the caller's validator after
the inventory callback returns, then re-raises that same exception. The observer
does not weaken, replace, or reproduce the identity policy. Its contract tests
use finite mocked inventories only; they do not establish behavior of CIM or an
actual host process.

A separate future E-only lifecycle regression may test this boundary: after the
caller has strictly captured the initial owned identity and its `OwnedProcess`
handle reports terminal, a later CIM tombstone or reused-PID row should not be
treated as the still-active original process for command-line validation. If no
initial identity was captured, the existing strict failure should remain. This
is a test recommendation only; it does not change the frozen runner or authorize
the current attempt to be rerun.
