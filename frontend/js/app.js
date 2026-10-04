// app.js — bootstrap: navigation, theme, health, task-queue rendering.
import { api } from "./api.js";
import { store, subscribeToJob } from "./store.js";
import { initModules } from "./modules.js";
import { el, fmtBytes, fmtTime, toast } from "./ui.js";

const META = {
  convert: ["格式转换", "Convert · 转码与封装"],
  trim: ["剪辑截取", "Trim · 精确截取片段"],
  merge: ["合并拼接", "Merge · 多段拼接"],
  compress: ["压缩优化", "Compress · 减小体积"],
  filter: ["滤镜处理", "Filters · 视频与音频滤镜"],
  custom: ["自定义参数", "Custom · 高级命令行"],
  batch: ["批量任务", "Batch · 批量转码"],
  tasks: ["任务队列", "Tasks · 进度与日志"],
};
const MODE_LABEL = {
  convert: "转换",
  trim: "剪辑",
  merge: "合并",
  compress: "压缩",
  filter: "滤镜",
  custom: "自定义",
};
const STATUS_LABEL = {
  queued: "排队中",
  running: "处理中",
  completed: "已完成",
  failed: "失败",
  canceled: "已取消",
};

let current = "convert";

function switchModule(m) {
  if (!META[m]) return;
  current = m;
  document.querySelectorAll(".nav-item").forEach((b) =>
    b.classList.toggle("active", b.dataset.module === m)
  );
  document.querySelectorAll(".panel").forEach((p) =>
    p.classList.toggle("active", p.dataset.panel === m)
  );
  el("page-title").textContent = META[m][0];
  el("page-crumb").textContent = META[m][1];
  if (location.hash.slice(1) !== m) {
    history.replaceState(null, "", "#" + m);
  }
}

function setTheme(t) {
  document.documentElement.setAttribute("data-theme", t);
  el("theme-icon").innerHTML =
    t === "light"
      ? '<use href="#ic-sun"></use>'
      : '<use href="#ic-moon"></use>';
  try {
    localStorage.setItem("ff-theme", t);
  } catch {}
}
function toggleTheme() {
  const cur = document.documentElement.getAttribute("data-theme");
  setTheme(cur === "light" ? "dark" : "light");
}

async function checkHealth() {
  try {
    const h = await api.health();
    const ok = h.ffmpeg && h.ffprobe;
    [el("ff-dot"), el("ff-dot2")].forEach((d) => d.classList.toggle("off", !ok));
    el("ff-text").textContent = ok ? "FFmpeg 已就绪" : "未检测到 FFmpeg";
    // Derive a compact "ffmpeg <build>" label from the raw version line.
    const m = h.version?.match(/ffmpeg version (\S+)/);
    const short = m ? m[1] : (h.version || "").slice(0, 30);
    el("ff-ver").textContent = ok ? short : "";
    el("ff-chip-ver").textContent = ok ? short : "缺失";
  } catch {
    [el("ff-dot"), el("ff-dot2")].forEach((d) => d.classList.add("off"));
    el("ff-text").textContent = "无法连接后端";
    el("ff-chip-ver").textContent = "离线";
  }
}

