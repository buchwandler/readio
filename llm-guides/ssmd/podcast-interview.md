# Readio Standalone SSMD Guide: Interview Podcast SSMD

## Mission

Create a natural two-speaker interview podcast from a topic and, when provided, source material about the guest or subject.

This file is a complete instruction set. Do not assume access to any other SSMD, Readio, prompt, template, or documentation file.

## Output contract

Your job is to create one SSMD source document.

### Preferred artifact mode

If this environment can create downloadable files or artifacts:

1. Create exactly one UTF-8 file with a short descriptive kebab-case `.ssmd.md` filename.
2. Put only SSMD source in that file.
3. Do not create helper files.
4. Do not wrap the file content in Markdown fences.
5. Expose or return the `.ssmd.md` file for download.

Use `.ssmd.md` for newly generated complete SSMD documents. Readio and SSMD continue to accept `.ssmd` for compatibility, but do not choose that filename for a new complete document unless the caller explicitly requests it.

### Fallback chat mode

If downloadable file or artifact creation is unavailable:

- Return the complete raw SSMD source directly in the response.
- If you save the raw response as a file, use a `something.ssmd.md` filename.
- Do not use Markdown code fences.
- Do not add an explanation before or after it.

### In either mode

- Do not put explanatory prose, shell commands, or Markdown fences inside the generated SSMD file.
- Use the requested language for spoken content. If no language is specified, infer it from the request and source material and stay consistent.
- Make the text sound natural when spoken aloud; do not write for silent reading.
- Do not claim that Readio/SSMD validation or audio rendering was executed unless this environment actually provides and runs that tooling.

## Audio quality contract

Write for a listener who cannot see the source, markup, speaker labels, or page layout.

- The listener should understand what the current section is about and why the next line follows.
- In multi-speaker content, each recurring speaker keeps one stable symbolic voice role throughout the document.
- `voice` identifies the speaker. Pitch, rate, volume, emphasis, and pauses change delivery only; prosody never substitutes for speaker identity.
- If two speakers could be confusing without visible labels, add a short spoken introduction, attribution, transition, or narrative action beat.
- Describe visual-only information when it is necessary for understanding. Do not assume the listener can see tables, headings, stage directions, diagrams, gestures, or formatting.
- Prefer a clear sequence of ideas over maximum information density. Give each spoken paragraph or turn one main job.
- Mix short and medium sentences. Use fragments deliberately for emphasis, comedy, suspense, or rhythm instead of making the whole document staccato.
- Most spoken lines should not need explicit prosody markup. Use SSMD controls only where they materially improve meaning or timing.
- If a duration is requested, make an approximate spoken-word and pause budget before drafting. Treat it as a planning estimate, not an exact render-duration guarantee.
- Treat this guide's example as a markup and performance demonstration only. Do not reuse its premise, cast dynamics, sequence, rhetorical structure, recurring objects, jokes, or wording unless requested.

## Target runtime

Generate conservative SSMD for this compatibility target:
Each generated document must include `ssmd_version: '0.9'` in its YAML front matter.

- Readio with SSMD 0.9 and Utterplan >=0.3.4,<0.4 support
- SSMD >=0.9.0,<0.10
- Utterplan >=0.3.4,<0.4
- PyKokoro with Utterplan schema-v3 support

These are authoring instructions, not a requirement to install or execute the runtime. They must work without Python, a Readio installation, the Readio Agent Skill, local SSMD tooling, or local model discovery. The generated file can be checked and rendered later on a Readio-capable system.

### Canonical directive fences — exact syntax

For every multi-line SSMD directive, the opening begins with the exact four-character prefix `:::{`: three ASCII colon characters followed immediately by `{`.

Valid:

```ssmd
---
ssmd_version: '0.9'
---
:::{voice="host"}
Hello.
:::
```

Invalid:

```text
::{voice="host"}
```

`::{...}` contains only two colons and is not an SSMD 0.9 directive opening. Never shorten, normalize, or retype `:::{` as `::{`. For a normal three-colon opening, the matching closing fence is exactly `:::` on a line by itself.

