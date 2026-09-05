# Readio standalone LLM guides

These are standalone authoring guides for generic LLMs. They create SSMD source files; they do not require Readio, Python, the Readio Agent Skill, SSMD tooling, or a local TTS engine on the authoring system.

## Authoring versus rendering

These guides are not Readio runtime templates:

- `readio template` manages ready-to-edit `.ssmd` runtime templates from `readio/resources/templates/`.
- `llm-guides/ssmd/` contains Markdown instructions that teach an arbitrary LLM how to author portable `.ssmd` source.
- The guide authoring step creates source only; it does not create audio.
- Validation, voice binding, and rendering happen later on a system where Readio is installed.

The intended lifecycle is:

```text
source or task
    ↓
generic web LLM + one standalone guide
    ↓
downloadable .ssmd file
    ↓
move the file to a system with Readio
    ↓
validate / bind voices / render
```

## How to use in a web harness

Choose exactly one guide and attach it together with the task and any source material.

Example request:

> Follow the attached Readio standalone SSMD guide. Turn the supplied report into a 7-minute German interview podcast. Create a downloadable `.ssmd` file. Do not create audio.

The guides are self-contained. The LLM must not need another guide, a shared prompt fragment, local configuration, model discovery, or runtime documentation.

## Artifact mode

When the web harness can create files or artifacts, ask the LLM to:

1. Create exactly one UTF-8 `.ssmd` file.
2. Use a short descriptive kebab-case filename.
3. Put only SSMD source in the file.
4. Expose the file for download.
5. Create no helper files and no audio.

The generated file must not contain Markdown fences, surrounding explanation, shell commands, or unexpanded placeholders.

## Chat fallback mode

When file creation is unavailable, the guide instructs the LLM to return the complete raw SSMD source directly in the response, without Markdown fences or surrounding explanation. Save that response as a `.ssmd` file before moving it to the rendering system.

## Compatibility target

The guides target the conservative authoring subset used by:

- Readio 0.2.x
- SSMD >=0.8.6,<0.9
- PyKokoro 0.9.x

This is a compatibility target, not a claim that the authoring environment ran validation. A generated file should be checked on the destination system before rendering.

## Voice portability

A portable standalone-generated SSMD file:

1. does not require the authoring environment to know local Readio configuration;
2. does not invent model-specific voice IDs;
3. uses no unnecessary model/provider-specific metadata;
4. keeps speaker roles symbolic only when speaker distinction is necessary;
5. can be moved to another machine for Readio preflight and rendering; and
6. may still require valid role binding at render time for multi-speaker content.

Portable does not mean guaranteed to render on every model with no later configuration. Concrete `voice_bindings` belong in the generated document only when the caller supplies provider/model-valid IDs; otherwise role resolution is deferred to the rendering environment.

## Guide catalog

- [`ssmd/general-narration.md`](ssmd/general-narration.md) — general narration
- [`ssmd/podcast-solo.md`](ssmd/podcast-solo.md) — one-speaker podcast or commentary
- [`ssmd/podcast-interview.md`](ssmd/podcast-interview.md) — host and guest interview
- [`ssmd/podcast-roundtable.md`](ssmd/podcast-roundtable.md) — moderated three-role discussion
- [`ssmd/news-briefing.md`](ssmd/news-briefing.md) — source-grounded news briefing
- [`ssmd/educational-explainer.md`](ssmd/educational-explainer.md) — tutorial or teaching audio
- [`ssmd/document-summary.md`](ssmd/document-summary.md) — faithful source summary
- [`ssmd/funny-story.md`](ssmd/funny-story.md) — comedy or funny story
- [`ssmd/dramatic-story.md`](ssmd/dramatic-story.md) — dramatic narrative or audiobook scene
- [`ssmd/kids-story.md`](ssmd/kids-story.md) — kids or bedtime story
- [`ssmd/audio-drama.md`](ssmd/audio-drama.md) — dialogue-forward audio drama
- [`ssmd/guided-meditation.md`](ssmd/guided-meditation.md) — meditation, grounding, or sleep narration
- [`ssmd/language-learning.md`](ssmd/language-learning.md) — listen/repeat or bilingual lesson
- [`ssmd/debate-pro-con.md`](ssmd/debate-pro-con.md) — balanced debate or tradeoff discussion
- [`ssmd/quiz-trivia.md`](ssmd/quiz-trivia.md) — quiz or trivia with thinking pauses

Each guide is intentionally complete and repeats the technical rules needed for its own generation task. Do not split a guide into required shared includes: one downloaded guide plus a task and optional source material must be enough.

## Later validation and rendering

These are optional destination-system steps, after the `.ssmd` file has been created and moved to a machine with Readio installed:

```bash
readio ssmd check output.ssmd
readio render --file output.ssmd -o output.mp3
```

Run these only where the commands and their runtime dependencies are actually available. Do not claim that they ran during web-only authoring.
