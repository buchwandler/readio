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
