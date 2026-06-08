"use strict";

/* Gmail Cleaner front-end. All privileged work happens in the Python server;
   this file only calls the JSON API and renders the result. */

async function api(path, options) {
  const res = await fetch(path, options);
  let data = {};
  try { data = await res.json(); } catch (_) { /* empty body */ }
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

function postJSON(path, body) {
  return api(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
}

function setStatus(el, message, kind) {
  el.textContent = message || "";
  el.className = "status" + (kind ? " " + kind : "");
}

/* ----------------------------------------------------------------------- */
/* Dashboard                                                                */
/* ----------------------------------------------------------------------- */
function initDashboard() {
  const connectCard = document.getElementById("connect-card");
  const cleanup = document.getElementById("cleanup");
  const account = document.getElementById("account");
  const connectBtn = document.getElementById("connect-btn");
  const connectStatus = document.getElementById("connect-status");

  const previewBtn = document.getElementById("preview-btn");
  const cleanBtn = document.getElementById("clean-btn");
  const result = document.getElementById("result");
  const ageSel = document.getElementById("age");

  const modal = document.getElementById("modal");
  const modalBody = document.getElementById("modal-body");
  const modalConfirm = document.getElementById("modal-confirm");
  const modalCancel = document.getElementById("modal-cancel");
  const primaryConfirm = document.getElementById("primary-confirm");
  const primaryInput = document.getElementById("primary-input");

  let lastPreview = null; // { counts, total, categories, older_than }

  function selectedCategories() {
    return [...document.querySelectorAll(".cat input:checked")].map((c) => c.value);
  }

  async function refreshStatus() {
    const s = await api("/api/status");
    if (!s.has_credentials) { window.location = "/setup"; return; }
    if (s.connected) {
      account.textContent = s.email;
      account.hidden = false;
      connectCard.hidden = true;
      cleanup.hidden = false;
    } else {
      connectCard.hidden = false;
      cleanup.hidden = true;
    }
  }

  connectBtn.addEventListener("click", async () => {
    connectBtn.disabled = true;
    setStatus(connectStatus, "A Google sign-in window should open. Approve it…", "busy");
    try {
      await postJSON("/api/connect", {});
      await refreshStatus();
    } catch (err) {
      setStatus(connectStatus, err.message, "err");
    } finally {
      connectBtn.disabled = false;
    }
  });

  previewBtn.addEventListener("click", async () => {
    const categories = selectedCategories();
    const older_than = ageSel.value;
    if (categories.length === 0) {
      setStatus(result, "Pick at least one category.", "err");
      return;
    }
    if (categories.includes("primary") && !older_than) {
      setStatus(result, "Primary needs an age filter — choose an age above.", "err");
      return;
    }
    previewBtn.disabled = true;
    cleanBtn.disabled = true;
    setStatus(result, "Counting…", "busy");
    try {
      const data = await postJSON("/api/preview", { categories, older_than });
      lastPreview = { ...data, categories, older_than };
      for (const el of document.querySelectorAll(".cat-count")) el.textContent = "—";
      for (const [cat, n] of Object.entries(data.counts)) {
        const el = document.querySelector(`.cat-count[data-count="${cat}"]`);
        if (el) el.textContent = n.toLocaleString();
      }
      setStatus(result,
        `${data.total.toLocaleString()} message(s) would be moved to Trash.`,
        data.total ? "ok" : "");
      cleanBtn.disabled = data.total === 0;
    } catch (err) {
      setStatus(result, err.message, "err");
    } finally {
      previewBtn.disabled = false;
    }
  });

  cleanBtn.addEventListener("click", () => {
    if (!lastPreview || lastPreview.total === 0) return;
    const hasPrimary = lastPreview.categories.includes("primary");
    const ageNote = lastPreview.older_than ? ` older than ${lastPreview.older_than}` : "";
    modalBody.textContent =
      `Move ${lastPreview.total.toLocaleString()} message(s)${ageNote} to Trash? ` +
      `They stay recoverable in Gmail's Trash for ~30 days.`;
    primaryConfirm.hidden = !hasPrimary;
    primaryInput.value = "";
    modal.hidden = false;
    if (hasPrimary) primaryInput.focus();
  });

  modalCancel.addEventListener("click", () => { modal.hidden = true; });
  modal.addEventListener("click", (e) => { if (e.target === modal) modal.hidden = true; });

  modalConfirm.addEventListener("click", async () => {
    const body = {
      categories: lastPreview.categories,
      older_than: lastPreview.older_than,
    };
    if (lastPreview.categories.includes("primary")) {
      body.confirm_primary = primaryInput.value;
    }
    modalConfirm.disabled = true;
    setStatus(result, "Moving to Trash…", "busy");
    try {
      const data = await postJSON("/api/clean", body);
      modal.hidden = true;
      setStatus(result,
        `Done — moved ${data.total.toLocaleString()} message(s) to Trash (recoverable ~30 days).`,
        "ok");
      cleanBtn.disabled = true;
      lastPreview = null;
    } catch (err) {
      setStatus(result, err.message, "err");
    } finally {
      modalConfirm.disabled = false;
    }
  });

  // Re-preview is required after changing the selection before trashing.
  for (const cb of document.querySelectorAll(".cat input")) {
    cb.addEventListener("change", () => { cleanBtn.disabled = true; });
  }
  ageSel.addEventListener("change", () => { cleanBtn.disabled = true; });

  refreshStatus().catch((err) => setStatus(result, err.message, "err"));
}

/* ----------------------------------------------------------------------- */
/* Setup wizard                                                             */
/* ----------------------------------------------------------------------- */
function initSetup() {
  const drop = document.getElementById("drop");
  const file = document.getElementById("file");
  const status = document.getElementById("setup-status");

  async function upload(f) {
    if (!f) return;
    setStatus(status, "Validating and saving…", "busy");
    try {
      // Send the file's raw bytes as the request body (server reads them
      // directly, so no multipart parser is needed).
      await api("/api/upload-credentials", { method: "POST", body: f });
      setStatus(status, "Saved! Taking you to the app…", "ok");
      setTimeout(() => { window.location = "/"; }, 700);
    } catch (err) {
      setStatus(status, err.message, "err");
    }
  }

  drop.addEventListener("click", () => file.click());
  drop.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); file.click(); }
  });
  file.addEventListener("change", () => upload(file.files[0]));

  ["dragenter", "dragover"].forEach((ev) =>
    drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("drag"); }));
  ["dragleave", "drop"].forEach((ev) =>
    drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("drag"); }));
  drop.addEventListener("drop", (e) => upload(e.dataTransfer.files[0]));
}

document.addEventListener("DOMContentLoaded", () => {
  if (document.getElementById("cleanup")) initDashboard();
  if (document.getElementById("drop")) initSetup();
});
