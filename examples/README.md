# Readio examples

This directory contains complete documents that demonstrate Readio workflows.

## Podcast example

[`readio-podcast.ssmd`](readio-podcast.ssmd) is a multi speaker podcast script. It demonstrates:

- Host and guest voice roles
- SSMD speaker blocks
- A complete workflow from validation to rendering

Check the document before rendering:

```bash
readio ssmd check examples/readio-podcast.ssmd
```

Render it to a WAV file:

```bash
readio render --file examples/readio-podcast.ssmd -o readio-podcast.wav
```

The configured SSMD role bindings determine which provider voices are used. Run `readio config show` to inspect the current configuration.

## Prosody example

[`readio-prosody.ssmd`](readio-prosody.ssmd) focuses on audible volume, rate, and pitch controls. It demonstrates named and numeric levels, relative values, combined controls, block-level prosody, and inline overrides. It intentionally uses only prosody syntax implemented by the current SSMD consumer; `vrp` and symbolic shorthand are not included.

Check it before rendering:

```bash
readio ssmd check examples/readio-prosody.ssmd
```

Render it to a WAV file:

```bash
readio render --file examples/readio-prosody.ssmd -o readio-prosody.wav
```

## Python API planning example

[`python_api.py`](python_api.py) creates a typed literal-text request and calls `Readio.speech.plan()`. Run it from the repository root with `python examples/python_api.py`. The example prints the JSON-safe plan and does not load a TTS model or create the requested audio file. Registry discovery follows the configured online/offline policy.
Persistent generic project examples use canonical `.utterplan.toml` artifacts from the current UtterPlan package API. Native audiobook examples use UtterPlan's `compile-book` and `render-book` commands and leave their artifacts in the `.ssmdbook` workspace.

## PocketSynth CLI example

Pocket synthesis requests select a registered bundle and either a predefined voice or a reference WAV. The source adapter accepts `--precision`, `--temperature`, `--lsd-steps`, `--max-frames`, and `--frames-after-eos`:

```bash
readio voices list --engine pocket --lang en-us
readio render --engine pocket --model BUNDLE_ID --voice VOICE "Hello from a PocketSynth bundle."
readio render --engine pocket --model BUNDLE_ID --voice-file reference.wav --precision fp32 "Hello from a reference voice."
```

The `readio[pocket]` extra installs the supported PocketSynth >=0.2.0,<0.3 runtime. The CLI examples use the strict request API; target catalogs are metadata-only, while synthesis may download the selected bundle when it is not already cached.

## Native `.ssmdbook` audiobook workflow

Alternatively, run the same stages entirely through specialist CLIs, or switch between Readio and specialist tools at any stage. Use the exact profile-fingerprinted variant returned by `render-plan`/`build` when continuing:

```bash
readio audiobook convert novel.epub --chapters 5-17 -o novel.ssmdbook
readio audiobook profile create narration.renderprofile.toml \
  --engine piper --target-id en_US-amy-medium --language en-US
readio audiobook build novel.ssmdbook --profile narration.renderprofile.toml \
  --chapters 5-17 --stop-after render-plan --json

# The matching specialist CLI stages share the same workspace artifacts.
ssmdconvert book convert novel.epub -o novel.ssmdbook --chapters 5-17
utterplan compile-book novel.ssmdbook --chapters 5-17
utterplan render-book novel.ssmdbook --profile narration.renderprofile.toml --chapters 5-17
# Set VARIANT to the exact returned RenderPlan directory name.
voicerender render-book novel.ssmdbook --variant "$VARIANT" --chapters 5-17
audiocompose compose-book novel.ssmdbook --variant "$VARIANT" --chapters 5-17
audioexport export-book novel.ssmdbook --variant "$VARIANT" --chapters 5-17 -o novel.m4b
```

`readio audiobook status novel.ssmdbook --json` reports package-owned artifacts. Dirty chapter files are surfaced without automatic refresh; run `ssmdconvert book refresh novel.ssmdbook` explicitly when updating the canonical manifest. Legacy `.readio/` data is warned about, ignored, and left untouched. See the [CLI reference](../docs/cli.md#native-ssmdbook-audiobook-workflows) and [Python API](../docs/api.md#projects-and-audiobooks).
