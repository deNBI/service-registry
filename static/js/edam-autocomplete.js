/**
 * de.NBI Service Registration Platform — EDAM Tag Picker, Compact Select & bio.tools Prefill
 *
 * buildEdamPicker      — pill-zone picker for EDAM ontology fields (select.edam-autocomplete)
 * buildCompactSelect   — searchable checkbox list for multi-select (select[data-compact-select="label"])
 *                        (currently: responsible_pis, service_categories)
 * buildCompactSelectSingle — searchable radio button list for single-select (select[data-compact-select-single="label"])
 *                        (currently: service_center)
 *                        Uses radio buttons to clearly communicate "pick one" to users
 * initBioToolsPrefill  — auto-fills form fields from bio.tools on URL entry
 */
"use strict";

/* ── shared helpers ──────────────────────────────────────────────────────── */
function _esc(s) {
  return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
}
function _hi(text, q) {
  if (!q) return _esc(text);
  return _esc(text).replace(new RegExp("(" + q.replace(/[.*+?^${}()|[\]\\]/g,"\\$&") + ")", "gi"), "<mark>$1</mark>");
}

/* ═══════════════════════════════════════════════════════════════════════════
   EDAM TAG PICKER
   Immediately hides the native <select class="edam-autocomplete"> and builds
   a pill-zone + search-input + fixed-position dropdown in its place.
   ═══════════════════════════════════════════════════════════════════════════ */
