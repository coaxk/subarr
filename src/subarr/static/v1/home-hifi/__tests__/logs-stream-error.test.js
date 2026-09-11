// #536: the Logs page rendered every stream_error as "Can't reach Docker" with
// socket-mount instructions, including the case where the socket was fine and
// SUBGEN_CONTAINER simply had a typo (`sugben`, #524).
import { describe, it, expect } from 'vitest';
import { streamErrorPanel } from '../logs-stream-error.mjs';

describe('streamErrorPanel', () => {
  it('names the missing container and points at SUBGEN_CONTAINER', () => {
    const p = streamErrorPanel({ reason: 'container_not_found', container: 'sugben', detail: 'x' });
    expect(p.title).toBe('No container named "sugben"');
    expect(p.body).toMatch(/SUBGEN_CONTAINER/);
    expect(p.body).not.toMatch(/docker\.sock/);
    expect(p.detail).toBe('x');
  });

  it('keeps the socket-mount instructions for a socket failure', () => {
    const p = streamErrorPanel({ reason: 'socket', container: 'subgen', detail: 'y' });
    expect(p.title).toBe("Can't reach Docker");
    expect(p.body).toMatch(/docker\.sock/);
  });

  it('treats a legacy plain-string payload as a socket failure', () => {
    const p = streamErrorPanel('the service is unavailable or misconfigured');
    expect(p.title).toBe("Can't reach Docker");
    expect(p.detail).toBe('the service is unavailable or misconfigured');
  });
});
