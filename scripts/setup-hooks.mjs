// Point git at the tracked hooks directory (.githooks) so the frontend
// auto-rebuild pre-commit hook activates after `npm install`. This is the
// husky-free way to ship a versioned hook: the hook lives in-repo and
// core.hooksPath makes git use it.
//
// No-ops (instead of failing the install) outside a git checkout — e.g. when
// the package is unpacked from a tarball or vendored without .git — so it
// never breaks a consumer's `npm install`.
import { execFileSync } from 'node:child_process';

try {
  execFileSync('git', ['rev-parse', '--git-dir'], { stdio: 'ignore' });
  execFileSync('git', ['config', 'core.hooksPath', '.githooks'], { stdio: 'ignore' });
} catch {
  // Not a git checkout, or git unavailable — nothing to wire up.
}
