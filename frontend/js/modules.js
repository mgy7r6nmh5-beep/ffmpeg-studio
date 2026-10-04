// modules.js — controllers for every tool panel.
import { api } from "./api.js";
import { submitJob } from "./store.js";
import {
  el,
  val,
  num,
  checked,
  fmtBytes,
  setCmd,
  toast,
  copyText,
  attachDZ,
  baseName,
  extOf,
} from "./ui.js";

const esc = (s) =>
  String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

const state = {
  convert: null,
  trim: null,
  compress: null,
  filter: null,
  custom: null,
  wm: null,
  merge: [],
  batch: [],
};

let previewTimer = null;
function schedulePreview(fn) {
  clearTimeout(previewTimer);
  previewTimer = setTimeout(fn, 220);
}
const goto = (m) => window.dispatchEvent(new CustomEvent("goto", { detail: m }));

async function uploadOne(file) {
  const arr = await api.upload([file]);
  return arr[0];
}

/* ---------------- file pills / lists ---------------- */
function renderPill(cid, f, onRemove) {
  const c = el(cid);
  c.innerHTML = `<div class="file-pill"><svg><use href="#ic-file"></use></svg>
    <div><div class="fp-name">${esc(f.name)}</div><div class="fp-meta">${fmtBytes(
      f.size
    )}</div></div>
    <button class="fp-x" title="移除"><svg><use href="#ic-x"></use></svg></button></div>`;
  c.querySelector(".fp-x").onclick = onRemove;
}

function renderMergeList() {
  const c = el("merge-list");
  if (!state.merge.length) {
    c.innerHTML = "";
    return;
  }
  c.innerHTML = state.merge
    .map(
      (f, i) => `<div class="file-row" data-i="${i}">
      <div class="fr-idx">${i + 1}</div>
      <div class="fr-name">${esc(f.name)}</div>
      <div class="fr-meta">${fmtBytes(f.size)}</div>
      <div class="fr-actions">
        <button class="mini-btn" data-act="up" ${i === 0 ? "disabled" : ""}><svg><use href="#ic-up"></use></svg></button>
        <button class="mini-btn" data-act="down" ${
          i === state.merge.length - 1 ? "disabled" : ""
        }><svg><use href="#ic-down"></use></svg></button>
        <button class="mini-btn danger" data-act="del"><svg><use href="#ic-trash"></use></svg></button>
      </div></div>`
    )
    .join("");
  c.querySelectorAll(".file-row").forEach((row) => {
    const i = +row.dataset.i;
    row.querySelector('[data-act="up"]').onclick = () => move(state.merge, i, -1);
    row.querySelector('[data-act="down"]').onclick = () => move(state.merge, i, 1);
    row.querySelector('[data-act="del"]').onclick = () => {
      state.merge.splice(i, 1);
      renderMergeList();
      previewMerge();
    };
  });
}
function renderBatchList() {
  const c = el("batch-list");
  if (!state.batch.length) {
    c.innerHTML = "";
    return;
  }
  c.innerHTML = state.batch
    .map(
      (f, i) => `<div class="file-row" data-i="${i}">
      <div class="fr-idx">${i + 1}</div>
      <div class="fr-name">${esc(f.name)}</div>
      <div class="fr-meta">${fmtBytes(f.size)}</div>
      <div class="fr-actions">
        <button class="mini-btn danger" data-act="del"><svg><use href="#ic-trash"></use></svg></button>
      </div></div>`
    )
    .join("");
  c.querySelectorAll(".file-row").forEach((row) => {
    const i = +row.dataset.i;
    row.querySelector('[data-act="del"]').onclick = () => {
      state.batch.splice(i, 1);
      renderBatchList();
      previewBatch();
    };
  });
}
function move(arr, i, d) {
  const j = i + d;
  if (j < 0 || j >= arr.length) return;
  [arr[i], arr[j]] = [arr[j], arr[i]];
  renderMergeList();
  previewMerge();
}

