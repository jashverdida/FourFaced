(() => {
  "use strict";

  const STYLE_META = [
    { key: "formal", label: "Formal", cls: "formal", var: "--voice-formal", speech: { rate: 0.92, pitch: 0.85 } },
    { key: "sarcastic", label: "Sarcastic", cls: "sarcastic", var: "--voice-sarcastic", speech: { rate: 0.82, pitch: 1.08 } },
    { key: "humorous_tech", label: "Humorous — Tech", cls: "tech", var: "--voice-tech", speech: { rate: 1.05, pitch: 0.95 } },
    { key: "humorous_non_tech", label: "Humorous — Everyday", cls: "everyday", var: "--voice-everyday", speech: { rate: 1.1, pitch: 1.18 } },
  ];

  const $ = (id) => document.getElementById(id);
  const dropzone = $("dropzone"), fileInput = $("fileInput"), dropzoneSub = $("dropzoneSub");
  const dropzoneDefaultText = dropzoneSub.textContent;
  const examplesEl = $("examples"), playerWrap = $("playerWrap"), player = $("player"), clearBtn = $("clearBtn");
  const videoProgress = $("videoProgress"), progressTrack = videoProgress.querySelector(".video-progress-track");
  const progressFill = $("progressFill"), progressBuffered = $("progressBuffered"), progressThumb = $("progressThumb");
  const progressCurrentTime = $("progressCurrentTime"), progressDuration = $("progressDuration");
  const styleToggles = $("styleToggles"), runBtn = $("runBtn");
  const progressBlock = $("progressBlock"), progressCurrent = $("progressCurrent"), stageLog = $("stageLog");
  const errorBox = $("errorBox"), results = $("results"), resultMeta = $("resultMeta");
  const cardGrid = $("cardGrid"), evidenceFacts = $("evidenceFacts"), evidenceFlags = $("evidenceFlags");
  const themeToggle = $("themeToggle");

  // ---------- Theme ----------
  const THEME_CYCLE = ["dark", "light"];
  const systemDefault = matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
  let theme = localStorage.getItem("fourfaced-theme") || systemDefault;
  function applyTheme() {
    document.documentElement.setAttribute("data-theme", theme);
    themeToggle.textContent = theme.toUpperCase();
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
  styleToggles.addEventListener("change", updateRunEnabled);

  // ---------- Custom video progress bar ----------
  function formatTime(s) {
    if (!isFinite(s) || s < 0) return "0:00";
    const m = Math.floor(s / 60);
    const sec = Math.floor(s % 60);
    return `${m}:${String(sec).padStart(2, "0")}`;
  }
  function updateVideoProgress() {
    const duration = player.duration;
    const pct = duration ? (player.currentTime / duration) * 100 : 0;
    progressFill.style.width = `${pct}%`;
    progressThumb.style.left = `${pct}%`;
    progressCurrentTime.textContent = formatTime(player.currentTime);
    progressDuration.textContent = formatTime(duration || 0);
    videoProgress.setAttribute("aria-valuenow", String(Math.round(pct)));
    if (player.buffered.length && duration) {
      const bufEnd = player.buffered.end(player.buffered.length - 1);
      progressBuffered.style.width = `${Math.min(100, (bufEnd / duration) * 100)}%`;
    } else {
      progressBuffered.style.width = "0%";
    }
  }
  function resetVideoProgress() {
    progressFill.style.width = "0%";
    progressThumb.style.left = "0%";
    progressBuffered.style.width = "0%";
    progressCurrentTime.textContent = "0:00";
    progressDuration.textContent = "0:00";
    videoProgress.setAttribute("aria-valuenow", "0");
  }
  ["timeupdate", "progress", "loadedmetadata", "durationchange"].forEach((evt) =>
    player.addEventListener(evt, updateVideoProgress));

  function seekFromClientX(clientX) {
    const rect = progressTrack.getBoundingClientRect();
    const ratio = Math.min(1, Math.max(0, (clientX - rect.left) / rect.width));
    if (player.duration) {
      player.currentTime = ratio * player.duration;
      updateVideoProgress();
    }
  }
  let draggingProgress = false;
  progressTrack.addEventListener("pointerdown", (e) => {
    draggingProgress = true;
    videoProgress.classList.add("dragging");
    progressTrack.setPointerCapture(e.pointerId);
    seekFromClientX(e.clientX);
  });
  progressTrack.addEventListener("pointermove", (e) => { if (draggingProgress) seekFromClientX(e.clientX); });
  ["pointerup", "pointercancel"].forEach((evt) =>
    progressTrack.addEventListener(evt, () => {
      draggingProgress = false;
      videoProgress.classList.remove("dragging");
    }));
  videoProgress.addEventListener("keydown", (e) => {
    if (!player.duration) return;
    const step = player.duration * 0.02;
    if (e.key === "ArrowRight") { player.currentTime = Math.min(player.duration, player.currentTime + step); updateVideoProgress(); }
    else if (e.key === "ArrowLeft") { player.currentTime = Math.max(0, player.currentTime - step); updateVideoProgress(); }
  });

  // ---------- Source selection ----------
  let source = null; // { type: 'file', file } | { type: 'example', url, label }
  let objectUrl = null;

  function setSource(next) {
    source = next;
    stopSpeaking();
    resetVideoProgress();
    results.classList.remove("visible");
    progressBlock.classList.remove("visible");
    errorBox.classList.remove("visible");
    if (objectUrl) { URL.revokeObjectURL(objectUrl); objectUrl = null; }

    if (source.type === "file") {
      objectUrl = URL.createObjectURL(source.file);
      player.src = objectUrl;
      dropzoneSub.textContent = source.file.name;
    } else {
      player.src = source.url;
      dropzoneSub.textContent = `Example: ${source.label}`;
    }
    playerWrap.classList.add("visible");
    dropzone.classList.add("hidden");
    [...examplesEl.children].forEach((el) =>
      el.classList.toggle("active", source.type === "example" && el.dataset.url === source.url));
    updateRunEnabled();
  }

  function clearSource() {
    source = null;
    stopSpeaking();
    if (objectUrl) { URL.revokeObjectURL(objectUrl); objectUrl = null; }
    player.pause();
    player.removeAttribute("src");
    player.load();
    resetVideoProgress();
    playerWrap.classList.remove("visible");
    dropzone.classList.remove("hidden");
    fileInput.value = "";
    dropzoneSub.textContent = dropzoneDefaultText;
    [...examplesEl.children].forEach((el) => el.classList.remove("active"));
    results.classList.remove("visible");
    progressBlock.classList.remove("visible");
    errorBox.classList.remove("visible");
    updateRunEnabled();
  }
  clearBtn.addEventListener("click", (e) => {
    e.preventDefault();
    e.stopPropagation();
    clearSource();
  });

  fileInput.addEventListener("change", () => {
    if (fileInput.files[0]) setSource({ type: "file", file: fileInput.files[0] });
  });
  ["dragover", "dragenter"].forEach((evt) =>
    dropzone.addEventListener(evt, (e) => { e.preventDefault(); dropzone.classList.add("drag-over"); }));
  ["dragleave", "drop"].forEach((evt) =>
    dropzone.addEventListener(evt, (e) => { e.preventDefault(); dropzone.classList.remove("drag-over"); }));
  dropzone.addEventListener("drop", (e) => {
    const file = e.dataTransfer.files[0];
    if (file) setSource({ type: "file", file });
  });

  fetch("/api/examples").then((r) => r.json()).then((examples) => {
    examples.forEach((ex) => {
      const btn = document.createElement("button");
      btn.className = "example-pill";
      btn.type = "button";
      btn.textContent = ex.label;
      btn.dataset.url = ex.url;
      btn.addEventListener("click", () => setSource({ type: "example", url: ex.url, label: ex.label }));
      examplesEl.appendChild(btn);
    });
  }).catch(() => {});

  function updateRunEnabled() {
    runBtn.disabled = !source || selectedStyles().length === 0;
  }

  // ---------- Signal-split canvas ----------
  const canvas = $("signal");
  const ctx = canvas.getContext("2d");
  const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
  let signalState = "idle"; // idle -> processing -> splitting -> split
  let splitStart = 0;
  let rafId = null;

  function cssVar(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }
  function drawSignal(t) {
    const w = canvas.width, h = canvas.height, midY = h / 2;
    ctx.clearRect(0, 0, w, h);

    if (signalState === "idle") {
      ctx.strokeStyle = cssVar("--border");
      ctx.lineWidth = 1.5;
      ctx.beginPath(); ctx.moveTo(0, midY); ctx.lineTo(w, midY); ctx.stroke();
      return;
    }

    if (signalState === "processing") {
      ctx.strokeStyle = cssVar("--border");
      ctx.lineWidth = 1.5;
      ctx.beginPath(); ctx.moveTo(0, midY); ctx.lineTo(w, midY); ctx.stroke();
      if (!reducedMotion) {
        const sweepW = w * 0.22;
        const x = ((t / 1400) % 1) * (w + sweepW) - sweepW;
        const grad = ctx.createLinearGradient(x, 0, x + sweepW, 0);
        grad.addColorStop(0, "transparent");
        grad.addColorStop(0.5, cssVar("--accent"));
        grad.addColorStop(1, "transparent");
        ctx.strokeStyle = grad;
        ctx.lineWidth = 2.5;
        ctx.beginPath(); ctx.moveTo(Math.max(0, x), midY); ctx.lineTo(Math.min(w, x + sweepW), midY); ctx.stroke();
      }
      rafId = requestAnimationFrame(drawSignal);
      return;
    }

    const colors = STYLE_META.map((s) => cssVar(s.var));
    const endXs = colors.map((_, i) => (w / (colors.length + 1)) * (i + 1));
    const progress = signalState === "split" || reducedMotion
      ? 1
      : Math.min(1, (t - splitStart) / 900);
    const ease = 1 - Math.pow(1 - progress, 3);

    colors.forEach((color, i) => {
      const endX = endXs[i];
      ctx.strokeStyle = color;
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(w / 2, midY);
      const cx1 = w / 2 + (endX - w / 2) * 0.3;
      const cy1 = midY;
      const cx2 = w / 2 + (endX - w / 2) * 0.7;
      const cy2 = midY + (h * 0.32) * ease;
      const curX = w / 2 + (endX - w / 2) * ease;
      const curY = midY + (h * 0.32) * ease;
      ctx.bezierCurveTo(cx1, cy1, cx2, cy2, curX, curY);
      ctx.stroke();
    });

    if (progress < 1) {
      rafId = requestAnimationFrame(drawSignal);
    } else {
      signalState = "split";
    }
  }
  function setSignalState(next) {
    if (rafId) cancelAnimationFrame(rafId);
    signalState = next;
    if (next === "splitting") splitStart = performance.now();
    rafId = requestAnimationFrame(drawSignal);
  }
  setSignalState("idle");

  // ---------- Run ----------
  runBtn.addEventListener("click", runPipeline);

  function logStage(label) {
    const li = document.createElement("li");
    li.textContent = label;
    stageLog.appendChild(li);
    stageLog.scrollTop = stageLog.scrollHeight;
  }

  async function runPipeline() {
    stopSpeaking();
    const styles = selectedStyles();
    runBtn.disabled = true;
    results.classList.remove("visible");
    errorBox.classList.remove("visible");
    stageLog.innerHTML = "";
    progressBlock.classList.add("visible");
    progressCurrent.innerHTML = '<span class="dot"></span> Uploading…';
    setSignalState("processing");

    try {
      const form = new FormData();
      if (source.type === "file") form.append("video", source.file);
      else form.append("example_url", source.url);

      const uploadRes = await fetch("/api/upload", { method: "POST", body: form });
      const uploadData = await uploadRes.json();
      if (!uploadRes.ok) throw new Error(uploadData.error || "Upload failed");

      const es = new EventSource(`/api/run/${uploadData.job_id}?styles=${encodeURIComponent(styles.join(","))}`);

      es.addEventListener("stage", (e) => {
        const data = JSON.parse(e.data);
        progressCurrent.innerHTML = `<span class="dot"></span> ${data.label}`;
        logStage(data.label);
      });

      es.addEventListener("done", (e) => {
        es.close();
        const data = JSON.parse(e.data);
        setSignalState("splitting");
        renderResults(data, styles);
        updateRunEnabled();
      });

      es.onerror = () => {
        es.close();
        if (!results.classList.contains("visible")) {
          showError("Lost connection to the FourFaced server mid-run. Check the terminal running ui/server.py and try again.");
        }
        updateRunEnabled();
      };
    } catch (err) {
      const msg = err instanceof TypeError
        ? "Can't reach the FourFaced server. Make sure ui/server.py is running (python ui/server.py), then try again."
        : err.message || String(err);
      showError(msg);
      updateRunEnabled();
    }
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

  function renderResults(data, requestedStyles) {
    progressCurrent.innerHTML = `<span class="dot"></span> Done in ${data.total_s ?? "?"}s`;

    resultMeta.innerHTML = "";
    const metaItems = [
      data.duration_s != null ? [`Clip length`, `${data.duration_s}s`] : null,
      [`Frames sampled`, data.frame_count],
      [`Total run`, `${data.total_s ?? "?"}s`],
      [`Refinement pass`, data.style_thinking ? "completed" : "skipped (budget)"],
    ].filter(Boolean);
    metaItems.forEach(([label, val]) => {
      const span = document.createElement("span");
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
    if (data.template_styles && data.template_styles.length)
      flags.push([`fallback used: ${data.template_styles.join(", ")}`, true]);
    if (data.strict_retry) flags.push([`strict JSON retry used`, false]);
    if (data.style_thinking) flags.push([`Gemma self-check completed`, false]);
    if (data.error) flags.push([`error: ${data.error}`, true]);
    flags.forEach(([text, warn]) => {
      const span = document.createElement("span");
      span.className = warn ? "flag warn" : "flag";
      span.textContent = text;
      evidenceFlags.appendChild(span);
    });

    results.classList.add("visible");
    progressBlock.classList.remove("visible");
  }
})();
