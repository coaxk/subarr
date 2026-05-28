// Subarr frontend — vanilla JS, no build step.
// Polls /api/queue + /api/gpu + /api/container on a 2s tick while Monitor /
// header is visible; opens an SSE stream for logs only when the Logs tab is
// active. Folder tree lazy-loads via /api/browse on each <details> expand.

(() => {
  const $ = (s, root = document) => root.querySelector(s);
  const $$ = (s, root = document) => Array.from(root.querySelectorAll(s));

  // ───── tab routing ─────
  function activate(tab) {
    $$('.tab').forEach((b) => b.classList.toggle('active', b.dataset.tab === tab));
    $$('.tabpanel').forEach((p) => p.classList.toggle('active', p.id === `tab-${tab}`));
    if (tab === 'logs') startLogs(); else stopLogs();
    if (tab === 'monitor') refreshMonitor();
    if (tab === 'settings') { loadSettings(); loadIntegrations(); }
    if (tab === 'automation') { loadSchedule(); loadProbeWalks(); startPendingPoll(); }
    else stopPendingPoll();
    if (tab === 'coverage') loadCoverage();
    if (tab === 'library') loadLibrary();
    if (tab === 'activity') startActivity(); else stopActivity();
  }
  $$('.tab').forEach((b) => b.addEventListener('click', () => activate(b.dataset.tab)));

  // ───── header poller (queue + gpu + container) ─────
  let headerTimer = null;
  async function pollHeader() {
    try {
      const [h, q, g, c] = await Promise.all([
        fetch('/api/health').then((r) => r.json()).catch(() => null),
        fetch('/api/queue').then((r) => (r.ok ? r.json() : null)).catch(() => null),
        fetch('/api/gpu').then((r) => r.json()).catch(() => null),
        fetch('/api/container').then((r) => (r.ok ? r.json() : null)).catch(() => null),
      ]);
      if (h) $('#version').textContent = `v${h.version}`;
      // Pending approvals badge — fire-and-forget, separate from header stats.
      fetch('/api/schedule/pending?status=pending')
        .then((r) => r.ok ? r.json() : null)
        .then((d) => {
          if (!d) return;
          const stat = $('#stat-pending');
          if (d.pending_count > 0) {
            stat.hidden = false;
            $('#pending-summary').textContent = `${d.pending_count} walk${d.pending_count === 1 ? '' : 's'}`;
          } else {
            stat.hidden = true;
          }
        })
        .catch(() => {});
      $('#container-status').textContent = c
        ? (c.running ? 'running' : (c.status || 'down'))
        : '—';
      $('#queue-summary').textContent = q
        ? `${q.processing_count} active · ${q.queued_count} queued`
        : 'unreachable';
      if (g?.online) {
        const used = g.memory.used_mib;
        const total = g.memory.total_mib;
        $('#gpu-summary').textContent = `${Math.round(used)}/${Math.round(total)} MiB · ${Math.round(g.utilization.gpu_pct)}%`;
      } else {
        $('#gpu-summary').textContent = g?.error ? '—' : '…';
      }
      if (window._currentTab === 'monitor') renderMonitor(g, c, q);
    } catch (e) {
      console.warn('header poll error', e);
    }
  }
  function startHeader() { if (!headerTimer) { pollHeader(); headerTimer = setInterval(pollHeader, 2000); } }

  // ───── Folder tree ─────
  async function browse(path) {
    const r = await fetch(`/api/browse?path=${encodeURIComponent(path)}`);
    if (!r.ok) throw new Error(`browse ${path} → ${r.status}`);
    return r.json();
  }

  const selectedPaths = new Set();

  // ───── Unified coverage badge ─────
  // Single source of truth for how a "does this file have English?" signal
  // gets rendered. Used by Scan tree leaves, Library tab rows, and Coverage
  // tree episode rows so the vocabulary + colours line up across views.
  //
  // Inputs:
  //   embedded_en: 'EN' | 'EN(SDH)' | 'EN(forced)' | 'EN(commentary)' | null
  //   has_sibling_srt: bool | undefined
  // Output: { label, cls, tooltip, coversFully (bool — used for confirm dialog) }
  function coverageSignal(embedded_en, has_sibling_srt) {
    if (embedded_en === 'EN') {
      const both = !!has_sibling_srt;
      return {
        label: 'EN', cls: 'embedded-full',
        tooltip: both
          ? 'sibling .srt + embedded English subtitle'
          : 'embedded English subtitle stream',
        coversFully: true,
      };
    }
    if (embedded_en === 'EN(SDH)') return {
      label: 'EN(SDH)', cls: 'embedded-full',
      tooltip: 'embedded English SDH track — counts as full coverage',
      coversFully: true,
    };
    if (embedded_en === 'EN(forced)') return {
      label: 'EN(forced)', cls: 'embedded-partial',
      tooltip: 'partial: forced subs only',
      coversFully: false,
    };
    if (embedded_en === 'EN(commentary)') return {
      label: 'EN(commentary)', cls: 'embedded-partial',
      tooltip: 'partial: commentary only',
      coversFully: false,
    };
    if (has_sibling_srt) return {
      label: 'srt', cls: 'embedded-full',
      tooltip: 'sibling .srt file on disk',
      coversFully: true,
    };
    return {
      label: '—', cls: 'embedded-none',
      tooltip: 'no English subtitle detected (probe cache may be empty — run a probe walk)',
      coversFully: false,
    };
  }

  function coverageBadgeHtml(embedded_en, has_sibling_srt) {
    const s = coverageSignal(embedded_en, has_sibling_srt);
    return `<span class="embedded-badge ${s.cls}" title="${escape(s.tooltip)}">${escape(s.label)}</span>`;
  }

  function renderFileLeaf(entry) {
    // Video file row — checkbox + unified badge + name + size.
    const row = document.createElement('div');
    const sig = coverageSignal(entry.embedded_en, entry.has_sibling_srt);
    row.className = 'file-row' + (sig.coversFully ? ' has-srt' : '');
    row.dataset.path = entry.path;
    row.dataset.searchKey = entry.name.toLowerCase();
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.checked = selectedPaths.has(entry.path);
    cb.addEventListener('change', () => {
      // Confirm before adding a file we already know has English coverage —
      // subgen will SKIP_IF_TARGET_SUBTITLES_EXIST and we'd get nothing in
      // the queue, leading to the 'where did it go?' confusion.
      if (cb.checked && sig.coversFully) {
        const msg = (
          `${entry.name} already has English coverage (${sig.label}).\n` +
          `subgen will most likely skip it (SKIP_IF_TARGET_SUBTITLES_EXIST=true).\n\n` +
          `Scan anyway?`
        );
        if (!confirm(msg)) {
          cb.checked = false;
          return;
        }
      }
      if (cb.checked) selectedPaths.add(entry.path);
      else selectedPaths.delete(entry.path);
      renderSelectedList();
    });
    const badge = document.createElement('span');
    badge.className = `embedded-badge ${sig.cls} file-row-badge`;
    badge.textContent = sig.label;
    badge.title = sig.tooltip;
    const name = document.createElement('span');
    name.className = 'name';
    name.textContent = entry.name;
    row.appendChild(cb);
    row.appendChild(badge);
    row.appendChild(name);
    if (entry.size_mb != null) {
      const size = document.createElement('span');
      size.className = 'size';
      size.textContent = `${entry.size_mb} MB`;
      row.appendChild(size);
    }
    return row;
  }

  function renderEntry(entry) {
    if (!entry.is_dir) return renderFileLeaf(entry);

    const item = document.createElement('details');
    item.dataset.path = entry.path;
    item.dataset.searchKey = entry.name.toLowerCase();
    const summary = document.createElement('summary');
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.checked = selectedPaths.has(entry.path);
    cb.addEventListener('click', (e) => e.stopPropagation());
    cb.addEventListener('change', () => {
      if (cb.checked) selectedPaths.add(entry.path);
      else selectedPaths.delete(entry.path);
      renderSelectedList();
    });
    cb.addEventListener('change', () => {
      // Cascade to children that are currently rendered. Children loaded
      // later (after an expand) won't auto-tick — that's intentional, so
      // ticking a parent doesn't silently include thousands of unseen files.
      const childCheckboxes = item.querySelectorAll('details input[type="checkbox"], .file-row input[type="checkbox"]');
      childCheckboxes.forEach((child) => {
        if (child === cb) return;
        if (child.checked !== cb.checked) {
          child.checked = cb.checked;
          child.dispatchEvent(new Event('change'));
        }
      });
    });
    summary.appendChild(cb);

    const name = document.createElement('span');
    name.className = 'name';
    name.textContent = entry.name;
    summary.appendChild(name);

    const badges = document.createElement('span');
    badges.className = 'badges';
    if (entry.video_count > 0) {
      const b = document.createElement('span');
      b.className = 'badge-vid';
      b.textContent = `${entry.video_count}v`;
      b.title = `${entry.video_count} video file${entry.video_count === 1 ? '' : 's'} in this folder`;
      badges.appendChild(b);
    }
    if (entry.srt_count > 0) {
      if (badges.children.length) badges.appendChild(document.createTextNode(' '));
      const b = document.createElement('span');
      b.className = 'badge-srt';
      b.textContent = `${entry.srt_count}srt`;
      b.title = `${entry.srt_count} existing .srt file${entry.srt_count === 1 ? '' : 's'} in this folder`;
      badges.appendChild(b);
    }
    summary.appendChild(badges);

    item.appendChild(summary);

    let loaded = false;
    item.addEventListener('toggle', async () => {
      if (!item.open || loaded) return;
      loaded = true;
      const inner = document.createElement('div');
      inner.textContent = 'loading…';
      inner.className = 'muted small';
      item.appendChild(inner);
      try {
        const data = await browse(entry.path);
        inner.innerHTML = '';
        inner.className = '';
        if (data.entries.length === 0) {
          const empty = document.createElement('div');
          empty.className = 'muted small';
          empty.textContent = '(empty)';
          inner.appendChild(empty);
        } else {
          for (const child of data.entries) inner.appendChild(renderEntry(child));
        }
        applyTreeFilter();
      } catch (e) {
        inner.innerHTML = '';
        inner.className = 'err small';
        inner.textContent = e.message;
      }
    });

    return item;
  }

  // ───── Tree filter ─────
  let treeFilter = '';
  function applyTreeFilter() {
    const f = treeFilter.toLowerCase();
    // Walk every node carrying a searchKey; hide rows that don't match and
    // (for dirs) don't have a matching descendant. Show ancestors of any
    // matching node so the path is preserved.
    const root = $('#tree-root');
    const nodes = root.querySelectorAll('[data-search-key]');
    if (!f) {
      nodes.forEach((n) => {
        n.classList.remove('hide-by-filter', 'match-by-filter');
      });
      return;
    }
    // Two passes: first mark direct matches, then propagate "visible" up
    // through ancestors. Anything not visible gets hidden.
    const visible = new Set();
    nodes.forEach((n) => {
      const direct = n.dataset.searchKey.includes(f);
      if (direct) {
        visible.add(n);
        // Mark ancestors visible.
        let p = n.parentElement;
        while (p && p !== root) {
          if (p.dataset && p.dataset.searchKey) visible.add(p);
          p = p.parentElement;
        }
        if (n.tagName === 'DETAILS') n.classList.add('match-by-filter');
      }
    });
    nodes.forEach((n) => {
      n.classList.toggle('hide-by-filter', !visible.has(n));
      if (visible.has(n) && n.tagName === 'DETAILS' && !n.dataset.searchKey.includes(f)) {
        // Show as ancestor — keep open so the match is reachable.
        n.open = true;
      }
    });
  }
  $('#tree-filter-input').addEventListener('input', (e) => {
    treeFilter = e.target.value.trim();
    applyTreeFilter();
  });
  $('#tree-clear-selection').addEventListener('click', () => {
    selectedPaths.clear();
    $$('.tree input[type="checkbox"]').forEach((cb) => (cb.checked = false));
    renderSelectedList();
  });

  async function loadRoot() {
    const root = $('#tree-root');
    root.textContent = 'loading…';
    try {
      const data = await browse('');
      root.innerHTML = '';
      if (data.entries.length === 0) {
        root.innerHTML = '<span class="muted">empty — check SUBARR_MEDIA_ROOT</span>';
        $('#tree-meta').textContent = '';
        return;
      }
      for (const entry of data.entries) root.appendChild(renderEntry(entry));
      const dirs = data.entries.filter((e) => e.is_dir).length;
      const files = data.entries.length - dirs;
      $('#tree-meta').textContent = `${dirs} folder${dirs === 1 ? '' : 's'}${files ? ` · ${files} file${files === 1 ? '' : 's'}` : ''}`;
      applyTreeFilter();
    } catch (e) {
      root.innerHTML = `<span class="err">${e.message}</span>`;
    }
  }
  $('#tree-refresh').addEventListener('click', loadRoot);

  function renderSelectedList() {
    const list = $('#scan-list');
    list.innerHTML = '';
    if (selectedPaths.size === 0) {
      const li = document.createElement('li');
      li.className = 'empty';
      li.textContent = 'No paths selected. Tick folders or files in the tree.';
      list.appendChild(li);
      $('#start-scan').disabled = true;
    } else {
      for (const p of selectedPaths) {
        const li = document.createElement('li');
        const span = document.createElement('span');
        span.className = 'path';
        span.textContent = p;
        const btn = document.createElement('button');
        btn.className = 'remove';
        btn.textContent = '✕';
        btn.addEventListener('click', () => {
          selectedPaths.delete(p);
          renderSelectedList();
          // Also uncheck the matching tree checkbox if visible.
          for (const cb of $$('.tree input[type="checkbox"]')) {
            const ent = cb.closest('details');
            if (ent && ent.querySelector('.name')?.textContent === p.split('/').pop()) {
              // best-effort — exact path match across multiple subtrees is racy
            }
          }
        });
        li.appendChild(span);
        li.appendChild(btn);
        list.appendChild(li);
      }
      $('#start-scan').disabled = false;
    }
    $('#queue-count').textContent = `${selectedPaths.size} path${selectedPaths.size === 1 ? '' : 's'}`;
  }
  renderSelectedList();

  // ───── Start scan + SSE progress ─────
  let activeScanSrc = null;
  function setActiveScanResults(scan) {
    const ul = $('#active-scan-results');
    ul.innerHTML = '';
    for (const r of scan.results) {
      const li = document.createElement('li');
      const status = document.createElement('span');
      status.className = `res-status ${r.status}`;
      status.textContent = r.status;
      // Distinct tooltip for skipped — subgen's reasoning is in r.error.
      if (r.status === 'skipped' && r.error) status.title = r.error;
      if (r.status === 'empty') status.title = 'subgen returned walked=0 — path resolved to no transcribable files';
      const path = document.createElement('span');
      path.textContent = r.path;
      li.appendChild(status);
      li.appendChild(path);
      // For skipped, surface a one-line reason inline so user doesn't have
      // to hover the badge to understand why nothing happened.
      if (r.status === 'skipped' && r.error) {
        const reason = document.createElement('span');
        reason.className = 'muted small';
        reason.textContent = ' — ' + r.error;
        li.appendChild(reason);
      }
      ul.appendChild(li);
    }
    if (scan.current_index < scan.paths.length && scan.status === 'running') {
      $('#active-scan-current').textContent = `running: ${scan.paths[scan.current_index]}`;
    } else if (scan.status === 'done') {
      $('#active-scan-current').textContent = `done · ${scan.paths.length} paths`;
    } else if (scan.status === 'error') {
      $('#active-scan-current').textContent = `error · see results`;
    }
  }

  async function startScanFlow() {
    const paths = [...selectedPaths];
    const reverse = $('#opt-reverse').checked;
    $('#start-scan').disabled = true;
    try {
      const r = await fetch('/api/scan', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ paths, reverse }),
      });
      if (!r.ok) {
        const body = await r.json().catch(() => ({}));
        throw new Error(body.detail || `scan failed: ${r.status}`);
      }
      const scan = await r.json();
      $('#active-scan').hidden = false;
      $('#active-scan-id').textContent = scan.id;
      $('#active-scan-current').textContent = 'starting…';
      if (activeScanSrc) activeScanSrc.close();
      activeScanSrc = new EventSource(`/api/scan/${scan.id}/events`);
      activeScanSrc.addEventListener('snapshot', (e) => setActiveScanResults(JSON.parse(e.data)));
      activeScanSrc.addEventListener('path_start', (e) => {
        const d = JSON.parse(e.data);
        $('#active-scan-current').textContent = `running [${d.index + 1}]: ${d.path}`;
      });
      activeScanSrc.addEventListener('path_done', async () => {
        const cur = await fetch(`/api/scan/${scan.id}`).then((r) => r.json());
        setActiveScanResults(cur);
      });
      activeScanSrc.addEventListener('done', (e) => {
        setActiveScanResults(JSON.parse(e.data));
        activeScanSrc.close();
        activeScanSrc = null;
        $('#start-scan').disabled = false;
      });
      activeScanSrc.addEventListener('error', () => {
        $('#start-scan').disabled = false;
      });
    } catch (e) {
      alert(e.message);
      $('#start-scan').disabled = false;
    }
  }
  $('#start-scan').addEventListener('click', startScanFlow);

  // ───── Logs SSE ─────
  let logsSrc = null;
  let logsPaused = false;
  let logFilter = '';
  const LOG_MAX = 2000;

  function classifyLog(line) {
    if (/ERROR|Failed|Traceback/.test(line)) return 'line-error';
    if (/WARN/.test(line)) return 'line-warn';
    if (/DEBUG/.test(line)) return 'line-debug';
    return 'line-info';
  }
  function startLogs() {
    if (logsSrc) return;
    $('#log-status').textContent = 'connecting…';
    logsSrc = new EventSource('/api/logs/events?tail=200');
    logsSrc.addEventListener('log', (e) => {
      if (logsPaused) return;
      const line = JSON.parse(e.data);
      if (logFilter && !line.toLowerCase().includes(logFilter)) return;
      const pre = $('#logs');
      const span = document.createElement('span');
      span.className = classifyLog(line);
      span.textContent = line + '\n';
      pre.appendChild(span);
      while (pre.childNodes.length > LOG_MAX) pre.removeChild(pre.firstChild);
      if ($('#log-autoscroll').checked) pre.scrollTop = pre.scrollHeight;
      $('#log-status').textContent = 'streaming';
    });
    logsSrc.addEventListener('error', () => {
      $('#log-status').textContent = 'error (will retry)';
    });
  }
  function stopLogs() {
    if (logsSrc) { logsSrc.close(); logsSrc = null; $('#log-status').textContent = 'disconnected'; }
  }
  $('#log-pause').addEventListener('click', () => {
    logsPaused = !logsPaused;
    $('#log-pause').textContent = logsPaused ? 'Resume' : 'Pause';
  });
  $('#log-clear').addEventListener('click', () => { $('#logs').innerHTML = ''; });
  $('#log-filter').addEventListener('input', (e) => { logFilter = e.target.value.toLowerCase(); });

  // ───── Monitor ─────
  function renderMonitor(g, c, q) {
    // GPU
    const gpuCard = $('#gpu-card');
    if (g?.online) {
      const memPct = g.memory.total_mib > 0 ? (g.memory.used_mib / g.memory.total_mib) * 100 : 0;
      gpuCard.innerHTML = `
        <div class="row"><span class="k">name</span><span class="v">${escape(g.name || '')}</span></div>
        <div class="row"><span class="k">vram</span><span class="v">${Math.round(g.memory.used_mib)} / ${Math.round(g.memory.total_mib)} MiB</span></div>
        <div class="bar"><div class="fill" style="width: ${memPct.toFixed(1)}%"></div></div>
        <div class="row"><span class="k">util</span><span class="v">${g.utilization.gpu_pct ?? '—'}%</span></div>
        <div class="row"><span class="k">temp</span><span class="v">${g.temperature_c ?? '—'} °C</span></div>
        <div class="row"><span class="k">power</span><span class="v">${g.power.draw_w ?? '—'} / ${g.power.limit_w ?? '—'} W</span></div>
        <div class="row"><span class="k">processes</span><span class="v">${g.processes.length} CUDA app(s)</span></div>
      `;
    } else {
      gpuCard.innerHTML = `<span class="muted">offline — ${escape(g?.error || '')}</span>`;
    }
    // Container
    const cCard = $('#container-card');
    if (c) {
      cCard.innerHTML = `
        <div class="row"><span class="k">name</span><span class="v">${escape(c.name || '')}</span></div>
        <div class="row"><span class="k">status</span><span class="v">${escape(c.status || '')}</span></div>
        <div class="row"><span class="k">started</span><span class="v">${escape(c.started_at || '')}</span></div>
        <div class="row"><span class="k">image</span><span class="v">${escape(c.image || '')}</span></div>
        <div class="row"><span class="k">id</span><span class="v">${escape(c.id_short || '')}</span></div>
      `;
    } else {
      cCard.innerHTML = `<span class="muted">docker unavailable</span>`;
    }
    // Queue (board layout, processing rows get progress bars)
    const qCard = $('#queue-card');
    const qProc = $('#q-processing');
    const qQueued = $('#q-queued');
    if (q) {
      qCard.innerHTML = `
        <div class="row"><span class="k">idle</span><span class="v">${q.idle ? 'yes' : 'no'}</span></div>
        <div class="row"><span class="k">queued</span><span class="v">${q.queued_count}</span></div>
        <div class="row"><span class="k">processing</span><span class="v">${q.processing_count}</span></div>
        <div class="row"><span class="k">version</span><span class="v">${escape(q.version || '')}</span></div>
      `;
      qProc.innerHTML = '';
      qQueued.innerHTML = '';
      if ((q.processing || []).length === 0) {
        qProc.innerHTML = '<li class="q-empty">idle</li>';
      } else {
        for (const t of q.processing) qProc.appendChild(renderQueueRow(t, 'processing'));
      }
      if ((q.queued || []).length === 0) {
        qQueued.innerHTML = '<li class="q-empty">empty</li>';
      } else {
        for (const t of q.queued) qQueued.appendChild(renderQueueRow(t, 'queued'));
      }
    } else {
      qCard.innerHTML = `<span class="muted">subgen unreachable</span>`;
      qProc.innerHTML = '';
      qQueued.innerHTML = '';
    }
  }

  function renderQueueRow(task, state) {
    const li = document.createElement('li');
    const basename = (task.path || '').split('/').pop() || task.path || '(unknown)';
    const head = document.createElement('div');
    head.className = 'q-row-head';
    head.innerHTML = `
      <span class="q-row-name" title="${escape(task.path || '')}">${escape(basename)}</span>
      <span class="q-row-type">${escape(task.type || '')}</span>
      <button class="q-cancel" disabled title="subgen v4.2 doesn't expose task cancel — needs a v4.3 patch">cancel</button>
    `;
    li.appendChild(head);
    if (state === 'processing' && task.progress) {
      const p = task.progress;
      const line = document.createElement('div');
      line.className = 'q-progress-line';
      line.innerHTML = `<span>${p.pct}%</span><span>${p.elapsed} elapsed · ${p.eta} eta · ${p.speed_s_per_s.toFixed(2)}s/s</span>`;
      li.appendChild(line);
      const bar = document.createElement('div');
      bar.className = 'q-progress-bar';
      bar.innerHTML = `<div class="fill" style="width: ${p.pct}%"></div>`;
      li.appendChild(bar);
    } else if (state === 'processing') {
      const line = document.createElement('div');
      line.className = 'q-progress-line';
      line.innerHTML = `<span class="muted">no progress data yet</span>`;
      li.appendChild(line);
    }
    return li;
  }
  function refreshMonitor() { pollHeader(); /* renderMonitor runs from pollHeader */ }

  // ───── Settings ─────
  async function loadSettings() {
    try {
      const data = await fetch('/api/mode').then((r) => r.json());
      $('#settings-compose-path').textContent = data.compose_path || '';
      const tbody = $('#kwargs-table tbody');
      tbody.innerHTML = '';
      for (const lk of data.per_language_kwargs || []) {
        const tr = document.createElement('tr');
        tr.innerHTML = `<td>${lk.code}</td><td>${escape(lk.parse_error ? lk.raw + '  /* ' + lk.parse_error + ' */' : JSON.stringify(lk.parsed))}</td>`;
        tbody.appendChild(tr);
      }
      $('#kwargs-top').textContent = data.top_level_kwargs
        ? JSON.stringify(data.top_level_kwargs, null, 2)
        : (data.top_level_raw || '(none)');
    } catch (e) {
      $('#settings-compose-path').textContent = `error: ${e.message}`;
    }
  }

  // ───── Activity tab ─────
  // Polls /api/provenance/recent on a 5s tick while the tab is active.
  // Tab badge is updated by the header poller independently so the count
  // is visible even when you're on another tab.
  let activityTimer = null;
  let activityStatusFilter = 'all';
  let activityRows = [];
  const activityExpanded = new Set();  // entry ids that have been expanded
  const activityScanCache = new Map(); // scan_id → fetched detail

  function startActivity() {
    loadActivity();
    if (!activityTimer) activityTimer = setInterval(loadActivity, 5000);
  }
  function stopActivity() {
    if (activityTimer) { clearInterval(activityTimer); activityTimer = null; }
  }
  function fmtAgo(ts) {
    if (!ts) return '—';
    const now = Date.now() / 1000;
    const s = Math.max(0, now - ts);
    if (s < 60) return `${Math.round(s)}s ago`;
    if (s < 3600) return `${Math.round(s / 60)}m ago`;
    if (s < 86400) return `${Math.round(s / 3600)}h ago`;
    return `${Math.round(s / 86400)}d ago`;
  }
  function rowStatus(entry) {
    if (entry.completed_at && entry.bazarr_scan_triggered_at) return { cls: 'bazarr', label: 'bazarr-notified' };
    if (entry.completed_at) return { cls: 'done', label: 'completed' };
    return { cls: 'pending', label: 'pending' };
  }
  function activityPasses(r) {
    if (activityStatusFilter !== 'all') {
      const s = rowStatus(r);
      if (s.cls !== activityStatusFilter) return false;
    }
    const filter = ($('#activity-filter')?.value || '').toLowerCase().trim();
    if (filter) {
      const hay = (r.canonical_path || '').toLowerCase();
      if (!hay.includes(filter)) return false;
    }
    return true;
  }

  function showFromPath(canonical) {
    const parts = (canonical || '').split('/').filter(Boolean);
    if (parts[0] === 'TV' && parts.length >= 2) return parts[1];
    if (parts[0] === 'Movies' && parts.length >= 2) return parts[1];
    return parts[0] || '(unknown)';
  }
  function seasonFromPath(canonical) {
    const parts = (canonical || '').split('/').filter(Boolean);
    if (parts[0] === 'TV' && parts.length >= 3) return parts[2];
    return null;
  }

  function renderActivityDetail(entry) {
    // Build the expanded detail block — fetches /api/scan/{id} lazily.
    const wrap = document.createElement('div');
    wrap.className = 'activity-detail';
    const series = entry.series_id != null ? entry.series_id : '—';
    const sonarrEp = entry.sonarr_episode_id != null ? entry.sonarr_episode_id : '—';
    const radarrMov = entry.radarr_movie_id != null ? entry.radarr_movie_id : '—';
    wrap.innerHTML = `
      <div class="kv-line"><span class="kv-k">canonical path</span><span class="kv-v">${escape(entry.canonical_path || '')}</span></div>
      <div class="kv-line"><span class="kv-k">scan_id</span><span class="kv-v">${escape(entry.scan_id || '')}</span></div>
      <div class="kv-line"><span class="kv-k">source</span><span class="kv-v">${escape(entry.source || '')}</span></div>
      <div class="kv-line"><span class="kv-k">queued_at</span><span class="kv-v">${entry.queued_at ? escape(new Date(entry.queued_at * 1000).toISOString()) : '—'}</span></div>
      <div class="kv-line"><span class="kv-k">completed_at</span><span class="kv-v">${entry.completed_at ? escape(new Date(entry.completed_at * 1000).toISOString()) : '—'}</span></div>
      <div class="kv-line"><span class="kv-k">bazarr_triggered_at</span><span class="kv-v">${entry.bazarr_scan_triggered_at ? escape(new Date(entry.bazarr_scan_triggered_at * 1000).toISOString()) : '—'}</span></div>
      <div class="kv-line"><span class="kv-k">sonarr_episode_id</span><span class="kv-v">${sonarrEp}</span></div>
      <div class="kv-line"><span class="kv-k">series_id</span><span class="kv-v">${series}</span></div>
      <div class="kv-line"><span class="kv-k">radarr_movie_id</span><span class="kv-v">${radarrMov}</span></div>
      <div class="kv-line"><span class="kv-k">subgen response</span><span class="kv-v" id="ad-scan-${entry.id}">loading…</span></div>
    `;
    // Lazy-fetch scan detail and inject. Cached so repeated expand is free.
    (async () => {
      const target = wrap.querySelector(`#ad-scan-${entry.id}`);
      if (!target) return;
      try {
        let detail = activityScanCache.get(entry.scan_id);
        if (!detail) {
          detail = await fetch(`/api/scan/${entry.scan_id}`).then((r) => r.ok ? r.json() : null);
          if (detail) activityScanCache.set(entry.scan_id, detail);
        }
        if (!detail) { target.textContent = '(scan record not found)'; return; }
        const ourPath = entry.canonical_path;
        const result = (detail.results || []).find((r) => r.path === ourPath) || (detail.results || [])[0];
        if (!result) { target.textContent = '(no matching result in scan)'; return; }
        const body = result.subgen_body || {};
        target.innerHTML = `
          <pre>status: ${escape(result.status)}
subgen_status_code: ${result.subgen_status_code}
walked: ${body.walked ?? '?'} · queued: ${body.queued ?? '?'} · skipped: ${body.skipped ?? '?'} · already_in_queue: ${body.already_in_queue ?? '?'} · no_audio: ${body.no_audio ?? '?'} · pending_language_detect: ${body.pending_language_detect ?? '?'}
${result.error ? 'note: ' + escape(result.error) : ''}</pre>
        `;
      } catch (e) {
        target.textContent = 'error: ' + e.message;
      }
    })();
    return wrap;
  }

  function activityRowEl(entry) {
    const row = document.createElement('div');
    row.className = 'activity-row';
    row.dataset.entryId = entry.id;
    const s = rowStatus(entry);
    const fileBase = (entry.canonical_path || '').split('/').pop() || entry.canonical_path || '?';
    const bazarrCell = entry.bazarr_scan_triggered_at
      ? `notified ${escape(fmtAgo(entry.bazarr_scan_triggered_at))}`
      : (entry.series_id ? 'awaiting completion' : 'n/a');
    row.innerHTML = `
      <span class="ar-status"><span class="activity-status ${s.cls}">${s.label}</span></span>
      <span class="ar-time" title="${escape(new Date(entry.queued_at * 1000).toLocaleString())}">${escape(fmtAgo(entry.queued_at))}</span>
      <span class="ar-file" title="${escape(entry.canonical_path)}">${escape(fileBase)}</span>
      <span class="ar-time">${entry.completed_at ? escape(fmtAgo(entry.completed_at)) : '—'}</span>
      <span class="ar-bazarr">${bazarrCell}</span>
      <span class="ar-id">${escape((entry.scan_id || '').slice(0, 8))}</span>
    `;
    row.addEventListener('click', (ev) => {
      ev.stopPropagation();
      const isExpanded = activityExpanded.has(entry.id);
      const next = row.nextElementSibling;
      if (isExpanded && next && next.classList.contains('activity-detail')) {
        next.remove();
        activityExpanded.delete(entry.id);
      } else {
        const detail = renderActivityDetail(entry);
        row.parentNode.insertBefore(detail, row.nextSibling);
        activityExpanded.add(entry.id);
      }
    });
    return row;
  }

  function renderActivityTree(rows) {
    const root = $('#activity-tree');
    root.innerHTML = '';
    if (rows.length === 0) {
      root.innerHTML = '<p class="muted">no rows match — try adjusting the status chips or filter</p>';
      return;
    }
    // Group by show (TV/<show> or Movies/<title>); then by season for TV.
    const byShow = new Map();
    for (const r of rows) {
      const show = showFromPath(r.canonical_path);
      let entry = byShow.get(show);
      if (!entry) { entry = { items: [], seasons: new Map() }; byShow.set(show, entry); }
      entry.items.push(r);
      const season = seasonFromPath(r.canonical_path) || '(files)';
      let bucket = entry.seasons.get(season);
      if (!bucket) { bucket = []; entry.seasons.set(season, bucket); }
      bucket.push(r);
    }
    // Sort shows by most-recent-queued
    const sorted = [...byShow.entries()].sort((a, b) => {
      const aMax = Math.max(...a[1].items.map((x) => x.queued_at || 0));
      const bMax = Math.max(...b[1].items.map((x) => x.queued_at || 0));
      return bMax - aMax;
    });
    for (const [show, grp] of sorted) {
      const showDet = document.createElement('details');
      showDet.open = true; // default open since we're already filtered down
      const sum = document.createElement('summary');
      const pending = grp.items.filter((x) => !x.completed_at).length;
      const done = grp.items.length - pending;
      sum.innerHTML = `
        <span class="lvl-name"><strong>${escape(show)}</strong></span>
        <span class="lvl-meta">${grp.items.length} · ${pending} pending · ${done} done</span>
      `;
      showDet.appendChild(sum);
      const seasonsSorted = [...grp.seasons.entries()].sort((a, b) => {
        const am = (a[0].match(/(\d+)/) || [])[1];
        const bm = (b[0].match(/(\d+)/) || [])[1];
        if (am && bm) return Number(am) - Number(bm);
        return a[0].localeCompare(b[0]);
      });
      for (const [season, items] of seasonsSorted) {
        items.sort((a, b) => (b.queued_at || 0) - (a.queued_at || 0));
        if (seasonsSorted.length === 1 && season === '(files)') {
          for (const it of items) showDet.appendChild(activityRowEl(it));
        } else {
          const seasonDet = document.createElement('details');
          seasonDet.open = true;
          const seasonSum = document.createElement('summary');
          const sPending = items.filter((x) => !x.completed_at).length;
          seasonSum.innerHTML = `
            <span class="lvl-name">${escape(season)}</span>
            <span class="lvl-meta">${items.length} · ${sPending} pending</span>
          `;
          seasonDet.appendChild(seasonSum);
          for (const it of items) seasonDet.appendChild(activityRowEl(it));
          showDet.appendChild(seasonDet);
        }
      }
      root.appendChild(showDet);
    }
  }

  function renderActivityFlat(rows) {
    const tbody = $('#activity-table tbody');
    tbody.innerHTML = '';
    if (rows.length === 0) {
      tbody.innerHTML = `<tr class="empty-row"><td colspan="6">no rows match — try adjusting the status chips or filter</td></tr>`;
      return;
    }
    for (const r of rows) {
      const s = rowStatus(r);
      const tr = document.createElement('tr');
      tr.style.cursor = 'pointer';
      const fileBase = (r.canonical_path || '').split('/').pop() || r.canonical_path || '?';
      const bazarrCell = r.bazarr_scan_triggered_at
        ? `<span class="activity-cell-time" title="${escape(new Date(r.bazarr_scan_triggered_at * 1000).toLocaleString())}">notified ${escape(fmtAgo(r.bazarr_scan_triggered_at))}</span>`
        : (r.series_id ? '<span class="muted">awaiting completion</span>' : '<span class="muted">n/a</span>');
      tr.innerHTML = `
        <td><span class="activity-status ${s.cls}">${s.label}</span></td>
        <td class="activity-cell-time" title="${escape(new Date(r.queued_at * 1000).toLocaleString())}">${escape(fmtAgo(r.queued_at))}</td>
        <td class="activity-cell-path" title="${escape(r.canonical_path)}">${escape(fileBase)}</td>
        <td class="activity-cell-time">${r.completed_at ? escape(fmtAgo(r.completed_at)) : '—'}</td>
        <td>${bazarrCell}</td>
        <td class="activity-cell-id">${escape(r.scan_id || '')}</td>
      `;
      tr.addEventListener('click', () => {
        const next = tr.nextElementSibling;
        if (next && next.classList.contains('activity-detail-row')) {
          next.remove();
        } else {
          const detailTr = document.createElement('tr');
          detailTr.className = 'activity-detail-row';
          const td = document.createElement('td');
          td.colSpan = 6;
          td.appendChild(renderActivityDetail(r));
          detailTr.appendChild(td);
          tr.parentNode.insertBefore(detailTr, tr.nextSibling);
        }
      });
      tbody.appendChild(tr);
    }
  }

  async function loadActivity() {
    const meta = $('#activity-meta');
    try {
      const data = await fetch('/api/provenance/recent').then((r) => r.json());
      activityRows = data.entries || [];
      const shown = activityRows.filter(activityPasses);
      const grouped = $('#activity-group')?.checked;
      if (grouped) {
        $('#activity-tree').hidden = false;
        $('#activity-table').hidden = true;
        renderActivityTree(shown);
      } else {
        $('#activity-tree').hidden = true;
        $('#activity-table').hidden = false;
        renderActivityFlat(shown);
      }
      const pending = activityRows.filter((r) => !r.completed_at).length;
      const completed = activityRows.length - pending;
      const bazarrFired = activityRows.filter((r) => r.bazarr_scan_triggered_at).length;
      meta.textContent = `${shown.length} shown of ${activityRows.length} recent · ${pending} pending · ${completed} completed · ${bazarrFired} bazarr-notified · last 50 entries`;
      updateActivityTabBadge(pending);
    } catch (e) {
      meta.textContent = `error: ${e.message}`;
    }
  }

  function updateActivityTabBadge(pendingCount) {
    const badge = $('#activity-tab-badge');
    if (!badge) return;
    if (pendingCount > 0) {
      badge.hidden = false;
      badge.textContent = String(pendingCount);
    } else {
      badge.hidden = true;
    }
  }

  $('#activity-refresh')?.addEventListener('click', loadActivity);
  $('#activity-group')?.addEventListener('change', loadActivity);
  $('#activity-filter')?.addEventListener('input', loadActivity);
  // Status chip switcher
  document.addEventListener('click', (ev) => {
    const chip = ev.target.closest('#activity-status-chips .chip');
    if (!chip) return;
    $$('#activity-status-chips .chip').forEach((c) => c.classList.toggle('active', c === chip));
    activityStatusFilter = chip.dataset.status;
    loadActivity();
  });

  // Keep the tab badge fresh even when user is on another tab — piggyback
  // on the header 2s poll. Activity panel itself uses its own 5s loop while
  // the tab is active.
  async function pollActivityBadge() {
    try {
      const data = await fetch('/api/provenance/recent').then((r) => r.ok ? r.json() : null);
      if (!data) return;
      const pending = (data.entries || []).filter((r) => !r.completed_at).length;
      updateActivityTabBadge(pending);
    } catch {}
  }
  setInterval(pollActivityBadge, 10000);
  pollActivityBadge();

  // ───── Pending approvals (manual_confirm mode) ─────
  let pendingTimer = null;
  function startPendingPoll() {
    // Pending walk lists need to stay fresh while user is in Settings,
    // because the scheduler can create new walks mid-session. 8s tick.
    loadPending();
    if (!pendingTimer) pendingTimer = setInterval(loadPending, 8000);
  }
  function stopPendingPoll() {
    if (pendingTimer) { clearInterval(pendingTimer); pendingTimer = null; }
  }
  async function loadPending() {
    try {
      const data = await fetch('/api/schedule/pending?status=pending').then((r) => r.json());
      const list = $('#pending-walks-list');
      const meta = $('#pending-meta');
      list.innerHTML = '';
      if (!data.walks || data.walks.length === 0) {
        meta.textContent = 'No pending walks. (manual_confirm mode populates this when the scheduler fires.)';
        return;
      }
      meta.textContent = `${data.pending_count} walk${data.pending_count === 1 ? '' : 's'} awaiting your review.`;
      for (const w of data.walks) {
        const wrap = document.createElement('div');
        wrap.className = 'pending-walk';
        const when = w.created_at ? new Date(w.created_at * 1000).toLocaleString() : '?';
        wrap.innerHTML = `
          <div class="pw-head">
            <span><strong>${w.decisions_total}</strong> matching · ${w.considered} considered · ${escape(when)}</span>
            <div class="pw-actions">
              <button class="primary" data-walk="${w.id}" data-act="approve-selected">Approve selected</button>
              <button class="ghost" data-walk="${w.id}" data-act="approve-all">Approve all</button>
              <button class="ghost" data-walk="${w.id}" data-act="reject-all">Reject all</button>
            </div>
          </div>
          <div class="pw-decisions" id="pw-${w.id}-decisions"></div>
        `;
        list.appendChild(wrap);
        const decBox = $('#pw-' + w.id + '-decisions');
        for (const d of w.decisions || []) {
          if (d.approved !== null) continue; // already decided
          const item = d.item || {};
          const cell = document.createElement('div');
          cell.className = 'pw-decision';
          const ep = (item.episode_number ? item.episode_number + ' ' : '') + (item.title || '?');
          cell.innerHTML = `
            <input type="checkbox" data-decision-id="${d.id}" checked>
            <label>[${item.score ?? 0}] ${escape(ep)} <span class="muted">${escape(item.canonical_path || '')}</span></label>
          `;
          decBox.appendChild(cell);
        }
      }
    } catch (e) {
      $('#pending-meta').textContent = 'error: ' + e.message;
    }
  }

  document.addEventListener('click', async (ev) => {
    const btn = ev.target.closest('.pw-actions button');
    if (!btn) return;
    const walkId = btn.dataset.walk;
    const act = btn.dataset.act;
    if (!walkId || !act) return;
    btn.disabled = true;
    try {
      let url, body;
      if (act === 'approve-all') {
        url = `/api/schedule/pending/${walkId}/approve`;
        body = { decision_ids: null };
      } else if (act === 'reject-all') {
        url = `/api/schedule/pending/${walkId}/reject`;
        body = { decision_ids: null };
      } else if (act === 'approve-selected') {
        const ids = $$('#pw-' + walkId + '-decisions input[type="checkbox"]:checked')
          .map((cb) => Number(cb.dataset.decisionId));
        if (ids.length === 0) {
          alert('No rows selected.');
          btn.disabled = false;
          return;
        }
        url = `/api/schedule/pending/${walkId}/approve`;
        body = { decision_ids: ids };
      }
      const r = await fetch(url, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(body),
      });
      const data = await r.json();
      if (!r.ok) throw new Error(data.error || data.detail || `HTTP ${r.status}`);
      await loadPending();
    } catch (e) {
      alert('failed: ' + e.message);
    } finally {
      btn.disabled = false;
    }
  });

  // ───── Probe walks ─────
  async function loadProbeWalks() {
    try {
      const data = await fetch('/api/probe/walks').then((r) => r.json());
      const list = $('#probe-walks-list');
      list.innerHTML = '';
      if (!data.walks || data.walks.length === 0) {
        list.innerHTML = '<span class="muted">No walks yet. Enter a path above and click Start walk.</span>';
        return;
      }
      for (const w of data.walks.slice().reverse()) {
        const row = document.createElement('div');
        row.className = 'probe-walk-row';
        const pct = w.total_files > 0 ? Math.round((w.processed / w.total_files) * 100) : 0;
        row.innerHTML = `
          <div class="pw-head">
            <span><span class="pw-status-${w.status}">${escape(w.status)}</span> · ${escape(w.root)}</span>
            <span>${w.processed}/${w.total_files} files · ${w.probed} probed · ${w.cached_hits} cached · ${w.errors.length} err</span>
          </div>
          <div class="pw-bar"><div class="pw-fill" style="width: ${pct}%"></div></div>
        `;
        list.appendChild(row);
      }
    } catch (e) {
      $('#probe-walks-list').textContent = 'error: ' + e.message;
    }
  }

  $('#probe-walk-start').addEventListener('click', async () => {
    const path = $('#probe-walk-path').value.trim();
    if (!path) { alert('Enter a canonical path (e.g. TV/Foo Bar)'); return; }
    try {
      const r = await fetch('/api/probe/walk', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ path }),
      });
      const body = await r.json();
      if (!r.ok) throw new Error(body.detail || `HTTP ${r.status}`);
      // Subscribe to SSE for live updates.
      const src = new EventSource(`/api/probe/walk/${body.id}/events`);
      src.addEventListener('snapshot', () => loadProbeWalks());
      src.addEventListener('progress', () => loadProbeWalks());
      src.addEventListener('done', () => { loadProbeWalks(); src.close(); });
      src.addEventListener('error', () => { loadProbeWalks(); });
      src.addEventListener('cancelled', () => { loadProbeWalks(); src.close(); });
      loadProbeWalks();
    } catch (e) {
      alert('walk failed: ' + e.message);
    }
  });

  // ───── Schedule + Auto-queue rules ─────
  function listOrEmpty(s) {
    if (!s) return [];
    return s.split(',').map((x) => x.trim()).filter(Boolean);
  }
  function fmtTs(ts) {
    if (!ts) return '—';
    try { return new Date(ts * 1000).toLocaleString(); } catch { return '—'; }
  }
  async function loadSchedule() {
    try {
      const data = await fetch('/api/schedule').then((r) => r.json());
      const sched = (data.schedules || []).find((s) => s.name === 'coverage_walk');
      if (sched) {
        $('#sched-enabled').checked = sched.enabled;
        $('#sched-kind').value = sched.kind;
        $('#sched-hhmm').value = sched.daily_hhmm || '03:00';
        $('#sched-dow').value = String(sched.day_of_week ?? 0);
        $('#sched-interval').value = String(sched.interval_minutes ?? 360);
        $('#sched-probe-roots').value = (sched.probe_roots || []).join(', ');
        applyKindVisibility();
        $('#sched-meta').innerHTML =
          `last run: ${escape(fmtTs(sched.last_run_at))} · ` +
          `next: ${escape(fmtTs(sched.next_run_at))} · ` +
          `last result: <code>${escape(sched.last_result || '—')}</code>`;
      }
      const rules = data.rules || {};
      $('#rule-mode').value = rules.mode || 'dashboard';
      $('#rule-min-score').value = rules.min_score ?? 200;
      $('#rule-max-per-run').value = rules.max_per_run ?? 50;
      $('#rule-require-monitored').checked = !!rules.require_monitored;
      $('#rule-skip-stale').checked = !!rules.skip_stale_disk;
      const skipEmb = $('#rule-skip-embedded');
      if (skipEmb) skipEmb.checked = rules.skip_embedded_en !== false;
      $('#rule-allow-langs').value = (rules.allow_languages || []).join(', ');
      $('#rule-deny-langs').value = (rules.deny_languages || []).join(', ');
    } catch (e) {
      $('#sched-meta').textContent = `error: ${e.message}`;
    }
  }
  function applyKindVisibility() {
    const k = $('#sched-kind').value;
    $('#sched-hhmm').hidden = k === 'interval';
    $('#sched-dow').hidden = k !== 'weekly';
    $('#sched-interval').hidden = k !== 'interval';
  }
  $('#sched-kind').addEventListener('change', applyKindVisibility);

  $('#sched-save').addEventListener('click', async () => {
    const body = {
      enabled: $('#sched-enabled').checked,
      kind: $('#sched-kind').value,
      daily_hhmm: $('#sched-hhmm').value || '03:00',
      day_of_week: Number($('#sched-dow').value || 0),
      interval_minutes: Number($('#sched-interval').value || 360),
      probe_roots: $('#sched-probe-roots').value || '',
    };
    const r = await fetch('/api/schedule/coverage_walk', {
      method: 'PATCH',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!r.ok) { $('#sched-meta').textContent = 'save failed: ' + (await r.text()); return; }
    await loadSchedule();
  });

  $('#sched-run-now').addEventListener('click', async () => {
    $('#sched-meta').textContent = 'running coverage_walk…';
    try {
      const r = await fetch('/api/schedule/coverage_walk/run-now', { method: 'POST' });
      const body = await r.json();
      $('#sched-meta').textContent = `result: ${JSON.stringify(body)}`;
    } catch (e) {
      $('#sched-meta').textContent = 'error: ' + e.message;
    }
    await loadSchedule();
  });

  $('#rules-save').addEventListener('click', async () => {
    const body = {
      mode: $('#rule-mode').value,
      min_score: Number($('#rule-min-score').value),
      max_per_run: Number($('#rule-max-per-run').value),
      require_monitored: $('#rule-require-monitored').checked,
      skip_stale_disk: $('#rule-skip-stale').checked,
      skip_embedded_en: $('#rule-skip-embedded')?.checked ?? true,
      allow_languages: listOrEmpty($('#rule-allow-langs').value),
      deny_languages: listOrEmpty($('#rule-deny-langs').value),
    };
    const r = await fetch('/api/schedule/rules', {
      method: 'PUT',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!r.ok) { alert('save rules failed: ' + (await r.text())); return; }
    await loadSchedule();
  });

  $('#rules-preview').addEventListener('click', async () => {
    const out = $('#rules-preview-out');
    out.hidden = false;
    out.textContent = 'evaluating…';
    try {
      const r = await fetch('/api/schedule/preview', { method: 'POST' });
      const body = await r.json();
      out.textContent = JSON.stringify({
        considered: body.considered,
        would_queue: body.would_queue,
        would_skip: body.would_skip,
        queue_preview_first_10: body.queue_preview.slice(0, 10),
      }, null, 2);
    } catch (e) {
      out.textContent = 'error: ' + e.message;
    }
  });

  $('#btn-restart').addEventListener('click', async () => {
    if (!confirm('Restart the subgen container? In-flight transcribes will be lost.')) return;
    $('#action-result').textContent = 'restarting…';
    try {
      const r = await fetch('/api/restart', { method: 'POST' });
      const body = await r.json();
      if (!r.ok) throw new Error(body.detail || `restart failed: ${r.status}`);
      $('#action-result').textContent = `restarted: ${body.container?.id_short || '?'}`;
    } catch (e) {
      $('#action-result').textContent = `restart failed: ${e.message}`;
    }
  });
  $('#btn-plex-scan').addEventListener('click', async () => {
    $('#action-result').textContent = 'triggering plex scan…';
    try {
      const r = await fetch('/api/plex/scan', { method: 'POST' });
      const body = await r.json();
      if (!r.ok) throw new Error(body.detail || `plex scan failed: ${r.status}`);
      $('#action-result').textContent = `plex scan triggered (section: ${body.section})`;
    } catch (e) {
      $('#action-result').textContent = `plex scan failed: ${e.message}`;
    }
  });

  // ───── Integrations health (Settings tab) ─────
  async function loadIntegrations() {
    const grid = $('#integrations-grid');
    grid.textContent = 'loading…';
    try {
      const [data, ollama] = await Promise.all([
        fetch('/api/integrations/health').then((r) => r.json()),
        fetch('/api/enrichment/health').then((r) => r.json()).catch(() => null),
      ]);
      grid.innerHTML = '';
      const allInts = [...(data.integrations || [])];
      if (ollama) allInts.push({
        name: 'ollama',
        online: ollama.online,
        configured: ollama.configured,
        version: ollama.model + (ollama.model_available ? ' ✓' : ' (model missing)'),
        error: ollama.error,
      });
      for (const it of allInts) {
        const cell = document.createElement('div');
        cell.className = 'integration';
        const dotCls = !it.configured ? 'unconf' : (it.online ? 'up' : 'down');
        const statusLabel = !it.configured ? 'not configured' : (it.online ? 'online' : 'offline');
        let metaHtml = `<div class="meta">${escape(statusLabel)}${it.version ? ' · ' + escape(it.version) : ''}</div>`;
        if (it.badges) {
          const b = it.badges;
          metaHtml += `<div class="meta">eps wanted: ${b.episodes ?? '?'} · movies: ${b.movies ?? '?'} · providers: ${b.providers ?? '?'}</div>`;
        }
        if (it.error) {
          metaHtml += `<div class="err">${escape(it.error)}</div>`;
        }
        cell.innerHTML = `<div class="name"><span class="dot ${dotCls}"></span>${escape(it.name)}</div>${metaHtml}`;
        grid.appendChild(cell);
      }
    } catch (e) {
      grid.innerHTML = `<span class="err">${escape(e.message)}</span>`;
    }
  }

  // ───── Coverage tab ─────
  let coverageRaw = null;

  // Track rows queued in this browser across page reloads. Keyed by:
  //   ep:<sonarr_episode_id>  for episodes
  //   mv:<canonical_path>     for movies
  // Value: { scan_id, queued_at_ms }. We forget entries older than 24h
  // to avoid stale-state confusion across days.
  const COV_QUEUED_KEY = 'subarr.coverage.queued.v1';
  function loadQueuedMap() {
    try {
      const raw = JSON.parse(localStorage.getItem(COV_QUEUED_KEY) || '{}');
      const now = Date.now();
      const fresh = {};
      for (const [k, v] of Object.entries(raw)) {
        if (v && v.queued_at_ms && (now - v.queued_at_ms) < 24 * 3600 * 1000) fresh[k] = v;
      }
      return fresh;
    } catch { return {}; }
  }
  function saveQueuedMap(m) {
    try { localStorage.setItem(COV_QUEUED_KEY, JSON.stringify(m)); } catch {}
  }
  function coverageRowKey(item) {
    const eid = item?.bazarr?.episode_id;
    if (eid) return `ep:${eid}`;
    if (item?.canonical_path) return `mv:${item.canonical_path}`;
    return null;
  }
  // Tracks per-row episode_id when user ticks a Coverage tree checkbox.
  // Cleared every renderCoverage(). Bulk Queue button drains this.
  const covSelectedEps = new Set();
  const covSelectedManualPaths = new Map();  // ep_id (or '__path:X') → {episode_id, canonical_path, title}

  function updateBulkQueueBtn() {
    const btn = $('#cov-queue-selected');
    if (!btn) return;
    const n = covSelectedManualPaths.size;
    btn.textContent = `Queue selected (${n})`;
    btn.disabled = n === 0;
  }

  function _epRowKey(item) {
    return coverageRowKey(item) || (item.canonical_path ? `mv:${item.canonical_path}` : null);
  }

  function renderCoverage() {
    if (!coverageRaw) return;
    covSelectedEps.clear();
    covSelectedManualPaths.clear();
    updateBulkQueueBtn();
    if ($('#cov-group')?.checked) {
      $('#cov-table').hidden = true;
      $('#cov-tree').hidden = false;
      renderCoverageTree();
      return;
    }
    $('#cov-table').hidden = false;
    $('#cov-tree').hidden = true;
    const tbody = $('#cov-table tbody');
    tbody.innerHTML = '';
    var covQueuedMap = loadQueuedMap();
    // Server already filtered hide_stale_disk + hide_embedded_en. Frontend
    // hide-stale logic dropped; the toggle is now show-stale and flips the
    // server param instead. Keep the flat-table render unchanged otherwise.
    const hideStale = $('#cov-hide-stale').checked;
    const filter = ($('#cov-filter').value || '').toLowerCase().trim();
    let shown = 0;
    for (const item of coverageRaw.items) {
      if (hideStale && item.has_sub_on_disk) continue;
      if (filter) {
        const hay = [item.title, item.episode_title, item.original_language, ...(item.tags || [])]
          .filter(Boolean).join(' ').toLowerCase();
        if (!hay.includes(filter)) continue;
      }
      shown++;
      const tr = document.createElement('tr');
      if (item.has_sub_on_disk) tr.classList.add('row-stale');
      const scoreCls = item.score >= 1000 ? 'high' : item.score >= 200 ? 'mid' : item.score < 0 ? 'neg' : 'low';
      const rowKey = coverageRowKey(item);
      const isAlreadyQueued = rowKey && (covQueuedMap[rowKey] !== undefined);
      if (isAlreadyQueued) tr.classList.add('row-queued');
      const canQueue = !!(item.bazarr?.episode_id) || !!item.canonical_path;
      let queueBtnLabel;
      let queueBtnTitle;
      if (isAlreadyQueued) {
        queueBtnLabel = '✓ queued';
        queueBtnTitle = `scan_id ${covQueuedMap[rowKey].scan_id} — queued earlier in this browser`;
      } else if (item.has_sub_on_disk) {
        queueBtnLabel = 'Re-scan';
        queueBtnTitle = 'Re-queue this file (existing .srt will be overwritten by subgen)';
      } else {
        queueBtnLabel = '→ Queue';
        queueBtnTitle = item.bazarr?.episode_id
          ? `Resolve sonarr episode ${item.bazarr.episode_id} → single .mkv and send to subgen`
          : 'Send this row to subgen (path-level)';
      }
      if (!canQueue) queueBtnTitle = 'No sonarr episode id or canonical path — nothing to queue';
      tr.dataset.rowKey = rowKey || '';
      // Embedded badge column — uses the unified coverageBadgeHtml helper,
      // plus the →Bazarr action when a probe-confirmed EN row needs Bazarr's
      // wanted list refreshed, and a 'probe' fallback when nothing's cached.
      const filePath = item.file_canonical_path || item.canonical_path || '';
      let embeddedHtml;
      if (item.embedded_en) {
        embeddedHtml = coverageBadgeHtml(item.embedded_en, false);
        if (item.suggest_bazarr_rescan) {
          embeddedHtml += `<button class="ghost small bazarr-resync-btn" data-series-id="${item.bazarr?.sonarr_id ?? ''}" data-canonical="${escape(filePath)}" title="Tell Bazarr to rescan disk — it missed this English track">→Bazarr</button>`;
        }
      } else {
        embeddedHtml = `<button class="ghost small probe-btn" data-canonical="${escape(filePath)}" title="Run ffprobe now (single file)">probe</button>`;
      }
      const audioHtml = (item.audio_langs && item.audio_langs.length)
        ? `<span class="audio-langs" title="audio language tags">${escape(item.audio_langs.join(','))}</span>`
        : '<span class="audio-langs">—</span>';
      tr.innerHTML = `
        <td class="score ${scoreCls}" title="${escape((item.score_reasons || []).join(' · '))}">${item.score}</td>
        <td>${escape(item.media_type)}</td>
        <td>${escape(item.title)}</td>
        <td>${escape(item.episode_number ? item.episode_number + ' ' : '')}${escape(item.episode_title || '')}</td>
        <td class="lang">${item.original_language ? escape(item.original_language) : `<button class="ghost small lang-enrich-btn" data-row-key="${escape(rowKey || '')}" data-canonical="${escape(item.canonical_path || '')}" data-title="${escape(item.title || '')}" title="Ask ollama to infer original language">? enrich</button>`}</td>
        <td>${item.monitored === null ? '—' : (item.monitored ? '✓' : '✗')}</td>
        <td class="${item.has_sub_on_disk ? 'disk-srt-yes' : 'disk-srt-no'}" title="${item.has_sub_on_disk ? 'Disk has .srt: Bazarr view is stale. Re-scan only if the existing sub is bad.' : ''}">${item.has_sub_on_disk ? '!' : '—'}</td>
        <td>${embeddedHtml}</td>
        <td>${audioHtml}</td>
        <td>${escape((item.bazarr?.missing_subtitles || []).join(', '))}</td>
        <td>${escape((item.tags || []).join(', '))}</td>
        <td><button class="ghost small cov-queue-btn ${isAlreadyQueued ? 'queued' : ''}"
                   data-episode-id="${item.bazarr?.episode_id ?? ''}"
                   data-canonical="${escape(item.canonical_path || '')}"
                   data-row-key="${escape(rowKey || '')}"
                   title="${escape(queueBtnTitle)}" ${(canQueue && !isAlreadyQueued) ? '' : 'disabled'}>${queueBtnLabel}</button></td>
      `;
      tbody.appendChild(tr);
    }
    if (shown === 0) {
      tbody.innerHTML = `<tr class="empty-row"><td colspan="9">no rows match (or coverage list is empty)</td></tr>`;
    }
    const t = coverageRaw.totals || {};
    const ts = coverageRaw.generated_at ? new Date(coverageRaw.generated_at * 1000).toLocaleTimeString() : '?';
    $('#coverage-meta').textContent = `${shown} shown · ${t.items} total (${t.episodes} ep + ${t.movies} mv) · ${t.with_disk_sub} with .srt on disk · generated ${ts}${coverageRaw.cached ? ` (cached ${coverageRaw.cache_age_s}s)` : ''}`;
  }

  function renderCoverageSources() {
    const wrap = $('#cov-sources');
    wrap.innerHTML = '';
    const s = coverageRaw?.sources || {};
    for (const [name, info] of Object.entries(s)) {
      const span = document.createElement('span');
      const cls = !info.configured ? 'unconf' : (info.ok ? 'ok' : 'err');
      span.className = `src ${cls}`;
      let label = `${name}: `;
      if (!info.configured) label += 'unconfigured';
      else if (info.ok) {
        const counts = [];
        if ('count' in info) counts.push(`${info.count} rows`);
        if ('episodes_wanted' in info) counts.push(`${info.episodes_wanted} eps wanted`);
        if ('movies_wanted' in info) counts.push(`${info.movies_wanted} movies wanted`);
        if ('history_rows' in info) counts.push(`${info.history_rows} history rows`);
        label += counts.length ? counts.join(', ') : 'ok';
      } else label += info.error || 'error';
      span.textContent = label;
      wrap.appendChild(span);
    }
  }

  async function loadCoverage(fresh = false) {
    const meta = $('#coverage-meta');
    meta.textContent = fresh ? 'refreshing (this can take ~10s on first run)…' : 'loading…';
    try {
      const useTautulli = $('#cov-tautulli').checked;
      const showSuppressed = $('#cov-show-suppressed')?.checked;
      const showStale = $('#cov-show-stale')?.checked;
      const hideEmbedded = showSuppressed ? 'false' : 'true';
      const hideStale = showStale ? 'false' : 'true';
      const url = `/api/coverage?tautulli=${useTautulli}&hide_embedded_en=${hideEmbedded}&hide_stale_disk=${hideStale}${fresh ? '&fresh=true' : ''}`;
      const r = await fetch(url);
      if (!r.ok) {
        const body = await r.json().catch(() => ({}));
        throw new Error(body.detail || `coverage failed: ${r.status}`);
      }
      coverageRaw = await r.json();
      renderCoverageSources();
      renderCoverage();
    } catch (e) {
      meta.textContent = `error: ${e.message}`;
    }
  }
  $('#cov-refresh').addEventListener('click', () => loadCoverage(true));
  $('#cov-tautulli').addEventListener('change', () => loadCoverage(true));
  $('#cov-show-suppressed').addEventListener('change', () => loadCoverage(true));
  $('#cov-show-stale').addEventListener('change', () => loadCoverage(true));
  $('#cov-filter').addEventListener('input', renderCoverage);
  $('#cov-group')?.addEventListener('change', renderCoverage);

  function scoreCss(score) {
    return score >= 1000 ? 'high' : score >= 200 ? 'mid' : score < 0 ? 'neg' : 'low';
  }

  // Group flat item list by show title → season number → episodes.
  function groupItemsByShow(items) {
    const tree = new Map();  // title → { items: [...], seasons: Map<seasonNum, [eps]> }
    for (const it of items) {
      if (it.media_type !== 'episode') continue;
      const title = it.title || '(unknown)';
      let entry = tree.get(title);
      if (!entry) {
        entry = { title, items: [], seasons: new Map() };
        tree.set(title, entry);
      }
      entry.items.push(it);
      let season = '?';
      if (it.episode_number && it.episode_number.includes('x')) {
        season = it.episode_number.split('x')[0];
      }
      let bucket = entry.seasons.get(season);
      if (!bucket) {
        bucket = [];
        entry.seasons.set(season, bucket);
      }
      bucket.push(it);
    }
    // Sort each show's seasons numerically, episodes numerically.
    for (const entry of tree.values()) {
      entry.seasonsSorted = [...entry.seasons.entries()].sort((a, b) => Number(a[0]) - Number(b[0]));
      for (const [, eps] of entry.seasonsSorted) {
        eps.sort((a, b) => {
          const an = Number((a.episode_number || '').split('x')[1] || 0);
          const bn = Number((b.episode_number || '').split('x')[1] || 0);
          return an - bn;
        });
      }
    }
    // Sort shows by max score in the group (descending).
    return [...tree.values()].sort((a, b) => {
      const ma = Math.max(...a.items.map((x) => x.score || 0));
      const mb = Math.max(...b.items.map((x) => x.score || 0));
      return mb - ma;
    });
  }

  function renderCoverageTree() {
    const root = $('#cov-tree');
    root.innerHTML = '';
    const covQueuedMap = loadQueuedMap();
    // Server filters stale + embedded-EN; frontend just applies the text filter.
    const filter = ($('#cov-filter').value || '').toLowerCase().trim();

    // Movies render as flat rows under a synthetic 'Movies' group at the bottom.
    const movieItems = coverageRaw.items.filter((i) => i.media_type === 'movie');
    const groups = groupItemsByShow(coverageRaw.items);

    let visible = 0;

    function _passes(item) {
      if (filter) {
        const hay = [item.title, item.episode_title, item.original_language, ...(item.tags || [])]
          .filter(Boolean).join(' ').toLowerCase();
        if (!hay.includes(filter)) return false;
      }
      return true;
    }

    for (const grp of groups) {
      const visEpsByseason = grp.seasonsSorted
        .map(([season, eps]) => [season, eps.filter(_passes)])
        .filter(([, eps]) => eps.length > 0);
      if (visEpsByseason.length === 0) continue;
      const totalVis = visEpsByseason.reduce((a, [, eps]) => a + eps.length, 0);
      visible += totalVis;

      const maxScore = Math.max(...grp.items.map((i) => i.score || 0));
      const showDet = document.createElement('details');
      const showSum = document.createElement('summary');
      showSum.innerHTML = `
        <input type="checkbox" class="lvl-show-cb">
        <span class="lvl-score ${scoreCss(maxScore)}">${maxScore}</span>
        <span class="lvl-name"><strong>${escape(grp.title)}</strong></span>
        <span class="lvl-meta">${totalVis} ep${totalVis === 1 ? '' : 's'} wanted · ${visEpsByseason.length} season${visEpsByseason.length === 1 ? '' : 's'}</span>
      `;
      showDet.appendChild(showSum);

      for (const [season, eps] of visEpsByseason) {
        const seasonDet = document.createElement('details');
        const seasonMax = Math.max(...eps.map((i) => i.score || 0));
        const seasonSum = document.createElement('summary');
        seasonSum.innerHTML = `
          <input type="checkbox" class="lvl-season-cb">
          <span class="lvl-score ${scoreCss(seasonMax)}">${seasonMax}</span>
          <span class="lvl-name">Season ${escape(season)}</span>
          <span class="lvl-meta">${eps.length} ep${eps.length === 1 ? '' : 's'} wanted</span>
        `;
        seasonDet.appendChild(seasonSum);

        for (const ep of eps) {
          const epRow = document.createElement('div');
          epRow.className = 'ep-row';
          if (ep.has_sub_on_disk) epRow.classList.add('row-stale');
          const rowKey = _epRowKey(ep);
          const isQueued = rowKey && (covQueuedMap[rowKey] !== undefined);
          if (isQueued) epRow.classList.add('row-queued');
          const epId = ep.bazarr?.episode_id ?? '';
          const canonical = ep.canonical_path || '';
          const epNum = ep.episode_number || '';
          const epTitle = ep.episode_title || '';
          const reasons = (ep.score_reasons || []).join(' · ');
          // Unified badge for the embedded signal (shared with Scan tree + Library).
          const emb = ep.embedded_en ? coverageBadgeHtml(ep.embedded_en, false) : '';
          const disk = ep.has_sub_on_disk
            ? coverageBadgeHtml(null, true)
            : '';
          const lang = ep.original_language || '';
          epRow.innerHTML = `
            <input type="checkbox" class="ep-cb"
                   data-episode-id="${epId}" data-canonical="${escape(canonical)}"
                   data-title="${escape(ep.title || '')}" data-row-key="${escape(rowKey || '')}"
                   ${isQueued ? 'disabled' : ''}>
            <span class="lvl-score ${scoreCss(ep.score || 0)}" title="${escape(reasons)}">${ep.score || 0}</span>
            <span class="ep-num">${escape(epNum)}</span>
            <span class="ep-title">${escape(epTitle)}${lang ? ` <span class="muted">[${escape(lang)}]</span>` : ''}</span>
            ${emb} ${disk}
            ${isQueued ? '<span class="muted small">✓ queued</span>' : ''}
          `;
          seasonDet.appendChild(epRow);
        }

        // Season-level cascade: tick season → tick all its eps.
        const seasonCb = seasonSum.querySelector('.lvl-season-cb');
        seasonCb.addEventListener('change', () => {
          for (const cb of seasonDet.querySelectorAll('.ep-cb')) {
            if (!cb.disabled && cb.checked !== seasonCb.checked) {
              cb.checked = seasonCb.checked;
              cb.dispatchEvent(new Event('change'));
            }
          }
        });
        seasonCb.addEventListener('click', (e) => e.stopPropagation());

        showDet.appendChild(seasonDet);
      }

      // Show-level cascade.
      const showCb = showSum.querySelector('.lvl-show-cb');
      showCb.addEventListener('change', () => {
        for (const cb of showDet.querySelectorAll('.lvl-season-cb, .ep-cb')) {
          if (!cb.disabled && cb.checked !== showCb.checked) {
            cb.checked = showCb.checked;
            cb.dispatchEvent(new Event('change'));
          }
        }
      });
      showCb.addEventListener('click', (e) => e.stopPropagation());

      root.appendChild(showDet);
    }

    // Movies (if any) as a flat group.
    const movsVisible = movieItems.filter(_passes);
    if (movsVisible.length > 0) {
      const movDet = document.createElement('details');
      const maxScore = Math.max(...movsVisible.map((i) => i.score || 0));
      const movSum = document.createElement('summary');
      movSum.innerHTML = `
        <input type="checkbox" class="lvl-show-cb">
        <span class="lvl-score ${scoreCss(maxScore)}">${maxScore}</span>
        <span class="lvl-name"><strong>Movies</strong></span>
        <span class="lvl-meta">${movsVisible.length} wanted</span>
      `;
      movDet.appendChild(movSum);
      for (const m of movsVisible) {
        const r = document.createElement('div');
        r.className = 'ep-row';
        const rowKey = _epRowKey(m);
        const isQueued = rowKey && (loadQueuedMap()[rowKey] !== undefined);
        const canonical = m.canonical_path || '';
        r.innerHTML = `
          <input type="checkbox" class="ep-cb" data-episode-id=""
                 data-canonical="${escape(canonical)}" data-title="${escape(m.title || '')}"
                 data-row-key="${escape(rowKey || '')}" ${isQueued ? 'disabled' : ''}>
          <span class="lvl-score ${scoreCss(m.score || 0)}">${m.score || 0}</span>
          <span class="ep-title">${escape(m.title)}</span>
        `;
        movDet.appendChild(r);
      }
      const showCb = movSum.querySelector('.lvl-show-cb');
      showCb.addEventListener('change', () => {
        for (const cb of movDet.querySelectorAll('.ep-cb')) {
          if (!cb.disabled && cb.checked !== showCb.checked) {
            cb.checked = showCb.checked;
            cb.dispatchEvent(new Event('change'));
          }
        }
      });
      showCb.addEventListener('click', (e) => e.stopPropagation());
      visible += movsVisible.length;
      root.appendChild(movDet);
    }

    // Episode checkbox change → update selection set.
    root.addEventListener('change', (ev) => {
      const cb = ev.target.closest('.ep-cb');
      if (!cb) return;
      const epId = cb.dataset.episodeId;
      const canonical = cb.dataset.canonical;
      const title = cb.dataset.title;
      const key = epId || `__path:${canonical}`;
      if (cb.checked) {
        covSelectedManualPaths.set(key, {
          episode_id: epId ? Number(epId) : null,
          canonical_path: canonical,
          title,
        });
      } else {
        covSelectedManualPaths.delete(key);
      }
      updateBulkQueueBtn();
    });

    // Update meta
    const t = coverageRaw.totals || {};
    const ts = coverageRaw.generated_at ? new Date(coverageRaw.generated_at * 1000).toLocaleTimeString() : '?';
    $('#coverage-meta').textContent =
      `${visible} ep${visible === 1 ? '' : 's'} shown across ${groups.length} show${groups.length === 1 ? '' : 's'} · ` +
      `${t.items} total (${t.episodes} ep + ${t.movies} mv) · ` +
      `${t.suppressed_by_embedded_en || 0} probe-suppressed · ` +
      `${t.suppressed_by_stale_disk || 0} stale-disk-suppressed · ` +
      `generated ${ts}${coverageRaw.cached ? ` (cached ${coverageRaw.cache_age_s}s)` : ''}`;
  }

  // Bulk queue button — drains covSelectedManualPaths.
  $('#cov-queue-selected')?.addEventListener('click', async () => {
    const btn = $('#cov-queue-selected');
    const items = [...covSelectedManualPaths.values()];
    btn.disabled = true;
    let ok = 0, fail = 0;
    const map = loadQueuedMap();
    for (const it of items) {
      try {
        const body = {};
        if (it.episode_id) body.sonarr_episode_id = it.episode_id;
        else if (it.canonical_path) body.canonical_path = it.canonical_path;
        else continue;
        const r = await fetch('/api/coverage/queue', {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify(body),
        });
        const data = await r.json();
        if (!r.ok) throw new Error(data.detail || `HTTP ${r.status}`);
        ok++;
        const key = it.episode_id ? `ep:${it.episode_id}` : `mv:${it.canonical_path}`;
        map[key] = { scan_id: data.id, canonical: data.canonical_path, queued_at_ms: Date.now() };
      } catch (e) {
        fail++;
        console.warn('bulk queue fail:', it, e);
      }
    }
    saveQueuedMap(map);
    btn.textContent = `Queued ${ok}${fail ? ` · ${fail} failed` : ''}`;
    covSelectedManualPaths.clear();
    setTimeout(() => { btn.textContent = 'Queue selected (0)'; loadCoverage(true); }, 2500);
  });

  // ───── Library Probe tab ─────
  // Library only has embedded data (no sibling-srt context per row),
  // so it always passes has_sibling_srt=undefined. Same helper covers it.
  function libBadge(track) {
    return coverageBadgeHtml(track, false);
  }
  function libSubSummary(streams) {
    return (streams || []).map((s) => {
      const flags = [];
      if (s.forced) flags.push('forced');
      if (s.sdh) flags.push('SDH');
      if (s.commentary) flags.push('comm');
      return `${escape(s.language || '?')}${flags.length ? '(' + flags.join(',') + ')' : ''}`;
    }).join(', ');
  }

  // Bucket library items by canonical_path structure.
  // TV/<Show>/Season X/<file>  → root=TV, show, season, leaf
  // Movies/<Movie>/<file>      → root=Movies, show=Movie, no season, leaf
  // <root>/<file or sub-dirs>  → root, leaf
  function groupLibraryByShow(items) {
    const roots = new Map();  // root → Map(show → Map(season → [items]))
    for (const it of items) {
      const parts = (it.canonical_path || '').split('/').filter(Boolean);
      if (parts.length < 2) continue;
      const root = parts[0];
      const show = parts.length >= 2 ? parts[1] : '(top)';
      let season = '(no season)';
      if (root === 'TV' && parts.length >= 3) season = parts[2];
      else if (root !== 'TV') season = '(files)';
      let byShow = roots.get(root);
      if (!byShow) { byShow = new Map(); roots.set(root, byShow); }
      let bySeason = byShow.get(show);
      if (!bySeason) { bySeason = new Map(); byShow.set(show, bySeason); }
      let bucket = bySeason.get(season);
      if (!bucket) { bucket = []; bySeason.set(season, bucket); }
      bucket.push(it);
    }
    return roots;
  }

  function countWithEng(items) {
    return items.filter((i) => i.usable_english).length;
  }

  function renderLibraryTree(data) {
    const root = $('#lib-tree');
    root.innerHTML = '';
    const roots = groupLibraryByShow(data.items || []);
    if (roots.size === 0) {
      root.innerHTML = '<p class="muted">no matches — run a probe walk from Settings to populate the cache</p>';
      return;
    }
    // Sort root order: TV, Movies, others alpha
    const rootOrder = [...roots.keys()].sort((a, b) => {
      if (a === 'TV') return -1;
      if (b === 'TV') return 1;
      if (a === 'Movies') return -1;
      if (b === 'Movies') return 1;
      return a.localeCompare(b);
    });
    for (const rootName of rootOrder) {
      const byShow = roots.get(rootName);
      const allInRoot = [...byShow.values()].flatMap((seasons) => [...seasons.values()].flat());
      const rootDet = document.createElement('details');
      const rootSum = document.createElement('summary');
      const engCount = countWithEng(allInRoot);
      rootSum.innerHTML = `
        <span class="lvl-name"><strong>${escape(rootName)}</strong></span>
        <span class="lvl-meta">${allInRoot.length} file${allInRoot.length === 1 ? '' : 's'} · ${engCount} with EN</span>
      `;
      rootDet.appendChild(rootSum);

      const showOrder = [...byShow.keys()].sort((a, b) => a.localeCompare(b));
      for (const showName of showOrder) {
        const bySeason = byShow.get(showName);
        const showFlat = [...bySeason.values()].flat();
        const showDet = document.createElement('details');
        const showSum = document.createElement('summary');
        const showEng = countWithEng(showFlat);
        showSum.innerHTML = `
          <span class="lvl-name">${escape(showName)}</span>
          <span class="lvl-meta">${showFlat.length} · ${showEng} EN</span>
        `;
        showDet.appendChild(showSum);

        const seasonOrder = [...bySeason.keys()].sort((a, b) => {
          // Numeric where possible
          const aMatch = (a.match(/(\d+)/) || [])[1];
          const bMatch = (b.match(/(\d+)/) || [])[1];
          if (aMatch && bMatch) return Number(aMatch) - Number(bMatch);
          return a.localeCompare(b);
        });

        // If only one (synthetic) season for non-TV roots, skip the level
        // and render files directly under the show.
        const renderItemsInto = (parent, items) => {
          for (const it of items) {
            const r = document.createElement('div');
            r.className = 'ep-row';
            const dur = it.duration_s ? `${Math.round(it.duration_s/60)}m` : '—';
            const basename = (it.canonical_path || '').split('/').pop() || it.canonical_path;
            const subSummary = libSubSummary(it.subtitle_streams);
            r.innerHTML = `
              <span style="width:18px"></span>
              ${libBadge(it.english_track)}
              <span class="ep-title" title="${escape(it.canonical_path)}">${escape(basename)}</span>
              <span class="audio-langs">audio: ${escape((it.audio_langs || []).join(',') || '—')}</span>
              <span class="audio-langs">sub: ${escape(subSummary || '—')}</span>
              <span class="audio-langs">${dur}</span>
            `;
            parent.appendChild(r);
          }
        };

        if (seasonOrder.length === 1 && seasonOrder[0] === '(files)') {
          renderItemsInto(showDet, bySeason.get('(files)'));
        } else {
          for (const seasonName of seasonOrder) {
            const items = bySeason.get(seasonName);
            const seasonDet = document.createElement('details');
            const seasonSum = document.createElement('summary');
            const seasonEng = countWithEng(items);
            seasonSum.innerHTML = `
              <span class="lvl-name">${escape(seasonName)}</span>
              <span class="lvl-meta">${items.length} · ${seasonEng} EN</span>
            `;
            seasonDet.appendChild(seasonSum);
            renderItemsInto(seasonDet, items);
            showDet.appendChild(seasonDet);
          }
        }
        rootDet.appendChild(showDet);
      }
      root.appendChild(rootDet);
    }
  }

  async function loadLibrary() {
    const meta = $('#library-meta');
    meta.textContent = 'loading…';
    try {
      const params = new URLSearchParams({
        filter_text: $('#lib-filter-text').value || '',
        sub_kind: $('#lib-filter-kind').value || '',
        only_with_eng: $('#lib-only-eng').checked ? 'true' : 'false',
        limit: '5000',
      });
      const data = await fetch(`/api/probe/library?${params}`).then((r) => r.json());

      if ($('#lib-group')?.checked) {
        $('#lib-table').hidden = true;
        $('#lib-tree').hidden = false;
        renderLibraryTree(data);
      } else {
        $('#lib-table').hidden = false;
        $('#lib-tree').hidden = true;
        const tbody = $('#lib-table tbody');
        tbody.innerHTML = '';
        if (data.items.length === 0) {
          tbody.innerHTML = `<tr class="empty-row"><td colspan="5">no matches — run a probe walk from Settings to populate the cache</td></tr>`;
        } else {
          for (const it of data.items) {
            const tr = document.createElement('tr');
            const dur = it.duration_s ? `${Math.round(it.duration_s/60)}m` : '—';
            tr.innerHTML = `
              <td>${libBadge(it.english_track)}</td>
              <td style="font-family: var(--font-mono); font-size: 11px; word-break: break-all;">${escape(it.canonical_path)}</td>
              <td class="audio-langs">${escape((it.audio_langs || []).join(',') || '—')}</td>
              <td class="audio-langs">${escape(libSubSummary(it.subtitle_streams) || '—')}</td>
              <td class="audio-langs">${dur}</td>
            `;
            tbody.appendChild(tr);
          }
        }
      }
      meta.textContent = `${data.shown} shown of ${data.total_cached} cached probes${data.shown < data.total_cached ? ' (filter applied)' : ''}`;
    } catch (e) {
      meta.textContent = `error: ${e.message}`;
    }
  }
  $('#lib-refresh').addEventListener('click', loadLibrary);
  $('#lib-filter-text').addEventListener('input', loadLibrary);
  $('#lib-filter-kind').addEventListener('change', loadLibrary);
  $('#lib-only-eng').addEventListener('change', loadLibrary);
  $('#lib-group')?.addEventListener('change', loadLibrary);

  // Per-row probe button (run ffprobe on a single file)
  $('#cov-table').addEventListener('click', async (ev) => {
    const btn = ev.target.closest('.probe-btn');
    if (!btn || btn.disabled) return;
    const canonical = btn.dataset.canonical;
    if (!canonical) return;
    btn.disabled = true;
    btn.textContent = '…';
    try {
      const r = await fetch(`/api/probe?path=${encodeURIComponent(canonical)}`);
      const body = await r.json();
      if (!r.ok) throw new Error(body.detail || `HTTP ${r.status}`);
      // Refresh coverage to fold the new probe data in.
      btn.textContent = '✓';
      setTimeout(() => loadCoverage(true), 400);
    } catch (e) {
      btn.textContent = '✗';
      btn.title = e.message;
      setTimeout(() => { btn.textContent = 'probe'; btn.disabled = false; }, 5000);
    }
  });

  // Bazarr resync button (when our probe found EN that Bazarr missed)
  $('#cov-table').addEventListener('click', async (ev) => {
    const btn = ev.target.closest('.bazarr-resync-btn');
    if (!btn || btn.disabled) return;
    const seriesId = btn.dataset.seriesId ? Number(btn.dataset.seriesId) : null;
    const canonical = btn.dataset.canonical || null;
    btn.disabled = true;
    btn.textContent = '…';
    try {
      const body = {};
      if (seriesId) body.series_id = seriesId;
      if (canonical) body.canonical_path = canonical;
      const r = await fetch('/api/bazarr/sync-disk', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(body),
      });
      const data = await r.json();
      if (!r.ok) throw new Error(data.detail || `HTTP ${r.status}`);
      btn.textContent = '✓ rescan';
      btn.title = `triggered ${data.task_id}`;
    } catch (e) {
      btn.textContent = '✗';
      btn.title = e.message;
      setTimeout(() => { btn.textContent = '→Bazarr'; btn.disabled = false; }, 6000);
    }
  });

  // Per-row enrichment button (event-delegated).
  $('#cov-table').addEventListener('click', async (ev) => {
    const btn = ev.target.closest('.lang-enrich-btn');
    if (!btn || btn.disabled) return;
    const canonical = btn.dataset.canonical;
    const title = btn.dataset.title;
    if (!canonical || !title) return;
    btn.disabled = true;
    btn.textContent = '…';
    try {
      const r = await fetch('/api/enrichment/lang?gate=true', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ canonical_path: canonical, title }),
      });
      const body = await r.json();
      if (r.status === 429) throw new Error(`GPU busy: ${body.detail}`);
      if (!r.ok) throw new Error(body.detail || `HTTP ${r.status}`);
      btn.textContent = body.iso_code || 'und';
      btn.title = `model=${body.model} raw=${body.raw_response}${body.cached ? ' (cached)' : ''}`;
      btn.classList.remove('lang-enrich-btn');
      btn.classList.add('lang-enriched');
    } catch (e) {
      btn.textContent = '✗';
      btn.title = e.message;
      setTimeout(() => { btn.textContent = '? enrich'; btn.disabled = false; }, 5000);
    }
  });

  // Per-row Queue button (event-delegated). Posts to /api/coverage/queue
  // which resolves the sonarr_episode_id to a single .mkv file before
  // enqueueing — so we queue ONE episode, not the whole series directory.
  $('#cov-table').addEventListener('click', async (ev) => {
    const btn = ev.target.closest('.cov-queue-btn');
    if (!btn || btn.disabled) return;
    const epId = btn.dataset.episodeId ? Number(btn.dataset.episodeId) : null;
    const canonical = btn.dataset.canonical || null;
    const rowKey = btn.dataset.rowKey || null;
    btn.disabled = true;
    btn.textContent = '…';
    try {
      const body = {};
      if (epId) body.sonarr_episode_id = epId;
      else if (canonical) body.canonical_path = canonical;
      else throw new Error('nothing to queue');
      const r = await fetch('/api/coverage/queue', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(body),
      });
      const data = await r.json();
      if (!r.ok) throw new Error(data.detail || `HTTP ${r.status}`);
      // Persist + flip row to queued state.
      if (rowKey) {
        const map = loadQueuedMap();
        map[rowKey] = { scan_id: data.id, canonical: data.canonical_path, queued_at_ms: Date.now() };
        saveQueuedMap(map);
      }
      btn.textContent = '✓ queued';
      btn.classList.add('queued');
      btn.title = `scan_id ${data.id} · resolved ${data.canonical_path}`;
      const tr = btn.closest('tr');
      if (tr) tr.classList.add('row-queued');
    } catch (e) {
      btn.textContent = '✗ ' + (e.message.length > 40 ? e.message.slice(0, 40) + '…' : e.message);
      btn.title = e.message;
      setTimeout(() => { btn.textContent = '→ Queue'; btn.disabled = false; }, 6000);
    }
  });

  // ───── helpers ─────
  function escape(s) {
    return String(s ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  // ───── global button-tap feedback ─────
  // Every button click gets a brief `.tapped` class so async actions show
  // visual feedback even before the network round-trip completes. CSS
  // animates a 320ms ring-flash. Capture phase so we run before
  // stopPropagation handlers.
  document.addEventListener('click', (ev) => {
    const btn = ev.target.closest('button');
    if (!btn || btn.disabled) return;
    btn.classList.remove('tapped');  // restart animation if already in flight
    // Force reflow so removing+adding takes effect even on repeat clicks
    // eslint-disable-next-line no-unused-expressions
    btn.offsetWidth;
    btn.classList.add('tapped');
    setTimeout(() => btn.classList.remove('tapped'), 350);
  }, true);

  // ───── boot ─────
  window._currentTab = 'scan';
  document.addEventListener('click', (e) => {
    const t = e.target.closest('.tab');
    if (t) window._currentTab = t.dataset.tab;
  });
  startHeader();
  loadRoot();
})();
