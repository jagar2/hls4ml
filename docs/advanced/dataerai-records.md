# How Dataerai provenance works

The integration answers four questions: which source and environment were
used, which activities executed, what they consumed and produced, and how
each activity ended. A journal captures those facts before network
synchronization. Dataerai exposes them as assets and directed relationships.

## Follow one workflow through the graph

The tutorial's conceptual graph looks like this. Arrows point in the same
direction as Dataerai relationships:

```text
workflow          --implements-------> source commit
prepare_dataset   --step_of-----------> workflow
prepare_dataset   --produces----------> dataset
fit_linear_model  --derived_from------> dataset
fit_linear_model  --produces----------> fitted model
generate_hls      --derived_from------> fitted model
graph.write       --step_of-----------> generate_hls
graph.write       --produces----------> C++ file checksum record
write completion  --records_telemetry-> graph.write
```

The diagram is abbreviated: the tutorial also includes evaluation, source
file records, optimization activities, and a final workflow completion.
The inspector's DOT export draws the actual journal rather than this summary.

| Relationship | Direction and meaning |
| --- | --- |
| `implements` | Workflow/activity → the source revision or script it uses |
| `step_of` | Nested activity → its parent activity/workflow |
| `derived_from` | Consuming activity → an input artifact or a recorded prior producer |
| `produces` | Producing activity → its output artifact |
| `records_telemetry` | Completion/log record → the activity whose outcome it reports |

`step_of` establishes containment, not data flow. A parent may contain several
independent children. Use `run.step(..., inputs=[artifact_id])` when a child
depends on a particular earlier artifact. These are local journal IDs returned
by `run.artifact`, not arbitrary server asset IDs or filenames.

Tracked synchronous functions also link consumers to prior producers when
recorded array/model fingerprints match. This is a content-based association;
it does not prove that two identical arrays share an object identity or that
every possible data dependency has been discovered. Explicit input references
make dependencies unambiguous.

## Start records do not change into completion records

A workflow or activity starts as a `simulation` record with status `running`.
Its final status and exception type live in a separate `log` record linked by
`records_telemetry`. Activity completions also carry duration and any supplied
results; the root completion includes the Git state observed at the end.

This preserves evidence when execution stops midway. A failed activity still
has a start and a failed completion. A hard-killed process can have a start
without a completion. Do not infer success from the existence of an output
file or from the first record you open in the console. Follow the completion
relationship; the supplied inspector does this for the root workflow.

The asset kinds are deliberately ordinary Dataerai kinds:

| Record | Dataerai kind |
| --- | --- |
| Git revision and script references | `software_code` |
| Workflow and activity starts | `simulation` |
| Activity/workflow completions and GitHub steps | `log` |
| Referenced external artifact files | `dataset` |
| Files whose bytes match their committed Git blob | `software_code` |

These kinds describe how the adapter stores evidence; an external model file
is not automatically classified as a specialized model type. The file's title,
role, metadata, and relationships provide its meaning.

## Git identities and external content identities

The initial source record contains a normalized repository URL when available,
full commit and tree hashes, parent commits, branch label, shallow-checkout
flag, submodule status, and dirty-state information. The branch is context;
the immutable commit/tree identities identify the source version. The full
history stays in Git and remains reachable through the commit's parents.

A file reference records its path, byte size, and SHA-256. When its bytes match
the committed blob, it also carries the repository, commit, relative path,
blob ID, and `matches_worktree=true`, with `storage=git`. Modified or untracked
files remain `storage=external`. Git and SHA-256 hashes serve different
purposes; never substitute one for the other.

Git LFS stores pointers in Git. A local large file's content differs from its
pointer blob and is consequently external in this integration. Preserve its
LFS or other storage separately. An uninitialized submodule records its pinned
state but its files are not magically present in the checkout.

`--allow-dirty` explicitly permits a source tree that a commit cannot fully
reconstruct. Changed paths and hashes are recorded, not patches or replacement
source bytes. Neither a dirty file's hash nor the run's journal is a backup.
Committing the source before running gives the strongest Git-based reference.

## What parameters and outputs mean

Parameters/results are converted to JSON-compatible metadata. NumPy arrays
carry shape, dtype, and SHA-256. Supported models carry configurations and
weight fingerprints. Ordinary dictionaries, lists, and scalar values remain
metadata values after redaction; avoid embedding bulk data in those containers.
An unsupported object is labeled `capture=type_only` rather than silently
treated as a fully captured object. Use an explicit file reference for it.

The active Python environment is described by Python/platform and installed
distribution versions. Imported Python files inside the selected workflow
repository are inventoried at completion. Function records include a hash of
the executed Python code object; that hash is interpreter-specific and is not
a portable substitute for a source commit. Vendor tool versions must be
provided explicitly through `tool_versions`.

File hashes are observations of files at recording time. Keep inputs and
outputs stable while they are being hashed; the adapter does not freeze a
filesystem or sandbox an arbitrary subprocess. Generated directories are
inventoried at operation boundaries, so nested write/compile/build activities
can each record an observation of the same path.

## Journal integrity and synchronization identity

Each JSONL line is one node with:

| Field | Meaning |
| --- | --- |
| `schema`, `id`, `kind`, `title` | Schema version, journal UUID, record kind, and display label |
| `sequence`, `time` | Append order and observed UTC timestamp |
| `metadata` | Redacted facts about this source, activity, completion, or artifact |
| `links` | References to earlier journal nodes and their relationship types |
| `previous`, `hash` | Previous node's hash and this node's canonical JSON hash |

Ordinary links run from this node to the earlier `target`. A link with
`direction=incoming` instead runs from the earlier node to this one. This lets
an immutable output record express `earlier activity → produces → output`
without editing an already written activity. `inspect_journal.py` resolves
this direction before reporting/exporting edges.

Synchronization derives server UUIDs from the server/project/collection
destination and each journal node's identity/hash. A local journal UUID is
therefore not the server asset UUID. Keep the synchronization response's
`run_asset_id` to find the root in Dataerai. Replaying the same file into the
same destination reuses asset identities. A new run, recaptured CI observation,
different collection, or changed server origin is a different identity scope.

Verification checks the recorded chain and references. It does not prove that
the recorder told the truth or detect an attacker recomputing an entire chain.
It also does not by itself prove completeness: removing complete trailing
lines leaves a valid prefix. Check the root completion and retain the final
integrity root independently when completeness matters. Standard sync rejects
an incomplete run; `--allow-incomplete` explicitly permits that evidence.

## What reproducibility still requires

A useful reproduction needs the recorded source commit, accessible external
inputs with matching hashes, parameters/seeds, compatible dependencies and
vendor tools, and the appropriate execution hardware. Provenance makes those
dependencies inspectable. It does not install them, restore expired CI
artifacts, recreate an uncommitted notebook cell, or promise bit-identical
results across different numerical libraries and compilers.
