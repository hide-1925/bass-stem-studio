// Thin wrappers around the local REST API.

async function request(method, url, body, isForm = false) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    if (isForm) opts.body = body;
    else {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    }
  }
  const res = await fetch(url, opts);
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const j = await res.json();
      if (j.detail) msg = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail);
    } catch (_) { /* not JSON */ }
    const err = new Error(msg);
    err.status = res.status;
    throw err;
  }
  const ct = res.headers.get('content-type') || '';
  return ct.includes('application/json') ? res.json() : res;
}

export const api = {
  system: () => request('GET', '/api/system'),
  listProjects: () => request('GET', '/api/projects'),
  createProject: (file, { title, kind = 'file', auto = 1 } = {}, filename) => {
    const fd = new FormData();
    fd.append('file', file, filename || file.name || 'audio.wav');
    if (title) fd.append('title', title);
    fd.append('kind', kind);
    fd.append('auto', String(auto));
    return request('POST', '/api/projects', fd, true);
  },
  getProject: (id) => request('GET', `/api/projects/${id}`),
  patchProject: (id, patch) => request('PATCH', `/api/projects/${id}`, patch),
  deleteProject: (id) => request('DELETE', `/api/projects/${id}`),
  peaks: (id) => request('GET', `/api/projects/${id}/peaks`),
  mixPeak: (id, gains) => {
    const g = Object.entries(gains).map(([k, v]) => `${k}:${v.toFixed(5)}`).join(',');
    return request('GET', `/api/projects/${id}/mixpeak?g=${encodeURIComponent(g)}`);
  },
  getNotes: (id) => request('GET', `/api/projects/${id}/notes`),
  putNotes: (id, body) => request('PUT', `/api/projects/${id}/notes`, body),
  startJob: (id, type, params) => request('POST', `/api/projects/${id}/jobs`, { type, params }),
  job: (jobId) => request('GET', `/api/jobs/${jobId}`),
  cancelJob: (jobId) => request('POST', `/api/jobs/${jobId}/cancel`),
  retryJob: (jobId) => request('POST', `/api/jobs/${jobId}/retry`),
  presets: () => request('GET', '/api/fingering/presets'),
  optimize: (notes, tuning, options, respectLocks = true) =>
    request('POST', '/api/fingering/optimize', { notes, tuning, options, respect_locks: respectLocks }),
  exportUrl: (id, fmt, params = {}) => {
    const q = new URLSearchParams(params).toString();
    return `/api/projects/${id}/export/${fmt}${q ? '?' + q : ''}`;
  },
};
