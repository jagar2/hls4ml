# Hands-on Dataerai tutorials

These tutorials use the actual recording implementation. The local examples
need Python 3.10 or newer and Git, but no Dataerai account, training framework,
C++ compiler, FPGA, or licensed HLS tool. They do not contact Dataerai.

## Prepare an isolated checkout

Use a terminal with Git and Python installed:

```bash
git clone --branch codex/dataerai-provenance https://github.com/jagar2/hls4ml.git
cd hls4ml
python3 -m venv .venv
source .venv/bin/activate
python -m pip install .
git status --short
```

The commands below assume this checkout is the current directory and the
virtual environment remains active. On Windows, activate the virtual
environment using its platform-specific `Scripts` activation command.
The checkout already ignores `.venv/` and `.dataerai/`. `git status --short`
should print nothing. Commit intentional source edits before recording.

## Tutorial 1: from data to fitted model to HLS C++

Run the provided, complete Python script:

```bash
python docs/tutorials/dataerai/first_workflow.py --dataerai-preserve
```

The script opens its own `Run`; do not also launch it through the provenance
`run --script` command, which would try to nest two workflow runs.

The script performs four explicit activities:

1. Generate 32 synthetic examples from a seeded random generator, using
   `y = 0.75*x[0] - 0.25*x[1] + 0.1`, and record the dataset file.
2. Fit a linear model with `numpy.linalg.lstsq`, record its fitted weights,
   and connect the fit to the dataset with `derived_from`.
3. Compute the model's mean squared error on those synthetic training examples,
   preserving the metric and its measurement method.
4. Construct an hls4ml Dense graph with those fitted weights and generate its
   C++ project. Existing graph, flow, optimizer, and writer hooks add their
   own nested provenance automatically.

The model fit is a real calculation over synthetic data. It is not a benchmark
of a trained neural network or a measurement of FPGA inference. The script
does not call `compile`, `predict`, or `build`; fixed-point accuracy, latency,
and FPGA resources are not measured. Its metric is explicitly named
`numpy_training_mse`, with `hls_inference_measured` set to `false`.

After successful completion, these files exist:

```text
.dataerai/tutorial/
  run.jsonl                 ordered, hash-chained provenance
  run.jsonl.files/          immutable SHA-256 snapshots
  run.jsonl.zip             portable preservation bundle
  dataset.npz               generated synthetic inputs and targets
  model.npz                 fitted weights and bias
  metrics.json              measured NumPy training error
  hls/
    firmware/linear_tutorial.cpp
    ...                     generated project/configuration/weight files
```

No generated files are committed. The journal records their paths and SHA-256
checksums. With `--dataerai-preserve`, snapshots preserve each recorded
version even if the original path is later overwritten or deleted. Keep the
bundle or upload it using the walkthrough below. The final console line reports the output location and measured
metrics; values are calculated on your machine, not copied from this page.

### Verify and inspect what was recorded

```bash
python -m hls4ml.provenance verify .dataerai/tutorial/run.jsonl
python docs/tutorials/dataerai/inspect_journal.py .dataerai/tutorial/run.jsonl \
  --dot .dataerai/tutorial/graph.dot
```

`verify` validates the chain and references, then prints an event count and
integrity root. The inspector prints a JSON report containing `status`,
`source`, `activities`, `artifacts`, and correctly directed `edges`.
Look for:

- Workflow status `succeeded` and the full source commit in `source.commit`.
- `prepare_dataset`, `fit_linear_model`, `evaluate_numpy_fit`, and `generate_hls`.
- Nested `hls4ml.model.graph.from_layer_list`, optimization, and write activities.
- Artifact checksums for `dataset.npz`, `model.npz`, `metrics.json`, and HLS files.
- A `derived_from` edge from the fit activity to the dataset, and `produces`
  edges from activities to their output records.

The DOT file is an optional graph export. If Graphviz is already installed,
render it with `dot -Tsvg .dataerai/tutorial/graph.dot -o .dataerai/tutorial/graph.svg`.
Neither Graphviz nor a rendered graph is needed to verify or synchronize a run.
See [graph semantics](dataerai-records.md) to distinguish step nesting from
data dependencies and start records from completion records.

### Read the full executable source

Open [the workflow script](../tutorials/dataerai/first_workflow.py) or
[the journal inspector](../tutorials/dataerai/inspect_journal.py) directly.
The tutorial source is included directly so this page and the tested script
cannot drift independently:

```{literalinclude} ../tutorials/dataerai/first_workflow.py
:language: python
```

## Tutorial 2: record a failure without losing completed work

Choose a new output directory; existing journals are never overwritten:

```bash
python docs/tutorials/dataerai/first_workflow.py \
  --output .dataerai/tutorial-failure --fail-after-fit --dataerai-preserve
```

This command intentionally exits with code 1 and prints a `RuntimeError`.
Run it separately from shell chains that stop at the first error, then inspect:

```bash
python -m hls4ml.provenance verify .dataerai/tutorial-failure/run.jsonl
python docs/tutorials/dataerai/inspect_journal.py .dataerai/tutorial-failure/run.jsonl
```

The journal should verify, the workflow should be `failed`, and dataset/model
references should remain present. No HLS project was generated because the
intentional failure occurred before that activity. The failure records the
exception type; raw exception messages and terminal output are not
automatically copied into the journal.

