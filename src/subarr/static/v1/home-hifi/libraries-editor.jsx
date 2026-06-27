// #134 slice 5: Libraries CRUD shared by Settings and Onboarding.
//
// Each library is one media location: a filesystem root (subarr's view), the
// prefix subgen sees it at, and the path prefix Sonarr/Radarr report. The
// default library (slug "") mirrors SUBARR_MEDIA_ROOT and is managed by the
// legacy media-root settings surface — read-only here. Extras are edited as
// a full list and saved wholesale via PUT /api/settings/libraries (server
// assigns immutable slugs to new entries).
//
// #161 Phase 4B: a non-default library can be BOUND to specific Sonarr/Radarr/
// Bazarr instances (which stack owns it). Bindings come from /api/instances;
// the resolved topology (Plex section + dangling-binding warnings) from
// /api/topology. Empty binding = the default instance.
//
// Own module (NOT chrome.jsx) so only the two importing bundles rebuild.

import { StatusDot } from './atoms.jsx';
import { FormRow, TextInput } from './onboarding.jsx';

const { useState, useEffect } = React;

const BIND_SERVICES = ['sonarr', 'radarr', 'bazarr'];
const BIND_LABEL = { sonarr: 'Sonarr', radarr: 'Radarr', bazarr: 'Bazarr' };

const Api = {
  get: () => fetch('/api/settings/libraries', { credentials: 'same-origin' }).then((r) => r.json()),
  put: (libraries) =>
    fetch('/api/settings/libraries', {
      method: 'PUT',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ libraries }),
    }).then(async (r) => ({ ok: r.ok, body: await r.json() })),
  validate: (fs_root) =>
    fetch('/api/settings/libraries/validate', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ fs_root }),
    }).then((r) => r.json()),
  rootFolders: () => fetch('/api/onboarding/root-folders', { credentials: 'same-origin' }).then((r) => r.json()),
  instances: () => fetch('/api/instances', { credentials: 'same-origin' }).then((r) => r.json()),
  topology: () => fetch('/api/topology', { credentials: 'same-origin' }).then((r) => r.json()),
};

const EMPTY_DRAFT = { slug: null, name: '', fs_root: '', subgen_prefix: '', arr_prefix: '', sonarr_id: '', radarr_id: '', bazarr_id: '' };

const DRAFT_FIELDS = [
  ['name', 'Name', 'e.g. 4K Movies', 'Display label; sets the permanent library id on first save'],
  ['fs_root', 'Filesystem root', '/media/disk2', "This library's mount as subarr sees it"],
  ['arr_prefix', '*arr path prefix', '/data/disk2/', 'How Sonarr/Radarr report paths under this root'],
  ['subgen_prefix', 'subgen prefix', '/media2', 'Blank = same as the default library'],
];

// Dropdown options for a service binding: the default instance first, then each
// non-default instance. Pure + exported for unit test.
export function instanceOptions(instances, service) {
  const out = [{ id: '', label: 'default' }];
  for (const i of instances || []) {
    if (i.service === service && !i.is_default) out.push({ id: i.id, label: i.name });
  }
  return out;
}

// Human label for a library's binding to one service (the instance name, or
// 'default' when unbound / pointing at instance 0). Pure + exported.
export function bindingLabel(lib, service, instances) {
  const id = lib[`${service}_id`] || '';
  if (!id) return 'default';
  const match = (instances || []).find((i) => i.service === service && i.id === id);
  return match ? match.name : `${id} (missing)`;
}

