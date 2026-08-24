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
