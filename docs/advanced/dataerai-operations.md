# Operating and troubleshooting Dataerai tracking

Local recording and live synchronization are separate operations. First run
the [local tutorial](dataerai-tutorials.md), inspect its evidence, and then
configure a destination. This page describes the current implementation;
it does not assume a particular Dataerai console layout or invent UI paths.

## Synchronize a tutorial run

Choose an existing Dataerai project where your account can create records and
relationships. Optionally choose an existing collection in that destination.
Use their UUIDs, not display names, URLs, or DIDs. Keep the bearer token in an
environment variable populated by your credential manager or shell session:

```bash
export DATAERAI_SERVER=https://beta.dataerai.com
export DATAERAI_PROJECT_ID=YOUR_EXISTING_PROJECT_UUID
# Optional; omit this if you are not selecting a collection:
export DATAERAI_COLLECTION_ID=YOUR_EXISTING_COLLECTION_UUID
# DATAERAI_TOKEN must already contain your bearer token.

python -m hls4ml.provenance verify .dataerai/tutorial/run.jsonl
python -m hls4ml.provenance sync .dataerai/tutorial/run.jsonl
```

Replace the two UUID placeholders before executing. The server value is an
origin, not an `/api/` URL. Do not put the token in a command argument, script,
notebook, committed `.env`, or the journal. The server enforces its normal
permissions and account requirements; the client does not grant itself access.

Sync prints the root's `run_asset_id`, the event count processed, and the
integrity root. Locate that asset in your project's records and follow its
relationships. Its related completion record gives the workflow's final
status. Generated files remain at their original paths; sync creates metadata
records, not file copies. A successful retry should return the same root asset
ID when the journal and destination have not changed.

You can synchronize the intentionally failed tutorial too. Failure is valid
evidence, and that run should retain its failed completion and earlier
successful outputs. Do not rerun computation just to recover a network write.

Live Dataerai synchronization requires your actual destination and credentials.
The automated client tests use a local HTTP contract server; those tests are
not a claim that your deployed server has been exercised.

## Adapt an existing workflow

For a Python script with ordinary hls4ml calls and no Run context, use:

```bash
python -m hls4ml.provenance run --repo . --name nightly-conversion \
  --journal .dataerai/nightly-001.jsonl --script scripts/convert.py -- --seed 42
```

Run from your repository root, with committed source and ignored generated
output directories. This automatically records the hls4ml entry points; it
does not wrap every custom Python function. Add `@tracked` to synchronous
preparation/training/analysis functions, or explicit `run.step` scopes in a
workflow that owns its own Run. Use the fully executable tutorial as a pattern
for linking training data, fitted model, evaluation, and conversion.

For an existing notebook, a Run context may span the code executed within a
cell. Declare its seed, parameters, inputs, and tool versions explicitly.
Commit the notebook or an exported script separately. Cell edit history,
previous interactive state, widgets, and unexecuted cells are not captured.
Do not call this complete notebook provenance without preserving those sources.

For shell workflows, wrap each meaningful command and declare its inputs and
outputs. Wrapping `bash pipeline.sh` records one command; it does not reveal
every process or internal script launched by that shell. Similarly, a Run
does not automatically follow work submitted to another machine.

## GitHub Actions: two complementary capture layers

The observer records workflow/job/step metadata after a run completes.
The instrumented runner inside a job records scientific operations and
content identities. Enable both when you need both levels of evidence.

### Enable the included observer

1. Review and adopt `.github/workflows/dataerai-provenance.yml` plus the
   integration code on the repository's default branch. A feature branch alone
   does not activate a `workflow_run` observer.
2. Set repository variable `DATAERAI_TRACKING` to `true`.
3. Set repository variables `DATAERAI_SERVER`, `DATAERAI_PROJECT_ID`, and
   optionally `DATAERAI_COLLECTION_ID`.
4. Set repository secret `DATAERAI_TOKEN`. The observer uses GitHub's own
   `GITHUB_TOKEN` for GitHub API reads.
5. Complete a small workflow, inspect the observer's result and saved journal,
   and confirm its source commit, run attempt, jobs, steps, and destination.

The observer checks out trusted default-branch code, not a triggering PR's
code. It does not download the observed run's logs or artifacts. Missing
Dataerai credentials skip publication while retaining the captured journal;
publication errors fail the observer job. The journal artifact is named
`dataerai-<observed-run-id>-<attempt>` and can be downloaded for the same sync
retry command used locally. It excludes its own workflow name from capture.

GitHub event delivery, workflow chaining limits, and artifact retention are
still platform constraints. The manual `github-run` command can capture an
accessible completed run when automatic observation was not active. It does
not restore expired logs or backfill all historical attempts.

### Record operations inside a trusted job

This recipe assumes `scripts/convert.py` exists, does not open its own Run,
and uses this integration. Add the following steps to an appropriate trusted
job after checkout and dependency installation:

