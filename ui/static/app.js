(() => {
  "use strict";

  const STYLE_META = [
    { key: "formal", label: "Formal", cls: "formal", var: "--voice-formal", speech: { rate: 0.92, pitch: 0.85 } },
    { key: "sarcastic", label: "Sarcastic", cls: "sarcastic", var: "--voice-sarcastic", speech: { rate: 0.82, pitch: 1.08 } },
    { key: "humorous_tech", label: "Humorous — Tech", cls: "tech", var: "--voice-tech", speech: { rate: 1.05, pitch: 0.95 } },
    { key: "humorous_non_tech", label: "Humorous — Everyday", cls: "everyday", var: "--voice-everyday", speech: { rate: 1.1, pitch: 1.18 } },
  ];

  const MAX_CLIPS = 1;

  const $ = (id) => document.getElementById(id);
  const dropzone = $("dropzone"), fileInput = $("fileInput");
  const examplesEl = $("examples");
  const carousel = $("carousel"), carouselStage = $("carouselStage");
  const styleToggles = $("styleToggles"), runBtn = $("runBtn");
  const progressBlock = $("progressBlock"), progressCurrent = $("progressCurrent"), stageLog = $("stageLog");
  const errorBox = $("errorBox"), results = $("results"), resultMeta = $("resultMeta");
  const modelBadge = $("modelBadge"), modelBadgeName = $("modelBadgeName");

  // Display names for the models the pipeline can use. The backup entry must
  // match llm.py's FALLBACK_MODEL; unknown ids fall through as raw text.
  const BACKUP_MODEL_ID = "gemma-4-26b-a4b-it";
  const MODEL_NAMES = {
    "gemma-4-31b-it": "Gemma 4 31B",
    "gemma-4-26b-a4b-it": "Gemma 4 26B",
  };
  function modelLabel(id) {
    if (!id) return null;
    const name = MODEL_NAMES[id] || id;
    return id === BACKUP_MODEL_ID ? `${name} — backup` : name;
  }

  // Live "which model is running this" badge in the progress block, fed by
  // /api/health's active_model (polled fast while a run is in flight).
  function renderModelBadge(activeModel) {
    if (!modelBadge) return;
    if (!activeModel || !isRunning) {
      modelBadge.hidden = true;
      return;
    }
    modelBadgeName.textContent = modelLabel(activeModel);
    modelBadge.classList.toggle("backup", activeModel === BACKUP_MODEL_ID);
    modelBadge.hidden = false;
  }

  // Poll /api/health only to show the active model badge while a run is in flight.
  async function pollHealth() {
    try {
      const res = await fetch("/api/health");
      if (!res.ok) return;
      const h = await res.json();
      renderModelBadge(h.active_model);
    } catch (e) { /* server unreachable; ignore */ }
  }
  const cardGrid = $("cardGrid"), evidenceFacts = $("evidenceFacts"), evidenceFlags = $("evidenceFlags");
  const resultClipName = $("resultClipName");
  const themeToggle = $("themeToggle");

  // ---------- Theme ----------
  const themeLabel = $("themeLabel");
  const THEME_CYCLE = ["dark", "light"];
  const systemDefault = matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
  let theme = localStorage.getItem("fourfaced-theme") || systemDefault;
  function applyTheme() {
    document.documentElement.setAttribute("data-theme", theme);
    themeToggle.setAttribute("aria-checked", String(theme === "dark"));
    themeLabel.textContent = theme.toUpperCase();
  }
  themeToggle.addEventListener("click", () => {
    theme = THEME_CYCLE[(THEME_CYCLE.indexOf(theme) + 1) % THEME_CYCLE.length];
    localStorage.setItem("fourfaced-theme", theme);
    applyTheme();
  });
  applyTheme();

  // ---------- Style toggles ----------
  STYLE_META.forEach((s) => {
    const label = document.createElement("label");
    label.className = "style-toggle";
    label.innerHTML = `<input type="checkbox" checked data-style="${s.key}"> ${s.label.toUpperCase()}`;
    styleToggles.appendChild(label);
  });
  function selectedStyles() {
    return [...styleToggles.querySelectorAll("input:checked")].map((i) => i.dataset.style);
  }

  // Unticking a Face in the picker shadows its chip in the line-up below.
  const voiceChips = document.querySelectorAll(".voice-chip[data-style]");
  function syncVoiceChips() {
    const active = new Set(selectedStyles());
    voiceChips.forEach((chip) => chip.classList.toggle("dimmed", !active.has(chip.dataset.style)));
  }
  styleToggles.addEventListener("change", () => {
    syncVoiceChips();
    updateChrome();
  });
  syncVoiceChips();

  // ---------- Single clip management ----------
  // The UI only supports one clip at a time. The carousel container is kept
  // because it hosts the video slide and its remove (X) button.
  let clips = []; // { id, kind: 'file'|'example', file?, url?, name, objectUrl, status, result, runStyles, slideEl, videoEl }
  let focusIdx = 0;
  let isRunning = false;
  let idCounter = 0;

  function formatTime(s) {
    if (!isFinite(s) || s < 0) return "0:00";
    const m = Math.floor(s / 60);
    const sec = Math.floor(s % 60);
    return `${m}:${String(sec).padStart(2, "0")}`;
  }

  // Per-slide seek bar: same behaviour as the old single-player bar, scoped
  // to this slide's own elements (class-based, no page-global IDs).
  function wireSlideMedia(clip) {
    const video = clip.videoEl;
    const vp = clip.slideEl.querySelector(".video-progress");
    const track = vp.querySelector(".video-progress-track");
    const fill = vp.querySelector(".video-progress-fill");
    const buffered = vp.querySelector(".video-progress-buffered");
    const thumb = vp.querySelector(".video-progress-thumb");
    const cur = vp.querySelector(".vp-current");
    const dur = vp.querySelector(".vp-duration");

    function update() {
      const duration = video.duration;
      const pct = duration ? (video.currentTime / duration) * 100 : 0;
      fill.style.width = `${pct}%`;
      thumb.style.left = `${pct}%`;
      cur.textContent = formatTime(video.currentTime);
      dur.textContent = formatTime(duration || 0);
      vp.setAttribute("aria-valuenow", String(Math.round(pct)));
      if (video.buffered.length && duration) {
        const bufEnd = video.buffered.end(video.buffered.length - 1);
        buffered.style.width = `${Math.min(100, (bufEnd / duration) * 100)}%`;
      } else {
        buffered.style.width = "0%";
      }
    }
    ["timeupdate", "progress", "loadedmetadata", "durationchange"].forEach((evt) =>
      video.addEventListener(evt, update));

    function seekFromClientX(clientX) {
      const rect = track.getBoundingClientRect();
      const ratio = Math.min(1, Math.max(0, (clientX - rect.left) / rect.width));
      if (video.duration) {
        video.currentTime = ratio * video.duration;
        update();
      }
    }
    let dragging = false;
    track.addEventListener("pointerdown", (e) => {
      dragging = true;
      vp.classList.add("dragging");
      track.setPointerCapture(e.pointerId);
      seekFromClientX(e.clientX);
    });
    track.addEventListener("pointermove", (e) => { if (dragging) seekFromClientX(e.clientX); });
    ["pointerup", "pointercancel"].forEach((evt) =>
      track.addEventListener(evt, () => {
        dragging = false;
        vp.classList.remove("dragging");
      }));
    vp.addEventListener("keydown", (e) => {
      if (!video.duration) return;
      const step = video.duration * 0.02;
      if (e.key === "ArrowRight") {
        e.stopPropagation();
        video.currentTime = Math.min(video.duration, video.currentTime + step);
        update();
      } else if (e.key === "ArrowLeft") {
        e.stopPropagation();
        video.currentTime = Math.max(0, video.currentTime - step);
        update();
      }
    });
  }

  function buildSlide(clip) {
    const slide = document.createElement("div");
    slide.className = "clip-slide";
    slide.innerHTML = `
      <div class="clip-frame">
        <video controls playsinline preload="metadata"></video>
        <button class="clear-btn" type="button" aria-label="Remove clip">&times;</button>
        <span class="clip-status" hidden></span>
      </div>
      <div class="video-progress" role="slider" aria-label="Seek" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0" tabindex="0">
        <div class="video-progress-track">
          <div class="video-progress-buffered"></div>
          <div class="video-progress-fill"></div>
          <div class="video-progress-thumb"></div>
        </div>
        <div class="video-progress-time"><span class="vp-current">0:00</span><span class="vp-duration">0:00</span></div>
      </div>
      <p class="clip-slide-name"></p>
    `;
    slide.querySelector(".clip-slide-name").textContent = clip.name;
    const video = slide.querySelector("video");
    if (clip.kind === "file") {
      clip.objectUrl = URL.createObjectURL(clip.file);
      video.src = clip.objectUrl;
    } else {
      video.src = clip.url;
    }
    clip.videoEl = video;
    clip.slideEl = slide;
    wireSlideMedia(clip);

    slide.addEventListener("click", () => {
      const idx = clips.indexOf(clip);
      if (idx !== -1 && idx !== focusIdx) focusClip(idx);
    });
    slide.querySelector(".clear-btn").addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      removeClip(clip.id);
    });
    return slide;
  }

  // Transient warning that auto-clears (unlike showError, which sticks).
  function showNotice(msg) {
    errorBox.textContent = msg;
    errorBox.classList.add("visible");
    setTimeout(() => {
      if (errorBox.textContent === msg) errorBox.classList.remove("visible");
    }, 3500);
  }

  function addClip(item) {
    if (isRunning) return;
    // Single-clip mode: replace any existing clip with the new one.
    if (clips.length) removeClip(clips[0].id);
    const clip = { id: `c${++idCounter}`, status: "ready", result: null, runStyles: null, objectUrl: null, ...item };
    carouselStage.appendChild(buildSlide(clip));
    clips.push(clip);
    focusClip(0, { force: true });
    updateChrome();
  }

  function removeClip(id) {
    if (isRunning) return;
    const idx = clips.findIndex((c) => c.id === id);
    if (idx === -1) return;
    const clip = clips[idx];
    stopSpeaking();
    clip.videoEl.pause();
    if (clip.objectUrl) URL.revokeObjectURL(clip.objectUrl);
    clip.slideEl.remove();
    clips.splice(idx, 1);
    if (clips.length === 0) {
      focusIdx = 0;
      results.classList.remove("visible");
      progressBlock.classList.remove("visible");
      errorBox.classList.remove("visible");
      fileInput.value = "";
      updateChrome();
    } else {
      focusClip(Math.min(idx, clips.length - 1), { force: true });
      updateChrome();
    }
  }

  function focusClip(idx, opts = {}) {
    if (!clips.length) return;
    idx = Math.max(0, Math.min(clips.length - 1, idx));
    if (idx === focusIdx && !opts.force) return;
    const prev = clips[focusIdx];
    if (prev && prev.videoEl && idx !== focusIdx) prev.videoEl.pause();
    stopSpeaking();
    focusIdx = idx;
    layoutCarousel();
    renderFocusedResults();
    updateChrome();
  }

  function layoutCarousel() {
    clips.forEach((clip, i) => {
      const off = i - focusIdx;
      const el = clip.slideEl;
      el.classList.toggle("focused", off === 0);
      el.style.zIndex = String(10 - Math.abs(off));
      if (off === 0) {
        el.style.transform = "translateX(0) scale(1) rotateY(0deg)";
        el.style.opacity = "1";
      } else if (Math.abs(off) === 1) {
        el.style.transform = `translateX(${off * 58}%) scale(0.78) rotateY(${off * -9}deg)`;
        el.style.opacity = "1";
      } else {
        el.style.transform = `translateX(${off * 116}%) scale(0.6) rotateY(${off * -9}deg)`;
        el.style.opacity = "0";
      }
      el.style.pointerEvents = Math.abs(off) > 1 ? "none" : "";
    });
  }

  function setClipStatus(clip, status, tooltip) {
    clip.status = status;
    const badge = clip.slideEl.querySelector(".clip-status");
    const labels = { queued: "QUEUED", running: "CAPTIONING…", done: "DONE", error: "FAILED" };
    if (labels[status]) {
      badge.hidden = false;
      badge.textContent = labels[status];
      badge.className = `clip-status ${status}`;
      badge.title = tooltip || "";
    } else {
      badge.hidden = true;
      badge.className = "clip-status";
    }
  }

  function updateChrome() {
    const hasClips = clips.length > 0;
    dropzone.classList.toggle("hidden", hasClips);
    carousel.classList.toggle("visible", hasClips);
    carousel.classList.toggle("single", true);
    carousel.classList.toggle("locked", isRunning);

    [...examplesEl.children].forEach((pill) =>
      pill.classList.toggle("active",
        clips.some((c) => c.kind === "example" && c.url === pill.dataset.url)));

    runBtn.textContent = "Caption this clip";
    runBtn.disabled = isRunning || !hasClips || selectedStyles().length === 0;
  }

  fileInput.addEventListener("change", () => {
    const file = fileInput.files[0];
    if (file && file.type.startsWith("video/")) addClip({ kind: "file", file, name: file.name });
    fileInput.value = "";
  });
  // Drag-and-drop onto the stage replaces the current single clip.
  const stageEl = document.querySelector(".stage");
  ["dragover", "dragenter"].forEach((evt) =>
    stageEl.addEventListener(evt, (e) => {
      e.preventDefault();
      (clips.length ? carousel : dropzone).classList.add("drag-over");
    }));
  stageEl.addEventListener("dragleave", (e) => {
    if (e.relatedTarget && stageEl.contains(e.relatedTarget)) return;
    dropzone.classList.remove("drag-over");
    carousel.classList.remove("drag-over");
  });
  stageEl.addEventListener("drop", (e) => {
    e.preventDefault();
    dropzone.classList.remove("drag-over");
    carousel.classList.remove("drag-over");
    const file = e.dataTransfer.files[0];
    if (file && file.type.startsWith("video/")) addClip({ kind: "file", file, name: file.name });
  });

  fetch("/api/examples").then((r) => r.json()).then((examples) => {
    examples.forEach((ex) => {
      const btn = document.createElement("button");
      btn.className = "example-pill";
      btn.type = "button";
      btn.textContent = ex.label;
      btn.dataset.url = ex.url;
      btn.addEventListener("click", () => {
        const existing = clips.findIndex((c) => c.kind === "example" && c.url === ex.url);
        if (existing !== -1) focusClip(existing);
        else addClip({ kind: "example", url: ex.url, name: `Example: ${ex.label}` });
      });
      examplesEl.appendChild(btn);
    });
  }).catch(() => {});

  // ---------- Run (single clip) ----------
  runBtn.addEventListener("click", runSingle);

  async function runSingle() {
    const styles = selectedStyles();
    if (!clips.length || !styles.length || isRunning) return;

    const clip = clips[0];
    stopSpeaking();
    isRunning = true;
    const modelPoll = setInterval(pollHealth, 2000);
    errorBox.classList.remove("visible");
    clip.result = null;
    setClipStatus(clip, "queued");
    renderFocusedResults();
    updateChrome();
    progressBlock.classList.add("visible");

    focusClip(0, { force: true });
    setClipStatus(clip, "running");
    stageLog.innerHTML = "";
    progressCurrent.innerHTML = `<span class="dot"></span> Uploading…`;

    try {
      const data = await runOne(clip, styles, (label) => {
        progressCurrent.innerHTML = `<span class="dot"></span> ${label}`;
        logStage(label);
      });
      clip.result = data;
      clip.runStyles = styles;
      setClipStatus(clip, "done");
      progressCurrent.innerHTML = `<span class="dot"></span> Done in ${data.total_s ?? "?"}s`;
      renderFocusedResults();
      pollHealth();
    } catch (err) {
      setClipStatus(clip, "error", err.message);
      pollHealth();
      if (err.fatal) showError(err.message);
      else logStage(`failed: ${err.message}`);
    }

    isRunning = false;
    clearInterval(modelPoll);
    if (modelBadge) modelBadge.hidden = true;
    updateChrome();
  }

  function logStage(label) {
    const li = document.createElement("li");
    li.textContent = label;
    stageLog.appendChild(li);
    stageLog.scrollTop = stageLog.scrollHeight;
  }

  // Uploads + streams one clip through the pipeline. Resolves with the
  // "done" payload, or rejects with an Error (marked .fatal when the whole
  // server is unreachable, vs. a single clip failing mid-run).
  function runOne(clip, styles, onStage) {
    return new Promise((resolve, reject) => {
      (async () => {
        try {
          const form = new FormData();
          if (clip.kind === "file") form.append("video", clip.file);
          else form.append("example_url", clip.url);

          const uploadRes = await fetch("/api/upload", { method: "POST", body: form });
          const uploadData = await uploadRes.json();
          if (!uploadRes.ok) throw new Error(uploadData.error || "Upload failed");

          const es = new EventSource(`/api/run/${uploadData.job_id}?styles=${encodeURIComponent(styles.join(","))}`);
          es.addEventListener("stage", (e) => onStage(JSON.parse(e.data).label));
          es.addEventListener("done", (e) => { es.close(); resolve(JSON.parse(e.data)); });
          es.onerror = () => {
            es.close();
            reject(new Error(`Lost connection to the FourFaced server while captioning "${clip.name}".`));
          };
        } catch (err) {
          const fatal = err instanceof TypeError;
          const message = fatal
            ? "Can't reach the FourFaced server. Make sure ui/server.py is running (python ui/server.py), then try again."
            : (err.message || String(err));
          const wrapped = new Error(message);
          wrapped.fatal = fatal;
          reject(wrapped);
        }
      })();
    });
  }

  // ---------- Text-to-speech ----------
  let speakingBtn = null;
  let speakingCard = null;

  function stopSpeaking() {
    if ("speechSynthesis" in window) window.speechSynthesis.cancel();
    if (speakingBtn) speakingBtn.classList.remove("speaking");
    if (speakingCard) speakingCard.classList.remove("card-speaking");
    speakingBtn = null;
    speakingCard = null;
  }

  function toggleSpeak(btn, card, text, voiceParams) {
    const wasThisBtn = speakingBtn === btn;
    stopSpeaking();
    if (wasThisBtn) return; // clicking the active speaker just stops it

    const utter = new SpeechSynthesisUtterance(text);
    utter.rate = voiceParams.rate;
    utter.pitch = voiceParams.pitch;
    utter.onstart = () => {
      speakingBtn = btn;
      speakingCard = card;
      btn.classList.add("speaking");
      btn.setAttribute("aria-pressed", "true");
      card.classList.add("card-speaking");
    };
    const reset = () => {
      btn.classList.remove("speaking");
      btn.setAttribute("aria-pressed", "false");
      card.classList.remove("card-speaking");
      if (speakingBtn === btn) speakingBtn = null;
      if (speakingCard === card) speakingCard = null;
    };
    utter.onend = reset;
    utter.onerror = reset;
    window.speechSynthesis.speak(utter);
  }

  function showError(msg) {
    errorBox.textContent = msg;
    errorBox.classList.add("visible");
    progressBlock.classList.remove("visible");
    setSignalState("idle");
  }

  // ---------- Results (always the focused clip's) ----------
  function renderFocusedResults() {
    const clip = clips[focusIdx];
    if (!clip || !clip.result) {
      results.classList.remove("visible");
      return;
    }
    renderResults(clip);
    results.classList.remove("swap");
    void results.offsetWidth; // restart the swap animation
    results.classList.add("swap");
  }

  function renderResults(clip) {
    const data = clip.result;
    const requestedStyles = clip.runStyles || STYLE_META.map((s) => s.key);
    resultClipName.textContent = clips.length > 1 ? ` — ${clip.name}` : "";

    resultMeta.innerHTML = "";
    const budget = data.budget_s;
    let totalCls = "";
    if (budget && data.total_s != null) {
      if (data.total_s >= budget) totalCls = "budget-over";
      else if (data.total_s >= 0.8 * budget) totalCls = "budget-warn";
    }
    // Which model actually served each stage — the headline transparency row.
    const models = data.models || {};
    const groundedBy = models.ground ? modelLabel(models.ground)
      : (data.facts ? "?" : "failed — no model answered");
    const styledBy = models.style ? modelLabel(models.style)
      : (data.facts ? "facts template (no AI call)" : "generic stand-in");
    const chipCls = (id) => id === BACKUP_MODEL_ID ? "model-chip backup"
      : id ? "model-chip" : "model-chip none";

    const metaItems = [
      [`Grounded by`, groundedBy, chipCls(models.ground)],
      [`Styled by`, styledBy, chipCls(models.style)],
      data.duration_s != null ? [`Clip length`, `${data.duration_s}s`] : null,
      [`Frames sampled`, data.frame_count],
      [`Total run`, `${data.total_s ?? "?"}s${budget ? ` / ${budget}s budget` : ""}`, totalCls],
      [`Refinement pass`, data.style_thinking
        ? `completed${models.refine ? ` (${modelLabel(models.refine)})` : ""}`
        : "skipped (budget)"],
    ].filter(Boolean);
    metaItems.forEach(([label, val, cls]) => {
      const span = document.createElement("span");
      if (cls) span.className = cls;
      span.innerHTML = `${label}: <b>${val}</b>`;
      resultMeta.appendChild(span);
    });

    cardGrid.innerHTML = "";
    STYLE_META.filter((s) => requestedStyles.includes(s.key)).forEach((s) => {
      const card = document.createElement("div");
      card.className = `v-card ${s.cls}`;
      card.style.setProperty("--voice-color", `var(${s.var})`);
      let savedText = data.captions[s.key] || "";
      card.innerHTML = `
        <div class="talk-avatar" aria-hidden="true">
          <span class="talk-avatar-eye talk-avatar-eye-l"></span>
          <span class="talk-avatar-eye talk-avatar-eye-r"></span>
          <span class="talk-avatar-mouth"></span>
        </div>
        <div class="card-head">
          <p class="eyebrow">${s.label}</p>
          <button class="speak-btn" type="button" aria-label="Read ${s.label} caption aloud" aria-pressed="false">
            <svg class="speak-icon" viewBox="0 0 24 24" fill="none" aria-hidden="true">
              <path d="M4 9v6h4l5 4V5L8 9H4z" fill="currentColor"/>
              <path class="speak-wave speak-wave-1" d="M16.5 8.5a5 5 0 0 1 0 7" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>
              <path class="speak-wave speak-wave-2" d="M19 6a8.5 8.5 0 0 1 0 12" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>
            </svg>
          </button>
        </div>
        <p class="caption-text" spellcheck="false"></p>
        <div class="card-actions">
          <button class="copy-btn" type="button">Copy</button>
          <button class="edit-btn" type="button">Edit</button>
          <button class="cancel-btn" type="button" hidden>Cancel</button>
        </div>
      `;
      const captionEl = card.querySelector(".caption-text");
      captionEl.textContent = savedText;

      const copyBtn = card.querySelector(".copy-btn");
      copyBtn.addEventListener("click", () => {
        navigator.clipboard.writeText(captionEl.textContent).then(() => {
          copyBtn.textContent = "Copied";
          copyBtn.classList.add("copied");
          setTimeout(() => { copyBtn.textContent = "Copy"; copyBtn.classList.remove("copied"); }, 1400);
        });
      });

      const speakBtn = card.querySelector(".speak-btn");
      if (!("speechSynthesis" in window)) {
        speakBtn.disabled = true;
        speakBtn.title = "Text-to-speech isn't supported in this browser";
      } else {
        speakBtn.addEventListener("click", () => toggleSpeak(speakBtn, card, captionEl.textContent, s.speech));
      }

      // ---------- Edit caption ----------
      const editBtn = card.querySelector(".edit-btn");
      const cancelBtn = card.querySelector(".cancel-btn");

      function startEdit() {
        if (speakingCard === card) stopSpeaking();
        captionEl.setAttribute("contenteditable", "true");
        captionEl.classList.add("editing");
        cancelBtn.hidden = false;
        editBtn.textContent = "Save";
        captionEl.focus();
        const range = document.createRange();
        range.selectNodeContents(captionEl);
        const sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(range);
      }
      function commitEdit() {
        const next = captionEl.textContent.trim();
        savedText = next.length ? next : savedText;
        data.captions[s.key] = savedText; // persist across focus switches
        captionEl.textContent = savedText;
        captionEl.removeAttribute("contenteditable");
        captionEl.classList.remove("editing");
        cancelBtn.hidden = true;
        editBtn.textContent = "Edit";
      }
      function cancelEdit() {
        captionEl.textContent = savedText;
        captionEl.removeAttribute("contenteditable");
        captionEl.classList.remove("editing");
        cancelBtn.hidden = true;
        editBtn.textContent = "Edit";
      }

      editBtn.addEventListener("click", () => {
        if (captionEl.isContentEditable) commitEdit();
        else startEdit();
      });
      cancelBtn.addEventListener("click", cancelEdit);
      captionEl.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); commitEdit(); }
        else if (e.key === "Escape") { e.preventDefault(); cancelEdit(); }
      });
      captionEl.addEventListener("paste", (e) => {
        e.preventDefault();
        const t = (e.clipboardData || window.clipboardData).getData("text/plain");
        document.execCommand("insertText", false, t);
      });

      cardGrid.appendChild(card);
    });

    evidenceFacts.textContent = data.facts || "Grounding did not complete for this clip — the captions above are generic fallbacks, not derived from this footage.";

    evidenceFlags.innerHTML = "";
    const flags = [];
    if (data.strict_retry) flags.push([`strict JSON retry used`, false]);
    if (data.style_thinking) flags.push([`Gemma self-check completed`, false]);
    const backupStages = ["ground", "style", "refine"]
      .filter((k) => (data.models || {})[k] === BACKUP_MODEL_ID);
    if (backupStages.length)
      flags.push([`backup model (Gemma 4 26B) served: ${backupStages.join(", ")}`, true]);
    flags.forEach(([text, warn]) => {
      const span = document.createElement("span");
      span.className = warn ? "flag warn" : "flag";
      span.textContent = text;
      evidenceFlags.appendChild(span);
    });

    results.classList.add("visible");
  }

  // ---------- Digital manual (page-flip book) ----------
  const manualBtn = $("manualBtn"), manualOverlay = $("manualOverlay"), manualBackdrop = $("manualBackdrop");
  const manualClose = $("manualClose"), manualBook = $("manualBook");
  const manualPrev = $("manualPrev"), manualNext = $("manualNext"), manualPageInfo = $("manualPageInfo");
  const manualSheets = [...manualBook.querySelectorAll(".manual-sheet")];
  const MANUAL_LABELS = ["Cover", "Pages 1–2", "Pages 3–4", "Pages 5–6", "Back page"];
  let manualState = 0; // how many sheets lie flipped to the left
  let manualAnimating = false;

  function manualChrome() {
    manualBook.dataset.state = String(manualState);
    manualPrev.disabled = manualState === 0;
    manualNext.disabled = manualState === manualSheets.length;
    manualPageInfo.textContent = MANUAL_LABELS[manualState];
  }
  function manualSettle() {
    manualSheets.forEach((sheet, i) => {
      sheet.classList.toggle("flipped", i < manualState);
      // unflipped stack: first on top; flipped stack: last on top
      sheet.style.zIndex = String(i < manualState ? i + 1 : manualSheets.length - i);
    });
    manualChrome();
  }
  function manualFlip(dir) {
    if (manualAnimating) return;
    const next = manualState + dir;
    if (next < 0 || next > manualSheets.length) return;
    const sheet = manualSheets[dir > 0 ? manualState : manualState - 1];
    manualAnimating = true;
    sheet.style.zIndex = "50"; // ride above both stacks while turning
    manualState = next;
    sheet.classList.toggle("flipped", dir > 0);
    manualChrome();
    setTimeout(() => { manualAnimating = false; manualSettle(); }, 780);
  }

  function manualKeydown(e) {
    if (e.key === "Escape") closeManual();
    else if (e.key === "ArrowRight") manualFlip(1);
    else if (e.key === "ArrowLeft") manualFlip(-1);
  }
  function openManual() {
    manualOverlay.hidden = false;
    document.body.style.overflow = "hidden"; // freeze the page behind
    manualSettle();
    document.addEventListener("keydown", manualKeydown);
    manualClose.focus();
  }
  function closeManual() {
    manualOverlay.hidden = true;
    document.body.style.overflow = "";
    document.removeEventListener("keydown", manualKeydown);
    manualBtn.focus();
  }

  manualBtn.addEventListener("click", openManual);
  $("refreshBtn").addEventListener("click", () => location.reload());
  manualClose.addEventListener("click", closeManual);
  if (location.hash === "#manual") openManual(); // deep link, also used by layout tests
  manualBackdrop.addEventListener("click", closeManual);
  manualNext.addEventListener("click", () => manualFlip(1));
  manualPrev.addEventListener("click", () => manualFlip(-1));
  // Clicking a right-hand page turns forward; a left-hand page turns back.
  manualBook.addEventListener("click", (e) => {
    const face = e.target.closest(".sheet-face");
    if (!face) return;
    manualFlip(face.classList.contains("sheet-front") ? 1 : -1);
  });
  manualSettle();

  updateChrome();
})();
