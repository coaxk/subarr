# First run: from `docker compose up` to a green dashboard

The README gets a container running. This page covers the next fifteen
minutes: what the setup wizard will ask for, what to have ready before you
open it, and what "done" looks like. Every section is one screen. If you only
read one, read [Before you open the wizard](#1-before-you-open-the-wizard).

## 1. Before you open the wizard

Have these to hand. Each is asked for once, on its own step, and the wizard
can test each one before you move on.

| You need | Where to find it |
|---|---|
| **Bazarr** URL and API key | Bazarr → Settings → General → Security. Required. |
| **Sonarr** URL and API key | Sonarr → Settings → General → Security. Required. |
| **Radarr** URL and API key | Radarr → Settings → General → Security. Optional. |
| **subgen** URL | `http://<subgen container name>:9000` when both run on the same Docker network. Required. |
| **Tautulli** URL and API key | Tautulli → Settings → Web Interface → API. Optional. |
| **The media path subarr sees** | The container-side path of your media mount, for example `/media/library`. |

**URLs are from inside the subarr container.** `http://bazarr:6767` works when
`bazarr` is a container on the same Docker network. `http://localhost:6767`
does not, because inside the container `localhost` is subarr itself. Your
LAN IP (`http://192.168.1.10:6767`) works from anywhere.

**Paths must agree across apps.** subarr writes subtitle files next to your
media. If Bazarr sees a file as `/data/Multimedia/TV/x.mkv` and subarr sees it
as `/media/library/TV/x.mkv`, tell subarr the prefix difference on the paths
step (or with `ARR_PATH_PREFIX` and `SUBGEN_MEDIA_PREFIX`). The cheapest setup
mounts the same host directory at the same container path in every app.

**Mount media `:rw`.** Everything else works `:ro`, but nothing lands.

**Tell subgen where media lives.** `ghcr.io/coaxk/subarr-subgen` only accepts
paths under `SUBGEN_PATH_ALLOWLIST` (default `/media`), a guard against its
unauthenticated endpoints walking the whole container. If your media is
mounted anywhere else inside the subgen container, set that variable on the
**subgen** service, colon-separated for several roots: `SUBGEN_PATH_ALLOWLIST=/data`.
Otherwise the first transcription fails with 403 "outside the allowed media root".

## 2. Auto-detect: what it needs and what it cannot do

The Welcome step has a **Detect my stack** button. It asks Docker which
containers are running next to subarr and prefills their URLs (and API keys
where the container's environment exposes them).

It needs one of:

- the Docker socket mounted into subarr:
  `- /var/run/docker.sock:/var/run/docker.sock:ro`, or
- a socket proxy such as `tecnativa/docker-socket-proxy`, with
  `SUBARR_DOCKER_PROXY_URL=http://socket-proxy:2375`.

No env var is needed for the mounted socket. When neither is present the
button says "Auto-detect unavailable" and why. Nothing else in subarr needs
Docker access except the Logs page, so it is fine to skip this and type the
URLs by hand on each step.

What auto-detect cannot do:

- find apps on another host, or outside Docker;
- read an API key the app stores only in its own config file (Bazarr,
  Sonarr and Radarr keep theirs in config, so you will usually paste those);
- tell you which of two Sonarrs is the right one. It lists both.

Two variables are only for the guided subgen setup and the Logs page, and
both must be exactly right or left unset: `SUBGEN_CONTAINER` (the container's
name; a typo shows as "No container named X" on the Logs page) and
`SUBGEN_COMPOSE_PATH` (a compose file readable by the `PUID` user, not just
by root).

## 3. The steps, in order

| Step | Asks for | Optional |
|---|---|---|
| Welcome | nothing; offers auto-detect | |
| Library paths | the container-side media root; detected bind mounts appear as one-click chips | |
| Bazarr | URL, API key; tested as you type | |
| Sonarr | URL, API key; tested as you type | |
| Radarr | URL, API key | yes |
| More stacks | nothing; explains multi-instance and where to add one later | yes, skip it |
| Tautulli | URL, API key, for playback-aware priority | yes |
| subgen | URL; tested as you type, with the cause on failure | |
| subgen tuning | Whisper model, device, compute type; needs `SUBGEN_COMPOSE_PATH` | yes |
| Ollama | URL, for the audio-review assistant | yes |
| GPU check | nothing; reports what `nvidia-smi` sees from inside subarr | |
| Speech detection | one checkbox; pulls a 2 MB model | recommended |
| First walk | which subfolders to probe first, for example `TV, Movies` | |

A failed connection test never blocks you: the button reads **Continue
anyway**. Do that when the service is not up yet, not when the URL is wrong.
Everything here is editable later under Settings, with the same test button.

The wizard takes about four minutes with the keys ready. It takes an hour
without them.

## 4. What "done" looks like

After **Finish** the first walk runs in the background. In order:

1. **Dashboard tiles go green.** Bazarr, Sonarr, subgen and any optional
   integration each show a status. A tile that is not green names the
   reason when you hover or click it.
2. **Library fills.** The Library tab lists files as the walk finds them, with
   their embedded subtitle tracks. A few thousand files takes a couple of
   minutes; a very large library can take an hour, and you can use every
   other page meanwhile.
3. **Coverage sorts the gaps.** Once the walk finishes, the Coverage tab
   shows what is missing, scored, with a reason chip per row (no track,
   embedded-only, bazarr-wanted, audio-mislabel, low-score, unmonitored).
4. **The first transcription.** Tick a file in Library and hit **Queue for
   transcription**. The Queue tab shows it running on subgen and the `.srt`
   lands next to the media file when it finishes. That single file proves
   every path and permission at once.

If step 4 works, setup is finished. Coverage rules, schedules and
auto-queue are for later, and nothing forces them.

## 5. When something is red

| You see | It means | Settle it with |
|---|---|---|
| Wizard: "nothing listening on that port" | wrong port, or the container is stopped | `docker ps` for the container; the port is the one inside the network, not the published one |
| Wizard: "hostname does not resolve" | the name is not on subarr's Docker network | `docker network inspect <net>` lists who is on it; use the LAN IP otherwise |
| Wizard: an HTTP error (404, 502, ...) from the URL | a reverse proxy or another app answered instead of subgen | open the URL in a browser from the same host; it should show subgen, not a login page |
| Dashboard: "subgen unreachable for 12m (refused)" | subgen is down or moved | `docker logs subgen --tail 50`; the banner names the cause |
| Logs page: "Can't reach Docker" | no socket or proxy reachable from subarr | mount the socket (section 2) and recreate subarr |
| Logs page: "No container named X" | `SUBGEN_CONTAINER` does not match a container | `docker ps --format '{{.Names}}'` and copy the name exactly, or unset the variable |
| subgen tuning: "not allowed to read /path" | the compose file is root-only and subarr runs as `PUID` | `chmod o+r` the file, or `chown` it to `PUID` |
| Queue: job finishes but no `.srt` appears | media mounted `:ro`, or paths differ between subarr and subgen | `docker exec subarr touch /media/library/.w && rm` the same; compare `SUBGEN_MEDIA_PREFIX` |
| Queue: "subgen refused the path (403): directory is outside the allowed media root" | subgen's `SUBGEN_PATH_ALLOWLIST` does not cover your media mount | set it on the subgen container to the mount root, e.g. `/data`, and recreate subgen |
| Every tile red after a reboot | the Docker network came up after subarr | `docker restart subarr` |

When none of these fit, the Logs page (subarr's own log, no socket needed)
shows the last error with its cause, and the [README common
questions](../README.md#common-questions) cover the next layer down. Open an
issue with that log line and your compose file with secrets removed; that is
enough to answer most reports in one reply.
