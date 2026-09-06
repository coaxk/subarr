// #494 P2-S5/P3-S3 — Review's bulk-event contract, tested through the two pure
// seams Phase 2/3 exposed for exactly this purpose:
//   - shouldRefetchAfterVerify(gate, eventPath): the `audio-lang-verified`
//     listener's decision — gate armed => suppress Review's refetch for the
//     batch's own files; null gate / foreign path / no identity => refetch.
//   - runVerifyBatch: the shared per-file batch driver that BOTH applyBulk
//     (language assign) and acceptSelected (multilingual accept) delegate to.
//     It arms the gate with the batch paths before the first file, emits one
//     global event per successful file, clears the gate in success AND error
//     cleanup, and calls refetchAfterBatch exactly once.
// The mutation transport (the per-file fetch + evidence) lives inside the
// callers' injected `submit` and is NOT exercised here — the DOM-free harness
// only drives the injected hooks, so the guarantees below are proven purely.
import { describe, it, expect } from 'vitest';
import { shouldRefetchAfterVerify, runVerifyBatch } from '../review.jsx';

describe('shouldRefetchAfterVerify (#494 P2-S5) — the audio-lang-verified listener decision', () => {
  it('no gate -> refetch (normal per-file operation)', () => {
    expect(shouldRefetchAfterVerify(null, '/m/a.mkv')).toBe(true);
    expect(shouldRefetchAfterVerify(undefined, '/m/a.mkv')).toBe(true);
  });

  it('gate armed + event path in the batch -> suppress Review refetch', () => {
    expect(shouldRefetchAfterVerify(new Set(['/m/a.mkv', '/m/b.mkv']), '/m/a.mkv')).toBe(false);
  });

  it('gate armed + foreign path (e.g. a mid-batch arena sweep) -> refetch', () => {
    expect(shouldRefetchAfterVerify(new Set(['/m/a.mkv']), '/arena/other.mkv')).toBe(true);
  });

  it('gate armed + event with no trackable identity -> refetch (cannot prove it belongs to the batch)', () => {
    expect(shouldRefetchAfterVerify(new Set(['/m/a.mkv']), undefined)).toBe(true);
  });

  it('armed empty gate -> suppress (armed but nothing to match)', () => {
    expect(shouldRefetchAfterVerify(new Set(), '/m/a.mkv')).toBe(false);
  });
});

