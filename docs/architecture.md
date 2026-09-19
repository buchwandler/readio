# Readio architecture

Persistent projects separate semantic and acoustic work:

```text
source/document
      |
      | semantic policy
      v
 UtterancePlan
      |
      | acoustic profile + unit content hash
      v
 unit WAV cache
      |
      | composition policy / loudness
      v
 master WAV + timeline
      |
      | encoder format/options
      v
 output files
```

The semantic plan is engine-neutral. Voice/model changes do not require
replanning; loudness changes do not require synthesis; codec changes do not
require composition. `render` orchestrates these same stage functions instead
of maintaining a second pipeline.