An operation failing and a journal being invalid are different outcomes.
This failed run is valid evidence and can be synchronized normally. A process
killed before completion may instead require `sync --allow-incomplete`.
See the [recovery table](dataerai-operations.md).

## Tutorial 3: compare runs and recover the source version

Change a parameter while keeping the source commit constant:

```bash
python docs/tutorials/dataerai/first_workflow.py \
  --output .dataerai/tutorial-seed43 --seed 43 --dataerai-preserve
```

Inspect both runs. They have distinct workflow IDs and timestamps. The source
commit is the same; the root parameters, generated dataset, and content
fingerprints identify the changed seed and data. Fitted weights can remain
numerically equivalent because both datasets use the same exact linear rule.
UUIDs, times, archive container bytes, and build stamps are not promised to be
identical across otherwise equivalent reruns.

Extract the recorded source commit without editing either journal:

```python
from hls4ml.provenance import read_journal

events = read_journal('.dataerai/tutorial/run.jsonl')
source = events[0]['metadata']
print(source['repository'], source['commit'], source['parents'])
print(events[1]['metadata']['parameters'])
```

Use `git show FULL_COMMIT:docs/tutorials/dataerai/first_workflow.py` and
`git log FULL_COMMIT -- docs/tutorials/dataerai/first_workflow.py`, replacing
`FULL_COMMIT` with that value. Git provides the script and its history; the
provenance system does not need another copy of its file contents. If the
object is absent locally, fetch it from the recorded repository after checking
that the repository is the one you expect.

Before claiming a reproduction, also check the recorded `dirty` flag,
environment, dependency versions, parameters, and external artifact checksums.
A clean Git commit reconstructs tracked source; it does not restore external
data or guarantee numerically identical behavior on another toolchain.

## Next: connect this evidence to Dataerai

Follow [the synchronization walkthrough](dataerai-operations.md)
using `.dataerai/tutorial/run.jsonl`. It records metadata and graph relationships
in an existing project. Retrying synchronization reuses the saved evidence;
rerunning the tutorial creates a new scientific execution.

These scripts are exercised by `test/pytest/test_dataerai_tutorials.py`,
including successful generation, fitted coefficients, dependency edges,
changed-seed comparisons, failure retention, and rejection of edited evidence.

## Preserve the tutorials in your Dataerai project

The same switches work for success, failure, and changed-seed tutorials. Add
`--dataerai-upload --dataerai-sync` to any command above once configured below.
To run the calculations with no provenance, use `--no-dataerai` alone.
To record only references without copying data, use `--dataerai` alone.

Install the Dataerai Python SDK using your Dataerai installation instructions,
and authenticate its transfer daemon to your intended server. The metadata API
uses `DATAERAI_TOKEN`; uploads use the SDK daemon's stored login. Both must have
access to the same project. Configure these in your shell:

```bash
export DATAERAI_SERVER=https://beta.dataerai.com
export DATAERAI_PROJECT_ID=YOUR_PROJECT_UUID
export DATAERAI_COLLECTION_ID=YOUR_COLLECTION_UUID
# Supply DATAERAI_TOKEN securely for metadata sync.
# DATAERAI_BINARY optionally points to a transfer daemon executable.
# DATAERAI_ALLOCATION_ID optionally chooses storage instead of the project default.
python -m hls4ml.provenance preserve .dataerai/tutorial/run.jsonl --upload --sync
python -m hls4ml.provenance preserve .dataerai/tutorial-failure/run.jsonl --upload --sync
python -m hls4ml.provenance preserve .dataerai/tutorial-seed43/run.jsonl --upload --sync
```

Each upload stores one bundle containing the complete journal, manifest, and
recorded external file versions, including data, model, metrics, generated HLS,
and external executed source. Git-backed source remains a commit/path/blob
reference. Identical snapshots share one ZIP member. The receipt at
`run.jsonl.preserved.json` names the exact uploaded content version and checksum.
An upload retry can add another content version; graph replay remains idempotent.
An account without a usable storage allocation cannot preserve bytes until
storage is configured. Failed uploads retain the local bundle for retry.

### Verify recovery independently

Download the bundle's recorded content version using Dataerai. Check its SHA-256
against the receipt, then check each member without extracting arbitrary paths:

```python
import hashlib
import json
import zipfile

with zipfile.ZipFile('downloaded-run.zip') as bundle:
    manifest = json.loads(bundle.read('manifest.json'))
    for item in manifest['files']:
        with bundle.open(item['member']) as stream:
            checksum = hashlib.file_digest(stream, 'sha256').hexdigest()  # Python 3.11+
        assert checksum == item['sha256']
    print(len(manifest['files']), 'recorded file versions verified')
```

On Python 3.10, update a `hashlib.sha256()` object from streamed blocks instead
of `file_digest`. The manifest maps each original path and journal event to its
content-addressed member. Recover Git files separately using the recorded full
commit and path. Never substitute the latest branch head for the recorded commit.

These tutorials preserve every file they produce during the scientific workflow.
Custom workflows must declare additional data paths or call `run.artifact`,
`run.artifacts`, or `run.array`; files created outside instrumented operations
are not discovered by watching the entire filesystem. Stdout/stderr, browser
activity, and remote service internals are not automatically captured. Save
needed logs explicitly and record them as artifacts.
