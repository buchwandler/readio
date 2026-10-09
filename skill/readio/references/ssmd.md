# SSMD and voice resolution

Use SSMD for multiple speakers, dialogue, logical roles, explicit voice/rate/volume/pitch, breaks, or chapter markers.

```bash
readio ssmd check episode.ssmd --json
readio voices list --json
readio render --file episode.ssmd --voice-bind moderator=kokoro:v1.0/af_sarah
```

Document front-matter bindings are portable and authoritative. User-global defaults use `readio roles bind ROLE REF`; project-local acoustic choices use `readio plan bind ROLE REF` and are inspected with `readio plan roles`. Voice references use `SYSTEM:TARGET[/VOICE]`, for example `kokoro:v1.0/af_heart`, `piper:en_US-amy-medium`, or `pocket:english_2026-04/alba`. A project binding never rewrites SSMD or changes Utterplan identity. Invocation-only overrides use repeatable `--voice-bind ROLE=REF`. Native voice IDs are accepted when discovery context resolves them uniquely. Numbered voice selectors are not supported.

For the effective cast of a one-shot render, inspect `readio render --dry-run`. The plan is resolved against the active model roster and its `decisions` report the origin of each mapping:

```bash
readio render --file episode.ssmd --dry-run --json
```

Project-role inspection does not require a generated plan index:

```bash
readio plan roles
```

The one-shot plan's `decisions` include `ssmd.bindings.<ref>` entries; `readio ssmd check` and rendering use the same central voice resolver.

When a check reports unresolved roles, fix the source/configuration or provide repeatable `--voice-bind ROLE=REF` values. `--resolve-voices` is a human-only interactive TTY convenience and must not be used by agents, scripts, JSON commands, or non-TTY processes.

For marker-derived Spotify chapters, use at least two named markers with the first at offset zero and strictly increasing integer offsets. Readio validates this before publishing.

## SSMD authoring handoff

Readio consumes SSMD; SSMDStudio 0.1.1 owns draft/template authoring, portable voice-binding materialization, and structural or roundtrip lint. Install separately with `python -m pip install "ssmdstudio[authoring]==0.1.1"`. Use `ssmdstudio template use podcast --output episode.ssmd.md` or `ssmdstudio draft new --output episode.ssmd.md --template podcast`; if bundled starters are absent, restore them intentionally with `ssmdstudio template reset --all`. Then run `ssmdstudio ssmd lint episode.ssmd.md --roundtrip --json`. Readio remains the consumer preflight and renderer: `readio ssmd check episode.ssmd.md --json`.