function buildEdamPicker(sel) {
  /* 1. Hide native select immediately (before any other work) */
  sel.style.cssText = "display:none!important;position:absolute;width:1px;height:1px;overflow:hidden";

  const MAX = parseInt(sel.dataset.maxItems || sel.dataset["max-items"] || "6", 10);
  const PH  = sel.dataset.placeholder || "Search EDAM terms…";
  const uid = "ep-" + (sel.id || Math.random().toString(36).slice(2));

  /* 2. Read all options once */
  const ALL = Array.from(sel.options)
    .map(o => ({ val: o.value, txt: o.text.trim(), sel: o.selected }))
    .filter(o => o.val && o.txt);

  /* 3. Selected state */
  const chosen = new Set(ALL.filter(o => o.sel).map(o => o.val));

  /* 4. Build widget */
  const root = document.createElement("div");
  root.id = uid;
  root.className = "ep-root";
  root.setAttribute("role","group");
  root.setAttribute("aria-label", PH);
  /* Inline base styles so they work even if registry.css hasn't loaded yet */
  root.style.cssText = "border:1.5px solid #d1d5db;border-radius:8px;background:#fff;font-family:inherit";

  root.innerHTML =
    `<div class="ep-header" style="display:flex;align-items:center;gap:.4rem;padding:.5rem .9rem .45rem;border-bottom:1px solid #f3f4f6;background:#f9fafb;border-radius:8px 8px 0 0">
       <span class="ep-title" style="font-size:.7rem;font-weight:700;letter-spacing:.07em;text-transform:uppercase;color:#6b7280;flex:1">${_esc(PH.replace(/…$/,""))}</span>
       <span class="ep-counter" id="${uid}-n" aria-live="polite" style="font-size:.7rem;font-weight:700;background:#f0f7e6;color:#4a7e1c;border:1px solid #c8e49e;border-radius:20px;padding:1px 8px;white-space:nowrap">0 / ${MAX}</span>
       <button type="button" class="ep-clear" id="${uid}-clr" aria-label="Clear all" style="display:none;font-size:.72rem;color:#9ca3af;background:none;border:1px solid transparent;border-radius:4px;padding:2px 6px;cursor:pointer">✕ Clear</button>
     </div>
     <div class="ep-pills" id="${uid}-pills" role="listbox" aria-multiselectable="true" style="min-height:44px;padding:.5rem .9rem .4rem;display:flex;flex-wrap:wrap;gap:.3rem;align-items:flex-start;border-bottom:1px solid #f3f4f6">
       <span class="ep-empty" id="${uid}-empty" style="font-size:.8rem;color:#9ca3af;font-style:italic;align-self:center">None selected — search below to add</span>
     </div>
     <div class="ep-maxwarn" id="${uid}-warn" role="alert" aria-live="polite" style="display:none;margin:.3rem .9rem;padding:.28rem .65rem;font-size:.775rem;color:#92400e;background:#fffbeb;border:1px solid #fde68a;border-radius:4px;text-align:center">
       Maximum ${MAX} terms reached — remove one to add another
     </div>
     <div style="padding:.5rem .9rem;position:relative">
       <div style="position:relative">
         <svg style="position:absolute;left:.6rem;top:50%;transform:translateY(-50%);pointer-events:none;color:#9ca3af" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>
         <input id="${uid}-inp" type="text" class="ep-input"
           placeholder="${_esc(PH)}" autocomplete="off" autocorrect="off" spellcheck="false"
           role="combobox" aria-expanded="false" aria-haspopup="listbox" aria-autocomplete="list"
           style="width:100%;border:1.5px solid #e5e7eb;border-radius:6px;padding:.4rem .7rem .4rem 2rem;font-size:.875rem;font-family:inherit;color:#1f2937;background:#fff;box-sizing:border-box;transition:border-color .15s">
       </div>
       <div id="${uid}-dd" role="listbox" style="display:none;position:fixed;z-index:9999;background:#fff;border:1.5px solid #e5e7eb;border-radius:8px;box-shadow:0 8px 28px rgba(0,0,0,.14);max-height:260px;overflow-y:auto;overscroll-behavior:contain"></div>
     </div>`;

  sel.parentNode.insertBefore(root, sel.nextSibling);

  /* 5. Refs */
  const pills   = root.querySelector("#" + uid + "-pills");
  const empty   = root.querySelector("#" + uid + "-empty");
  const counter = root.querySelector("#" + uid + "-n");
  const clrBtn  = root.querySelector("#" + uid + "-clr");
  const inp     = root.querySelector("#" + uid + "-inp");
  const dd      = root.querySelector("#" + uid + "-dd");
  const warn    = root.querySelector("#" + uid + "-warn");
  let hiIdx = -1, timer = null;

  /* 6. Sync to native select. `silent` skips the change event — used only
     during the initial render at build time so we don't trigger form-level
     `change` listeners (draft autosave, etc.) before the user has interacted. */
  function sync(silent) {
    Array.from(sel.options).forEach(o => { o.selected = chosen.has(o.value); });
    if (!silent) sel.dispatchEvent(new Event("change", {bubbles:true}));
  }

  /* 7. Render pills */
  function render(silent) {
    pills.querySelectorAll(".ep-pill").forEach(p => p.remove());
    const n = chosen.size;
    counter.textContent = n + " / " + MAX;
    counter.style.background = n >= MAX ? "#fffbeb" : "#f0f7e6";
    counter.style.color       = n >= MAX ? "#92400e" : "#4a7e1c";
    counter.style.borderColor = n >= MAX ? "#fde68a" : "#c8e49e";
    clrBtn.style.display   = n > 0 ? "inline-flex" : "none";
    empty.style.display    = n === 0 ? "" : "none";
    warn.style.display     = n >= MAX ? "block" : "none";
    inp.disabled           = n >= MAX;
    inp.placeholder        = n >= MAX ? "Maximum " + MAX + " terms selected" : PH;

    chosen.forEach(val => {
      const opt = ALL.find(o => o.val === val); if (!opt) return;
      const pill = document.createElement("div");
      pill.className = "ep-pill";
      pill.dataset.val = val;
      pill.setAttribute("role","option"); pill.setAttribute("aria-selected","true");
      pill.style.cssText = "display:inline-flex;align-items:center;gap:.28rem;background:#f0f7e6;border:1px solid #c8e49e;color:#3a6216;border-radius:20px;padding:3px 7px 3px 10px;font-size:.8rem;font-weight:500;animation:ep-in .13s ease;max-width:260px";
      pill.innerHTML =
        `<span style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis" title="${_esc(opt.txt)}">${_esc(opt.txt.replace(/ \(.*?\)$/,""))}</span>` +
        `<button type="button" aria-label="Remove ${_esc(opt.txt)}" style="flex-shrink:0;display:inline-flex;align-items:center;justify-content:center;width:16px;height:16px;border-radius:50%;background:rgba(92,157,37,.15);border:none;cursor:pointer;color:#4a7e1c;font-size:13px;padding:0;transition:background .12s">×</button>`;
      pill.querySelector("button").addEventListener("click", () => { chosen.delete(val); render(); sync(); inp.focus(); });
      pills.appendChild(pill);
    });
    sync(silent);
  }

  clrBtn.addEventListener("click", () => { chosen.clear(); render(); inp.focus(); });

  /* 8. Dropdown */
  function posDD() {
    const r = inp.getBoundingClientRect();
    dd.style.left   = r.left + "px";
    dd.style.width  = r.width + "px";
    const sb = window.innerHeight - r.bottom, sa = r.top;
    if (sb < 180 && sa > sb) { dd.style.top = "auto"; dd.style.bottom = (window.innerHeight - r.top + 4) + "px"; }
    else                     { dd.style.top = (r.bottom + 3) + "px"; dd.style.bottom = "auto"; }
  }

  function openDD(results) {
    hiIdx = -1; dd.innerHTML = ""; inp.setAttribute("aria-expanded","true"); posDD();
    if (!results.length) {
      dd.innerHTML = `<div style="padding:.9rem;text-align:center;color:#9ca3af;font-size:.875rem;font-style:italic">No results</div>`;
    } else {
      const q = inp.value.trim();
      results.forEach((opt, i) => {
        const isSel = chosen.has(opt.val);
        const item = document.createElement("div");
        item.dataset.val = opt.val; item.dataset.i = i;
        item.setAttribute("role","option"); item.setAttribute("aria-selected", isSel ? "true" : "false");
        item.style.cssText = "display:flex;align-items:center;gap:.5rem;padding:.48rem .9rem;cursor:" + (isSel?"default":"pointer") + ";border-bottom:1px solid #f3f4f6;" + (isSel?"background:#f0fdf4;color:#4a7e1c;pointer-events:none;":"");
        // Strip accession from display label
        const label = opt.txt.replace(/ \(.*?\)$/, "");
        const accs  = (opt.txt.match(/\(([^)]+)\)$/) || [])[1] || "";
        item.innerHTML =
          `<span style="flex:1;font-size:.875rem;font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${_hi(label,q)}</span>` +
          `<span style="font-size:.7rem;font-family:monospace;color:#9ca3af;white-space:nowrap">${_esc(accs)}</span>` +
          (isSel ? `<span style="color:#5c9d25;font-weight:700;font-size:.8rem">✓</span>` : "");
        if (!isSel) {
          item.addEventListener("mousedown", e => { e.preventDefault(); chosen.add(opt.val); render(); closeDD(); inp.value=""; inp.focus(); });
          item.addEventListener("mouseenter", () => setHi(i));
        }
        dd.appendChild(item);
      });
    }
    dd.style.display = "block";
  }

  function closeDD() { dd.style.display="none"; dd.innerHTML=""; inp.setAttribute("aria-expanded","false"); hiIdx=-1; }

  function setHi(idx) {
    const items = dd.querySelectorAll("[data-i]");
    items.forEach((el,i) => el.style.background = i===idx ? "#f0f7e6" : (chosen.has(el.dataset.val) ? "#f0fdf4" : ""));
    hiIdx = idx;
  }

  inp.addEventListener("focus", () => { inp.style.borderColor="#5c9d25"; inp.style.boxShadow="0 0 0 3px rgba(92,157,37,.22)"; });
  inp.addEventListener("blur",  () => { inp.style.borderColor="#e5e7eb"; inp.style.boxShadow="none"; setTimeout(closeDD,160); });

  inp.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(() => {
      const q = inp.value.trim().toLowerCase();
      if (!q) { closeDD(); return; }
      openDD(ALL.filter(o => o.txt.toLowerCase().includes(q)).slice(0,50));
    }, 100);
  });

  inp.addEventListener("keydown", e => {
    const open = dd.style.display !== "none";
    const items = dd.querySelectorAll("[data-i]");
    if (e.key === "Backspace" && !inp.value && chosen.size) {
      const last = Array.from(chosen).pop(); chosen.delete(last); render(); return;
    }
    if (!open) { if (e.key==="ArrowDown") { e.preventDefault(); inp.dispatchEvent(new Event("input")); } return; }
    if (e.key==="ArrowDown")  { e.preventDefault(); setHi(Math.min(hiIdx+1, items.length-1)); items[hiIdx]?.scrollIntoView({block:"nearest"}); }
    else if (e.key==="ArrowUp") { e.preventDefault(); setHi(Math.max(hiIdx-1,0)); items[hiIdx]?.scrollIntoView({block:"nearest"}); }
    else if (e.key==="Enter")   { e.preventDefault(); if (hiIdx>=0&&items[hiIdx]) { chosen.add(items[hiIdx].dataset.val); render(); closeDD(); inp.value=""; } }
    else if (e.key==="Escape")  { closeDD(); }
  });

  ["scroll","resize"].forEach(ev => window.addEventListener(ev, () => { if (dd.style.display!=="none") posDD(); }, {passive:true}));

  /* 9. Public API */
  sel._edamPicker = {
    addItem:    v => { if (chosen.size<MAX) { chosen.add(v); render(); } },
    removeItem: v => { chosen.delete(v); render(); },
    getValue:   ()   => Array.from(chosen),
    setValue:   vals => { chosen.clear(); vals.forEach(v => { if (chosen.size<MAX) chosen.add(v); }); render(); },
  };
  // Shared draft-restore hook consumed by form_scripts.html
  sel._draftRestore = vals => sel._edamPicker.setValue(Array.isArray(vals) ? vals : [vals].filter(Boolean));

  /* 10. Inject keyframe animation once */
  if (!document.getElementById("ep-style")) {
    const s = document.createElement("style");
    s.id = "ep-style";
    s.textContent = "@keyframes ep-in{from{opacity:0;transform:scale(.82)}to{opacity:1;transform:scale(1)}}";
    document.head.appendChild(s);
  }

  render(true);  // initial paint only — don't fire change (would trip draft autosave before restore)
}

