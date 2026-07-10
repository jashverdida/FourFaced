(() => {
  "use strict";

  const STYLE_META = [
    { key: "formal", label: "Formal", cls: "formal", var: "--voice-formal" },
    { key: "sarcastic", label: "Sarcastic", cls: "sarcastic", var: "--voice-sarcastic" },
    { key: "humorous_tech", label: "Humorous — Tech", cls: "tech", var: "--voice-tech" },
    { key: "humorous_non_tech", label: "Humorous — Everyday", cls: "everyday", var: "--voice-everyday" },
  ];

  const $ = (id) => document.getElementById(id);
  const dropzone = $("dropzone"), fileInput = $("fileInput"), dropzoneSub = $("dropzoneSub");
  const examplesEl = $("examples"), playerWrap = $("playerWrap"), player = $("player");
  const styleToggles = $("styleToggles"), runBtn = $("runBtn");
  const progressBlock = $("progressBlock"), progressCurrent = $("progressCurrent"), stageLog = $("stageLog");
  const errorBox = $("errorBox"), results = $("results"), resultMeta = $("resultMeta");
  const cardGrid = $("cardGrid"), evidenceFacts = $("evidenceFacts"), evidenceFlags = $("evidenceFlags");
  const themeToggle = $("themeToggle");

  // ---------- Theme ----------
  const THEME_CYCLE = ["auto", "dark", "light"];
  let theme = localStorage.getItem("fourfaced-theme") || "auto";
  function applyTheme() {
    if (theme === "auto") document.documentElement.removeAttribute("data-theme");
    else document.documentElement.setAttribute("data-theme", theme);
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

  // ---------- Source selection ----------
  let source = null; // { type: 'file', file } | { type: 'example', url, label }
  let objectUrl = null;

  function setSource(next) {
    source = next;
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
    [...examplesEl.children].forEach((el) =>
      el.classList.toggle("active", source.type === "example" && el.dataset.url === source.url));
    updateRunEnabled();
  }

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
      showError(err.message || String(err));
      updateRunEnabled();
    }
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
      const text = data.captions[s.key] || "";
      card.innerHTML = `
        <p class="eyebrow">${s.label}</p>
        <p class="caption-text"></p>
        <button class="copy-btn" type="button">Copy</button>
      `;
      card.querySelector(".caption-text").textContent = text;
      const copyBtn = card.querySelector(".copy-btn");
      copyBtn.addEventListener("click", () => {
        navigator.clipboard.writeText(text).then(() => {
          copyBtn.textContent = "Copied";
          copyBtn.classList.add("copied");
          setTimeout(() => { copyBtn.textContent = "Copy"; copyBtn.classList.remove("copied"); }, 1400);
        });
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
