# SSMD and specialist ownership

Readio does not provide a general-purpose Markdown/SSMD reader, standalone SSMD preflight command, or Readio-owned project bundle. SSMDConvert converts supported source material into native `.ssmdoc` and `.ssmdbook` workspaces; UtterPlan compiles semantic content and projects profile-specific RenderPlans.

VoiceRender owns RenderProfiles, logical role bindings, voice resolution, and synthesis. AudioCompose and AudioExport own their respective later stages. Readio exposes thin delegated engine, model, voice, and profile controls; it does not own catalogs or role-binding schemas.

```bash
readio init chapter.docx -o chapter.ssmdoc
readio plan chapter.ssmdoc --language en-US
readio render-plan chapter.ssmdoc --profile narrator.renderprofile.toml
readio run chapter.ssmdoc --profile narrator.renderprofile.toml --language en-US
```

For direct audiobook stage control, preserve UtterPlan's exact variant:

```bash
ssmdconvert book convert novel.epub -o novel.ssmdbook
utterplan compile-book novel.ssmdbook
voicerender profile create --engine kokoro --target v1.0 --voice af_heart \
  --language en-US -o novel.ssmdbook/narrator.renderprofile.toml
utterplan render-book novel.ssmdbook \
  --profile novel.ssmdbook/narrator.renderprofile.toml

VARIANT="<exact variant returned by utterplan render-book>"
voicerender render-book novel.ssmdbook --variant "$VARIANT"
audiocompose compose-book novel.ssmdbook --variant "$VARIANT"
audioexport export-book novel.ssmdbook --variant "$VARIANT" --output novel.m4b
```

See the [CLI reference](cli.md) and [Python API guide](../../../docs/api.md). Existing Readio project formats are unsupported and have no migration path.
