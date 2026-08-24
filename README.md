# readio

`readio` is a small terminal tool that reads text aloud. The initial TTS backend uses the current PyKokoro pipeline API, while the CLI name is intentionally engine-neutral so another TTS engine can be added later. It keeps model/pipeline state resident during a command, sends generated audio through one bounded `sounddevice` output stream, supports persistent TOML config, and includes an Agent Skills-compatible `SKILL.md`.

## Install

For CPU ONNX Runtime:

```bash
python -m pip install -e ".[cpu]"
```

For NVIDIA GPU ONNX Runtime:

```bash
python -m pip install -e ".[gpu]"
```

PyKokoro may download model/voice assets on first use unless you already have them cached/configured.

## Basic usage

```bash
readio speak "Hello from the terminal."
printf '%s\n' "Read this from stdin." | readio speak
readio speak --file notes.md
```

By default, non-live input is globally prepared by PyKokoro and rendered in sentence units for lower startup latency. Audio generation can continue while the bounded player queue consumes already generated audio.

### Select a paragraph

```bash
readio speak --file notes.md --select last-paragraph
readio speak --file notes.md --select paragraph:3
```

Selectors use PyKokoro's own prepared paragraph descriptors rather than a separate regex, so the selected unit matches the TTS pipeline's document segmentation.

### Live stdin

```bash
some-streaming-producer | readio speak --live
```

Live mode cannot globally prepare text that has not arrived yet. It therefore frames input at blank lines, prepares each completed paragraph independently, and keeps one output audio stream open for the command. Within each paragraph, `unit = "sentence"` is still the low-latency default.

## Configuration

```bash
readio config init
readio config show
readio config set voice af_sarah
readio config set lang en-us
readio config set speed 1.1
readio config set unit sentence
readio config set queue_size 2
```

The default config is:

```toml
[reader]
voice = "af_sarah"
lang = "en-us"
speed = 1.0
pause_mode = "tts"
unit = "sentence"
queue_size = 2
```

Set `READIO_CONFIG=/path/to/config.toml` to override the config location.

## Agent Skill

The portable skill is in `skill/readio/SKILL.md`. Copy the `readio` skill directory into the skill directory used by your agent client. For Codex-style project skills, for example:

```bash
mkdir -p .agents/skills
cp -R skill/readio .agents/skills/readio
```

The key design rule is that the skill extracts conversation text and passes it to the CLI. The CLI itself does not inspect or store your LLM transcript.

## Useful commands

```bash
readio doctor
readio config path
readio --version
```

## Versioning

`readio` uses `setuptools-scm`: the package version is derived from Git tags at build/install time rather than being hard-coded in source. Tag releases as `v0.1.0`, `v0.2.0`, and so on. Development commits after a tag receive a PEP 440 development version automatically. An unpacked tree without Git metadata falls back to `0+unknown`.

The import package lives directly at `readio/`; there is intentionally no `src/` directory.
