# Security policy

## Reporting a vulnerability

**Don't open a public GitHub issue for security bugs.** Email the
maintainer directly: **security@subarr.dev** (or DM `@coaxk` on
GitHub if you can't email).

Include:
- What the issue is
- Steps to reproduce (proof-of-concept if you have one)
- Affected version(s)
- Impact assessment from your perspective

We'll acknowledge within 72 hours, scope an assessment, and coordinate
a release if a fix is needed. Public disclosure happens after a fix
ships unless you specifically request earlier.

We don't have a bug-bounty program. We do credit reporters in the
release notes if you'd like recognition.

## Scope

In scope:
- Subarr's own code in this repo
- The subarr-subgen patches (https://github.com/coaxk/subarr-subgen)
- The published Docker images (`ghcr.io/coaxk/subarr` and
  `ghcr.io/coaxk/subarr-subgen`)

Out of scope:
- Upstream subgen vulnerabilities — file at McCloudS/subgen
- Bazarr/Sonarr/Radarr/Tautulli vulnerabilities — file at the
  respective project
- Self-inflicted misconfigurations (exposed to the public internet
  without a reverse proxy, weak passwords, etc.)

## Threat model

Subarr is designed to run on a **trusted LAN behind a reverse proxy
with proper auth**. The basic-auth fallback (`SUBARR_USER` /
`SUBARR_PASS`) is for users who can't put it behind a proxy; it's
not equivalent to Authelia or OIDC.

The docker-socket-proxy permission tiers are documented in
`deploy/templates/README.md`. We assume users who pick Tier 3
(config-mount auto-extract) accept the trade-off that subarr can
read every mounted config dir — that's the documented price for
zero manual API-key entry.

## Defenses currently in place

- **Telemetry**: anonymous install ID only, payload contents
  enumerated in `src/subarr/telemetry.py`, regression test guards
  against accidental fingerprintable-field additions
  (`test_payload_never_includes_forbidden_fields`)
- **Auth**: basic-auth middleware uses `secrets.compare_digest` to
  protect against timing attacks; constant-time compare is
  regression-tested (`test_uses_constant_time_compare`)
- **API keys**: never returned in any API response. ExtractedKey.masked
  surfaces `••••f6c0` masks; raw key only lives in dataclass internal
  field
- **Path safety**: every filesystem operation uses canonical paths
  routed through `canonical_to_fs()` which rejects path-traversal
  outside the configured media root
- **SQL**: parameterised queries throughout; no string concatenation
  into SQL anywhere
- **Subprocess**: `shell=False` everywhere; no user input flows into
  `subprocess.run` arguments
- **CI**: weekly bandit + pip-audit + semgrep + trivy scans on both
  repos. SARIF uploaded to GitHub Security tab.

## Known limitations

- **No CSRF protection** beyond same-origin policy — the API is
  cookie-less + token-less, so a malicious page on another origin
  can't authenticate. If you put subarr behind a reverse proxy with
  cookie-based session auth, ensure the proxy adds CSRF tokens.
- **Subarr has no rate limiting**. Put it behind a reverse proxy
  with `fail2ban` or similar for anything internet-facing.

If you find anything not listed here that surprises you, that's
exactly the kind of report we want.
