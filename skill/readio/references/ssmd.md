# SSMD and voice resolution

Use SSMD for multiple speakers, dialogue, logical roles, explicit voice/rate/volume/pitch, breaks, or chapter markers.

```bash
readio ssmd check episode.ssmd --json
readio voices list --json
readio render --file episode.ssmd --voice-bind moderator=af_sarah
```

Document front-matter bindings are portable and authoritative. User-global defaults use `readio roles bind ROLE VOICE_ID`; project-local acoustic choices use `readio plan bind ROLE VOICE` and are inspected with `readio plan roles`. A project binding never rewrites SSMD or changes Utterplan identity. Invocation-only overrides use repeatable `--voice-bind ROLE=VOICE_ID`. Stable selectors are lookup aliases; project, config, and document settings persist canonical concrete voice IDs.

For the effective cast of a one-shot render, inspect `readio render --dry-run`. The plan is resolved against the active model roster and its `decisions` report the origin of each mapping:

```bash
readio render --file episode.ssmd --dry-run --json
```

Project-role inspection does not require a generated plan index:

```bash
readio plan roles
```

The one-shot plan's `decisions` include `ssmd.bindings.<ref>` entries; `readio ssmd check` and rendering use the same central voice resolver.

When a check reports unresolved roles, fix the source/configuration or provide repeatable `--voice-bind ROLE=VOICE_ID` values. `--resolve-voices` is a human-only interactive TTY convenience and must not be used by agents, scripts, JSON commands, or non-TTY processes.

For marker-derived Spotify chapters, use at least two named markers with the first at offset zero and strictly increasing integer offsets. Readio validates this before publishing.

Templates can be inspected with `readio template list`, `readio template show NAME`, and `readio template validate --all --json`.