/* ---------------- spec builders ---------------- */
function specConvert() {
  if (!state.convert) return null;
  return {
    mode: "convert",
    inputIds: [state.convert.stored],
    outputName: baseName(state.convert.name),
    outputFormat: val("fmt-convert"),
    params: {
      vcodec: val("vcodec-convert"),
      acodec: val("acodec-convert"),
      resolution: val("res-convert"),
      preset: val("preset-convert"),
      crf: num("crf-convert"),
      audiobr: val("audiobr-convert"),
    },
  };
}
function specTrim() {
  if (!state.trim) return null;
  return {
    mode: "trim",
    inputIds: [state.trim.stored],
    outputName: baseName(state.trim.name),
    outputFormat: val("fmt-trim") || extOf(state.trim.name) || "mp4",
    params: {
      start: val("trim-start") || "0",
      end: val("trim-end"),
      duration: val("trim-duration"),
      reencode: checked("trim-reencode"),
    },
  };
}
function specMerge() {
  if (state.merge.length < 2) return null;
  const mode = el("merge-enc").querySelector(".active").dataset.v;
  return {
    mode: "merge",
    inputIds: state.merge.map((f) => f.stored),
    outputName: "merged",
    outputFormat: val("fmt-merge"),
    params: { reencode: mode === "reencode" },
  };
}
function specCompress() {
  if (!state.compress) return null;
  const spec = {
    mode: "compress",
    inputIds: [state.compress.stored],
    outputName: baseName(state.compress.name),
    outputFormat: val("fmt-compress") || extOf(state.compress.name) || "mp4",
    params: {
      crf: num("crf-compress"),
      preset: val("preset-compress"),
      resolution: val("res-compress"),
      audiobr: val("audiobr-compress"),
      twopass: checked("twopass-compress"),
    },
  };
  if (checked("twopass-compress")) spec.params.targetSize = num("target-size-compress");
  return spec;
}
function specFilter() {
  if (!state.filter) return null;
  const scale =
    val("scale-filter") === "custom"
      ? val("scale-custom")
      : val("scale-filter") === "Original"
      ? null
      : val("scale-filter");
  const fps = val("fps-filter") === "Original" ? null : num("fps-filter");
  return {
    mode: "filter",
    inputIds: [state.filter.stored],
    outputName: baseName(state.filter.name),
    outputFormat: val("fmt-filter") || extOf(state.filter.name) || "mp4",
    params: {
      scale,
      rotate: num("rotate-filter"),
      hflip: checked("hflip-filter"),
      vflip: checked("vflip-filter"),
      brightness: num("brightness-filter"),
      contrast: num("contrast-filter"),
      saturation: num("saturation-filter"),
      speed: num("speed-filter"),
      fps,
      grayscale: checked("grayscale-filter"),
      volume: num("volume-filter"),
      fadeIn: val("fade-in"),
      fadeOut: val("fade-out"),
      wm_type: val("wm-type"),
      wm_text: val("wm-text"),
      wm_size: num("wm-size"),
      wm_color: val("wm-color"),
      wm_pos: val("wm-pos"),
      wm_image: state.wm ? state.wm.stored : null,
    },
  };
}
function specCustom() {
  if (!state.custom) return null;
  return {
    mode: "custom",
    inputIds: [state.custom.stored],
    outputName: baseName(state.custom.name),
    outputFormat: val("fmt-custom"),
    params: { extra_args: val("extra-custom") },
  };
}
function specBatchOne(f) {
  return {
    mode: "convert",
    inputIds: [f.stored],
    outputName:
      (val("batch-prefix") || "") +
      baseName(f.name) +
      (val("batch-suffix") || ""),
    outputFormat: val("batch-fmt"),
    params: {
      vcodec: "libx264",
      acodec: "aac",
      resolution: val("batch-res"),
      preset: "medium",
      crf: num("batch-crf"),
      audiobr: "128k",
    },
  };
}

/* ---------------- preview + run ---------------- */
async function preview(spec, cmdId, fallback) {
  if (!spec) {
    setCmd(cmdId, null);
    return;
  }
  try {
    const r = await api.preview(spec);
    setCmd(cmdId, r.command);
  } catch (e) {
    setCmd(cmdId, `预览失败：${e.message}`);
  }
}
function copyCmd(cmdId) {
  const t = el(cmdId).textContent.replace(/^\$\s*/, "");
  copyText(t);
}
async function run(spec, cmdId, moduleName) {
  if (!spec) {
    toast("error", "请先选择文件并完成设置");
    return null;
  }
  try {
    await submitJob(spec, state[moduleName]?.name, state[moduleName]?.name);
    toast("success", "任务已加入队列");
    goto("tasks");
    return true;
  } catch (e) {
    toast("error", `提交失败：${e.message}`);
    return null;
  }
}

const previewConvert = () => preview(specConvert(), "cmd-convert");
const previewTrim = () => preview(specTrim(), "cmd-trim");
const previewMerge = () => preview(specMerge(), "cmd-merge");
const previewCompress = () => preview(specCompress(), "cmd-compress");
const previewFilter = () => preview(specFilter(), "cmd-filter");
const previewCustom = () => preview(specCustom(), "cmd-custom");
function previewBatch() {
  if (!state.batch.length) {
    setCmd("cmd-batch", null);
    return;
  }
  preview(specBatchOne(state.batch[0]), "cmd-batch");
}

