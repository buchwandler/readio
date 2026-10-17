# Python API

The supported facade is `readio.api`. Readio functions orchestrate specialist public APIs and return their native objects; it does not mirror specialist schemas or translate specialist exceptions.

```python
from readio.api import (
    BookBuildRequest,
    DocumentRenderRequest,
    build_book,
    convert_book,
    render_document,
    speak,
)
```

## Engine, voice, and profile controls

`readio.api` exposes VoiceRender-delegating operations for engine and model discovery/management (`list_engines`, `inspect_engine`, `list_engine_options`, `list_engine_models`, `inspect_engine_model`, `install_engine_model`, `remove_engine_model`, `verify_engine_model`); voice discovery and reusable voice artifacts (`list_voices`, `inspect_voice`, `create_voice_source`, `show_voice_source`, `verify_voice_source`, `clone_voice`, `design_voice`); and RenderProfile operations (`create_profile`, `load_profile`, `validate_profile`, `refresh_profile`, `list_profile_roles`, `set_profile_role`, `remove_profile_role`, `set_default_profile_role`). These functions forward supported specialist arguments and preserve specialist validation and result objects.

`parse_voice_selector` delegates selector interpretation to VoiceRender. `speak(text, ...)` resolves and synthesizes literal text through VoiceRender, then plays it locally. It accepts direct VoiceRender selection or a saved `profile` and logical `role`, and supports optional WAV output and `no_play`; it does not use UtterPlan, AudioCompose, or AudioExport.

## Explicit settings

`CONFIG_TEMPLATE`, `init_settings(path, force=False)`, `load_settings(path)`, `validate_settings(mapping)`, and `merge_settings(settings, overrides)` expose Readio's small TOML settings bridge. The CLI reads settings only when a command explicitly selects `--config PATH`. Merge precedence is specialist defaults < selected settings < CLI flags. Settings never replace a saved RenderProfile's engine or voice identity.

## Single-document rendering

`DocumentRenderRequest` contains the source and RenderProfile paths, output and artifact controls, a stop stage, and native specialist option objects. Common specialist fields include `conversion_options` (`ssmdconvert.DocumentConversionOptions`), `planner_config` (`utterplan.PlannerConfig`), `compose_output_policy` (`audiocompose.OutputPolicy`), and AudioExport format/metadata/cover/FFmpeg options. `render_document(request, on_progress=None)` executes:

```text
SSMDConvert → UtterPlan compile → RenderPlan projection
            → VoiceRender preflight/synthesis
            → AudioCompose → AudioExport
```

`stop_after` accepts `convert`, `plan`, `render-plan`, `synth`, `compose`, or `export`. `DocumentRenderResult` returns artifact paths and the original conversion, compile, RenderPlan, preflight, VoiceRender, AudioCompose, and AudioExport results for stages that ran. `ProgressEvent` has `stage`, `kind`, optional progress/message fields, and the original specialist event as `native`. Specialist exceptions propagate unchanged.

The convenience CLI's temporary/retained artifact policy is described in the [CLI reference](cli.md). Readio does not define a second RenderPlan, preflight, audio-job, or export format.

## Audiobook operations

`convert_book(source, output=None, *, chapters="all", language=None, metadata=None, sequence_fallback_mode=None, speech_mode=None, spacy=None, on_progress=None)` constructs SSMDConvert's `BookConversionOptions`/`SpeechOptions`, writes the specialist-owned bundle, and returns its `BookWorkspace`.

`BookBuildRequest` holds the workspace and stage selection, plus specialist-native `utterplan.BookCompileRequest` and `utterplan.BookRenderRequest` values when callers need non-default planning options. It forwards VoiceRender selectors/roots/cache and stale-output controls, AudioCompose `BookComposeOptions`/`OutputPolicy` and roots, and AudioExport profile, metadata, cover, bitrate, sample rate, channels, and FFmpeg paths. No specialist option schema is copied into Readio.

```python
workspace = convert_book("novel.epub", "novel.ssmdbook", chapters="5-17")
result = build_book(
    BookBuildRequest(
        workspace=workspace.path,
        profile="narration.renderprofile.toml",
        chapters="5-17",
        output="novel.m4b",
        export_metadata={"title": "Novel"},
    )
)
print(result.variant)  # exact specialist variant
```

`build_book` returns a `BookBuildResult` containing the exact variant and original results for stages that ran. `BookProgressEvent` adapts stage and specialist progress while retaining the native event. Variants and specialist errors are not rewritten.

## Stable exports

The complete public surface is listed by `readio.api.__all__`. The top-level `readio` package intentionally remains small; use `readio.api` for orchestration and management operations.
