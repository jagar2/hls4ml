# Dataerai API and command reference

The public Python imports include `Run`, `tracked`, `read_journal`,
`preservation_bundle`, `preserve_to_dataerai`, `add_arguments`, and `workflow` from
`hls4ml.provenance`. Use the CLI for synchronization. The implementation's
underscore-prefixed modules are internal and are not a separate stable SDK.

## Python: `Run`

```python
Run(name, *, repository='.', journal=None, allow_dirty=False,
    parameters=None, tool_versions=None, enabled=True, preserve=False)
```

| Argument | Meaning |
| --- | --- |
| `enabled` | Default `True`; `False` disables recording, Git checks, and preservation IO |
| `preserve` | Default `False`; snapshot recorded non-Git files before they can change |
| `name` | Workflow title in the journal and Dataerai |
| `repository` | An existing Git working tree with a committed `HEAD` |
| `journal` | New JSONL file; default is `repository/.dataerai/<uuid>.jsonl` |
| `allow_dirty` | Default `False`; explicit permission to record a dirty tree with incomplete Git reproducibility |
| `parameters` | Root workflow metadata, such as seed, dataset selection, and experiment configuration |
| `tool_versions` | Caller-supplied vendor/tool version metadata; not an automatic version probe |

Use it as a context manager. Entering captures source/environment and opens
the journal. Exiting records success or failure and closes the file. An
existing journal raises `FileExistsError`. A dirty tree raises `ValueError`
unless explicitly permitted. Git must be installed and the repository must
have a commit. A source archive without `.git` is not a substitute.

After entering, `run.path` is the absolute journal path and `run.id` is the
root journal UUID. A `Run` cannot be entered twice or nested in another Run;
use steps for nesting. This class records locally and does not automatically
contact Dataerai when used directly from Python.

### `run.step`

```python
with run.step(name, parameters=None, inputs=(), code=None) as result:
    result['metrics'] = {'accuracy': 0.9}  # Your actual measured value
```

`parameters` describes the activity. `inputs` contains IDs returned by earlier
`run.artifact` calls in the same journal, not server asset IDs. `code` optionally
identifies a source file (for example `__file__`). A nested step is linked to
the enclosing step; otherwise it is linked to the root workflow.

`result` is a dictionary of facts to put on the completion record. Add measured
metrics or application results there. Leave `status`, `duration_seconds`, and
`exception_type` to the recorder. Exceptions are recorded and propagated.
Recording errors also propagate; they are not silently converted into success.

### `run.artifact`

```python
artifact_id = run.artifact(path, role='output', uri=None)
```

`path` is an existing file. Use `role='input'` for a consumed file or the default
`'output'` for a produced file. An input links its current activity to the file
with `derived_from`; an output links the activity to the file with `produces`.
The return value identifies this journal record for later step inputs.

`uri` is an optional stable external location stored as metadata. It neither
uploads the file nor fetches/verifies the URI. URL credentials and query
strings are redacted, so avoid using a signed URL as your only durable pointer.
The stored SHA-256 describes the local file you actually supplied.

Use `run.artifacts(directory, role="input")` or `run.artifacts(directory)`
to inventory a directory recursively. The current run journal, snapshot store,
bundle, and receipt are excluded to prevent recursive capture. Generated HLS project files are inventoried automatically
by the operation hooks. Symlinks are recorded as link references, not as a
snapshot of the target's contents. Preservation mode rejects symlinks. All relative file paths resolve against
the Python process's current working directory.

### `run.command`

```python
completed = run.command(['bash', 'scripts/build.sh'], cwd=None)
```

Pass a nonempty argument sequence, not a shell command string. The subprocess
working directory defaults to `run.repository`; override it with `cwd`.
The recorder captures the argument list, executable identity when resolvable,
recognizable script-file arguments, duration, and return code. It does not
capture stdout/stderr or intercept the subprocess's internal function calls.
It returns `subprocess.CompletedProcess` on success and raises
`subprocess.CalledProcessError` on nonzero exit. Add explicit artifact calls
for other inputs and outputs.

## Python: `tracked`

