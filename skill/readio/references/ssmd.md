# SSMD and voice resolution

Use SSMD for multiple speakers, dialogue, logical roles, explicit voice/rate/volume/pitch, breaks, or chapter markers.

```bash
readio ssmd check episode.ssmd --json
readio voices list --json
readio render --file episode.ssmd --voice-bind moderator=af_sarah
```

Document-local bindings are authoritative. Readio supplies only missing configured defaults. Persist reusable mappings with `readio voices bind ROLE VOICE_ID`; inspect them with `readio voices roles`.

When a check reports unresolved roles, fix the source/configuration or provide repeatable `--voice-bind ROLE=VOICE_ID` values. `--resolve-voices` is a human-only interactive TTY convenience and must not be used by agents, scripts, JSON commands, or non-TTY processes.

For marker-derived Spotify chapters, use at least two named markers with the first at offset zero and strictly increasing integer offsets. Readio validates this before publishing.

Templates can be inspected with `readio template list`, `readio template show NAME`, and `readio template validate --all --json`.
