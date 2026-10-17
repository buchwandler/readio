# SSMD and specialist ownership

Readio does not provide the former general-purpose Readio Markdown/SSMD pipeline or a standalone SSMD preflight command. `readio render SOURCE --profile PROFILE` is a convenience orchestration path for a supported source document; SSMDConvert owns source support and conversion, and UtterPlan owns planning.

For audiobook workflows, SSMDConvert owns `.ssmdbook` workspace creation and conversion. UtterPlan compiles semantic content and projects the RenderPlan. VoiceRender owns RenderProfiles, logical role bindings, voice resolution, and synthesis. AudioCompose and AudioExport own their respective later stages. Readio exposes thin delegated `voices` and `profile role` commands; it does not own voice catalogs or role-binding schemas.

```bash
readio render chapter.docx --profile narration.renderprofile.toml --stop-after render-plan

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

Use `readio audiobook build` for convenience orchestration, or consult each specialist CLI for complete options. See the [CLI reference](cli.md) and [Python API guide](../../../docs/api.md).
