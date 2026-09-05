# SSMD and voice resolution

Use SSMD for multiple speakers, dialogue, logical roles, explicit voice/rate/volume/pitch, breaks, or chapter markers.

```bash
readio ssmd check episode.ssmd --json
readio voices list --json
readio render --file episode.ssmd --voice-bind moderator=af_sarah
```

Document-local bindings are authoritative. Readio supplies only missing configured defaults. Persist reusable mappings with `readio voices bind ROLE VOICE_ID`; inspect them with `readio voices roles`.

For the exact effective cast a render will use — including document, invocation, configured-role, and direct bindings resolved against the active model roster, each with its origin — inspect the plan JSON:

```bash
readio plan --file episode.ssmd --json
```

`ssmd.bindings` lists every executable `reference -> voice` mapping with its `origin`, `ssmd.unresolved` lists references that would block rendering, and the same mappings appear as `ssmd.bindings.<ref>` entries in `decisions`. Preflight (`readio ssmd check`) and rendering derive their bindings from the same resolution, so plan, check, and render always agree.

When a check reports unresolved roles, fix the source/configuration or provide repeatable `--voice-bind ROLE=VOICE_ID` values. `--resolve-voices` is a human-only interactive TTY convenience and must not be used by agents, scripts, JSON commands, or non-TTY processes.

For marker-derived Spotify chapters, use at least two named markers with the first at offset zero and strictly increasing integer offsets. Readio validates this before publishing.

Templates can be inspected with `readio template list`, `readio template show NAME`, and `readio template validate --all --json`.
