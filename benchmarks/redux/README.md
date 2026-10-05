# Readio Redux benchmarks

These opt-in tools exercise a real Readio synthesis pipeline and independently
transcribe its composed WAV with Parakeet Redux. They are benchmark tooling, not
a Readio runtime feature. Redux remains an optional dependency and normal tests
do not download models.

## Install

From the repository root:

```bash
python -m pip install -e ".[kokoro,benchmark]"
```

The first run may download the Kokoro voice/model and Parakeet Redux assets via
their respective public libraries. Readio uses its normal synthesis defaults;
Redux uses CPU by default.

## Single-voice end-to-end benchmark

```bash
python -m benchmarks.redux.benchmark_e2e
```

This uses the fixed `en-us` / Kokoro `v1.0` / `kokoro:v1.0/af_sarah` profile,
creates a project, resolves the target, plans, synthesizes, composes a normal
master at 16 kHz mono, and verifies that master with Redux. It prints each stage,
WER/CER, expected and recognized text, timings, and artifact locations. A fresh
timestamped directory is created under `benchmark-output/`.

Use a fresh explicit work directory when reproducing or collecting artifacts:

```bash
python -m benchmarks.redux.benchmark_e2e \
  --work-dir benchmark-output/redux-e2e-manual \
  --language en-us --engine kokoro --model v1.0 \
  --voice kokoro:v1.0/af_sarah \
  --redux-device cpu --json
```

The directory contains `source.txt`, `result.json`, and the Readio project and
master WAV. Artifacts are retained for PASS, REVIEW, and FAIL in this initial
release; `--keep` is accepted for clarity and future cleanup policy. Do not reuse
a work directory whose `project.readio` already exists.

Options include `--device` for a Readio engine device override, `--redux-model`,
`--pass-wer`, `--pass-cer`, `--fail-wer`, `--fail-cer`, `--strict`, and `--json`.
PASS exits 0; REVIEW exits 0 unless `--strict`; audio, semantic, or pipeline
FAIL exits 1; configuration/preflight failures exit 2. A result JSON is written
when possible even if a pipeline stage fails.

## All-voices compatibility matrix

```bash
python -m benchmarks.redux.benchmark_voices \
  --engine kokoro --model v1.0 --language en-us
```

The catalog is the source of truth. By default the runner keeps runtime-available
non-experimental voices in catalog order. `--include-experimental` opts into
experimental voices. Each voice gets its own fresh project, uses the same
`run_case()` pipeline as the single benchmark, and is transcribed using one Redux
instance for the whole process. A failing voice is recorded and the next voice
still runs.

The output directory (timestamped under `benchmark-output/` by default) contains
`source.txt`, `voices.json`, `results.json`, `results.csv`, per-voice projects,
and copied WAVs named with stable ordinal and sanitized target/voice components.
JSON is authoritative and contains transcripts; CSV is transcript-free for
regression plotting. All artifacts are retained. The command exits nonzero if
any voice fails; `--strict` also makes REVIEW nonzero.

The matrix accepts the same target, device, Redux, threshold, `--work-dir`, and
`--json` options as the single benchmark, plus `--include-experimental`. Use a
fresh `--work-dir` for each run.

## Opt-in pytest test

The real model test uses the same `run_default_e2e()` entry point as the CLI and
is skipped in the normal test suite:

```bash
READIO_E2E_REDUX=1 python -m pytest -q -m e2e tests/e2e/test_redux_e2e.py
```

Fast helper and matrix tests need no Redux model downloads:

```bash
python -m pytest -q tests/test_redux_benchmark.py
```

## Interpreting results

Default classification is PASS at WER <= 0.10 and CER <= 0.05; REVIEW at WER <=
0.20 and CER <= 0.10; worse scores, empty transcripts, invalid audio, or pipeline
errors FAIL. Thresholds can be overridden but are shared across voices.

Zero WER/CER establishes recognized word content, not voice identity, phoneme
quality, naturalness, prosody, timbre, loudness, clipping, or background-noise
quality. The benchmark deliberately does not semantically rewrite numbers,
abbreviations, or symbols; keep the initial corpus plain and fixed.

## Manual GitHub Actions run

`.github/workflows/audio-e2e.yml` is `workflow_dispatch` only. It runs one fixed
benchmark on Ubuntu/Python 3.13 and uploads the full output directory even when
the benchmark fails. It does not add model downloads to the normal pull-request
suite and does not run the all-voices matrix.
