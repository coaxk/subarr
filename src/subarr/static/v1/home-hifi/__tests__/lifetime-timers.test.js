// The rule these pages kept breaking: nothing a page starts may outlive it.
// See lifetime-timers.mjs for the two bugs that motivated it (#556 Review,
// and Coverage's three-minute "Probe now" poll).
import { describe, it, expect } from 'vitest';
import { createTimerScope } from '../lifetime-timers.mjs';

const tick = (ms) => new Promise((r) => setTimeout(r, ms));

describe('createTimerScope', () => {
  it('sleep resolves normally while alive', async () => {
    const s = createTimerScope();
    let done = false;
    s.sleep(10).then(() => { done = true; });
    await tick(40);
    expect(done).toBe(true);
    expect(s.alive()).toBe(true);
  });

  it('a sleep pending at dispose never resolves, so its loop stops', async () => {
    const s = createTimerScope();
    let rounds = 0;
    (async () => {
      for (let i = 0; i < 50; i++) {
        await s.sleep(10);
        rounds += 1;          // only reached while the scope is alive
      }
    })();
    await tick(35);
    const atDispose = rounds;
    s.dispose();
    await tick(120);          // ~12 more rounds would have run
    expect(rounds).toBe(atDispose);
    expect(s.alive()).toBe(false);
  });

  it('later() never fires after dispose, and a sleep started after dispose never resolves', async () => {
    const s = createTimerScope();
    let fired = 0;
    let resolved = false;
    s.later(() => { fired += 1; }, 20);
    s.dispose();
    s.sleep(10).then(() => { resolved = true; });
    s.later(() => { fired += 1; }, 10);
    await tick(80);
    expect(fired).toBe(0);
    expect(resolved).toBe(false);
  });

  it('cancel() drops one timer without touching the rest', async () => {
    const s = createTimerScope();
    const hits = [];
    const id = s.later(() => hits.push('cancelled'), 20);
    s.later(() => hits.push('kept'), 20);
    s.cancel(id);
    await tick(70);
    expect(hits).toEqual(['kept']);
  });
});