/* ═══════════════════════════════════════════════════════════════════════════
   PI COMPACT SELECT
   Turns the tall native <select multiple> for responsible_pis into a
   compact search-filtered list with visible selections.
   ═══════════════════════════════════════════════════════════════════════════ */
function buildCompactSelect(sel, label) {
  /* Immediately collapse the native select */
  sel.style.cssText = "display:none!important;position:absolute;width:1px;height:1px";

  const uid = "cs-" + (sel.id || Math.random().toString(36).slice(2));
  const ALL = Array.from(sel.options).map(o => ({ val: o.value, txt: o.text.trim() })).filter(o => o.val);
  const chosen = new Set(Array.from(sel.selectedOptions).map(o => o.value));

  const root = document.createElement("div");
  root.id = uid;
  root.style.cssText = "border:1.5px solid #d1d5db;border-radius:8px;background:#fff;font-family:inherit;overflow:hidden";

  root.innerHTML =
    /* Selected pills — top, matching EDAM picker layout */
    `<div id="${uid}-sel" style="min-height:36px;padding:.4rem .9rem;border-bottom:1px solid #f3f4f6;display:flex;flex-wrap:wrap;gap:.28rem;align-items:center;background:#fafafa;border-radius:8px 8px 0 0">
       <span id="${uid}-sh" style="font-size:.78rem;color:#9ca3af;font-style:italic">No ${_esc(label)} selected</span>
     </div>
     <!-- Search input -->
     <div style="padding:.5rem .9rem;border-bottom:1px solid #f3f4f6;background:#f9fafb">
       <div style="position:relative">
         <svg style="position:absolute;left:.55rem;top:50%;transform:translateY(-50%);color:#9ca3af" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>
         <input id="${uid}-s" type="text" placeholder="Filter ${_esc(label)}…" autocomplete="off" spellcheck="false"
           style="width:100%;border:1px solid #e5e7eb;border-radius:5px;padding:.33rem .6rem .33rem 1.85rem;font-size:.82rem;font-family:inherit;box-sizing:border-box">
       </div>
     </div>
     <!-- Scrollable options list -->
     <div id="${uid}-list" style="max-height:160px;overflow-y:auto;overscroll-behavior:contain"></div>`;

  sel.parentNode.insertBefore(root, sel.nextSibling);

  const listEl  = root.querySelector("#" + uid + "-list");
  const selEl   = root.querySelector("#" + uid + "-sel");
  const selHint = root.querySelector("#" + uid + "-sh");
  const searchI = root.querySelector("#" + uid + "-s");

  function sync() {
    Array.from(sel.options).forEach(o => { o.selected = chosen.has(o.value); });
    sel.dispatchEvent(new Event("change",{bubbles:true}));
  }

  function renderList(filter) {
    listEl.innerHTML = "";
    const q = (filter||"").toLowerCase();
    ALL.filter(o => !q || o.txt.toLowerCase().includes(q)).forEach(opt => {
      const row = document.createElement("div");
      const isSel = chosen.has(opt.val);
      row.style.cssText = "display:flex;align-items:center;gap:.6rem;padding:.38rem .9rem;cursor:pointer;border-bottom:1px solid #f9fafb;" + (isSel?"background:#f0f7e6;":"");
      row.innerHTML =
        `<span style="display:inline-flex;align-items:center;justify-content:center;width:16px;height:16px;border-radius:3px;border:1.5px solid ${isSel?"#5c9d25":"#d1d5db"};background:${isSel?"#5c9d25":"#fff"};flex-shrink:0;transition:all .12s">
           ${isSel?`<svg width="10" height="10" viewBox="0 0 10 10" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round"><polyline points="1.5,5 4,8 8.5,2"/></svg>`:""}
         </span>
         <span style="font-size:.875rem;color:#374151;flex:1">${_hi(opt.txt,filter||"")}</span>`;
      row.addEventListener("mousedown", e => { e.preventDefault(); });
      row.addEventListener("click", () => {
        if (chosen.has(opt.val)) chosen.delete(opt.val); else chosen.add(opt.val);
        sync(); renderList(searchI.value); renderSel();
      });
      listEl.appendChild(row);
    });
    if (!listEl.children.length) listEl.innerHTML = `<div style="padding:.7rem .9rem;font-size:.82rem;color:#9ca3af;font-style:italic">No matches</div>`;
  }

  function renderSel() {
    selEl.querySelectorAll(".cs-pill").forEach(p=>p.remove());
    if (chosen.size === 0) { selHint.style.display=""; return; }
    selHint.style.display = "none";
    chosen.forEach(val => {
      const opt = ALL.find(o=>o.val===val); if (!opt) return;
      const pill = document.createElement("div");
      pill.className="cs-pill";
      pill.style.cssText="display:inline-flex;align-items:center;gap:.25rem;background:#f0f7e6;border:1px solid #c8e49e;color:#3a6216;border-radius:20px;padding:2px 7px 2px 9px;font-size:.78rem;font-weight:500";
      pill.innerHTML=`<span>${_esc(opt.txt)}</span><button type="button" aria-label="Remove" style="background:none;border:none;cursor:pointer;color:#4a7e1c;font-size:13px;padding:0;line-height:1;display:inline-flex;align-items:center">×</button>`;
      pill.querySelector("button").addEventListener("click",()=>{ chosen.delete(val); sync(); renderList(searchI.value); renderSel(); });
      selEl.appendChild(pill);
    });
  }

  searchI.addEventListener("input", () => renderList(searchI.value));
  searchI.addEventListener("focus", () => { searchI.style.borderColor="#5c9d25"; searchI.style.boxShadow="0 0 0 3px rgba(92,157,37,.2)"; });
  searchI.addEventListener("blur",  () => { searchI.style.borderColor="#e5e7eb"; searchI.style.boxShadow="none"; });

  // Shared draft-restore hook
  sel._draftRestore = function(vals) {
    const list = Array.isArray(vals) ? vals : [vals].filter(Boolean);
    chosen.clear();
    list.forEach(v => { if (ALL.find(o => o.val === v)) chosen.add(v); });
    sync(); renderList(searchI.value); renderSel();
  };

  renderList(); renderSel();
}

/* ═══════════════════════════════════════════════════════════════════════════
   SINGLE-SELECT COMPACT WITH RADIO BUTTONS
   Like buildCompactSelect but restricted to one item only.
   Uses radio buttons (instead of checkboxes) to clearly signal single-select.
   Used for fields like service_center.
   ═══════════════════════════════════════════════════════════════════════════ */
function buildCompactSelectSingle(sel, label) {
  /* Immediately collapse the native select */
  sel.style.cssText = "display:none!important;position:absolute;width:1px;height:1px";

  const uid = "css-" + (sel.id || Math.random().toString(36).slice(2));
  const ALL = Array.from(sel.options).map(o => ({ val: o.value, txt: o.text.trim() })).filter(o => o.val);
  const chosen = new Set(Array.from(sel.selectedOptions).map(o => o.value));

  const root = document.createElement("div");
  root.id = uid;
  root.style.cssText = "border:1.5px solid #d1d5db;border-radius:8px;background:#fff;font-family:inherit;overflow:hidden";

  root.innerHTML =
    /* Selected pill — top */
    `<div id="${uid}-sel" style="min-height:36px;padding:.4rem .9rem;border-bottom:1px solid #f3f4f6;display:flex;flex-wrap:wrap;gap:.28rem;align-items:center;background:#fafafa;border-radius:8px 8px 0 0">
       <span id="${uid}-sh" style="font-size:.78rem;color:#9ca3af;font-style:italic">No ${_esc(label)} selected</span>
     </div>
     <!-- Search input -->
     <div style="padding:.5rem .9rem;border-bottom:1px solid #f3f4f6;background:#f9fafb">
       <div style="position:relative">
         <svg style="position:absolute;left:.55rem;top:50%;transform:translateY(-50%);color:#9ca3af" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>
         <input id="${uid}-s" type="text" placeholder="Search and select ${_esc(label)}…" autocomplete="off" spellcheck="false"
           style="width:100%;border:1px solid #e5e7eb;border-radius:5px;padding:.33rem .6rem .33rem 1.85rem;font-size:.82rem;font-family:inherit;box-sizing:border-box">
       </div>
     </div>
     <!-- Scrollable options list -->
     <div id="${uid}-list" style="max-height:160px;overflow-y:auto;overscroll-behavior:contain"></div>`;

  sel.parentNode.insertBefore(root, sel.nextSibling);

  const listEl  = root.querySelector("#" + uid + "-list");
  const selEl   = root.querySelector("#" + uid + "-sel");
  const selHint = root.querySelector("#" + uid + "-sh");
  const searchI = root.querySelector("#" + uid + "-s");

  function sync() {
    Array.from(sel.options).forEach(o => { o.selected = chosen.has(o.value); });
    sel.dispatchEvent(new Event("change",{bubbles:true}));
  }

  function renderList(filter) {
    listEl.innerHTML = "";
    const q = (filter||"").toLowerCase();
    ALL.filter(o => !q || o.txt.toLowerCase().includes(q)).forEach(opt => {
      const row = document.createElement("div");
      const isSel = chosen.has(opt.val);
      row.style.cssText = "display:flex;align-items:center;gap:.6rem;padding:.38rem .9rem;cursor:pointer;border-bottom:1px solid #f9fafb;" + (isSel?"background:#f0f7e6;":"");
      /* Radio button: circular indicator with filled center when selected */
      row.innerHTML =
        `<span style="display:inline-flex;align-items:center;justify-content:center;width:16px;height:16px;border-radius:50%;border:2px solid ${isSel?"#5c9d25":"#d1d5db"};background:#fff;flex-shrink:0;transition:all .12s;position:relative">
           ${isSel?`<span style="position:absolute;width:6px;height:6px;border-radius:50%;background:#5c9d25"></span>`:""}
         </span>
         <span style="font-size:.875rem;color:#374151;flex:1">${_hi(opt.txt,filter||"")}</span>`;
      row.addEventListener("mousedown", e => { e.preventDefault(); });
      row.addEventListener("click", () => {
        /* Single-select only: clear previous selections */
        chosen.clear();
        chosen.add(opt.val);
        sync(); renderList(searchI.value); renderSel(); searchI.focus();
      });
      listEl.appendChild(row);
    });
    if (!listEl.children.length) listEl.innerHTML = `<div style="padding:.7rem .9rem;font-size:.82rem;color:#9ca3af;font-style:italic">No matches</div>`;
  }

  function renderSel() {
    selEl.querySelectorAll(".css-pill").forEach(p=>p.remove());
    if (chosen.size === 0) { selHint.style.display=""; return; }
    selHint.style.display = "none";
    chosen.forEach(val => {
      const opt = ALL.find(o=>o.val===val); if (!opt) return;
      const pill = document.createElement("div");
      pill.className="css-pill";
      pill.style.cssText="display:inline-flex;align-items:center;gap:.25rem;background:#f0f7e6;border:1px solid #c8e49e;color:#3a6216;border-radius:20px;padding:2px 7px 2px 9px;font-size:.78rem;font-weight:500";
      pill.innerHTML=`<span>${_esc(opt.txt)}</span><button type="button" aria-label="Remove" style="background:none;border:none;cursor:pointer;color:#4a7e1c;font-size:13px;padding:0;line-height:1;display:inline-flex;align-items:center">×</button>`;
      pill.querySelector("button").addEventListener("click",()=>{ chosen.clear(); sync(); renderList(searchI.value); renderSel(); searchI.focus(); });
      selEl.appendChild(pill);
    });
  }

  searchI.addEventListener("input", () => renderList(searchI.value));
  searchI.addEventListener("focus", () => { searchI.style.borderColor="#5c9d25"; searchI.style.boxShadow="0 0 0 3px rgba(92,157,37,.2)"; });
  searchI.addEventListener("blur",  () => { searchI.style.borderColor="#e5e7eb"; searchI.style.boxShadow="none"; });

  // Shared draft-restore hook (single-select: first value wins)
  sel._draftRestore = function(vals) {
    const v = Array.isArray(vals) ? vals[0] : vals;
    chosen.clear();
    if (v && ALL.find(o => o.val === v)) chosen.add(v);
    sync(); renderList(searchI.value); renderSel();
  };

  renderList(); renderSel();
}

/* ═══════════════════════════════════════════════════════════════════════════
   bio.tools PREFILL  (unchanged)
   ═══════════════════════════════════════════════════════════════════════════ */
let _prefillData = null;
function initBioToolsPrefill() {
  const bi = document.getElementById("id_biotools_url"); if (!bi) return;
  const banner = document.getElementById("biotools-prefill-banner");
  const errBan = document.getElementById("biotools-error-banner");
  const applyB = document.getElementById("biotools-apply-btn");
  const dismissB = document.getElementById("biotools-dismiss-btn");
  const fieldsL = document.getElementById("biotools-prefill-fields");
  const errMsg  = document.getElementById("biotools-error-msg");
  if (!banner||!errBan) return;

  bi.addEventListener("blur", function() {
    const url = this.value.trim(); if (!url) return;
    let id = url.startsWith("https://bio.tools/") ? url.replace("https://bio.tools/","").replace(/\/$/,"") : url;
    if (!id||(_prefillData&&_prefillData.biotools_id===id)) return;
    banner.classList.add("d-none"); errBan.classList.add("d-none"); _prefillData=null;
    fetch("/biotools/prefill/?id="+encodeURIComponent(id),{headers:{"X-Requested-With":"XMLHttpRequest"}})
      .then(r=>r.json().then(d=>({ok:r.ok,d})))
      .then(({ok,d})=>{ if(ok&&d.found){_prefillData=d;showBanner(d);}else showErr(d.error||"Lookup failed."); })
      .catch(()=>showErr("bio.tools temporarily unavailable."));
  });

  function showBanner(d) {
    const a=[];
    if(d.name)a.push("Name"); if(d.description)a.push("Description"); if(d.homepage)a.push("Homepage");
    if(d.license)a.push("License"); if(d.publications)a.push("Publications"); if(d.github_url)a.push("GitHub");
    if(d.edam_topics?.length)a.push(d.edam_topics.length+" EDAM Topic(s)");
    if(d.edam_operations?.length)a.push(d.edam_operations.length+" EDAM Operation(s)");
    if(fieldsL)fieldsL.textContent="Available: "+a.join(", ");
    banner.classList.remove("d-none");
  }
  function showErr(msg){ if(errMsg)errMsg.textContent=msg; errBan.classList.remove("d-none"); }
  applyB?.addEventListener("click",()=>{ if(!_prefillData)return; apply(_prefillData); banner.classList.add("d-none"); });
  dismissB?.addEventListener("click",()=>{ banner.classList.add("d-none"); errBan.classList.add("d-none"); });

  function apply(d) {
    const m={id_service_name:d.name,id_service_description:d.description,id_website_url:d.homepage,id_github_url:d.github_url,id_publications_pmids:d.publications};
    Object.entries(m).forEach(([id,val])=>{ if(!val)return; const el=document.getElementById(id); if(el&&!el.value){el.value=val;el.dispatchEvent(new Event("change",{bubbles:true}));} });
    if (d.license) {
      const lic=document.getElementById("id_licenses");
      const note=document.getElementById("id_license_note");
      let matched=false;
      if (lic && lic._edamPicker && !lic.selectedOptions.length) {
        const n=d.license.toLowerCase().replace(/[^a-z0-9]/g,"");
        for (const o of lic.options) {
          if (o.value.toLowerCase().replace(/[^a-z0-9]/g,"")===n) {
            lic._edamPicker.addItem(o.value);
            matched=true;
            break;
          }
        }
      }
      // Fallback: if bio.tools returned a non-SPDX license string, capture it in license_note
      // (truncate to the field's max_length=200 to avoid a silent backend validation error).
      if (!matched && note && !note.value) {
        note.value=d.license.slice(0,200);
        note.dispatchEvent(new Event("change",{bubbles:true}));
      }
    }
    applyEdam("id_edam_topics",d.edam_topics||[]);
    applyEdam("id_edam_operations",d.edam_operations||[]);
  }
  function applyEdam(sid,terms){
    const el=document.getElementById(sid); if(!el?._edamPicker)return;
    terms.forEach(t=>{ const o=Array.from(el.options).find(x=>x.text.toLowerCase().replace(/ \(.*?\)$/,"").trim()===(t.term||"").toLowerCase()); if(o)el._edamPicker.addItem(o.value); });
  }
}

/* ═══════════════════════════════════════════════════════════════════════════
   BOOT
   ═══════════════════════════════════════════════════════════════════════════ */
document.addEventListener("DOMContentLoaded", () => {
  /* EDAM pickers */
  document.querySelectorAll("select.edam-autocomplete").forEach(buildEdamPicker);

  /* Compact select — multi-select */
  document.querySelectorAll("select[data-compact-select]").forEach(el => {
    buildCompactSelect(el, el.dataset.compactSelect);
  });

  /* Compact select — single-select */
  document.querySelectorAll("select[data-compact-select-single]").forEach(el => {
    buildCompactSelectSingle(el, el.dataset.compactSelectSingle);
  });

  initBioToolsPrefill();
});
