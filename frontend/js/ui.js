// ui.js — small DOM / formatting helpers used across modules.

export const el = (id) => document.getElementById(id);
export const val = (id) => document.getElementById(id)?.value ?? "";
export const num = (id) => {
  const v = parseFloat(document.getElementById(id)?.value);
  return isNaN(v) ? null : v;
};
export const checked = (id) => !!document.getElementById(id)?.checked;

export function fmtBytes(n) {
  if (n == null) return "—";
  if (n < 1024) return n + " B";
  const u = ["KB", "MB", "GB", "TB"];
  let i = -1;
  do {
    n /= 1024;
    i++;
  } while (n >= 1024 && i < u.length - 1);
  return n.toFixed(1) + " " + u[i];
}

export function fmtTime(s) {
  if (s == null || isNaN(s)) return "—";
  s = Math.max(0, Math.floor(s));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const p = (x) => String(x).padStart(2, "0");
  return h > 0 ? `${h}:${p(m)}:${p(sec)}` : `${m}:${p(sec)}`;
}

export function fmtDuration(raw) {
  // raw may be "00:00:05" or seconds number/string
  if (raw == null || raw === "") return null;
  if (/^\d+:\d\d:\d\d$/.test(raw) || /^\d+:\d\d$/.test(raw)) return raw;
  const s = parseFloat(raw);
  return isNaN(s) ? null : s;
}

export function baseName(name) {
  return name.replace(/\.[^.]+$/, "");
}
export function extOf(name) {
  const m = name.match(/\.([^.]+)$/);
  return m ? m[1].toLowerCase() : "";
}

export function setCmd(id, text) {
  const node = el(id);
  if (!node) return;
  if (!text) {
    node.textContent = "选择文件并设置参数后生成命令…";
    node.classList.add("cmd-empty");
    return;
  }
  node.classList.remove("cmd-empty");
  node.innerHTML = `<span class="cmd-prompt">$ </span>${escapeHtml(text)}`;
}

function escapeHtml(s) {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast("success", "命令已复制到剪贴板");
  } catch {
    toast("error", "复制失败，请手动选择");
  }
}

export function toast(type, msg) {
  const wrap = el("toast-wrap");
  const icons = {
    success: "ic-check",
    error: "ic-alert",
    info: "ic-info",
  };
  const node = document.createElement("div");
  node.className = `toast ${type}`;
  node.innerHTML = `<svg><use href="#${icons[type] || "ic-info"}"></use></svg><span>${escapeHtml(
    msg
  )}</span>`;
  wrap.appendChild(node);
  setTimeout(() => {
    node.style.opacity = "0";
    node.style.transform = "translateX(30px)";
    node.style.transition = "all .3s";
    setTimeout(() => node.remove(), 300);
  }, 2600);
}

// Wire a dropzone element to a hidden file input and an onFiles callback.
export function attachDZ(dzEl, inputEl, onFiles, multiple = false) {
  inputEl.multiple = multiple;
  dzEl.addEventListener("click", () => inputEl.click());
  inputEl.addEventListener("change", () => {
    if (inputEl.files.length) onFiles(Array.from(inputEl.files));
    inputEl.value = "";
  });
  ["dragenter", "dragover"].forEach((ev) =>
    dzEl.addEventListener(ev, (e) => {
      e.preventDefault();
      dzEl.classList.add("drag");
    })
  );
  ["dragleave", "drop"].forEach((ev) =>
    dzEl.addEventListener(ev, (e) => {
      e.preventDefault();
      dzEl.classList.remove("drag");
    })
  );
  dzEl.addEventListener("drop", (e) => {
    const files = e.dataTransfer?.files;
    if (files && files.length) onFiles(Array.from(files));
  });
}
