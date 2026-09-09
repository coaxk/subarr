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
    expect(res).toEqual({ done: 3, errors: 0, failed: [], cancelled: false, remaining: [] });
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
    expect(res).toEqual({ done: 3, errors: 1, failed: ['/bad.mkv'], cancelled: false, remaining: [] });
  });

  // #515: the count was returned and then discarded by both callers, and the
  // only render of it lived under state that the batch's own completion
  // falsified. The runner now names WHICH units failed and hands the final
  // stats to `finish`, so the caller can keep exactly those rows selected in
  // the same commit that ends the batch - no second render to lose them in.
  it('#515: names every failed unit in order and hands the final stats to finish', async () => {
    const submit = async (p) => {
      if (p.startsWith('/bad')) throw new Error('HTTP 401');
      return { lang_code: 'es' };
    };
    const { hooks, state } = harness();
    let seenByFinish;
    const res = await runVerifyBatch({
      items: ['/bad1.mkv', '/ok.mkv', '/bad2.mkv'], total: 3, concurrency: 1,
      submit, pathOf: (p) => p,
      emitVerified: hooks.emitVerified, onProgress: hooks.onProgress,
      setGate: hooks.setGate, clearGate: hooks.clearGate,
      finish: (stats) => { seenByFinish = stats; hooks.finish(); },
      refetchAfterBatch: hooks.refetchAfterBatch,
    });
    expect(res.failed).toEqual(['/bad1.mkv', '/bad2.mkv']);
    expect(res.errors).toBe(2);
    // finish sees the SAME numbers the caller gets back - not a pre-batch zero.
    expect(seenByFinish).toBeDefined();
    expect(seenByFinish.failed).toEqual(['/bad1.mkv', '/bad2.mkv']);
    expect(seenByFinish.errors).toBe(2);
    expect(seenByFinish.done).toBe(3);
    expect(state.log).toContain('finish');
  });

  // #515: a batch is now minutes long and had no way to stop short of closing
  // the tab. `shouldStop` is polled before each unit is dequeued: in-flight
  // requests finish (each POST commits independently server-side), nothing
  // else is started, and the caller learns exactly which paths were never
  // attempted so it can leave them selected.
  it('#515: shouldStop halts the queue after in-flight units - unattempted paths are reported, afterBatch is skipped, cleanup and the single refetch still run', async () => {
    let stop = false;
    const submit = async (p) => {
      if (p === '/a.mkv') stop = true;   // user hits Stop while the first file is in flight
      return { lang_code: 'es' };
    };
    const { hooks, state } = harness();
    let afterBatchRan = false;
    const res = await runVerifyBatch({
      items: ['/a.mkv', '/b.mkv', '/c.mkv'], total: 3, concurrency: 1,
      submit, pathOf: (p) => p,
      shouldStop: () => stop,
      emitVerified: hooks.emitVerified, onProgress: hooks.onProgress,
      setGate: hooks.setGate, clearGate: hooks.clearGate, finish: hooks.finish,
      afterBatch: async () => { afterBatchRan = true; },
      refetchAfterBatch: hooks.refetchAfterBatch,
    });
    // The in-flight file completed and emitted; nothing after it was started.
    expect(emits(state.log)).toEqual(['emit:/a.mkv']);
    expect(res).toEqual({ done: 1, errors: 0, failed: [], cancelled: true, remaining: ['/b.mkv', '/c.mkv'] });
    // A durable remember-for-future rule must not be declared for a series the
    // user just stopped applying to.
    expect(afterBatchRan).toBe(false);
    expect(state.log).toContain('clear');
    expect(state.log).toContain('finish');
    expect(refetches(state.log)).toHaveLength(1);
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
    expect(res).toEqual({ done: 2, errors: 0, failed: [], cancelled: false, remaining: [] });
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
    expect(res).toEqual({ done: 9, errors: 0, failed: [], cancelled: false, remaining: [] });
  });

  it('an afterBatch throw still clears the gate, runs finish, and refetches exactly once after clear — and the ORIGINAL error is rethrown', async () => {
    const paths = ['/a.mkv', '/b.mkv'];
    const boom = new Error('remember-for-future failed');
    const { hooks, state } = harness();
    // The batch's own files all succeed; AFTER them a single afterBatch step
    // throws. Cleanup + the authoritative refetch must still happen even though
    // the batch as a whole rejects.
    await expect(
      runVerifyBatch({
        items: paths, total: 2, concurrency: 1,
        submit: () => ({ lang_code: 'es' }), pathOf: (p) => p,
        emitVerified: hooks.emitVerified, onProgress: hooks.onProgress,
        setGate: hooks.setGate, clearGate: hooks.clearGate, finish: hooks.finish,
        afterBatch: async () => { throw boom; },
        refetchAfterBatch: hooks.refetchAfterBatch,
      })
    ).rejects.toThrow('remember-for-future failed');
    // Both files progressed and emitted while armed; then clear + finish ran.
    expect(emits(state.log).map((l) => l.slice(5))).toEqual(paths);
    expect(state.log).toContain('clear');
    expect(state.log).toContain('finish');
    // Exactly one authoritative refetch, and it happened only AFTER the gate
    // cleared (not masked/skipped by the afterBatch throw).
    const refetches = state.log.filter((l) => l.startsWith('refetch:'));
    expect(refetches).toHaveLength(1);
    expect(state.log[state.log.length - 1]).toBe('refetch:after-clear');
    const clearIdx = state.log.indexOf('clear');
    expect(state.log.indexOf('refetch:after-clear')).toBeGreaterThan(clearIdx);
    // Cleanup ran before the refetch (gate cleared + finish, then refetch).
    expect(state.log.indexOf('finish')).toBeLessThan(state.log.indexOf('refetch:after-clear'));
  });
});