export function LibrariesEditor({ showDetected = false }) {
  const [libs, setLibs] = useState(null); // server truth (incl. default)
  const [draft, setDraft] = useState(null); // add/edit form (EMPTY_DRAFT shape)
  const [check, setCheck] = useState(null); // validate result for draft.fs_root
  const [detected, setDetected] = useState(null); // root-folders payload
  const [instances, setInstances] = useState([]); // for binding dropdowns
  const [topo, setTopo] = useState(null); // resolved topology (plex section + warnings)
  const [msg, setMsg] = useState(null); // {kind: 'ok'|'err', text}

  const reloadTopo = () => Api.topology().then(setTopo).catch(() => {});

  useEffect(() => {
    Api.get().then((d) => setLibs(d.libraries)).catch(() => setMsg({ kind: 'err', text: 'failed to load libraries' }));
    Api.instances().then((d) => setInstances(d.instances || [])).catch(() => {});
    reloadTopo();
    if (showDetected) Api.rootFolders().then(setDetected).catch(() => {});
  }, []);

  const extras = (libs || []).filter((l) => !l.is_default);
  const plexBySlug = {};
  for (const t of (topo && topo.libraries) || []) plexBySlug[t.slug] = t;

  const save = async (nextExtras) => {
    setMsg(null);
    const res = await Api.put(
      nextExtras.map(({ slug, name, fs_root, subgen_prefix, arr_prefix, sonarr_id, radarr_id, bazarr_id }) => ({
        slug: slug || null, name, fs_root, subgen_prefix: subgen_prefix || null, arr_prefix,
        sonarr_id: sonarr_id || null, radarr_id: radarr_id || null, bazarr_id: bazarr_id || null,
      })),
    );
    if (!res.ok) {
      setMsg({ kind: 'err', text: res.body.detail || 'save failed' });
      return false;
    }
    setLibs(res.body.libraries);
    reloadTopo();
    setMsg({ kind: 'ok', text: 'Libraries saved' });
    return true;
  };

  const submitDraft = async () => {
    if (!draft.name || !draft.fs_root || !draft.arr_prefix) {
      setMsg({ kind: 'err', text: 'name, filesystem root and *arr prefix are required' });
      return;
    }
    const next = draft.slug ? extras.map((l) => (l.slug === draft.slug ? draft : l)) : [...extras, draft];
    if (await save(next)) { setDraft(null); setCheck(null); }
  };

  // Detected *arr root folders not already covered by a configured arr_prefix.
  const uncovered = [];
  if (detected && libs) {
    const prefixes = libs.map((l) => l.arr_prefix).filter(Boolean).map((p) => p.replace(/\/$/, ''));
    for (const svc of ['sonarr', 'radarr']) {
      for (const f of (detected[svc] && detected[svc].folders) || []) {
        if (!prefixes.some((p) => f.path === p || f.path.startsWith(p + '/'))) {
          uncovered.push({ service: svc, ...f });
        }
      }
    }
  }

  const warnings = (topo && topo.binding_warnings) || [];
  // Only surface binding controls once a second instance of some service exists.
  const hasExtraInstances = (instances || []).some((i) => !i.is_default);

  if (!libs) return <div style={{ color: 'var(--fg-3)', fontSize: 'var(--text-sm)' }}>Loading libraries…</div>;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      {warnings.length > 0 && (
        <div style={{
          padding: '10px 12px', borderRadius: 'var(--radius-md)',
          background: 'rgba(245,158,11,0.08)', border: '1px solid rgba(245,158,11,0.4)',
          fontSize: 'var(--text-xs)', color: 'var(--fg-1)',
        }}>
          <div style={{ fontWeight: 600, marginBottom: 4 }}>⚠ Some library bindings point at an instance that isn't configured</div>
          {warnings.map((w, i) => <div key={i} className="mono" style={{ color: 'var(--fg-2)' }}>{w}</div>)}
        </div>
      )}

      {libs.map((l) => {
        const plex = plexBySlug[l.slug];
        return (
          <div
            key={l.slug || '(default)'}
            style={{
              display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap',
              padding: '10px 12px', background: 'var(--bg-2)',
              border: '1px solid var(--bg-4)', borderRadius: 'var(--radius-md)',
            }}
          >
            <StatusDot kind={l.reachable ? 'ok' : 'error'} />
            <span style={{ fontWeight: 600, color: 'var(--fg-0)', minWidth: 90 }}>
              {l.is_default ? 'default' : l.name}
            </span>
            <span className="mono" style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-2)' }}>{l.fs_root}</span>
            {hasExtraInstances && !l.is_default && (
              <span style={{ fontSize: 'var(--text-2xs)', color: 'var(--fg-3)', display: 'inline-flex', gap: 8 }}>
                {BIND_SERVICES.map((svc) => (
                  <span key={svc} className="mono">{BIND_LABEL[svc]}:{bindingLabel(l, svc, instances)}</span>
                ))}
              </span>
            )}
            {plex && (
              <span style={{ fontSize: 'var(--text-2xs)', color: plex.plex_matched ? 'var(--fg-3)' : 'var(--warn-500, #f59e0b)' }}>
                {plex.plex_matched ? `Plex: ${plex.plex_section}` : '⚠ no Plex section'}
              </span>
            )}
            <span style={{ flex: 1 }} />
            {l.is_default ? (
              <span style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-3)' }}>managed by media-root settings</span>
            ) : (
              <>
                <button className="btn sm ghost" onClick={() => { setDraft({ ...EMPTY_DRAFT, ...l }); setCheck(null); }}>
                  Edit
                </button>
                <button className="btn sm ghost" onClick={() => save(extras.filter((x) => x.slug !== l.slug))}>
                  Remove
                </button>
              </>
            )}
          </div>
        );
      })}

      {showDetected && uncovered.length > 0 && (
        <div style={{
          padding: '10px 12px', borderRadius: 'var(--radius-md)',
          background: 'rgba(139,92,246,0.06)', border: '1px solid rgba(139,92,246,0.32)',
        }}>
          <div style={{ fontWeight: 600, fontSize: 'var(--text-sm)', color: 'var(--fg-0)', marginBottom: 6 }}>
            Detected *arr root folders not covered by any library
          </div>
          {uncovered.map((f) => (
            <div key={f.service + f.path} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '4px 0' }}>
              <span className="mono" style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-2)' }}>
                [{f.service}] {f.path}
              </span>
              <button
                className="btn sm ghost"
                onClick={() => {
                  setCheck(null);
                  setDraft({
                    ...EMPTY_DRAFT,
                    name: f.path.split('/').filter(Boolean).pop() || 'library',
                    arr_prefix: f.path.endsWith('/') ? f.path : f.path + '/',
                  });
                }}
              >
                Add as library…
              </button>
            </div>
          ))}
        </div>
      )}

      {draft ? (
        <div style={{
          display: 'flex', flexDirection: 'column', gap: 10,
          padding: '12px 14px', background: 'var(--bg-2)',
          border: '1px solid var(--bg-4)', borderRadius: 'var(--radius-md)',
        }}>
          <div style={{ fontWeight: 600, color: 'var(--fg-0)' }}>
            {draft.slug ? `Edit ${draft.name}` : 'Add library'}
          </div>
          {DRAFT_FIELDS.map(([k, label, ph, hint]) => (
            <FormRow key={k} label={label} hint={hint}>
              <TextInput value={draft[k]} placeholder={ph} onChange={(v) => setDraft({ ...draft, [k]: v })} />
            </FormRow>
          ))}
          {hasExtraInstances && (
            <FormRow label="Instance bindings" hint="Which stack owns this library. Default = your primary instance.">
              <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
                {BIND_SERVICES.map((svc) => (
                  <label key={svc} style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
                    <span style={{ fontSize: 'var(--text-2xs)', color: 'var(--fg-3)' }}>{BIND_LABEL[svc]}</span>
                    <select
                      value={draft[`${svc}_id`] || ''}
                      onChange={(e) => setDraft({ ...draft, [`${svc}_id`]: e.target.value })}
                      style={{
                        background: 'var(--bg-1)', color: 'var(--fg-1)',
                        border: '1px solid var(--bg-4)', borderRadius: 'var(--radius-sm)',
                        padding: '4px 8px', fontSize: 'var(--text-xs)',
                      }}
                    >
                      {instanceOptions(instances, svc).map((o) => (
                        <option key={o.id || '(default)'} value={o.id}>{o.label}</option>
                      ))}
                    </select>
                  </label>
                ))}
              </div>
            </FormRow>
          )}
          <div style={{ display: 'flex', gap: 8 }}>
            <button className="btn sm ghost" onClick={() => Api.validate(draft.fs_root).then(setCheck)}>
              Test path
            </button>
            <button className="btn sm" onClick={submitDraft}>Save</button>
            <button className="btn sm ghost" onClick={() => { setDraft(null); setCheck(null); }}>Cancel</button>
          </div>
          {check && (
            <div style={{
              fontSize: 'var(--text-xs)',
              color: check.ok ? 'var(--success-500, #34d399)' : 'var(--error-500)',
            }}>
              {check.ok ? `OK — ${check.total} entries (${(check.samples || []).join(', ')})` : check.error}
            </div>
          )}
        </div>
      ) : (
        <div>
          <button className="btn sm" onClick={() => { setDraft({ ...EMPTY_DRAFT }); setCheck(null); }}>
            + Add library
          </button>
        </div>
      )}

      {msg && (
        <div style={{
          fontSize: 'var(--text-xs)',
          color: msg.kind === 'ok' ? 'var(--success-500, #34d399)' : 'var(--error-500)',
        }}>
          {msg.text}
        </div>
      )}
    </div>
  );
}
