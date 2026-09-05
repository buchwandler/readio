# Readio Standalone SSMD Guide: Kids or Bedtime Story SSMD

## Mission

Create a warm, age-appropriate spoken story with simple structure, gentle repetition, clear emotional stakes, and a reassuring ending.

This file is a complete instruction set. Do not assume access to any other SSMD, Readio, prompt, template, or documentation file.

## Output contract

Your job is to create one SSMD source document.

### Preferred artifact mode

If this environment can create downloadable files or artifacts:

1. Create exactly one UTF-8 file with a descriptive kebab-case `.ssmd` filename.
2. Put only SSMD source in that file.
3. Do not create helper files.
4. Do not wrap the file content in Markdown fences.
5. Expose or return the `.ssmd` file for download.

Do not force a fixed filename; choose a short name that describes the generated topic or use case.

### Fallback chat mode

If downloadable file or artifact creation is unavailable:

- Return the complete raw SSMD source directly in the response.
- Do not use Markdown code fences.
- Do not add an explanation before or after it.

### In either mode

- Do not put explanatory prose, shell commands, or Markdown fences inside the generated SSMD file.
- Use the requested language for spoken content. If no language is specified, infer it from the request and source material and stay consistent.
- Make the text sound natural when spoken aloud; do not write for silent reading.
- Do not claim that Readio/SSMD validation or audio rendering was executed unless this environment actually provides and runs that tooling.

## Target runtime

Generate conservative SSMD for this compatibility target:

- Readio 0.2.x
- SSMD >=0.8.6,<0.9
- PyKokoro 0.9.x

These are authoring instructions, not a requirement to install or execute the runtime. They must work without Python, a Readio installation, the Readio Agent Skill, local SSMD tooling, or local model discovery. The generated file can be checked and rendered later on a Readio-capable system.

### Safe document header

Keep YAML front matter small and limited to portable metadata and defaults. Normally use only:

```yaml
---
title: Example title
pause_defaults:
  enabled: true
  sentence: 220ms
  paragraph: 600ms
  voice_change: 250ms
---
```

`title` is metadata and is not spoken. Add other fields only when the user's task requires them. Do not emit model IDs, model sources, quality settings, lexicon choices, Readio filesystem paths, local configuration values, or bindings inferred from examples.

### Voice policy

Do not invent concrete model or voice IDs. Voice inventories are model-specific and are normally resolved later on the rendering system.

For a single-speaker document, prefer the renderer's default voice and omit explicit `voice` references unless the task requires a named role or distinct voice.

For genuinely multi-speaker documents, use only the minimum conventional symbolic roles needed by the use case: `narrator`, `host`, `guest`, or `analyst`. These symbolic roles may require later binding on the Readio system. Do not invent extra roles such as character, teacher, expert, moderator, villain, or child unless the caller supplies an explicit binding plan.

Emit document-local `voice_bindings` only when the caller explicitly supplies concrete provider/model-valid voice IDs. Copy supplied IDs exactly; otherwise omit `voice_bindings`. Never leave explanatory metavariables or placeholders in generated SSMD.

Use block directives for distinct turns when roles are necessary:

```ssmd
<div voice="host">
Welcome to the show.
</div>

<div voice="guest">
Thanks for having me.
</div>
```

These are voice references, not visible speaker labels. Do not write `HOST:` or `GUEST:` unless the label itself should be spoken.

### Prosody

Use explicit, readable prosody. Prefer long attribute names:

```ssmd
[very important]{volume="loud"}
[slowly now]{rate="slow"}
[with lift]{pitch="high"}
[excited]{volume="loud" rate="fast" pitch="high"}
```

Block-level prosody is valid:

```ssmd
<div voice="narrator" rate="slow" pitch="low">
The room went silent.
</div>
```

Named values:

- volume: `silent`, `x-soft`, `soft`, `medium`, `loud`, `x-loud`
- rate: `x-slow`, `slow`, `medium`, `fast`, `x-fast`
- pitch: `x-low`, `low`, `medium`, `high`, `x-high`

Relative values such as `rate="+10%"`, `pitch="-5%"`, or `volume="+3dB"` are possible, but prefer named values unless fine control is important.

Do not use compact `vrp="..."` notation or symbolic prosody shorthand such as `++text++`, `>>text>>`, or `^^text^^`.

### Pauses

Use explicit pauses where they materially improve delivery:

```ssmd
This matters. ...500ms
Now listen carefully.
```

Supported forms include:

- `...100ms`, `...500ms`, `...1s`, `...2s`
- `...w` weak
- `...c` medium/comma-like
- `...s` strong/sentence-like
- `...p` extra-strong/paragraph-like

A bare `...` is a literal ellipsis, not a pause marker. Prefer `pause_defaults` for ordinary rhythm and explicit timed breaks only for intentional dramatic, comedic, or teaching moments.

### Emphasis

```ssmd
*moderate emphasis*
**strong emphasis**
~~reduced emphasis~~
```

Do not overuse emphasis. If every sentence is emphasized, none of it feels emphasized.

### Language changes

Use `lang` annotations only for genuine language changes. For a short phrase:

```ssmd
[Bonjour tout le monde]{lang="fr"}
```

For a longer passage:

```ssmd
<div lang="de">
Guten Morgen.
Heute sprechen wir über künstliche Intelligenz.
</div>
```

### Pronunciation/substitution

When necessary and when pronunciation information is supplied or confidently known:

```ssmd
[AWS]{sub="Amazon Web Services"}
[tomato]{ph="təˈmeɪtoʊ"}
```