```python
from hls4ml.provenance import tracked

@tracked('normalize samples')
def normalize(samples):
    return samples / 2
```

This decorator records a synchronous function's bound arguments/defaults,
source reference, executed-code hash, result, and outcome while a Run is
active. Outside a Run it calls the original function without recording IO.
It preserves the original signature and return value. NumPy arrays and
supported model objects are fingerprinted; ordinary scalar/container values
are metadata. Unsupported objects are labeled `type_only`.

Use `run.step` around awaited work instead of decorating an async function.
The decorator's `outputs=True` option is intended for hls4ml graph methods
with `self.config.get_output_dir()`; it is not a general-purpose output-path
setting for user functions.

## Python: `read_journal`

```python
from hls4ml.provenance import read_journal
events = read_journal('.dataerai/tutorial/run.jsonl')
```

Returns a list of all events after checking schema/sequence, hashes, UUIDs,
and references. It raises on invalid/empty input; malformed JSON can raise a
JSON parsing exception. A valid prefix can still describe an incomplete run,
so inspect root completion separately. The tutorial inspector demonstrates
that distinction and resolves incoming relationship directions.

## CLI commands

```text
python -m hls4ml.provenance run [options] --script SCRIPT -- [script arguments]
python -m hls4ml.provenance run [options] --module MODULE -- [module arguments]
python -m hls4ml.provenance run [options] -- COMMAND [arguments]
python -m hls4ml.provenance verify JOURNAL
python -m hls4ml.provenance sync [--allow-incomplete] JOURNAL
python -m hls4ml.provenance github-run --repo OWNER/REPO --run-id ID --journal JOURNAL
```

Use `--help` after any subcommand for its parser-generated help.

### `run` options

| Option | Default / behavior |
| --- | --- |
| `--repo PATH` | `.`; repository used for source identity |
| `--name TEXT` | `hls4ml workflow` |
| `--journal PATH` | New UUID-named file under `repository/.dataerai/` |
| `--allow-dirty` | Off; enables explicitly marked dirty-source recording |
| `--sync` | Off; synchronize the finished journal, including a failed workflow |
| `--script PATH` | Execute a Python script in the recording process |
| `--module NAME` | Execute a Python module in the recording process; mutually exclusive with `--script` |
| `--input PATH` | Repeatable input-file references recorded before execution |
| `--output PATH` | Repeatable output-file references recorded after execution, including failures, if the files exist |
| `--` | End runner options; remaining arguments belong to the script/module/command |

The Python modes execute in the current working directory. An external
command executes in `--repo`. `--repo` does not reinterpret the Python script
path or `--input`/`--output` paths. The simplest convention is to `cd` to the
workflow repository before invoking any mode. Missing declared outputs are
skipped rather than asserted; validate required outputs in your workflow.

Do not combine a Python script that opens its own Run with `run --script`.
Choose either a Python Run context or the CLI-managed context.

Normal command/script exits preserve their integer exit code. A subprocess
terminated by a signal maps to `128 + signal`. Unexpected Python exceptions
propagate and make the CLI fail. Synchronization errors also fail the CLI and
can replace an earlier exit code; inspect the journal to distinguish compute
failure from a publication failure. Verify does not change the journal.

### Sync and observer outputs

Successful sync prints `assets`, `run_asset_id`, and `integrity_root` as JSON.
`assets` is the number of journal nodes processed, including idempotent
replays, not necessarily the number of newly created records. The count does
not include relationships. Keep `run_asset_id` to locate the root in Dataerai.

`github-run` captures one completed run's latest reported attempt at capture
time. It follows job/artifact pagination and preserves the attempt number.
It does not backfill every historical run or earlier attempt automatically,
and rerunning the capture creates a new observation journal. Its job status
and log references describe GitHub's metadata; commands inside the jobs need
their own instrumented runner for operation-level evidence.

## Environment variables

