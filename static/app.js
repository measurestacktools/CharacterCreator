const $ = (id) => document.getElementById(id);
let character = null;
let busyCreate = false, busyChat = false, busyModify = false;

async function api(path, opts = {}) {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || `Request failed (${r.status})`);
  return data;
}
function showErr(id, e) { $(id).textContent = e.message || String(e); }
function clearErr(id) { $(id).textContent = ""; }
function esc(s) { return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }

async function refreshStatus() {
  try {
    const s = await api("/api/status");
    $("statusDot").className = "dot " + (s.has_key ? "ok" : "bad");
    $("statusDot").title = s.has_key ? "Groq key connected" : "No Groq key — open Settings";
    if (s.has_character && !character) {
      try { const c = await api("/api/character"); renderSheet(c.character); } catch { /* ignore */ }
    }
  } catch { $("statusDot").className = "dot bad"; }
}

function collect() {
  return {
    concept: $("concept").value.trim(),
    world: $("world").value.trim(),
    personality: $("personality").value.trim(),
    powers: $("powers").value.trim(),
    role: $("role").value.trim(),
    genre: $("genre").value.trim(),
  };
}

async function doCreate() {
  if (busyCreate) return;
  clearErr("createErr"); clearErr("sheetErr");
  const body = collect();
  if (!body.concept) { $("createErr").textContent = "Give your character a concept first — even one line works."; return; }
  busyCreate = true;
  $("createBtn").disabled = true; $("regenBtn").disabled = true; $("createBtn").textContent = "Dreaming…";
  try {
    const d = await api("/api/create", { method: "POST", body: JSON.stringify(body) });
    renderSheet(d.character);
    $("chatLog").innerHTML = `<div class="sys">Say hello to ${esc(d.character.name)}.</div>`;
  } catch (e) { showErr("createErr", e); }
  finally { busyCreate = false; $("createBtn").disabled = false; $("regenBtn").disabled = !character; $("createBtn").textContent = "Create character"; }
}

function renderSheet(c) {
  character = c;
  $("emptySheet").classList.add("hidden"); $("card").classList.remove("hidden");
  $("regenBtn").disabled = false;
  $("avatar").textContent = (c.name || "?").trim().charAt(0).toUpperCase() || "?";
  $("cName").textContent = c.name || "Unnamed";
  $("chatName").textContent = c.name || "your character";
  $("cTag").textContent = [collect().genre, collect().role].filter(Boolean).join(" · ");
  $("cAppearance").textContent = c.appearance || "";
  $("cPersonality").textContent = c.personality || "";
  $("cBackstory").textContent = c.backstory || "";
  const stats = $("cStats"); stats.innerHTML = "";
  for (const [k, v] of Object.entries(c.stats || {})) {
    const row = document.createElement("div"); row.className = "stat";
    row.innerHTML = `<span>${esc(k)}</span><div class="bar"><i></i></div><b>${esc(v)}</b>`;
    stats.appendChild(row);
    requestAnimationFrame(() => requestAnimationFrame(() => { row.querySelector("i").style.width = `${Math.max(0, Math.min(100, Number(v) || 0))}%`; }));
  }
  const fill = (id, arr) => { $(id).innerHTML = (arr || []).map((x) => `<li>${esc(x)}</li>`).join("") || "<li>—</li>"; };
  fill("cAbilities", c.abilities); fill("cWeak", c.weaknesses); fill("cGoals", c.goals);
  fill("cCatch", c.catchphrases); fill("cRel", c.relationships);
}

async function doModify() {
  if (busyModify) return;
  clearErr("sheetErr");
  const ins = $("modifyInput").value.trim();
  if (!ins) { $("sheetErr").textContent = "Describe the change first."; return; }
  if (!character) { $("sheetErr").textContent = "Create a character first."; return; }
  busyModify = true;
  $("modifyBtn").disabled = true; $("modifyBtn").textContent = "Applying…";
  try {
    const d = await api("/api/modify", { method: "POST", body: JSON.stringify({ instruction: ins }) });
    renderSheet(d.character); $("modifyInput").value = "";
    $("sheetErr").textContent = "Change filed in dossier.";
    setTimeout(() => { if ($("sheetErr").textContent === "Change filed in dossier.") $("sheetErr").textContent = ""; }, 2500);
  } catch (e) { showErr("sheetErr", e); }
  finally { busyModify = false; $("modifyBtn").disabled = false; $("modifyBtn").textContent = "Apply change"; }
}

function toMarkdown(c) {
  let md = `# ${c.name}\n\n**Appearance:** ${c.appearance}\n\n**Personality:** ${c.personality}\n\n## Backstory\n${c.backstory}\n`;
  for (const [k, t] of [["abilities", "Abilities"], ["weaknesses", "Weaknesses"], ["goals", "Goals"], ["catchphrases", "Catchphrases"], ["relationships", "Relationships"]]) {
    md += `\n## ${t}\n` + (((c[k] || []).map((x) => `- ${x}`).join("\n")) || "_None_") + "\n";
  }
  md += "\n## Stats\n" + Object.entries(c.stats || {}).map(([k, v]) => `- ${k}: ${v}/100`).join("\n") + "\n";
  return md;
}

function addBubble(who, text) {
  const d = document.createElement("div");
  d.className = "bubble " + who; d.textContent = text;
  $("chatLog").appendChild(d); $("chatLog").scrollTop = $("chatLog").scrollHeight;
}