Prefer rewriting awkward abbreviations into naturally spoken words instead of adding advanced markup unnecessarily.

### Marks for chapters/events

Marks do not speak. They can be useful for chapter/timeline workflows:

```ssmd
@intro
@topic_one
@conclusion
```

Use short, unique, snake_case names. Add marks only when the user requests chapters or markers or when this guide explicitly recommends them.

### Formatting rules

- Put each sentence on its own line whenever practical.
- Separate paragraphs with a blank line.
- Put `<div ...>` and `</div>` on their own lines for multi-line blocks.
- Keep speaker turns as separate voice blocks.
- Avoid deeply nested annotations.
- Do not use Markdown headings merely for visual organization: SSMD headings are spoken.
- Do not insert URLs, citation syntax, bullet markers, code fences, tables, or raw Markdown structure unless it is intentionally meant to be spoken.
- Rewrite lists into spoken transitions such as “First… Second… Finally…”.
- Rewrite symbols, equations, dates, abbreviations, and punctuation into forms that sound natural in TTS when needed.

## Content integrity

When source material is supplied:

- Preserve its important claims, names, numbers, dates, caveats, and uncertainty.
- Do not fabricate facts, quotes, statistics, dialogue, or attributions.
- Distinguish source facts from interpretation.
- If the source does not support a claim, omit it or state the uncertainty naturally.
- Do not read citations, URLs, footnote markers, or Markdown syntax aloud unless explicitly requested.
- Do not turn missing information into invented detail merely to make the script flow.

## Final self-check

Before answering, verify silently that:

1. The requested output mode is satisfied: one downloadable `.ssmd` artifact when file creation is available, otherwise complete raw SSMD in chat.
2. The generated SSMD itself contains no Markdown fences, helper-file content, shell commands, or explanatory prose.
3. YAML front matter is valid and closed with `---`.
4. Every opened `<div>` has a matching `</div>`.
5. Single-speaker content omits unnecessary explicit voice references.
6. Voice references are limited to necessary symbolic roles unless valid caller-supplied bindings were provided.
7. No invented concrete voice IDs, `<...>` metavariables, or other unexpanded placeholders appear.
8. No `vrp` or symbolic prosody shorthand appears.
9. Bare `...` is not being used accidentally as a pause.
10. The script sounds natural when spoken and source-based claims remain faithful to the supplied material.
11. The document is constructed so it should be suitable for later `readio ssmd check FILE.ssmd`, but no validation or rendering is claimed unless it actually ran.

## Use-case voice design

Use no explicit voice for narrator-first storytelling. Add `host` or `guest` only when dialogue is important to the story, and use `analyst` only when a third distinct role is genuinely needed.

## Recommended structure

1. Introduce the hero and a small, understandable wish or problem.
2. Send the hero on a simple journey or task.
3. Repeat a phrase or pattern two or three times.
4. Let the hero solve the problem through kindness, curiosity, patience, or courage.
5. Return to safety.
6. End with a calm final image.

## Use-case writing and performance rules

- Use concrete words and relatively short sentences.
- Avoid frightening detail, cruelty, or unresolved danger for bedtime requests.
- Use repetition intentionally; young listeners benefit from predictability.
- Keep comic voices gentle rather than shrill.
- Use slower pacing near the ending.
- Do not moralize for a long paragraph; let the lesson emerge from the story.

## Recommended default header

Unless the user asks for different pacing, start from:

```yaml
---
title: Example title
pause_defaults:
  enabled: true
  sentence: 300ms
  paragraph: 820ms
  voice_change: 350ms
---
```

Replace `Example title` with a real title. Do not leave this example title in final SSMD.

## Minimal pattern example

The following is an example of the _shape_ and markup style. Do not copy its factual content unless the user's request is actually about that subject.

```ssmd
---
title: Pip and the moon button
pause_defaults:
  enabled: true
  sentence: 300ms
  paragraph: 820ms
  voice_change: 350ms
---

<div voice="narrator">
Pip found a silver button under the old apple tree.
It was round, shiny, and just a little warm.
</div>

<div voice="host" pitch="high">
I wonder what you belong to.
</div>

<div voice="narrator">
Pip asked the sleepy cat.
Pip asked the garden gate.
Pip even asked the wind.

No one knew.
</div>

<div voice="guest">
Maybe it belongs to the moon.
</div>

<div voice="narrator" rate="slow">
Pip looked up.
The moon was bright and whole.
Nothing was missing after all.

So Pip put the silver button in a little box beside the bed.
And by morning, it had become an ordinary pebble.
A very good pebble.
</div>
```

## Generation procedure

1. Identify the requested audience, language, length, tone, and source constraints.
2. Choose the minimum number of voices needed for this use case; use no explicit voice for ordinary single-speaker output.
3. Build the episode, story, lesson, or debate structure before writing individual turns.
4. Write for the ear: short spoken sentences, explicit transitions, and natural phrasing.
5. Add prosody only where it changes delivery meaningfully.
6. Add timed breaks only at deliberate moments; rely on `pause_defaults` for ordinary pacing.
7. Preserve source fidelity whenever source material is supplied.
8. Run the final self-check from this guide mentally and remove all placeholders.
9. If artifact creation is available, create exactly one UTF-8 downloadable `.ssmd` file; otherwise return complete raw SSMD without fences or surrounding explanation.
10. Do not claim that validation or rendering was executed unless the current environment actually provided and ran that tooling.
