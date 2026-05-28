<!-- Thanks for the PR! Help us merge it faster by filling in below. -->

## What this changes

<!-- One paragraph. Resist the urge to recap the diff. -->

## Why

<!-- Link the issue this resolves: closes #NNN.
If there's no issue, explain why you skipped the "open an issue first"
step in CONTRIBUTING.md. -->

## Testing

- [ ] `PYTHONPATH=src pytest -q` passes locally
- [ ] Added a regression test (or explained below why it's untestable)
- [ ] Smoke-tested in the dev stack (subarr-next at port 9923) where relevant

## Migration impact (skip if N/A)

- [ ] Added a new `src/subarr/migrations/NNN_*.sql` file
- [ ] OR: justified in the PR body why ad-hoc schema change is fine

## Compat-mode impact (skip if N/A)

- [ ] Considered behaviour when subgen lacks `/queue` (vanilla)
- [ ] Considered behaviour when subgen lacks `/batch` (vanilla)
- [ ] Updated `src/subarr/subgen_client.py::SubgenCapabilities` if probe needs new fields

## Telemetry impact (skip if N/A)

- [ ] Doesn't add any new fingerprintable field to the payload
- [ ] OR: explained why the new field is safe + added to the docstring enumeration

## Screenshots

<!-- For UI changes. Before/after if applicable. -->