describe('runVerifyBatch (#494 P2-S5/P3-S3) — the shared per-file batch driver', () => {
  // Instrument the injected hooks so the test observes gate lifecycle, global
  // event count, and authoritative-refetch count. `decision` records what the
  // Review listener WOULD have decided for each emitted path while the gate is
  // armed — proving no per-file Review refetch is triggered mid-batch.
  const harness = () => {
    const log = [];
    const decisions = [];
    const live = { gate: null, armed: null };
    const hooks = {
      emitVerified: (p) => {
        decisions.push({ p, refetch: shouldRefetchAfterVerify(live.gate, p) });
        log.push(`emit:${p}`);
      },
      onProgress: (done, total, errors) => log.push(`progress:${done}/${total}/${errors}`),
      setGate: (s) => { live.gate = s; live.armed = new Set(s); log.push('arm'); },
      clearGate: () => { live.gate = null; log.push('clear'); },
      finish: () => log.push('finish'),
      refetchAfterBatch: () => log.push(`refetch:${live.gate === null ? 'after-clear' : 'while-armed'}`),
    };
    return { hooks, state: { log, decisions, get armed() { return live.armed; } } };
  };
  const okSubmit = async () => ({ lang_code: 'es' });
  const emits = (log) => log.filter((l) => l.startsWith('emit:'));
  const refetches = (log) => log.filter((l) => l.startsWith('refetch:'));

  it('arms the gate with the batch paths, holds it through every success emit (no per-file refetch), exactly ONE refetch after clearing', async () => {
    const paths = ['/a.mkv', '/b.mkv', '/c.mkv'];
    const { hooks, state } = harness();
    const res = await runVerifyBatch({
      items: paths, total: 3, concurrency: 1,
      submit: okSubmit, pathOf: (p) => p,
      emitVerified: hooks.emitVerified, onProgress: hooks.onProgress,
      setGate: hooks.setGate, clearGate: hooks.clearGate, finish: hooks.finish,
      refetchAfterBatch: hooks.refetchAfterBatch,
    });
    // Gate is armed with exactly the batch paths before the first file is touched.
    expect(state.log[0]).toBe('arm');
    expect(state.armed).toEqual(new Set(paths));
    // One global event per successful file, in order (concurrency 1).
    expect(emits(state.log)).toEqual(paths.map((p) => `emit:${p}`));
    // Every emit happened while the gate was armed, so the listener suppressed
    // each one — no per-file Review refetch occurred mid-batch.
    expect(state.decisions.map((d) => d.refetch)).toEqual([false, false, false]);
    // All emits sit between arm and clear.
    const arm = state.log.lastIndexOf('arm');
    const clear = state.log.indexOf('clear');
    expect(state.log.slice(arm + 1, clear).filter((l) => l.startsWith('emit:'))).toHaveLength(paths.length);
    // Exactly one authoritative refetch, only after the gate cleared.
    expect(refetches(state.log)).toHaveLength(1);
    expect(state.log[state.log.length - 1]).toBe('refetch:after-clear');
    // Cleanup (bulkRunning off + selection clear) runs on the success path too.
    expect(state.log).toContain('finish');
    expect(res).toEqual({ done: 3, errors: 0 });
  });

  it('a failed file emits nothing yet still clears the gate and still triggers the single authoritative refetch (failure cleanup)', async () => {
    const submit = async (p) => {
      if (p === '/bad.mkv') throw new Error('HTTP 500');
      return { lang_code: 'es' };
    };
    const { hooks, state } = harness();
    const res = await runVerifyBatch({
      items: ['/a.mkv', '/bad.mkv', '/c.mkv'], total: 3, concurrency: 1,
      submit, pathOf: (p) => p,
      emitVerified: hooks.emitVerified, onProgress: hooks.onProgress,
      setGate: hooks.setGate, clearGate: hooks.clearGate, finish: hooks.finish,
      refetchAfterBatch: hooks.refetchAfterBatch,
    });
    // No global event for the failed file — only the two successes.
    expect(emits(state.log).map((l) => l.slice(5))).toEqual(['/a.mkv', '/c.mkv']);
    expect(state.decisions.map((d) => d.refetch)).toEqual([false, false]);
    // Gate + finish cleanup still run on the failure path, and one refetch follows.
    expect(state.log).toContain('clear');
    expect(state.log).toContain('finish');
    expect(refetches(state.log)).toHaveLength(1);
    expect(res).toEqual({ done: 3, errors: 1 });
  });

  it('skipped null bodies (empty selection) never emit but still count as done, and the batch refetches once', async () => {
    const submit = async (p) => (p === '/skip.mkv' ? null : { lang_code: 'es' });
    const { hooks, state } = harness();
    const res = await runVerifyBatch({
      items: ['/skip.mkv', '/b.mkv'], total: 2, concurrency: 1,
      submit, pathOf: (p) => p,
      emitVerified: hooks.emitVerified, onProgress: hooks.onProgress,
      setGate: hooks.setGate, clearGate: hooks.clearGate, finish: hooks.finish,
      refetchAfterBatch: hooks.refetchAfterBatch,
    });
    expect(emits(state.log)).toHaveLength(1);
    expect(state.log).toContain('progress:2/2/0');  // the skipped file counts as done
    expect(refetches(state.log)).toHaveLength(1);
    expect(res).toEqual({ done: 2, errors: 0 });
  });

  it('default 4-worker concurrency still yields one emit per file and one refetch (no double events)', async () => {
    const paths = Array.from({ length: 9 }, (_, i) => `/f${i}.mkv`);
    const submit = async () => { await new Promise((r) => setTimeout(r, 1)); return { lang_code: 'es' }; };
    const { hooks, state } = harness();
    const res = await runVerifyBatch({
      items: paths, total: 9, submit, pathOf: (p) => p,
      emitVerified: hooks.emitVerified, onProgress: hooks.onProgress,
      setGate: hooks.setGate, clearGate: hooks.clearGate, finish: hooks.finish,
      refetchAfterBatch: hooks.refetchAfterBatch,
    });
    expect(emits(state.log)).toHaveLength(9);
    expect(state.decisions.every((d) => d.refetch === false)).toBe(true); // all suppressed while armed
    expect(refetches(state.log)).toHaveLength(1);
    expect(state.log[state.log.length - 1]).toBe('refetch:after-clear');
    expect(res).toEqual({ done: 9, errors: 0 });
  });
});
