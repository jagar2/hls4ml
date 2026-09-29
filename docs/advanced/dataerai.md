# Dataerai workflow provenance

The optional Dataerai integration records workflow execution, source revisions,
parameters, model and array fingerprints, generated artifacts, and failures.
By default it creates metadata records and relationships without uploading file content.
Opt into snapshots and Dataerai bundle uploads to preserve recorded data bytes. Git commit/tree/blob references identify committed files.
Checksums and locations identify other artifacts. Explicit parameters and
results are metadata: ordinary Python lists, dictionaries, and strings are
stored as supplied after redaction. Use file references or NumPy arrays for
bulk data; arrays are hashed rather than embedded. With `preserve=True`,
NumPy arguments and results of instrumented operations are additionally saved
as non-pickled `.npy` snapshots.

## Where to start

| Your goal | Read this |
| --- | --- |
| Run a complete example without an account or FPGA tools | [Hands-on tutorials](dataerai-tutorials.md) |
| Understand records, graph directions, Git identity, and reproducibility | [How provenance works](dataerai-records.md) |
| Look up every Python method, CLI option, and environment variable | [API and command reference](dataerai-reference.md) |
| Connect Dataerai, enable CI, retry failures, or diagnose missing records | [Operations and troubleshooting](dataerai-operations.md) |
| Review implementation decisions and validation limits | [Integration design](dataerai-design.md) |

The first tutorial generates a synthetic dataset, fits a linear model, writes
HLS C++, and inspects its provenance graph. The second deliberately fails and
shows which evidence survives. The third repeats the workflow with changed
parameters and explains how to recover the recorded source revision.

```{toctree}
:hidden:

dataerai-tutorials
dataerai-records
dataerai-reference
dataerai-operations
dataerai-design
```

## Record an existing Python workflow

Install this integration branch in the environment that runs your workflow:

```bash
pip install 'git+https://github.com/jagar2/hls4ml.git@codex/dataerai-provenance'
python -m hls4ml.provenance run --repo . --script scripts/workflow.py -- --seed 42
```

Commit source changes first. The default requires a clean Git checkout.
Ignore `.dataerai/` and generated output directories in your workflow repository.
`--allow-dirty` permits development runs, recording changed-file hashes and
marking the source dirty; such a run cannot be reconstructed from its commit
alone. Source state is recorded again at completion. Git LFS content that
differs from its committed pointer is identified as external content.

The Python runner executes the script in its process, so existing hls4ml calls
are captured automatically. It preserves script arguments and exit codes.
`--module package.module` also works. Use `--journal /path/run.jsonl` to choose
the journal path; an existing journal is never overwritten.

The shared hooks cover:

- Public conversion functions, including direct Keras, PyTorch, and ONNX entry points.
- Graph creation, optimization flows and their applied pass lists.
- Project writing, compilation, prediction, tracing, synthesis/build, and save.
- Multi-model graph operations and report parsing.
- Existing hls4ml CLI config, convert, build, and report handlers when invoked
  with the `--dataerai` switch on each command.

Model configurations and weight fingerprints describe the model used. NumPy
arrays are represented by shape, dtype, and SHA-256, not their contents.
Recorded producers of the same array/model fingerprint are linked to subsequent
consumers. Generated project files receive individual checksum records after
write/compile/build, including partial files when an operation fails.

## Include training, analysis, data, and other tools

Wrap a whole notebook or Python workflow with `Run`. Add steps around work
outside hls4ml, such as data preparation and training:

```python
from pathlib import Path
import hls4ml
from hls4ml.provenance import Run

with Run(
    'jet classifier', repository='.', enabled=True, preserve=True, parameters={'seed': 42},
    tool_versions={'vitis_hls': 'your installed version'},
) as run:
    dataset = run.artifact(Path('datasets/jets.npy'), role='input',
                           uri='s3://your-bucket/datasets/jets.npy')
    with run.step('training', inputs=[dataset], parameters={'epochs': 10}) as record:
        model = train_model()
        record['metrics'] = evaluate_model(model)
        model.save('outputs/model.keras')
        run.artifact('outputs/model.keras')
    hls_model = hls4ml.converters.convert_from_keras_model(model, output_dir='outputs/hls')
    hls_model.compile()
    hls_model.build()
```

The example assumes your imports and `train_model`/`evaluate_model` functions.
Use `@tracked('step name')` on your own synchronous functions for automatic parameters,
results, timings, exceptions, and function source references.

For shell scripts, Make, or other executables:

```bash
python -m hls4ml.provenance run --repo . --input configs/model.yml \
  --output outputs/report.json -- bash scripts/build.sh
```