/* ---------------- task queue ---------------- */
function renderJobs(jobs) {
  const wrap = el("jobs-container");
  const empty = el("jobs-empty");
  empty.style.display = jobs.length ? "none" : "block";

  wrap.innerHTML = jobs
    .map((j) => {
      const running = j.status === "running";
      const pct = j.progress == null ? null : Math.round(j.progress);
      const bar = pct == null
        ? `<div class="progress indeterminate"></div>`
        : `<div class="progress"><div class="progress-bar" style="width:${pct}%"></div></div>`;
      const log = (j.log || []).length
        ? (j.log || [])
            .slice(-60)
            .map((l) => {
              const cls = /error|failed|invalid/i.test(l)
                ? "err"
                : /frame=|progress=/i.test(l)
                ? ""
                : "ok";
              return `<div class="log-line ${cls}">${escapeLog(l)}</div>`;
            })
            .join("")
        : `<div class="log-line muted">（暂无日志）</div>`;
      const errLine = j.error ? `<div class="log-line err">错误：${escapeLog(j.error)}</div>` : "";
      return `<div class="job-card ${running ? "running" : ""}" data-id="${j.id}">
        <div class="job-head">
          <span class="mode-tag">${MODE_LABEL[j.mode] || j.mode}</span>
          <span class="job-title">${escapeLog(j.outputName)}</span>
          <span class="badge ${j.status}">${STATUS_LABEL[j.status] || j.status}</span>
          <div class="job-actions">
            ${
              running
                ? `<button class="mini-btn" data-act="cancel" title="取消"><svg><use href="#ic-x"></use></svg></button>`
                : ""
            }
            ${
              j.status === "completed"
                ? `<button class="mini-btn" data-act="download" title="下载 / 另存为"><svg><use href="#ic-download"></use></svg></button>
                   <button class="mini-btn" data-act="reveal" title="在文件夹中显示"><svg><use href="#ic-folder"></use></svg></button>`
                : ""
            }
            <button class="mini-btn" data-act="log" title="日志"><svg><use href="#ic-info"></use></svg></button>
            <button class="mini-btn danger" data-act="del" title="移除"><svg><use href="#ic-trash"></use></svg></button>
          </div>
        </div>
        ${bar}
        <div class="progress-meta">
          <span>进度 <b>${pct == null ? "—" : pct + "%"}</b></span>
          <span>速度 <b>${j.speed || "—"}</b></span>
          <span>帧率 <b>${j.fps ? j.fps.toFixed(1) : "—"}</b></span>
          <span>已处理 <b>${fmtTime(j.timeSec)}</b></span>
          <span>大小 <b>${fmtBytes(j.sizeBytes)}</b></span>
          <span>剩余 <b>${j.etaSec ? fmtTime(j.etaSec) : "—"}</b></span>
        </div>
        <div class="log-box" data-log="${j.id}">${log}${errLine}</div>
      </div>`;
    })
    .join("");

  // wire actions
  wrap.querySelectorAll(".job-card").forEach((card) => {
    const id = card.dataset.id;
    const logBox = wrap.querySelector(`[data-log="${id}"]`);
    card.querySelector('[data-act="log"]').onclick = () =>
      logBox.classList.toggle("open");
    const del = card.querySelector('[data-act="del"]');
    if (del)
      del.onclick = async () => {
        try {
          await api.remove(id);
        } catch {}
        store.remove(id);
      };
    const cancel = card.querySelector('[data-act="cancel"]');
    if (cancel)
      cancel.onclick = async () => {
        await api.cancel(id);
        toast("info", "已发送取消请求");
      };
    const dl = card.querySelector('[data-act="download"]');
    if (dl) dl.onclick = () => downloadJob(id);
    const rv = card.querySelector('[data-act="reveal"]');
    if (rv) rv.onclick = () => revealJob(id);
  });

  // summary + nav badge
  const active = jobs.filter((j) => j.status === "running" || j.status === "queued").length;
  const done = jobs.filter((j) => j.status === "completed").length;
  el("tasks-summary").textContent = jobs.length
    ? `共 ${jobs.length} 个任务 · 进行中 ${active} · 已完成 ${done}`
    : "暂无任务";
  const badge = el("nav-task-count");
  if (active > 0) {
    badge.style.display = "grid";
    badge.textContent = active;
  } else {
    badge.style.display = "none";
  }
}

/** 桌面模式走原生「另存为」；网页模式回退到浏览器下载。
 *
 *  为什么不能统一用 <a download>：桌面版里 WebView2 的下载事件 pywebview
 *  没有接管，点下去是**静默失败**的（实测 Downloads 目录前后无变化）。
 */
async function downloadJob(id) {
  const native = window.pywebview && window.pywebview.api;
  if (native && native.save_output) {
    try {
      const r = await native.save_output(id);
      if (r && r.ok) toast("success", "已保存到 " + r.path);
      else if (!r || !r.cancelled) toast("error", (r && r.error) || "保存失败");
    } catch (e) {
      toast("error", "保存失败：" + e);
    }
    return;
  }
  // 网页模式
  const a = document.createElement("a");
  a.href = api.downloadUrl(id);
  a.download = "";
  document.body.appendChild(a);
  a.click();
  a.remove();
}

/** 在资源管理器中定位输出文件（仅桌面模式可用） */
async function revealJob(id) {
  const native = window.pywebview && window.pywebview.api;
  if (!native || !native.reveal_output) return;
  try {
    const r = await native.reveal_output(id);
    if (r && !r.ok) toast("error", r.error || "打开失败");
  } catch (e) {
    toast("error", "打开失败：" + e);
  }
}

function escapeLog(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

async function loadExistingJobs() {
  try {
    const list = await api.jobs();
    list.reverse().forEach((j) => {
      // hydrate missing fields that the summary doesn't include
      if (!j.log) j.log = [];
      store.add(j);
      // Open a live SSE stream for any job that's still in flight, so a
      // page reload during a long task still shows live progress.
      if (j.status === "running" || j.status === "queued") subscribeToJob(j.id);
    });
  } catch {}
}

/* ---------------- bootstrap ---------------- */
function init() {
  // Allow ?theme=light|dark to override saved preference (handy for
  // deep-links, embedded demos, and headless screenshot capture).
  const qp = new URLSearchParams(location.search).get("theme");
  let theme = "dark";
  try {
    theme = qp === "light" || qp === "dark" ? qp : (localStorage.getItem("ff-theme") || "dark");
  } catch {
    theme = qp === "light" ? "light" : "dark";
  }
  setTheme(theme);
  el("theme-toggle").onclick = toggleTheme;

  document.querySelectorAll(".nav-item").forEach((b) => {
    b.onclick = () => switchModule(b.dataset.module);
  });
  window.addEventListener("goto", (e) => switchModule(e.detail));
  window.addEventListener("hashchange", () => {
    const m = location.hash.slice(1);
    if (m && m !== current) switchModule(m);
  });
  // deep-link: open the panel named in the URL hash on load
  const initial = location.hash.slice(1);
  if (META[initial]) switchModule(initial);

  el("clear-tasks").onclick = () => {
    store.clearCompleted();
    toast("info", "已清理已完成任务");
  };

  initModules();
  store.subscribe(renderJobs);
  checkHealth();
  loadExistingJobs();
}

document.addEventListener("DOMContentLoaded", init);