/* ---------------- init ---------------- */
export function initModules() {
  // range value displays
  document.querySelectorAll('input[type="range"]').forEach((r) => {
    const out = el(r.id + "-val");
    if (out) {
      out.textContent = r.value;
      r.addEventListener("input", () => (out.textContent = r.value));
    }
  });

  // dropzones
  const dz = (n) => document.querySelector(`[data-dz="${n}"]`);
  attachDZ(dz("convert"), el("file-convert"), async (files) => {
    state.convert = await uploadOne(files[0]);
    renderPill("fileinfo-convert", state.convert, () => {
      state.convert = null;
      el("fileinfo-convert").innerHTML = "";
      previewConvert();
    });
    previewConvert();
  });
  attachDZ(dz("trim"), el("file-trim"), async (files) => {
    state.trim = await uploadOne(files[0]);
    el("fmt-trim").value = extOf(state.trim.name);
    renderPill("fileinfo-trim", state.trim, () => {
      state.trim = null;
      el("fileinfo-trim").innerHTML = "";
      previewTrim();
    });
    previewTrim();
  });
  attachDZ(dz("compress"), el("file-compress"), async (files) => {
    state.compress = await uploadOne(files[0]);
    el("fmt-compress").value = extOf(state.compress.name);
    renderPill("fileinfo-compress", state.compress, () => {
      state.compress = null;
      el("fileinfo-compress").innerHTML = "";
      previewCompress();
    });
    previewCompress();
  });
  attachDZ(dz("filter"), el("file-filter"), async (files) => {
    state.filter = await uploadOne(files[0]);
    el("fmt-filter").value = extOf(state.filter.name);
    renderPill("fileinfo-filter", state.filter, () => {
      state.filter = null;
      el("fileinfo-filter").innerHTML = "";
      previewFilter();
    });
    previewFilter();
  });
  attachDZ(dz("custom"), el("file-custom"), async (files) => {
    state.custom = await uploadOne(files[0]);
    renderPill("fileinfo-custom", state.custom, () => {
      state.custom = null;
      el("fileinfo-custom").innerHTML = "";
      previewCustom();
    });
    previewCustom();
  });
  attachDZ(dz("wm"), el("wm-image"), async (files) => {
    state.wm = await uploadOne(files[0]);
    renderPill("fileinfo-wm", state.wm, () => {
      state.wm = null;
      el("fileinfo-wm").innerHTML = "";
      previewFilter();
    });
    previewFilter();
  });
  attachDZ(dz("merge"), el("file-merge"), async (files) => {
    for (const f of files) state.merge.push(await uploadOne(f));
    renderMergeList();
    previewMerge();
  }, true);
  attachDZ(dz("batch"), el("file-batch"), async (files) => {
    for (const f of files) state.batch.push(await uploadOne(f));
    renderBatchList();
    previewBatch();
  }, true);

  // merge encoding segmented
  el("merge-enc")
    .querySelectorAll("button")
    .forEach((b) => {
      b.onclick = () => {
        el("merge-enc").querySelectorAll("button").forEach((x) => x.classList.remove("active"));
        b.classList.add("active");
        previewMerge();
      };
    });

  // conditional wraps
  el("scale-filter").addEventListener("change", () => {
    el("scale-custom-wrap").style.display =
      val("scale-filter") === "custom" ? "block" : "none";
    previewFilter();
  });
  el("wm-type").addEventListener("change", () => {
    const t = val("wm-type");
    el("wm-text-wrap").style.display = t === "text" ? "grid" : "none";
    el("wm-image-wrap").style.display = t === "image" ? "block" : "none";
    el("wm-pos-wrap").style.display = t === "none" ? "none" : "block";
    previewFilter();
  });
  el("twopass-compress").addEventListener("change", () => {
    el("target-wrap").style.display = checked("twopass-compress") ? "block" : "none";
    previewCompress();
  });

  // generic control -> preview wiring (per panel)
  const bind = (panelName, fn) => {
    const p = document.querySelector(`.panel[data-panel="${panelName}"]`);
    if (!p) return;
    p.addEventListener("input", () => schedulePreview(fn));
    p.addEventListener("change", () => schedulePreview(fn));
  };
  bind("convert", previewConvert);
  bind("trim", previewTrim);
  bind("merge", previewMerge);
  bind("compress", previewCompress);
  bind("filter", previewFilter);
  bind("custom", previewCustom);
  bind("batch", previewBatch);

  // run buttons
  el("run-convert").onclick = () =>
    run(specConvert(), "cmd-convert", "convert");
  el("run-trim").onclick = () => run(specTrim(), "cmd-trim", "trim");
  el("run-merge").onclick = () => run(specMerge(), "cmd-merge", "merge");
  el("run-compress").onclick = () => run(specCompress(), "cmd-compress", "compress");
  el("run-filter").onclick = () =>
    run(specFilter(), "cmd-filter", "filter");
  el("run-custom").onclick = () => run(specCustom(), "cmd-custom", "custom");
  el("run-batch").onclick = async () => {
    if (state.batch.length < 1) {
      toast("error", "请先添加文件");
      return;
    }
    let n = 0;
    for (const f of state.batch) {
      await submitJob(specBatchOne(f), f.name, f.name);
      n++;
    }
    toast("success", `已加入 ${n} 个批量任务`);
    goto("tasks");
  };

  // copy buttons
  ["convert", "trim", "merge", "compress", "filter", "custom", "batch"].forEach((m) => {
    const b = el("copy-" + m);
    if (b)
      b.onclick = () => copyCmd("cmd-" + m);
  });
}
