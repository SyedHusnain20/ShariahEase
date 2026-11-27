// Force LTR always — clear any old RTL settings from previous versions
document.documentElement.setAttribute("dir", "ltr");
document.documentElement.setAttribute("lang", "en");
document.body && document.body.classList.remove("urdu-mode");
localStorage.removeItem("ShariahEase_lang");

// ── PKR CURRENCY FORMATTER ─────────────────────────────────
function formatPKR(amount) {
  if (amount === null || amount === undefined) return "PKR 0";
  return "PKR " + Math.round(Number(amount)).toLocaleString("en-PK");
}

// ── ELEMENT HELPERS ────────────────────────────────────────
function show(id) {
  const el = document.getElementById(id);
  if (el) el.classList.remove("hidden");
}

function hide(id) {
  const el = document.getElementById(id);
  if (el) el.classList.add("hidden");
}

// ── ASYNC FETCH HELPERS ────────────────────────────────────
async function postJSON(url, data) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Request failed (${res.status})`);
  }
  return res.json();
}

async function getJSON(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Request failed (${res.status})`);
  return res.json();
}

// ── TOAST NOTIFICATION ─────────────────────────────────────
function showToast(message, type = "info") {
  const colors = {
    success: "background:#192b23;border:1px solid rgba(16,185,129,0.4);color:#34d399",
    error: "background:#2a1415;border:1px solid rgba(229,72,77,0.4);color:#e5484d",
    info: "background:#1c1a18;border:1px solid #2c2925;color:#efe9de",
  };
  const toast = document.createElement("div");
  toast.style.cssText = `position:fixed;bottom:24px;right:24px;z-index:9999;
    padding:10px 16px;border-radius:12px;font-size:13px;font-weight:500;
    box-shadow:0 2px 8px rgba(0,0,0,0.08);transition:opacity 0.3s,transform 0.3s;
    ${colors[type] || colors.info}`;
  toast.textContent = message;
  document.body.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = "0";
    toast.style.transform = "translateY(8px)";
    setTimeout(() => toast.remove(), 400);
  }, 3000);
}

// ── NUMBER INPUT GUARD ─────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll('input[type="number"]').forEach(input => {
    input.addEventListener("input", () => {
      if (parseFloat(input.value) < 0) input.value = 0;
    });
  });
});
