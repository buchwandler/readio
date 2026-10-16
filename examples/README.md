# Readio examples

These examples use Readio's v0.5.0 public surface and the specialist packages that own audiobook stages.

## Specialist audiobook workflow

Use the package CLIs directly when you need detailed control. Create the workspace with SSMDConvert, compile and project with UtterPlan, create a VoiceRender profile, then pass the exact returned variant through synthesis, composition, and export:

```bash
ssmdconvert book convert novel.epub -o novel.ssmdbook
utterplan compile-book novel.ssmdbook
voicerender profile create --engine kokoro --target v1.0 --voice af_heart \
  --language en-US -o narration.renderprofile.toml
utterplan render-book novel.ssmdbook --profile narration.renderprofile.toml

VARIANT="<exact variant returned by utterplan render-book>"
voicerender render-book novel.ssmdbook --variant "$VARIANT" --emphasis-policy defer-to-compose
audiocompose compose-book novel.ssmdbook --variant "$VARIANT"
audioexport export-book novel.ssmdbook --variant "$VARIANT" -o novel.m4b
```

## Readio shorthand

```bash
readio audiobook convert novel.epub --output novel.ssmdbook
readio audiobook build novel.ssmdbook --profile narration.renderprofile.toml --output novel.m4b
readio speak --voice kokoro:v1.0/af_heart --lang en-US "Hello from Readio."
```

## Python API

[`python_api.py`](python_api.py) demonstrates direct literal-text speech through `readio.api.speak`. Install a VoiceRender backend extra and connect an audio device before running it:

```bash
python -m pip install readio "voicerender[kokoro]"
python examples/python_api.py
```

Readio does not migrate its former generic projects. Start from an EPUB or other source accepted by SSMDConvert and use a specialist-owned `.ssmdbook` workspace.