Before returning the document, inspect every line that begins with `::`. If the line opens attributes with `{`, it MUST begin with `:::{`. The final document must contain zero intended directive openings beginning with `::{`.

### Safe document header

Keep YAML front matter small and limited to portable metadata and defaults. Normally use only:

```yaml
---
ssmd_version: "0.9"
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

Before drafting multi-speaker content, assign each recurring speaker exactly one symbolic role and keep that assignment stable for the whole document. Do not reuse one symbolic role for two different recurring people, even if they never speak at the same time.

Use only the minimum conventional symbolic roles needed by the use case: `narrator`, `host`, `guest`, or `analyst`. Do not invent additional roles unless the caller supplies an explicit binding plan. If more recurring speakers are needed than these roles can distinguish clearly, simplify or combine incidental speakers unless the caller provides a binding plan.

A recurring speaker's identity must not depend on `pitch`, `rate`, or `volume`. These attributes express temporary delivery, not casting. Do not make a character "the high-pitched one" by applying `pitch="high"` to some or all of that character's turns. If a speaker needs to be distinguishable, use a distinct symbolic role and let the rendering system bind it to an appropriate concrete voice.

Emit document-local `voice_bindings` only when the caller explicitly supplies concrete provider/model-valid voice IDs. Copy supplied IDs exactly; otherwise omit `voice_bindings`. Never leave explanatory metavariables or placeholders in generated SSMD.

Use block directives for distinct turns when roles are necessary:

```ssmd
:::{voice="host"}
Welcome to the show.
:::

:::{voice="guest"}
Thanks for having me.
:::
```

These are voice references, not visible speaker labels. Do not write `HOST:` or `GUEST:` unless the label itself should be spoken.

### Prosody

Use explicit, readable prosody only when it materially changes meaning or timing. Prefer long attribute names and attach a change to the words it affects:

```ssmd
[This definition matters.]{rate="slow"}

