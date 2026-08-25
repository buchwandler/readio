# Spotify publishing

Publishing is an explicit external write. Readio renders speech locally and delegates the completed media to `save-to-spotify`; it never reads token files or performs authentication.

## Render and publish

```bash
readio spotify publish --file episode.ssmd --title "Episode" --format mp3 --wait
readio spotify publish --file episode.ssmd --title "Episode" --chapters-from-markers
```

Without `--output`, Readio owns and deletes the generated temporary media after success or failure. With `--output`, it retains the caller-requested artifact. Use `--show-id ID` or `--new-show TITLE`; `--new-show` creates a new show. Cover images remain optional.

## Existing media

```bash
readio spotify upload recording.m4a --title "Lecture 3" --show-id spotify:show:abc --wait 2m
```

Direct upload does not synthesize, transcode, delete, or overwrite the caller-owned `.wav`, `.mp3`, `.m4a`, or `.ogg` file.

## Lifecycle primitives

```bash
readio spotify shows --json
readio spotify status spotify:episode:abc --wait
readio spotify status abc --wait 2m
readio spotify doctor --json
```

IDs and full Spotify URIs are accepted. `--wait` optionally takes a duration. `--wait-timeout` is a deprecated flag alias. `--api-timeout` controls an upstream API request and is distinct from readiness waiting.

## Timelines

`--chapters-from-markers` creates a temporary Readio timeline and deletes it after the upstream call. `--timeline FILE` validates a caller-owned JSON object and passes it through without modifying or deleting it. The options are mutually exclusive; either one waits for READY before setting the timeline.

Errors in agent mode are one JSON object with `ok`, `code`, and `error`. Readio does not wrap login/logout, token output, destructive deletion, or upstream TTS management.