async function doChat() {
  if (busyChat) return;
  clearErr("chatErr");
  const inp = $("chatInput"); const msg = inp.value.trim();
  if (!msg) return;
  if (!character) { $("chatErr").textContent = "Create a character first, then chat."; return; }
  busyChat = true;
  inp.value = ""; addBubble("user", msg);
  $("sendBtn").disabled = true; $("sendBtn").textContent = "…";
  try {
    const d = await api("/api/chat", { method: "POST", body: JSON.stringify({ message: msg }) });
    addBubble("ai", d.reply);
  } catch (e) { showErr("chatErr", e); }
  finally { busyChat = false; $("sendBtn").disabled = false; $("sendBtn").textContent = "Send"; $("chatInput").focus(); }
}

// settings modal — key sent once to backend, never stored in browser
function openSettings() { $("settingsModal").classList.remove("hidden"); $("keyMsg").textContent = ""; $("keyInput").focus(); }
function closeSettings() { $("settingsModal").classList.add("hidden"); $("keyInput").value = ""; }

$("createBtn").onclick = doCreate;
$("regenBtn").onclick = doCreate;
$("modifyBtn").onclick = doModify;
$("modifyInput").addEventListener("keydown", (e) => { if (e.key === "Enter") doModify(); });
$("sendBtn").onclick = doChat;
$("resetBtn").onclick = async () => { await api("/api/reset", { method: "POST" }).catch(() => {}); $("chatLog").innerHTML = '<div class="sys">Conversation reset.</div>'; };
$("chatInput").addEventListener("keydown", (e) => { if (e.key === "Enter") doChat(); });
const _updateCount = () => { $("conceptCount").textContent = `${$("concept").value.length}/500`; };
$("concept").addEventListener("input", _updateCount);
_updateCount();
$("settingsBtn").onclick = openSettings;
$("closeSettingsBtn").onclick = closeSettings;
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("settingsModal").classList.contains("hidden")) closeSettings(); });
$("keyInput").addEventListener("keydown", (e) => { if (e.key === "Enter") $("saveKeyBtn").click(); });
$("settingsModal").addEventListener("click", (e) => { if (e.target.id === "settingsModal") closeSettings(); });
$("saveKeyBtn").onclick = async () => {
  $("keyMsg").textContent = "Verifying…";
  try {
    await api("/api/key", { method: "POST", body: JSON.stringify({ key: $("keyInput").value }) });
    $("keyMsg").textContent = "Key verified for this session.";
    $("keyInput").value = ""; refreshStatus();
  } catch (e) { $("keyMsg").textContent = e.message; }
};
$("clearKeyBtn").onclick = async () => {
  await api("/api/key", { method: "DELETE" }).catch(() => {});
  $("keyMsg").textContent = "Server memory key cleared."; refreshStatus();
};
$("exportBtn").onclick = async () => {
  if (!character) return;
  const save = (md, name) => {
    const blob = new Blob([md], { type: "text/markdown" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = name || "character.md";
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  };
  try {
    const d = await api("/api/export");
    save(d.markdown, d.filename);
  } catch { // offline fallback
    save(toMarkdown(character), "character.md");
  }
};
$("copyBtn").onclick = async () => {
  if (!character) return;
  try { await navigator.clipboard.writeText(toMarkdown(character)); $("sheetErr").textContent = "Copied to clipboard."; setTimeout(() => { if ($("sheetErr").textContent === "Copied to clipboard.") $("sheetErr").textContent = ""; }, 2500); }
  catch { $("sheetErr").textContent = "Copy failed in this browser."; }
};

// SillyTavern chara_card v2 export/import (PNG with `chara` chunk, or plain JSON)
function downloadBlob(blob, name) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob); a.download = name;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}
function cardFileName(ext) {
  const base = (character && character.name ? character.name : "character").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "") || "character";
  return `${base}.card.${ext}`;
}
async function downloadCard(fmt) {
  if (!character) { $("sheetErr").textContent = "Create a character first."; return; }
  try {
    const r = await fetch(`/api/card/export?format=${fmt}`);
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || `Export failed (${r.status})`);
    downloadBlob(await r.blob(), cardFileName(fmt));
  } catch (e) { showErr("sheetErr", e); }
}
$("cardPngBtn").onclick = () => downloadCard("png");
$("cardJsonBtn").onclick = () => downloadCard("json");
$("cardImportBtn").onclick = () => $("cardImportInput").click();
$("cardImportInput").onchange = async (e) => {
  const f = e.target.files && e.target.files[0];
  e.target.value = "";
  if (!f) return;
  clearErr("sheetErr");
  try {
    const fd = new FormData();
    fd.append("file", f, f.name);
    const r = await fetch("/api/card/import", { method: "POST", body: fd });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.error || `Import failed (${r.status})`);
    renderSheet(d.character);
    $("chatLog").innerHTML = `<div class="sys">Card filed — say hello to ${esc(d.character.name)}.</div>`;
    $("sheetErr").textContent = `Imported “${d.character.name}”. Dossier updated.`;
    setTimeout(() => { if ($("sheetErr").textContent.startsWith("Imported")) $("sheetErr").textContent = ""; }, 3000);
  } catch (err) { showErr("sheetErr", err); }
};

refreshStatus();