| Variable | Used by | Requirement |
| --- | --- | --- |
| `DATAERAI_SERVER` | `sync`, `run --sync` | HTTPS origin, with no path/query; HTTP allowed only on localhost for testing |
| `DATAERAI_TOKEN` | `sync`, `run --sync` | Bearer token with permission to create metadata records/relationships in the destination |
| `DATAERAI_PROJECT_ID` | `sync`, `run --sync` | Existing project UUID |
| `DATAERAI_COLLECTION_ID` | `sync`, `run --sync` | Optional existing destination collection UUID |
| `GITHUB_TOKEN` | `github-run` | Access to the requested GitHub run/jobs/artifacts metadata |
| `DATAERAI_TRACKING` | Observer workflow repository variable | Set to the string `true` to enable the observer job |

The Dataerai client does not read SDK credential files or refresh tokens.
Supply/renew `DATAERAI_TOKEN` through your credential management process.
No Dataerai variable is required for local recording or verification.

The recorder also copies this restricted CI context when available:
`GITHUB_RUN_ID`, `GITHUB_RUN_ATTEMPT`, `GITHUB_JOB`, `GITHUB_WORKFLOW`,
`GITHUB_SHA`, `CI_PIPELINE_ID`, `CI_JOB_ID`, `CI_COMMIT_SHA`, `BUILD_NUMBER`,
and `BUILD_URL`. It does not dump the full environment. Seeds, arbitrary
environment variables, and tool versions need explicit workflow metadata.

## Optional flags across commands and tutorials

Every `hls4ml config`, `convert`, `build`, and `report` subcommand accepts:

| Switch | Effect |
| --- | --- |
| `--dataerai` | Record locally; no network |
| `--no-dataerai` | Disable recording; incompatible with preservation/publication switches |
| `--dataerai-preserve` | Enable recording, snapshot files, and build a local ZIP |
| `--dataerai-sync` | Enable recording and publish metadata/relationships |
| `--dataerai-upload` | Enable preservation and upload the ZIP through the optional SDK |
| `--dataerai-journal PATH` | Choose a new journal path |
| `--dataerai-repo PATH` | Source checkout; defaults to current directory |
| `--dataerai-input PATH` | Repeatable input file or directory |
| `--dataerai-output PATH` | Repeatable output file or directory, captured even on failure |

Combine `--dataerai-upload --dataerai-sync` to upload the bytes, publish the graph,
and connect the workflow to its preserved bundle. Upload alone retains the
complete journal and graph in the bundle, without creating individual graph
records. Existing commands remain untracked by default. The dedicated Dataerai
tutorial retains its recording-enabled default; it accepts the same switches.

For the generic runner, use `run --preserve`, `run --upload`, and `run --sync`.
Its `--input` and `--output` accept files or directory trees. In preservation mode all explicitly declared paths must exist; missing outputs
are a recording failure. Metadata-only runner mode retains its historical
behavior of skipping missing outputs.

```bash
hls4ml convert -c config.yml --dataerai-preserve
python -m hls4ml.provenance run --preserve --input dataset --output results --script workflow.py
python -m hls4ml.provenance preserve run.jsonl
python -m hls4ml.provenance preserve run.jsonl --upload --sync
```

`preservation_bundle(journal)` returns the ZIP path. `preserve_to_dataerai(journal,
client=connected_sdk_client, server=origin, project=uuid, collection=uuid,
allocation=uuid)` returns an upload receipt with asset ID, immutable content ID,
bundle checksum, and journal integrity root. Collection and allocation are optional.
The SDK must report the same authenticated server; older daemons lacking that
field must be upgraded. SDK upload completion and byte count are required.

The public `add_arguments(parser, enabled=False)` and `workflow(args, name, ...)`
helpers let other scripts use these same switches without duplicating connection
logic. `workflow` accepts `repository`, `journal`, and `parameters` overrides.
It closes evidence before publication, and tries publication after a scientific
failure while propagating the original exception. Publication errors on otherwise
successful work propagate normally. Local evidence is retained in both cases.

`run.array(numpy_array, name='array', role='output')` snapshots an in-memory
array when preservation is enabled and returns its artifact ID. Object arrays
are rejected because preservation never enables pickle. Instrumented hls4ml
operations automatically capture NumPy arrays in argument/result containers.
Save other model/framework objects using their native file format, then call
`run.artifact`; arbitrary object memory is not serialized.
