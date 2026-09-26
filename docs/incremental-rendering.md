# Incremental rendering

Readio projects are built as explicit stages:

```text
source/document -> UtterancePlan -> unit WAV cache -> master/timeline -> output
```

The status command explains freshness from content identities rather than
filesystem timestamps:

```bash
readio status manuscript.readio
readio status manuscript.readio --json
```

A changed sentence changes its Utterplan unit content hash, so the next build
reuses unchanged unit cache entries. Inserting text can shift positional unit
IDs without defeating reuse because the cache key is the unit content hash plus
acoustic profile identity. A complete synthesis cache hit does not open a TTS
session.

```bash
readio render manuscript.readio --format mp3
```

The high-level command rebuilds only stale stages. Typical decisions are:

| Change                                            | Rebuilt stages                                           |
| ------------------------------------------------- | -------------------------------------------------------- |
| source sentence                                   | plan, changed synthesis units, composition, export       |
| voice/model/engine                                | synthesis units for the new profile, composition, export |
| target LUFS or peak policy                        | composition, export                                      |
| Generic WAV/FLAC/MP3/M4A/Ogg/Vorbis/Opus settings | export                                                   |

`readio export` tracks each output independently. Its identity includes the master, format, and normalized effective encoder options: M4A defaults to 192k and Opus defaults to 96k. WAV and FLAC do not accept bitrate options. `.ogg` remains Ogg/Vorbis; `.opus` is separate.

Audiobook M4B is a separate output identity created by `readio audiobook export PROJECT --format m4b`. It includes the composition master and timeline, resolved title/author, explicit cover hash, and AAC bitrate (192k by default). Changing only metadata, cover, or bitrate requires a new M4B encode, not replanning, resynthesis, or recomposition. A master/timeline change follows the normal composition invalidation path. Automatic cover extraction is not supported; pass a JPEG/PNG with `--cover`.

`readio preview PROJECT --select paragraph:1-3 --voice VOICE -o preview.wav`
uses the same synthesis and composition primitives for a selected range. It
keeps audition profiles out of the active project trace unless `--activate` is
provided.

Composition writes a standard Audiocompose bundle under `composition/` and
validates source hashes. Synthesis writes each cache WAV atomically and updates
the trace only after all requested audio has been persisted, making interrupted
runs resumable.
