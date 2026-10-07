# Readio Redux verification

These opt-in checks exercise Readio's normal planning, synthesis, and composition pipeline, then independently transcribe the resulting audio with Parakeet Redux. Redux is optional: ordinary Readio synthesis and composition do not require the verification extra, load Moondream, or generate derived ASR timings.

## Install

From the repository root:

```bash
python -m pip install -e ".[kokoro,verification]"
```

The first run may download Kokoro assets and the selected Redux model. The normal test and pull-request workflows do not perform real-model inference or bundle model assets.

## Supported end-to-end self-test

Prefer the packaged CLI for new automation and manual verification runs:

```bash
readio selftest e2e \
  --engine kokoro \
  --model v1.0 \
  --voice kokoro:v1.0/af_sarah \
  --lang en-us \
  --output benchmark-output/audio-e2e \
  --json
```

The output directory contains stable JSON evidence, the source and project, and the composed master WAV. Planning provenance and any blocked-plan attempt remain inspectable. A failing pipeline still writes a diagnostic result when possible. Add `--strict` to make REVIEW return a nonzero status.

For the Pocket short-tail regression, run a sequence of fresh projects while reusing one Redux session:

```bash
readio selftest e2e \
  --case pocket-short-tail-v1 \
  --repetitions 20 \
  --engine pocket \
  --model english_2026-04 \
  --voice pocket:english_2026-04/alba \
  --lang en \
  --output benchmark-output/pocket-short-tail \
  --json
```

Each attempt records terminal completeness and WER/CER; the exact short phrase must be complete for an attempt to pass.

## Voice matrix and timestamp QA

Use the packaged API/CLI for voice matrices. Each voice gets an independent project while the Redux lifecycle is shared:

```bash
readio selftest voices \
  --engine kokoro --model v1.0 --lang en-us \
  --output benchmark-output/voice-matrix --json
```

`--include-experimental` opts into experimental voices. The command writes JSON and transcript-free CSV artifacts and returns nonzero for failures; `--strict` also rejects REVIEW.

Timestamp QA is explicit and separate from composition:

```bash
readio selftest timestamps \
  --engine kokoro --model v1.0 --voice kokoro:v1.0/af_sarah \
  --lang en-us --output benchmark-output/timestamps --json
```

To generate ASR-derived word timing artifacts for a project, call `Readio().verification.generate_timestamps(...)` explicitly. These derived artifacts are kept in the verification cache; they do not mutate synthesis sidecars, synthesis cache identity, or native timing declarations, and composition never invokes ASR implicitly.

## Legacy benchmark entry points

The old Python module commands remain available for compatibility and preserve their result JSON/CSV format:

```bash
python -m benchmarks.redux.benchmark_e2e --work-dir benchmark-output/legacy-e2e
python -m benchmarks.redux.benchmark_voices --work-dir benchmark-output/legacy-voices
```

They delegate execution to the public `Readio().verification` API. Prefer the supported `readio selftest` commands for new scripts and CI. Existing scalar `--engine-option KEY=VALUE`, threshold, JSON, and strict options remain accepted by the wrappers. Credential-like option names are redacted in output.

## Manual GitHub Actions run

`.github/workflows/audio-e2e.yml` is `workflow_dispatch` only. It installs the Kokoro and verification extras, runs `readio selftest e2e`, and uploads the output directory even when the run fails. Normal pull-request CI does not download real models or run model inference.

## Interpretation

Default classification is PASS at WER <= 0.10 and CER <= 0.05; REVIEW at WER <= 0.20 and CER <= 0.10; worse scores, empty transcripts, invalid audio, or pipeline errors FAIL. Thresholds can be overridden by supported requests and the compatibility wrappers.

WER/CER measure recognized word content; they do not assess speaker identity, naturalness, prosody, timbre, loudness, clipping, or background noise. Cases remain fixed and plain-text; the benchmark does not semantically rewrite numbers, abbreviations, or symbols.

Fast helper tests need no Redux model download:

```bash
python -m pytest -q tests/test_redux_benchmark.py
```
