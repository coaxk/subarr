# Subtitle tuning preview samples

Static, bundled `.srt` samples served to the subtitle-tuning preview UI
(`/api/settings/subtitle-tuning/samples`). These are hand-authored and shipped
in the package — never read from arbitrary paths. The sample "playback"
affordance is loading one of these into the preview operation, which runs the
same retimer path (`parse_srt` → `RetimeParams` → `retime_srt`) as the real
completion retimer but never writes any media.

| id         | file               | purpose                                                     |
|------------|--------------------|-------------------------------------------------------------|
| `dialogue` | `dialogue.en.srt`  | Calm spoken back-and-forth — mostly unchanged by re-timing. |
| `dense`    | `dense.en.srt`     | Long-form prose with over-CPS cues and a micro-cue.         |
| `sdh`      | `sdh.en.srt`       | HI/SDH text: speaker labels + audio descriptions.           |
| `rapid`    | `rapid.en.srt`     | Rapid exchange: over-CPS and micro cues back-to-back.       |

To add a sample: drop the file here and register it in the `_SAMPLES` manifest
in `src/subarr/routers/subtitle_tuning.py`. Only manifest-registered files are
ever served.