```yaml
- name: Record conversion
  run: >-
    python -m hls4ml.provenance run --repo .
    --journal .dataerai/conversion.jsonl
    --script scripts/convert.py
- name: Preserve evidence even if conversion failed
  if: always()
  uses: actions/upload-artifact@v7
  with:
    name: conversion-provenance
    path: .dataerai/conversion.jsonl
    include-hidden-files: true
```

Use a complete checkout when local history/submodules are needed, and ignore
the journal and generated output directories. Publish the retained journal
from a trusted context with the destination credentials. Do not expose a
Dataerai token to arbitrary pull-request code. This recipe has no publication
step, so it is useful in jobs that should produce only local evidence.

### GitLab CI and Jenkins

The same runner can be used in their existing job scripts. It records
GitLab pipeline/job/commit IDs or Jenkins build identifiers when those
variables are present. There is no separate automatic GitLab/Jenkins observer.

For a GitLab job with the integration and its dependencies already installed:

```yaml
record-conversion:
  script:
    - python -m hls4ml.provenance run --journal .dataerai/conversion.jsonl --script scripts/convert.py
  artifacts:
    when: always
    paths:
      - .dataerai/conversion.jsonl
```

In Jenkins, invoke the same command in the appropriate shell step and archive
`.dataerai/conversion.jsonl` from an `always` post-action. The command runs only
one script; the Jenkins pipeline itself must preserve journals from every
job/node you want represented. Each process needs its own journal filename.

## Troubleshooting and recovery

| Symptom | Meaning | Next action |
| --- | --- | --- |
| `Git worktree is dirty` | Source/generated files are not clean or ignored | Inspect `git status`, commit intended source, ignore generated paths, or explicitly use `--allow-dirty` and retain the changed source |
| `FileExistsError` for the journal | The chosen evidence file already exists | Use a new path for a new execution; use `sync` to retry the existing execution |
| Missing Git/`HEAD` error | Git is absent, directory is not a working tree, or no commit exists | Install/use Git and a committed checkout; a downloaded source archive is insufficient |
| Nested Run error | Both the script and CLI wrapper opened a Run | Use one owner for the workflow context; use steps for nesting |
| `type_only` in parameters/results | That object is not fully serializable by the adapter | Supply a file reference or explicit scalar configuration; do not infer complete object capture |
| No inner training/shell steps | Code ran outside the instrumented boundaries | Add steps/decorators or instrument the Python process itself |
| Missing declared output record | `--output` file did not exist at final recording time | Check the script's output path and working directory; make required-output validation explicit |
| HTTP 401 | Token missing/expired/rejected | Renew `DATAERAI_TOKEN`, then re-sync the same journal |
| HTTP 403 | Server rejected permissions or an account requirement | Resolve the server-reported requirement; re-sync without rerunning computation |
| HTTP 409 creating an asset | Identity/destination conflict | Check the destination and server response; do not treat arbitrary conflicts as successful retries |
| Connection/5xx error midway through sync | Some writes may already have succeeded | Retain the original journal and retry it with the same destination |
| HTTPS/redirect error | Server is not a permitted origin or redirects requests | Use the final HTTPS origin; bearer credentials are not forwarded through redirects |
| `Run is incomplete` | Root completion is absent | Inspect the partial record and use `--allow-incomplete` only when publishing interrupted evidence intentionally |
| Invalid hash chain or JSON | Evidence was changed, reordered, corrupted, or truncated mid-line | Preserve the original for diagnosis; restore a known intact copy rather than editing it into apparent success |
| Run asset still says `running` | It is an immutable start record | Follow its `records_telemetry` completion relation or use the inspector |
| GitHub observer never starts | Configuration/default-branch/event prerequisites may be absent | Check default-branch adoption, variable value, Actions status, and the observed run; try manual capture |
| External URI or CI log expired | A reference outlived the original storage | Recover from your retained storage; the journal cannot recreate deleted bytes |

The only 409 treated as a successful duplicate relationship is the server's
specific identical-relationship response. Do not run concurrent sync processes
against the same journal; relationship uniqueness may not be transactional
on every Dataerai deployment.

## Retention, concurrency, and production rollout

Retain the journal, its final root, referenced external files, and access to
the recorded Git commits for as long as reproducibility is required. Decide
where generated weights, datasets, and build products will live; a local
absolute path alone is not a portable shared storage plan.

Use one journal per process. Async tasks inherit their context; spawned
threads need an explicit copied context and must finish before the Run exits.
Remote jobs and child processes do not inherit an in-memory recording context.
Do not share a journal file between processes.

Start with a small local run, then one destination project, then a trusted CI
job, and finally the observer. Inspect the graph and run a retry before
depending on it. Hashing and per-event disk synchronization add overhead,
especially for large model arrays or generated project trees. Removing the
Run wrapper or disabling `DATAERAI_TRACKING` stops new capture without deleting
existing evidence. Deleting remote records is a separate administrative action.