[Do not press that button.]{volume="loud"}
```

Most sentences should use the speaker's normal delivery. Prefer one meaningful prosody change over stacking several attributes. Use combined rate, pitch, or volume changes only for a rare moment when all of them serve the same intended delivery. Prosody is temporary delivery, never a character identity.

Named values:

- volume: `silent`, `x-soft`, `soft`, `medium`, `loud`, `x-loud`
- rate: `very-slow`, `slow`, `moderate`, `normal`, `brisk`, `fast`, `very-fast`
- pitch: `very-low`, `low`, `moderate-low`, `normal`, `moderate-high`, `high`, `very-high`

Older `x-slow` / `medium` / `x-fast` rate names and corresponding legacy pitch names may be accepted for compatibility, but do not generate them in new documents.

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
:::{lang="de"}
Guten Morgen.
Heute sprechen wir über künstliche Intelligenz.
:::
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
- A multi-line directive opening MUST begin with the exact prefix `:::{` (three ASCII colons followed immediately by `{`). The form `::{` is invalid.
- For the standard three-colon form, put the matching closing `:::` on its own line.
- Do not place spoken text on either the opening or closing fence line.
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

## Use-case voice design

Keep `host` as interviewer and `guest` as one interviewee or generic discussion voice for the whole document. Do not invent a guest biography, credentials, employers, memories, or first-person experiences when none are supplied.

## Recommended structure

1. Host opens and briefly introduces the topic.
2. Guest is welcomed.
3. Ask an easy opening question.
4. Develop three to six thematic question-and-answer rounds.
5. Ask follow-ups that react to the immediately preceding answer.
6. Include one reflective or practical closing question.
7. Host summarizes and signs off.

## Use-case writing and performance rules

- Ask one main question at a time. Host turns are normally shorter, and the next question should react to something in the preceding answer.
- Use follow-ups to clarify, challenge gently, request an example, or connect topics. Avoid repeating the full answer before asking again.
- Do not make the guest praise the host or show without a reason, and do not simulate interruptions unless requested.
- Some follow-up turns should be impossible to write without reading the previous answer. Avoid alternating prewritten mini-essays.
- Use brief pauses before important answers, not after every turn.
- For duration planning, estimate about 135–155 spoken words per minute.

## Recommended default header

Unless the user asks for different pacing, start from:

```yaml
---
ssmd_version: "0.9"
title: Example title
pause_defaults:
  enabled: true
  sentence: 180ms
  paragraph: 480ms
  voice_change: 240ms
---
```

Replace `Example title` with a real title. Do not leave this example title in final SSMD.

## Minimal pattern example

The following is a compact markup and performance example, not a content template. Do not reuse its subject, premise, cast relationships, sequence of events, rhetorical structure, recurring objects, joke mechanism, or wording unless the caller explicitly asks for them.

```ssmd
---
ssmd_version: '0.9'
title: Designing better defaults
pause_defaults:
  enabled: true
  sentence: 180ms
  paragraph: 480ms
  voice_change: 240ms
---

@intro
:::{voice="host"}
Welcome to the show.
Today we are looking at a deceptively simple design choice: the default.
:::

:::{voice="guest"}
Thanks for having me.
Defaults look small, but they often determine what most people experience.
:::

:::{voice="host"}
What makes a default genuinely useful rather than merely convenient for the designer?
:::

:::{voice="guest"}
A useful default handles the common case well while keeping the alternative easy to understand.
The important part is that the user can still see that a choice exists.
:::

:::{voice="host"}
So visibility is part of the design, not just the configuration?
:::

:::{voice="guest"}
Exactly.
**A hidden choice is barely a choice at all.**
:::

@conclusion
:::{voice="host"}
That is a useful place to end.
Good defaults reduce effort, but good interfaces still make agency visible.
Thanks for listening.
:::
```

## Final self-check

Imagine the listener receives only the rendered audio.

- Can they tell what topic or scene they are in?
- Can they tell who is speaking when speaker identity matters?
- Can they follow time, place, action, argument, and topic transitions without seeing formatting?
- Are pronouns and references clear after speaker or section changes?
- Are important numbers, names, and terms spoken in an understandable form?
- Does prosody serve meaning rather than decorate the text?
- If visual source structure disappears, does the adaptation provide enough spoken signposting to preserve its important hierarchy?

Before returning the document, verify the requested output mode, SSMD 0.9 front matter and directive fences, stable symbolic voice roles, source fidelity, and that no validation or rendering is claimed unless it actually ran. No invented concrete voice IDs or unexpanded placeholders appear.

## Generation procedure

1. Identify the audience, language, requested duration or length, tone, source constraints, and whether the result is single-speaker or multi-speaker.
2. If source material exists, extract the facts, claims, uncertainty, and structure that must survive adaptation before writing prose.
3. If a duration is requested, establish an approximate spoken-word and pause budget, including time for explicit pauses, repeated phrases, questions, and speaker changes.
4. For multi-speaker output, make a small internal role map assigning each recurring speaker exactly one symbolic voice role.
5. Build the content arc before writing individual lines: opening context, main beats or sections, transitions, and ending.
6. Draft for the ear. Give each paragraph or speaker turn one clear purpose and use spoken transitions where the listener cannot rely on visual layout.
7. Read the draft conceptually as audio-only. Add attribution, narration, or context where understanding depends on seeing the document or hearing perfectly distinct voices.
8. Add SSMD prosody only after the plain spoken text works. Most lines should remain unannotated.
9. Add explicit timed pauses only for deliberate thinking, breathing, dramatic, comedic, learning, or interaction moments. Use `pause_defaults` for ordinary rhythm.
10. Check requested duration or length again. Tighten repetition or add useful explanation; do not add filler.
11. Preserve source fidelity and remove unsupported connective facts, quotes, personal experiences, or claims.
12. Run the final self-check and remove placeholders.
13. Mechanically inspect directive openings and closings.
14. Create exactly one `.ssmd.md` artifact when artifact creation is available; otherwise return complete raw SSMD.
15. Never claim validation or rendering unless it actually ran in the current environment.
