# Readio examples

These examples use Readio's `.ssmd-bundle` lifecycle and specialist package APIs.

## Readio pipeline

Convert and run a source through Readio's bundle group:

```bash
readio bundle convert novel.epub -o novel.ssmd-bundle
readio profile create --engine kokoro --target v1.0 --voice af_heart \
  --language en-US -o novel.ssmd-bundle/narrator.renderprofile.toml
readio bundle run novel.ssmd-bundle \
  --profile novel.ssmd-bundle/narrator.renderprofile.toml \
  --language en-US --export --output novel.m4b
```

AudioExport is optional and requires `python -m pip install "readio[audioexport]"`.

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

[`python_api.py`](python_api.py) demonstrates source conversion, UtterPlan compilation, and profile-specific RenderPlan projection through `readio.api`. It prints the resulting bundle kind and exact variant; it does not require a speech engine to run these planning stages.

```bash
python examples/python_api.py SOURCE --profile narrator.renderprofile.toml --language en-US
```
