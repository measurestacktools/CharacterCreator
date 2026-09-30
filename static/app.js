const $ = (id) => document.getElementById(id);
let character = null;

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
  clearErr("createErr"); clearErr("sheetErr");
  const body = collect();
  if (!body.concept) { $("createErr").textContent = "Give your character a concept first — even one line works."; return; }
  $("createBtn").disabled = true; $("createBtn").textContent = "Dreaming…";
  try {
    const d = await api("/api/create", { method: "POST", body: JSON.stringify(body) });
    renderSheet(d.character);
    $("chatLog").innerHTML = `<div class="sys">Say hello to ${esc(d.character.name)}.</div>`;
  } catch (e) { showErr("createErr", e); }
  finally { $("createBtn").disabled = false; $("createBtn").textContent = "Create character"; }
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
    requestAnimationFrame(() => requestAnimationFrame(() => { row.querySelector("i").style.width = `${v}%`; }));
  }
  const fill = (id, arr) => { $(id).innerHTML = (arr || []).map((x) => `<li>${esc(x)}</li>`).join("") || "<li>—</li>"; };
  fill("cAbilities", c.abilities); fill("cWeak", c.weaknesses); fill("cGoals", c.goals);
  fill("cCatch", c.catchphrases); fill("cRel", c.relationships);
}

async function doModify() {
  clearErr("sheetErr");
  const ins = $("modifyInput").value.trim();
  if (!ins) { $("sheetErr").textContent = "Describe the change first."; return; }
  if (!character) { $("sheetErr").textContent = "Create a character first."; return; }
  $("modifyBtn").disabled = true;
  try {
    const d = await api("/api/modify", { method: "POST", body: JSON.stringify({ instruction: ins }) });
    renderSheet(d.character); $("modifyInput").value = "";
  } catch (e) { showErr("sheetErr", e); }
  finally { $("modifyBtn").disabled = false; }
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
  clearErr("chatErr");
  const inp = $("chatInput"); const msg = inp.value.trim();
  if (!msg) return;
  if (!character) { $("chatErr").textContent = "Create a character first, then chat."; return; }
  inp.value = ""; addBubble("user", msg);
  $("sendBtn").disabled = true;
  try {
    const d = await api("/api/chat", { method: "POST", body: JSON.stringify({ message: msg }) });
    addBubble("ai", d.reply);
  } catch (e) { showErr("chatErr", e); }
  finally { $("sendBtn").disabled = false; }
}

// settings modal — key sent once to backend, never stored in browser
function openSettings() { $("settingsModal").classList.remove("hidden"); $("keyMsg").textContent = ""; }
function closeSettings() { $("settingsModal").classList.add("hidden"); $("keyInput").value = ""; }

$("createBtn").onclick = doCreate;
$("regenBtn").onclick = doCreate;
$("modifyBtn").onclick = doModify;
$("sendBtn").onclick = doChat;
$("resetBtn").onclick = async () => { await api("/api/reset", { method: "POST" }).catch(() => {}); $("chatLog").innerHTML = '<div class="sys">Conversation reset.</div>'; };
$("chatInput").addEventListener("keydown", (e) => { if (e.key === "Enter") doChat(); });
$("concept").addEventListener("input", () => { $("conceptCount").textContent = `${$("concept").value.length}/500`; });
$("settingsBtn").onclick = openSettings;
$("closeSettingsBtn").onclick = closeSettings;
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
  try {
    const d = await api("/api/export");
    const blob = new Blob([d.markdown], { type: "text/markdown" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = d.filename || "character.md"; a.click();
    URL.revokeObjectURL(a.href);
  } catch { // offline fallback
    const blob = new Blob([toMarkdown(character)], { type: "text/markdown" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = "character.md"; a.click();
    URL.revokeObjectURL(a.href);
  }
};
$("copyBtn").onclick = async () => {
  if (!character) return;
  try { await navigator.clipboard.writeText(toMarkdown(character)); $("sheetErr").textContent = "Copied to clipboard."; }
  catch { $("sheetErr").textContent = "Copy failed in this browser."; }
};

refreshStatus();
