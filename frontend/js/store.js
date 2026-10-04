// store.js — central job registry + SSE subscription.
import { api } from "./api.js";

const jobs = []; // newest first
const listeners = new Set();

export const store = {
  subscribe(fn) {
    listeners.add(fn);
    return () => listeners.delete(fn);
  },
  notify() {
    listeners.forEach((fn) => fn(jobs));
  },
  list() {
    return jobs;
  },
  get(id) {
    return jobs.find((j) => j.id === id);
  },
  add(job) {
    jobs.unshift(job);
    this.notify();
  },
  update(id, patch) {
    const j = this.get(id);
    if (!j) return;
    Object.assign(j, patch);
    this.notify();
  },
  appendLog(id, line) {
    const j = this.get(id);
    if (!j) return;
    j.log.push(line);
    if (j.log.length > 400) j.log.shift();
    this.notify();
  },
  remove(id) {
    const i = jobs.findIndex((j) => j.id === id);
    if (i >= 0) jobs.splice(i, 1);
    this.notify();
  },
  clearCompleted() {
    for (let i = jobs.length - 1; i >= 0; i--) {
      if (jobs[i].status === "completed" || jobs[i].status === "failed" || jobs[i].status === "canceled")
        jobs.splice(i, 1);
    }
    this.notify();
  },
};

// Submit a job spec to the backend and wire live progress via SSE.
export async function submitJob(spec, displayName, inputLabel) {
  const res = await api.createJob(spec);
  const id = res.id;
  store.add({
    id,
    mode: spec.mode,
    status: "queued",
    progress: 0,
    fps: 0,
    speed: "",
    timeSec: 0,
    sizeBytes: 0,
    etaSec: null,
    log: [],
    outputName: `${spec.outputName || displayName || "output"}.${spec.outputFormat}`,
    inputLabel: inputLabel || "",
    outputPath: null,
    error: null,
    createdAt: Date.now(),
  });
  subscribeToJob(id);
  return id;
}

// Open an EventSource for a job and pipe its events into the store.
// Idempotent: a given id is only subscribed once.
const subscribed = new Set();
export function subscribeToJob(id) {
  if (subscribed.has(id)) return;
  const j = store.get(id);
  // Don't bother subscribing to jobs that already finished.
  if (j && (j.status === "completed" || j.status === "failed" || j.status === "canceled")) return;
  subscribed.add(id);
  const es = new EventSource(api.streamUrl(id));
  let terminal = false;
  const finish = () => {
    if (terminal) return;
    terminal = true;
    subscribed.delete(id);
    es.close();
  };
  es.addEventListener("progress", (e) => {
    try { store.update(id, JSON.parse(e.data)); } catch {}
  });
  es.addEventListener("log", (e) => {
    try { store.appendLog(id, JSON.parse(e.data).line); } catch {}
  });
  es.addEventListener("done", (e) => {
    try {
      const d = JSON.parse(e.data);
      store.update(id, { status: "completed", outputPath: d.outputPath, progress: 100 });
    } catch {
      store.update(id, { status: "completed", progress: 100 });
    }
    finish();
  });
  es.addEventListener("error", (e) => {
    try {
      const d = JSON.parse(e.data);
      store.update(id, { status: "failed", error: d.error || "处理失败" });
    } catch {
      store.update(id, { status: "failed", error: "处理失败" });
    }
    finish();
  });
  es.onerror = () => {
    if (terminal) return;
    // Stream closed (e.g. job ended). Avoid leaving a dangling subscription.
    finish();
  };
}
