# CLI reference: project pipeline

## Project lifecycle

```text
readio project init SOURCE -o PROJECT
readio plan PROJECT
readio synth PROJECT [--engine ENGINE] [--voice VOICE] [--select SELECTOR]
readio preview PROJECT --select SELECTOR [--voice VOICE] [-o PREVIEW.wav]
readio compose PROJECT [--target-lufs FLOAT]
readio export PROJECT --format {wav,mp3,m4a,ogg}
readio status PROJECT [--json]
readio render PROJECT --format FORMAT
```

`plan` is semantic and engine-free for a project or when its output ends in
`.utterplan.json`. Ordinary text `plan` and `render --dry-run` retain the
existing resolved execution-plan inspection behavior. Non-project `speak` and
`render` remain supported.
