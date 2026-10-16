# SSMD and specialist ownership

Readio 0.5 does not provide a general SSMD reader, SSMD preflight command, voice catalog, or role-binding system. Do not use the removed `readio ssmd`, `readio voices`, `readio roles`, or `readio render` commands.

For audiobook workflows, SSMDConvert owns `.ssmdbook` workspace creation and conversion. UtterPlan compiles semantic content and projects the RenderPlan. VoiceRender resolves voices and synthesizes audio from its RenderProfile. AudioCompose and AudioExport own their respective later stages.

```bash
ssmdconvert book convert novel.epub -o novel.ssmdbook
utterplan compile-book novel.ssmdbook
voicerender profile create --engine kokoro --target v1.0 --voice af_heart \
  --language en-US -o narration.renderprofile.toml
utterplan render-book novel.ssmdbook --profile narration.renderprofile.toml
```

Keep the exact variant returned by `utterplan render-book` unchanged when continuing with specialist tools:

```bash
VARIANT="<exact variant returned by utterplan render-book>"
voicerender render-book novel.ssmdbook --variant "$VARIANT"
audiocompose compose-book novel.ssmdbook --variant "$VARIANT"
audioexport export-book novel.ssmdbook --variant "$VARIANT" --output novel.m4b
```

Use `readio audiobook build` for convenience orchestration, or consult each specialist CLI for its complete options. See [the Python API guide](../../../docs/api.md) for the supported facade.
