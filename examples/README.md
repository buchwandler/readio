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

## PocketSynth CLI example

Pocket synthesis requests select a registered bundle and either a predefined voice or a reference WAV. The source adapter accepts `--precision`, `--temperature`, `--lsd-steps`, `--max-frames`, and `--frames-after-eos`:

```bash
readio voices list --engine pocket --lang en-us
readio render --engine pocket --model BUNDLE_ID --voice VOICE "Hello from a PocketSynth bundle."
readio render --engine pocket --model BUNDLE_ID --voice-file reference.wav --precision fp32 "Hello from a reference voice."
```

The `readio[pocket]` extra installs the supported PocketSynth >=0.2.0,<0.3 runtime. The CLI examples use the strict request API; target catalogs are metadata-only, while synthesis may download the selected bundle when it is not already cached.
