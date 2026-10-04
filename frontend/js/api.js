// api.js — thin wrappers around the FastAPI backend.
const BASE = "";

async function jsonFetch(url, opts = {}) {
  const res = await fetch(BASE + url, opts);
  if (!res.ok) {
    let msg = `请求失败 (${res.status})`;
    try {
      const e = await res.json();
      msg = e.detail || e.message || msg;
    } catch {}
    throw new Error(msg);
  }
  return res.json();
}

export const api = {
  async health() {
    return jsonFetch("/api/health");
  },

  async upload(files) {
    const fd = new FormData();
    for (const f of files) fd.append("files", f, f.name);
    const res = await fetch(BASE + "/api/upload", { method: "POST", body: fd });
    if (!res.ok) throw new Error("上传失败");
    return res.json();
  },

  async probe(file) {
    const fd = new FormData();
    fd.append("file", file, file.name);
    const res = await fetch(BASE + "/api/probe", { method: "POST", body: fd });
    if (!res.ok) throw new Error("探测失败");
    return res.json();
  },

  async preview(spec) {
    return jsonFetch("/api/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(spec),
    });
  },

  async createJob(spec) {
    return jsonFetch("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(spec),
    });
  },

  async jobs() {
    return jsonFetch("/api/jobs");
  },

  async job(id) {
    return jsonFetch(`/api/jobs/${id}`);
  },

  async cancel(id) {
    return jsonFetch(`/api/jobs/${id}/cancel`, { method: "POST" });
  },

  async remove(id) {
    return jsonFetch(`/api/jobs/${id}`, { method: "DELETE" });
  },

  downloadUrl(id) {
    return BASE + `/api/download/${id}`;
  },

  streamUrl(id) {
    return BASE + `/api/jobs/${id}/stream`;
  },
};
