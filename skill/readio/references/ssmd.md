# SSMD and specialist ownership

Readio does not provide a general-purpose Markdown/SSMD reader, standalone SSMD preflight command, or Readio-owned project bundle. SSMDConvert converts supported source material into editable `.ssmd-bundle` directories. The bundle manifest records whether its content is a book or a document; UtterPlan, VoiceRender, AudioCompose, and AudioExport perform kind-sensitive stage dispatch.

VoiceRender owns RenderProfiles, logical role bindings, voice resolution, and synthesis. AudioCompose and AudioExport own their respective later stages. Readio exposes thin delegated engine, model, voice, and profile controls; it does not own catalogs or role-binding schemas.

The user-facing file-to-audio path is the unified Readio workflow. Temporary workspaces are the default; use `--bundle PATH` only when an editable bundle should persist:

```bash
readio render novel.epub --bundle novel.ssmd-bundle \
  --profile narrator.renderprofile.toml --language en-US --chapters 5-17
readio status novel.ssmd-bundle --profile narrator.renderprofile.toml --chapters 5-17
```

For direct specialist stage control, preserve UtterPlan's exact variant:

```bash
ssmdconvert bundle convert novel.epub -o novel.ssmd-bundle
utterplan bundle compile novel.ssmd-bundle
voicerender profile create --engine kokoro --target v1.0 --voice af_heart \
  --language en-US -o novel.ssmd-bundle/narrator.renderprofile.toml
utterplan bundle render novel.ssmd-bundle \
  --profile novel.ssmd-bundle/narrator.renderprofile.toml

VARIANT="<exact variant returned by utterplan bundle render>"
voicerender bundle render novel.ssmd-bundle --variant "$VARIANT"
audiocompose bundle compose novel.ssmd-bundle --variant "$VARIANT"
audioexport bundle export novel.ssmd-bundle --variant "$VARIANT" --output novel.m4b
```

See the [CLI reference](cli.md) and [Python API guide](../../../docs/api.md).
