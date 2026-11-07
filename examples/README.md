# Readio examples

These examples use Readio's unified workflow API and specialist package APIs. File renders use temporary intermediate storage unless a persistent bundle is explicitly requested.

## Readio workflow

Render a source directly, or explicitly retain the editable bundle. A persistent book bundle contains the complete source inventory on first creation; chapter selection limits downstream work.

```bash
readio profile create --engine kokoro --target v1.0 --voice af_heart \
  --language en-US -o narrator.renderprofile.toml
readio render novel.epub --bundle novel.ssmd-bundle \
  --profile narrator.renderprofile.toml --language en-US --chapters 5-17
readio status novel.ssmd-bundle --profile narrator.renderprofile.toml --chapters 5-17
```

AudioExport is included in the core installation; FFmpeg and FFprobe must be available for export.

## Specialist workflow

For detailed stage controls, use the specialist CLIs directly and preserve the exact variant returned by UtterPlan:

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
audioexport bundle export novel.ssmd-bundle --variant "$VARIANT" -o novel.m4b
```

## Python API

[`python_api.py`](python_api.py) demonstrates read-only inspection of a complete `RenderRequest` through `Readio.inspect()`. It prints the workflow mode and planned steps without synthesis.

```bash
python examples/python_api.py SOURCE --profile narrator.renderprofile.toml --language en-US
```