This records the command and its exit status. External subprocess interiors
are opaque: use the Python runner for hls4ml call-level tracking, or wrap each
script/command as a separate step. `run.command([...])` nests a command inside
an active Python workflow. Declare generated files with `--output` and data
dependencies with `--input` or `run.artifact`. Retain external artifacts at the
recorded location; a checksum is evidence of content, not a backup.

## Synchronize to Dataerai

Provide an existing project (and optionally collection) where your account can
create records. Credentials stay in environment variables:

```bash
export DATAERAI_SERVER=https://beta.dataerai.com
export DATAERAI_PROJECT_ID=your-project-uuid
export DATAERAI_COLLECTION_ID=your-collection-uuid  # optional
# Supply DATAERAI_TOKEN through your credential manager or CI secret store.
python -m hls4ml.provenance verify .dataerai/run.jsonl
python -m hls4ml.provenance sync .dataerai/run.jsonl
```

`run --sync ...` synchronizes at the end, including failed runs. Each node is an
ordinary Dataerai asset linked with `implements`, `step_of`, `derived_from`,
`produces`, or `records_telemetry`. Start records remain immutable; a related
completion record carries the final status and result. These are the asset
relationships used by the console graph, not the separate LineageRun API.

Synchronization uses client-assigned UUIDs and replays the exact journal.
Retry the same file after a connection failure or after renewing an expired
token; successful writes retain their identities. A rejected write is an error,
and the journal remains available. There is no token refresh or hidden success
fallback. Keep one synchronization process per journal to avoid concurrent
duplicate relationship races on servers without uniqueness constraints.

Each event is flushed to disk and chained to the previous event's SHA-256.
Verification detects edits, reordering, and broken references. This is a local
integrity chain, not a signature, trusted timestamp, or protection against an
attacker rewriting the entire journal. Preserve the final root independently
when that threat matters. After an interrupted run, `sync --allow-incomplete`
publishes a valid partial journal with its still-running steps visible. A hard
kill cannot emit a final status; malformed/truncated JSON is rejected.

## Observe GitHub Actions workflows

The included `Dataerai provenance` workflow observes completed workflows after
it is present on the repository's default branch. Set repository variable
`DATAERAI_TRACKING=true`, the server/project/collection variables above, and
the `DATAERAI_TOKEN` repository secret. Missing credentials retain a journal
without attempting Dataerai writes; failed synchronization fails the observer
job and preserves its journal as a GitHub artifact for retry.

The observer records the triggering commit and parents, workflow definition
path, run/attempt, actor, jobs, steps, status, times, artifact digests, expiry,
and log/archive references. It checks out only trusted default-branch code;
it never executes the observed PR's checkout or downloads its artifacts/logs.
It excludes itself to prevent recursion. GitHub's retention policies still
apply to referenced logs and artifacts.

To import an existing completed run manually:

```bash
python -m hls4ml.provenance github-run --repo owner/repository \
  --run-id 123456 --journal .dataerai/github-123456.jsonl
python -m hls4ml.provenance sync .dataerai/github-123456.jsonl
```

`github-run` reads `GITHUB_TOKEN`. Re-sync the saved journal for idempotent
retries; recapturing creates a separate observation. To capture commands and
model operations inside CI jobs, invoke the instrumented runner in those jobs.
GitLab and Jenkins jobs can use the same runner; their pipeline/job identifiers
are included when provided by the CI environment.

## Capture boundaries

Recording is opt-in and adds hashing/disk overhead. No recording or network IO
occurs when no `Run` is active. Training frameworks, arbitrary subprocesses,
notebook cell edits, and unwrapped custom operations are not auto-instrumented.
Unsupported objects are explicitly marked `type_only`; provide a file/model
reference for reproducibility. Tool versions must be supplied explicitly;
Python and installed distribution versions are captured automatically.

Use one journal per process. Async tasks inherit their context; new threads
need `contextvars.copy_context()` and must finish before the run exits. Do not
share a journal across processes. Imported Python files inside the workflow
repository are inventoried at completion; other distributions are recorded by
installed version, not uploaded.

Sensitive metadata keys, common credential flags, URL user-info/query strings,
and known secret environment values are redacted. Arbitrary positional strings
cannot reliably be classified as secrets: pass credentials through environment
variables, never literal command arguments or metadata. Raw stdout, stderr,
exception messages, and environment dumps are intentionally not captured.

Git references require continued access to the referenced repository/commit.
This integration does not mirror Git history, upload artifact files, or silently
claim that unobserved activity was captured.

See the [integration design and verification matrix](dataerai-design.md).
