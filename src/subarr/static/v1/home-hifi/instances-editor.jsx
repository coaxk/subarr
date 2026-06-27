// #161 Phase 4B: Settings > Instances. Manage multiple Sonarr/Radarr/Bazarr
// instances — the multi-instance config surface. Wraps the Phase 4A REST API
// (/api/instances[/test], persisted to the override store + applied live).
//
// The default instance per service (id "") is env-backed: shown, but edited via
// the media-root/credential settings, not removable here. Own module (NOT
// chrome.jsx) so only the importing bundle rebuilds.

import { StatusDot } from './atoms.jsx';
import { FormRow, TextInput } from './onboarding.jsx';
import { indexHealth, dotKindForInstance } from './instance-health-util.mjs';

// Re-exported for back-compat with existing importers/tests; the canonical
// definitions now live in instance-health-util.mjs (shared with the
// Integrations summary tiles — one home for the dot-kind truth table).
export { indexHealth, dotKindForInstance };

const { useState, useEffect } = React;

const SERVICES = ['sonarr', 'radarr', 'bazarr'];
const SERVICE_LABEL = { sonarr: 'Sonarr', radarr: 'Radarr', bazarr: 'Bazarr' };

const Api = {
  list: () => fetch('/api/instances', { credentials: 'same-origin' }).then((r) => r.json()),
  health: () => fetch('/api/instances/health', { credentials: 'same-origin' }).then((r) => r.json()),
  test: (body) =>
    fetch('/api/instances/test', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then((r) => r.json()),
  add: (body) =>
    fetch('/api/instances', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then(async (r) => ({ ok: r.ok, body: await r.json() })),
  edit: (service, id, body) =>
    fetch(`/api/instances/${service}/${encodeURIComponent(id)}`, {
      method: 'PUT',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then(async (r) => ({ ok: r.ok, body: await r.json() })),
  remove: (service, id) =>
    fetch(`/api/instances/${service}/${encodeURIComponent(id)}`, {
      method: 'DELETE',
      credentials: 'same-origin',
    }).then(async (r) => ({ ok: r.ok, body: await r.json() })),
};

// Group the flat instance list per service, default instance first. Pure +
// exported for unit test.
export function groupByService(instances) {
  const out = { sonarr: [], radarr: [], bazarr: [] };
  for (const i of instances || []) {
    if (out[i.service]) out[i.service].push(i);
  }
  for (const s of SERVICES) {
    out[s].sort((a, b) => Number(b.is_default) - Number(a.is_default) || a.name.localeCompare(b.name));
  }
  return out;
}

const EMPTY_DRAFT = { service: 'sonarr', id: null, name: '', url: '', api_key: '' };

export function InstancesEditor() {
  const [instances, setInstances] = useState(null);
  const [health, setHealth] = useState({}); // {`${service}/${id}`: {online, configured, detail}}
  const [draft, setDraft] = useState(null); // {service, id, name, url, api_key}
  const [probe, setProbe] = useState(null); // {ok, detail, root_folders} | 'pending'
  const [msg, setMsg] = useState(null); // {kind:'ok'|'err', text}

  // #378: live per-instance reachability — fans out a probe per instance, so it
  // can be slow; fetched separately from the (instant) list and folded in when
  // it resolves. A probe failure leaves rows at their pending/unknown dot.
  const reloadHealth = () =>
    Api.health()
      .then((d) => setHealth(indexHealth(d.health)))
      .catch(() => {});

  const reload = () =>
    Api.list()
      .then((d) => {
        setInstances(d.instances || []);
        reloadHealth();
      })
      .catch(() => setMsg({ kind: 'err', text: 'failed to load instances' }));

  useEffect(() => {
    reload();
  }, []);

  const testDraft = async () => {
    if (!draft.url || !draft.api_key) {
      setMsg({ kind: 'err', text: 'url and api key are required to test' });
      return;
    }
    setProbe('pending');
    const res = await Api.test({ service: draft.service, url: draft.url, api_key: draft.api_key });
    setProbe(res);
  };

  const submitDraft = async () => {
    setMsg(null);
    if (!draft.name || !draft.url || (!draft.id && !draft.api_key)) {
      setMsg({ kind: 'err', text: 'name, url and api key are required' });
      return;
    }
    const res = draft.id
      ? await Api.edit(draft.service, draft.id, {
          name: draft.name,
          url: draft.url,
          api_key: draft.api_key || null, // blank = keep existing
        })
      : await Api.add({ service: draft.service, name: draft.name, url: draft.url, api_key: draft.api_key });
    if (!res.ok) {
      setMsg({ kind: 'err', text: (res.body && res.body.detail) || 'save failed' });
      return;
    }
    setDraft(null);
    setProbe(null);
    setMsg({ kind: 'ok', text: draft.id ? 'Instance updated' : 'Instance added' });
    reload();
  };

  const removeInstance = async (inst) => {
    if (!window.confirm(`Remove the ${SERVICE_LABEL[inst.service]} instance "${inst.name}"?`)) return;
    setMsg(null);
    const res = await Api.remove(inst.service, inst.id);
    if (!res.ok) {
      setMsg({ kind: 'err', text: (res.body && res.body.detail) || 'remove failed' });
      return;
    }
    const warns = (res.body && res.body.binding_warnings) || [];
    setMsg({
      kind: 'ok',
      text: warns.length
        ? `Removed. ${warns.length} library binding(s) now fall back to the default instance — re-bind them under Libraries.`
        : 'Instance removed',
    });
    reload();
  };

  if (!instances) {
    return <div style={{ color: 'var(--fg-3)', fontSize: 'var(--text-sm)' }}>Loading instances…</div>;
  }

  const grouped = groupByService(instances);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 18, maxWidth: 820 }}>
      {SERVICES.map((svc) => (
        <div key={svc} style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <span style={{ fontWeight: 600, color: 'var(--fg-0)', fontSize: 'var(--text-md)' }}>
              {SERVICE_LABEL[svc]}
            </span>
            <span style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-3)' }}>
              {grouped[svc].length} instance{grouped[svc].length === 1 ? '' : 's'}
            </span>
            <span style={{ flex: 1 }} />
            <button
              className="btn sm ghost"
              onClick={() => {
                setProbe(null);
                setMsg(null);
                setDraft({ ...EMPTY_DRAFT, service: svc });
              }}
            >
              + Add {SERVICE_LABEL[svc]}
            </button>
          </div>

          {grouped[svc].map((inst) => {
            const h = health[`${inst.service}/${inst.id}`];
            const dotKind = dotKindForInstance(inst, h);
            const dotTitle = !h
              ? 'Checking reachability…'
              : !h.configured
                ? 'Not configured'
                : h.online
                  ? 'Online'
                  : `Offline${h.detail ? ` — ${h.detail}` : ''}`;
            return (
            <div
              key={inst.id || '(default)'}
              style={{
                display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap',
                padding: '10px 12px', background: 'var(--bg-2)',
                border: '1px solid var(--bg-4)', borderRadius: 'var(--radius-md)',
              }}
            >
              <span title={dotTitle} style={{ display: 'inline-flex' }}>
                <StatusDot kind={dotKind} />
              </span>
              <span style={{ fontWeight: 600, color: 'var(--fg-0)', minWidth: 110 }}>
                {inst.is_default ? 'default' : inst.name}
              </span>
              <span className="mono" style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-2)' }}>
                {inst.url || '(not configured)'}
              </span>
              <span style={{ flex: 1 }} />
              {inst.is_default ? (
                <span style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-3)' }}>
                  env-managed — edit via credentials settings
                </span>
              ) : (
                <>
                  <button
                    className="btn sm ghost"
                    onClick={() => {
                      setProbe(null);
                      setMsg(null);
                      setDraft({ service: inst.service, id: inst.id, name: inst.name, url: inst.url, api_key: '' });
                    }}
                  >
                    Edit
                  </button>
                  <button className="btn sm ghost" onClick={() => removeInstance(inst)}>
                    Remove
                  </button>
                </>
              )}
            </div>
            );
          })}
        </div>
      ))}

      {draft && (
        <div style={{
          display: 'flex', flexDirection: 'column', gap: 10,
          padding: '12px 14px', background: 'var(--bg-2)',
          border: '1px solid var(--bg-4)', borderRadius: 'var(--radius-md)',
        }}>
          <div style={{ fontWeight: 600, color: 'var(--fg-0)' }}>
            {draft.id ? `Edit ${SERVICE_LABEL[draft.service]} · ${draft.name}` : `Add ${SERVICE_LABEL[draft.service]} instance`}
          </div>
          <FormRow label="Name" hint="Display label; sets the permanent instance id on first save">
            <TextInput value={draft.name} placeholder="e.g. Anime" onChange={(v) => setDraft({ ...draft, name: v })} />
          </FormRow>
          <FormRow label="URL" hint="Base URL subarr reaches this instance at">
            <TextInput value={draft.url} placeholder="http://sonarr-anime:8989" onChange={(v) => setDraft({ ...draft, url: v })} />
          </FormRow>
          <FormRow label="API key" hint={draft.id ? 'Leave blank to keep the current key' : 'From the *arr Settings > General page'}>
            <TextInput
              value={draft.api_key}
              type="password"
              placeholder={draft.id ? '•••••••• (unchanged)' : 'api key'}
              onChange={(v) => setDraft({ ...draft, api_key: v })}
            />
          </FormRow>
          <div style={{ display: 'flex', gap: 8 }}>
            <button className="btn sm ghost" onClick={testDraft}>Test connection</button>
            <button className="btn sm" onClick={submitDraft}>Save</button>
            <button className="btn sm ghost" onClick={() => { setDraft(null); setProbe(null); }}>Cancel</button>
          </div>
          {probe === 'pending' && (
            <div style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-3)' }}>Testing…</div>
          )}
          {probe && probe !== 'pending' && (
            <div style={{ fontSize: 'var(--text-xs)', color: probe.ok ? 'var(--success-500, #34d399)' : 'var(--error-500)' }}>
              {probe.ok
                ? `Connected${(probe.root_folders || []).length ? ` — root folders: ${probe.root_folders.join(', ')}` : ''}`
                : `Failed — ${probe.detail}`}
            </div>
          )}
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
