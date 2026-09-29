# Standalone guide evaluation corpus

This small corpus supports manual release review and optional model-to-model regression comparisons. It contains one representative task for each of the 15 standalone guides, plus a second source- or constraint-based task for the six higher-risk formats: funny story, dramatic story, kids story, audio drama, podcast interview, and podcast roundtable.

## Use

1. Select exactly one guide from `../ssmd/` and its matching prompt from `prompts/`.
2. Give both to the model under review, along with any source text included in the prompt.
3. Save the generated `.ssmd.md` artifact for structural checks, then render it on a suitable destination system if listening review is available.
4. Score the output with [`rubric.md`](rubric.md). Record the model, guide revision, prompt, and any destination voice-binding context separately from the artifact.

Do not use a different guide as a hidden shared include. Do not treat one model's output as an expected-answer template. The task prompts deliberately vary topics and structures so reviewers can notice whether a guide example is being cloned.

The corpus is evaluation material, not a CI benchmark. External-model generation, synthesis, listening, and scoring are optional manual activities. Normal CI must not require a model, provider account, audio engine, or rendered audio.

## Prompt coverage

Each guide has a plain generative prompt:

- `audio-drama.md`
- `debate-pro-con.md`
- `document-summary.md`
- `dramatic-story.md`
- `educational-explainer.md`
- `funny-story.md`
- `general-narration.md`
- `guided-meditation.md`
- `kids-story.md`
- `language-learning.md`
- `news-briefing.md`
- `podcast-interview.md`
- `podcast-roundtable.md`
- `podcast-solo.md`
- `quiz-trivia.md`

The higher-risk formats also have a source- or constraint-based prompt:

- `audio-drama-source.md`
- `dramatic-story-constraints.md`
- `funny-story-source.md`
- `kids-story-constraints.md`
- `podcast-interview-source.md`
- `podcast-roundtable-source.md`
