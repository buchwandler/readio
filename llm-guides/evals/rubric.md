# Manual audio-quality rubric

Score each dimension from 0 to 2. Judge the artifact's spoken content, not just its Markdown appearance. For listening-only criteria, use rendered audio when available; otherwise mark the result as unverified rather than inferring audio quality from valid syntax.

| Dimension                          | 0                                                           | 1                                                               | 2                                                                                      |
| ---------------------------------- | ----------------------------------------------------------- | --------------------------------------------------------------- | -------------------------------------------------------------------------------------- |
| Valid artifact shape               | Wrong mode, malformed output, or incomplete artifact        | Usable with a correctable artifact issue                        | One complete standalone SSMD 0.9 artifact in the requested mode                        |
| Role stability                     | Roles change or identity depends on pitch, rate, or volume  | Roles are mostly stable, with attribution ambiguity             | Stable symbolic roles and clear speaker introductions or transitions                   |
| Speaker distinguishability by role | Turns are confusing when voices are similar                 | Roles are identifiable with occasional ambiguity                | Wording, context, and attribution keep speakers clear without relying on voice effects |
| Audio-only comprehension           | Requires markup, page layout, or visual inference           | Mostly understandable, with a few unclear references or actions | Topic, scene, action, and references are clear in audio alone                          |
| Scene/topic transitions            | Progression is difficult to follow                          | Basic progression exists, but transitions are uneven            | Transitions make changes in time, place, speaker, or topic easy to follow              |
| Requested length/duration fit      | Clearly ignores the request or pads/drops necessary content | Approximate fit with some pacing or scope issues                | Useful content and pause budget fit the request; no exact render time is assumed       |
| Prosody restraint                  | Effects define character identity or decorate many lines    | Some unnecessary or stacked effects                             | Sparse, purposeful changes serve meaning or timing                                     |
| Pause usefulness                   | Pauses interrupt, arrive too soon, or are mostly decorative | Some pauses help, others feel mistimed or excessive             | Pauses support thinking, breathing, suspense, comedy, learning, or interaction         |
| Example non-cloning                | Reuses the guide example's premise, roles, or progression   | Noticeable scaffolding overlap                                  | Independently structured and clearly unrelated to the guide example                    |
| Natural spoken language            | Stilted, repetitive, or consistently staccato               | Understandable but uneven                                       | Varied, coherent, and natural when spoken                                              |
| Ending quality                     | Abrupt, unresolved, or introduces an unrelated new idea     | Concludes, but weakly or repetitively                           | Resolves or closes the requested arc without unnecessary padding                       |

## Source-grounded tasks

Score these separately when the prompt supplies source material:

| Dimension                          | 0                                                               | 1                                                  | 2                                                               |
| ---------------------------------- | --------------------------------------------------------------- | -------------------------------------------------- | --------------------------------------------------------------- |
| Source fidelity                    | Material is distorted or unsupported claims are added           | Mostly faithful, with a lost distinction or caveat | Claims and attribution remain faithful to the supplied material |
| Caveats and uncertainty            | Caveats disappear or uncertainty becomes fact                   | Most caveats survive, with some flattening         | Scope, uncertainty, and limits remain clear in spoken form      |
| Invented quotes or persona details | Adds unsupported quotes, biography, credentials, or experiences | Includes a minor unsupported detail                | Adds no unsupported quotes or personal details                  |

## Review record

Record the prompt filename, guide filename and revision, model and date, artifact path, whether rendering/listening occurred, destination binding context if applicable, each score, and a brief evidence note for any score below 2. Do not claim validation, rendering, or listening unless it actually occurred.
