const COLUMNS = [
  { id: "shortlisted", label: "Shortlisted", color: "var(--blue)" },
  { id: "applied", label: "Applied", color: "var(--orange)" },
  { id: "interviewing", label: "Interviewing", color: "var(--yellow)" },
  { id: "offer", label: "Offer", color: "var(--green)" },
  { id: "rejected", label: "Rejected", color: "var(--red)" },
  { id: "no_response", label: "No Response", color: "color-mix(in srgb, var(--orange) 45%, var(--grey))" },
  { id: "dead", label: "Skipped", color: "var(--grey)" },
];
// Job categories come from profile.yaml (`categories:`) via /api/board.
let CATEGORIES = ["Other"];
let CHIPS = ["All", "Contract"];

const SOURCE_LABELS = {
  linkedin: "LinkedIn", greenhouse: "Greenhouse", ashby: "Ashby", lever: "Lever", remoteok: "Remote OK",
  remotive: "Remotive", weworkremotely: "We Work Remotely", remoterocketship: "Remote Rocketship", dice: "Dice",
  talent: "Talent.com", wellfound: "Wellfound", remotejobs: "RemoteJobs.com",
  smartrecruiters: "SmartRecruiters", workable: "Workable", recruitee: "Recruitee", breezy: "Breezy HR",
  bamboohr: "BambooHR", rippling: "Rippling", personio: "Personio", workday: "Workday",
  himalayas: "Himalayas", jobicy: "Jobicy", hackernews: "HN Who's Hiring", usajobs: "USAJOBS", adzuna: "Adzuna",
  url: "Careers page", manual: "Added manually", import: "Google Sheet",
};
const sourceLabel = (s) => SOURCE_LABELS[s] || s || "Unknown";

let state = { jobs: [], profile: {}, chip: "All", source: "All", search: "" };
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// Only http(s) links are shown as links. Job links come from scraped data.
const safeUrl = (u) => { try { const x = new URL(u, location.href); return ["http:", "https:"].includes(x.protocol) ? x.href : ""; } catch { return ""; } };

async function api(path, opts = {}) {
  // X-Plott tells the server the request came from this page (see the guard in app/main.py).
  const r = await fetch(path, { headers: { "Content-Type": "application/json", "X-Plott": "1" }, ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || r.statusText);
  return data;
}

function toDate(v) {
  if (!v) return null;
  const d = /^\d{12,}$/.test(v) ? new Date(Number(v)) : new Date(v);
  return isNaN(d) ? null : d;
}
const fmt = (v) => { const d = toDate(v); return d ? d.toLocaleDateString("en-US", { month: "short", day: "numeric" }) : ""; };
const daysSince = (v) => { const d = toDate(v); return d ? Math.floor((Date.now() - d) / 864e5) : null; };
const sameDay = (a, b) => a.toDateString() === b.toDateString();

async function load() {
  const data = await api("/api/board");
  state.jobs = data.jobs;
  state.profile = data.profile;
  state.nextRun = data.next_run;
  state.claude = !!data.claude;
  setCategories(data.profile.categories);
  render();
}

function isContract(j) { return /contract|temp/i.test(j.employment_type || ""); }

function visible() {
  const q = state.search.trim().toLowerCase();
  return state.jobs.filter(j => {
    if (q && !`${j.company} ${j.title}`.toLowerCase().includes(q)) return false;
    if (state.source !== "All" && j.source !== state.source) return false;
    if (state.chip === "All") return true;
    if (state.chip === "Contract") return isContract(j);
    return j.category === state.chip;
  });
}

function renderStats() {
  const applied = state.jobs.filter(j => j.applied_at);
  const now = new Date();
  const today = applied.filter(j => sameDay(toDate(j.applied_at), now)).length;
  const week = applied.filter(j => daysSince(j.applied_at) < 7).length;
  const count = (s) => state.jobs.filter(j => j.status === s).length;
  const replied = applied.filter(j => ["interviewing", "offer", "rejected"].includes(j.status)).length;
  const rate = applied.length ? Math.round((replied / applied.length) * 100) : 0;
  const stats = [
    [`${today} <small>/ ${esc(state.profile.daily_goal)}</small>`, "Applied today"], [week, "Last 7 days"],
    [applied.length, "Total applied"], [count("applied"), "Awaiting reply"], [count("interviewing"), "Interviewing"],
    [count("offer"), "Offers"], [`${rate}%`, "Reply rate"],
  ];
  $("#stats").innerHTML = stats.map(([v, l]) => `<div class="stat"><b>${v}</b><span>${l}</span></div>`).join("");
}

function tagsFor(j) {
  const t = [];
  if (isContract(j)) t.push(`<span class="tag yellow">Contract</span>`);
  if ((state.profile.tag_categories || []).includes(j.category)) t.push(`<span class="tag blue">${esc(j.category)}</span>`);
  if (j.workplace && j.workplace !== "remote" && j.workplace !== "unknown") t.push(`<span class="tag">${esc(j.workplace)}</span>`);
  t.push(j.pay ? `<span class="tag green">${esc(j.pay)}</span>` : `<span class="tag green">Not posted</span>`);
  if (j.has_docs) t.push(`<span class="tag blue">Tailored</span>`);
  if (j.status === "shortlisted" && j.score != null) {
    const cls = j.score >= 85 ? "green" : j.score >= 70 ? "" : "yellow";
    t.push(`<span class="tag ${cls}">match ${esc(Number(j.score))}</span>`);
  }
  const d = daysSince(j.applied_at);
  if (j.status === "applied" && d != null && d >= (state.profile.no_reply_days || 14))
    t.push(`<span class="tag red">no reply in ${d} days</span>`);
  return t.join("");
}

// Which of the profile's skill tiers the posting mentions (core and gaps on Shortlisted cards; all
// tiers in Details).
function skillLine(j, max = 4) {
  const sk = j.skills;
  if (!sk || j.status !== "shortlisted") return "";
  const list = (names, n) => esc(names.slice(0, n).join(" · ")) + (names.length > n ? ` +${names.length - n}` : "");
  const parts = [];
  if (sk.core.length) parts.push(`<span class="sk-core" title="Core skills it mentions">✓ ${list(sk.core, max)}</span>`);
  if (sk.gap.length) parts.push(`<span class="sk-gap" title="Gaps it mentions">✗ ${list(sk.gap, 2)}</span>`);
  return parts.length ? `<div class="skills">${parts.join(" ")}</div>` : "";
}

function card(j) {
  const meta = j.status === "shortlisted"
    ? `Found ${fmt(j.found_at)}${fmt(j.posted_at) ? ` · posted ${fmt(j.posted_at)}` : ""}`
    : j.applied_at ? `Applied ${fmt(j.applied_at)}` : `Added ${fmt(j.found_at)}`;
  const via = `<span class="via"> · via ${esc(sourceLabel(j.source))}</span>`;
  const opts = COLUMNS.map(c => `<option value="${c.id}" ${c.id === j.status ? "selected" : ""}>${c.label}</option>`).join("");
  return `<article class="card" draggable="true" data-id="${j.id}">
    <div class="company">${esc(j.company)}</div>
    <a class="role" ${safeUrl(j.url) ? `href="${esc(safeUrl(j.url))}" target="_blank" rel="noopener noreferrer"` : ""}>${esc(j.title)}</a>
    <div class="tags">${tagsFor(j)}</div>
    ${skillLine(j)}
    <div class="meta">${meta}${via}</div>
    <div class="card-foot"><select data-status="${j.id}">${opts}</select><button class="details" data-details="${j.id}">Details</button></div>
  </article>`;
}

function render() {
  $("#who").textContent = state.profile.name || "";
  $("#today").textContent = new Date().toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric" });
  renderStats();
  $("#chips").innerHTML = CHIPS.map(c => `<button class="chip ${c === state.chip ? "on" : ""}" data-chip="${esc(c)}">${esc(c)}</button>`).join("");
  const counts = {};
  state.jobs.forEach(j => (counts[j.source] = (counts[j.source] || 0) + 1));
  const srcs = Object.keys(counts).sort((a, b) => counts[b] - counts[a]);
  if (state.source !== "All" && !counts[state.source]) state.source = "All";
  $("#srcChips").innerHTML = [["All", "All sources", state.jobs.length], ...srcs.map(s => [s, sourceLabel(s), counts[s]])]
    .map(([v, l, n]) => `<button class="chip sm ${v === state.source ? "on" : ""}" data-src="${esc(v)}">${esc(l)}<span class="n">${n}</span></button>`).join("");
  const jobs = visible();
  $("#board").innerHTML = COLUMNS.map(c => {
    let list = jobs.filter(j => j.status === c.id);
    if (c.id === "shortlisted") list.sort((a, b) => (b.score ?? -1) - (a.score ?? -1));
    return `<section class="col" style="--c:${c.color}" data-col="${c.id}">
      <div class="col-head">${c.label}<span>${list.length}</span></div>
      ${list.length ? list.map(card).join("") : `<div class="empty">${c.id === "offer" ? "No offers yet." : "Nothing here."}</div>`}
    </section>`;
  }).join("");
}

async function setStatus(id, status) {
  const j = state.jobs.find(x => x.id == id);
  if (!j || j.status === status) return;
  const from = j.status;
  Object.assign(j, await api(`/api/jobs/${id}`, { method: "PATCH", body: { status } }));
  render();
  if (from === "shortlisted" && status === "applied" && state.claude) askTailor(j);
}

// --- tailored resume / cover letter
function askTailor(j) {
  $("#tJob").textContent = `${j.company} – ${j.title}`;
  $("#tResume").checked = $("#tCover").checked = true;
  $("#tailorDlg").dataset.id = j.id;
  $("#tailorDlg").showModal();
}
$("#tailorForm").addEventListener("change", () => ($("#tGo").disabled = !$("#tResume").checked && !$("#tCover").checked));
$("#tailorDlg").addEventListener("close", () => {
  if ($("#tailorDlg").returnValue === "go")
    makeDocs(Number($("#tailorDlg").dataset.id), $("#tResume").checked, $("#tCover").checked);
});

const docLinks = (id, f) => [["resume_pdf", "Resume PDF"], ["cover_pdf", "Cover letter PDF"]]
  .filter(([k]) => f[k]).map(([k, l]) => `<a href="/api/jobs/${id}/documents/${k}" target="_blank" rel="noopener">${l}</a>`).join("");

function toast(key, html, cls = "") {
  let el = document.querySelector(`.toast[data-key="${key}"]`);
  if (!el) { el = document.createElement("div"); el.dataset.key = key; $("#toasts").append(el); }
  el.className = `toast ${cls}`;
  el.innerHTML = `<button class="x" aria-label="Dismiss" data-dismiss>×</button>${html}`;
}

async function makeDocs(id, resume, cover) {
  const j = state.jobs.find(x => x.id == id) || current || {};
  const label = `${esc(j.company)} – ${esc(j.title)}`;
  try {
    await api(`/api/jobs/${id}/documents`, { method: "POST", body: { resume, cover_letter: cover } });
  } catch (e) { return toast(`doc${id}`, `Couldn't start: ${esc(e.message)}`, "error"); }
  toast(`doc${id}`, `<b>${label}</b><br>Tailoring… this takes a minute or two.`);
  const poll = setInterval(async () => {
    const s = await api(`/api/jobs/${id}/documents`).catch(() => null);
    if (!s || s.state === "running") { if (s) toast(`doc${id}`, `<b>${label}</b><br>${esc(s.message)}`); return; }
    clearInterval(poll);
    if (s.state === "done") {
      toast(`doc${id}`, `<b>${label}</b><br>Ready: ${docLinks(id, s.files)}`, "done");
      const jj = state.jobs.find(x => x.id == id); if (jj) { jj.has_docs = 1; render(); }
      if ($("#detailDlg").open && current?.id == id) renderDocs(id);
    } else toast(`doc${id}`, `<b>${label}</b><br>Failed: ${esc(s.message)}`, "error");
  }, 3000);
}

async function renderDocs(id) {
  const s = await api(`/api/jobs/${id}/documents`);
  if (!state.claude && !Object.keys(s.files).length) {
    $("#dDocs").innerHTML = `<div class="row"><b>Documents</b><span class="hint">Tailored resumes and cover letters need Claude. Add a Claude token or API key to the board's .env file.</span></div>`;
    return;
  }
  const has = Object.keys(s.files).length;
  $("#dDocs").innerHTML = s.state === "running" ? `<div class="row"><b>Documents</b> ${esc(s.message)}</div>`
    : has ? `<div class="row"><b>Documents</b>${docLinks(id, s.files)}</div>
        ${s.changes ? `<div class="changes">${esc(s.changes)}</div>` : ""}
        <button type="button" class="btn" data-tailor="${id}">Regenerate</button>`
      : `<div class="row"><b>Documents</b><button type="button" class="btn" data-tailor="${id}">Tailor resume / cover letter</button></div>`;
}

// --- events
document.addEventListener("click", (e) => {
  if (e.target.closest("[data-close]")) e.target.closest("dialog")?.close();
  const dismiss = e.target.closest("[data-dismiss]");
  if (dismiss) dismiss.parentElement.remove();
  const chip = e.target.closest("[data-chip]");
  if (chip) { state.chip = chip.dataset.chip; render(); }
  const src = e.target.closest("[data-src]");
  if (src) { state.source = src.dataset.src; render(); }
  const tl = e.target.closest("[data-tailor]");
  if (tl) { const j = state.jobs.find(x => x.id == tl.dataset.tailor) || current; $("#detailDlg").close(); askTailor(j); }
  const det = e.target.closest("[data-details]");
  if (det) openDetails(det.dataset.details);
});
document.addEventListener("change", (e) => {
  if (e.target.matches("[data-status]")) setStatus(e.target.dataset.status, e.target.value);
});
$("#search").addEventListener("input", (e) => { state.search = e.target.value; render(); });

// drag & drop between columns
document.addEventListener("dragstart", (e) => {
  const c = e.target.closest?.(".card");
  if (!c) return;
  e.dataTransfer.setData("text/plain", c.dataset.id);
  c.classList.add("dragging");
});
document.addEventListener("dragend", (e) => e.target.closest?.(".card")?.classList.remove("dragging"));
document.addEventListener("dragover", (e) => {
  const col = e.target.closest(".col");
  if (!col) return;
  e.preventDefault();
  document.querySelectorAll(".col.drop").forEach(c => c !== col && c.classList.remove("drop"));
  col.classList.add("drop");
});
document.addEventListener("dragleave", (e) => { if (!e.relatedTarget?.closest?.(".col")) document.querySelectorAll(".col.drop").forEach(c => c.classList.remove("drop")); });
document.addEventListener("drop", (e) => {
  const col = e.target.closest(".col");
  document.querySelectorAll(".col.drop").forEach(c => c.classList.remove("drop"));
  if (!col) return;
  e.preventDefault();
  setStatus(e.dataTransfer.getData("text/plain"), col.dataset.col);
});

// --- details modal
const statusOpts = COLUMNS.map(c => `<option value="${c.id}">${c.label}</option>`).join("");
$("#dStatus").innerHTML = statusOpts; $("#aStatus").innerHTML = statusOpts; $("#aStatus").value = "applied";

function setCategories(cats) {
  cats = cats && cats.length ? cats : ["Other"];
  // Rebuilding the selects would reset an open Details dialog, so only do it when they change.
  if ($("#aCategory").options.length && cats.join("|") === CATEGORIES.join("|")) return;
  CATEGORIES = cats;
  CHIPS = ["All", ...CATEGORIES.filter(c => c !== "Other"), "Contract"];
  if (!CHIPS.includes(state.chip)) state.chip = "All";
  const catOpts = CATEGORIES.map(c => `<option>${esc(c)}</option>`).join("");
  $("#dCategory").innerHTML = `<option value=""></option>` + catOpts; $("#aCategory").innerHTML = catOpts;
}
let current = null;

function skillDetails(j) {
  const sk = j && j.skills;
  if (!sk) return "";
  const row = (label, names) => names.length ? `<div><b>${label}:</b> ${esc(names.join(", "))}</div>` : "";
  return row("Your core skills it mentions", sk.core) + row("Working skills it mentions", sk.working) +
    row("Your gaps it mentions", sk.gap);
}

async function openDetails(id) {
  const j = current = await api(`/api/jobs/${id}`);
  $("#dCompany").textContent = j.company;
  $("#dTitle").textContent = j.title;
  safeUrl(j.url) ? $("#dTitle").setAttribute("href", safeUrl(j.url)) : $("#dTitle").removeAttribute("href");
  const where = [j.location, j.workplace, j.employment_type, j.source].filter(Boolean).map(esc).map(s => `<span class="tag">${s}</span>`);
  $("#dTags").innerHTML = tagsFor(j) + where.join("");
  $("#dFit").innerHTML = (j.fit_summary ? `<div><b>Why it fits:</b> ${esc(j.fit_summary)}</div>` : "") +
    (j.gaps ? `<div><b>Gaps:</b> ${esc(j.gaps)}</div>` : "") + skillDetails(state.jobs.find(x => x.id === j.id));
  $("#dStatus").value = j.status;
  $("#dPay").value = j.pay || "";
  $("#dApplied").value = j.applied_at ? j.applied_at.slice(0, 10) : "";
  $("#dCategory").value = j.category || "";
  $("#dNotes").value = j.notes || "";
  $("#dDesc").textContent = j.description || "No description saved.";
  renderDocs(j.id);
  $("#detailDlg").showModal();
}
$("#dSave").addEventListener("click", async () => {
  const body = { status: $("#dStatus").value, pay: $("#dPay").value, category: $("#dCategory").value,
    notes: $("#dNotes").value, applied_at: $("#dApplied").value ? `${$("#dApplied").value}T12:00:00` : null };
  const was = current.status;
  await api(`/api/jobs/${current.id}`, { method: "PATCH", body });
  $("#detailDlg").close(); await load();
  if (was === "shortlisted" && body.status === "applied") askTailor(current);
});
$("#dDelete").addEventListener("click", async () => {
  if (!confirm(`Delete ${current.company} – ${current.title}? It won't be scraped again.`)) return;
  await api(`/api/jobs/${current.id}`, { method: "DELETE" });
  $("#detailDlg").close(); load();
});

// --- add modal
$("#addBtn").addEventListener("click", () => { $("#addForm").reset(); $("#aStatus").value = "applied"; $("#addErr").textContent = ""; $("#addDlg").showModal(); });
$("#addForm").addEventListener("submit", async (e) => {
  if (e.submitter?.value === "close") return;
  e.preventDefault();
  try {
    await api("/api/jobs", { method: "POST", body: Object.fromEntries(new FormData(e.target)) });
    $("#addDlg").close(); load();
  } catch (err) { $("#addErr").textContent = err.message; }
});

// --- sources + scraping
$("#sourcesBtn").addEventListener("click", async () => {
  $("#srcText").value = (await api("/api/sources")).text;
  $("#srcErr").textContent = "";
  await Promise.all([pollStatus(), loadCompanies()]);
  $("#srcDlg").showModal();
});
$("#srcSave").addEventListener("click", async () => {
  try {
    const r = await api("/api/sources", { method: "PUT", body: { text: $("#srcText").value } });
    $("#srcErr").textContent = ""; $("#srcSave").textContent = `Saved (${r.count} sources)`;
    setTimeout(() => ($("#srcSave").textContent = "Save"), 2000);
  } catch (err) { $("#srcErr").textContent = err.message; }
});

async function loadCompanies() {
  const cos = await api("/api/companies");
  const on = cos.filter(c => c.enabled).length;
  $("#coCount").textContent = cos.length ? `${on} of ${cos.length} scanning` : "";
  if (!cos.length) { $("#coList").textContent = "None yet. They appear after the next scrape."; return; }
  $("#coList").innerHTML = cos.map(c => {
    const st = c.configured ? "in sources.yaml" : c.last_status === "ok" ? `${c.last_jobs} open jobs` : c.last_status ? esc(c.last_status) : "not scanned yet";
    return `<div class="co ${c.enabled ? "" : "off"}" title="Found via ${esc(c.discovered_from || "")}">
      <label><input type="checkbox" data-co="${esc(c.ats)}/${esc(c.slug)}" ${c.enabled ? "checked" : ""}>${esc(c.name || c.slug)}</label>
      <span class="ats">${esc(c.ats)}</span><span class="st">${st}</span></div>`;
  }).join("");
}
document.addEventListener("change", async (e) => {
  if (!e.target.matches("[data-co]")) return;
  await api(`/api/companies/${e.target.dataset.co}`, { method: "PATCH", body: { enabled: e.target.checked } });
  loadCompanies();
});

let polling = null;
async function pollStatus() {
  const s = await api("/api/scrape/status");
  $("#scrapeBtn").disabled = s.running;
  $("#scrapeBtn").textContent = s.running ? "Scraping…" : "Scrape now";
  const last = s.last;
  let txt = "";
  if (s.running) txt = s.log.at(-1) || "Starting…";
  else if (last) txt = `Last run ${fmt(last.finished_at)} ${toDate(last.finished_at)?.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}: ` +
    `${last.shortlisted} new${last.errors?.length ? `, ${last.errors.length} error(s)` : ""}`;
  $("#scrapeStatus").textContent = txt;
  $("#scrapeStatus").title = state.nextRun ? `Next scheduled run: ${toDate(state.nextRun).toLocaleString()}` : "";
  if (s.log.length) $("#runLog").textContent = s.log.join("\n");
  else if (last) $("#runLog").textContent = [`${last.shortlisted} shortlisted, ${last.screened_out} screened out, ${last.scored} scored`,
    ...(last.sources || []).map(x => `${x.name}: ${x.found} found, ${x.new} new, ${x.filtered} filtered`),
    ...(last.errors || []).map(e => `ERROR ${e}`)].join("\n");
  if (s.running && !polling) polling = setInterval(pollStatus, 2000);
  if (!s.running && polling) { clearInterval(polling); polling = null; load(); }
}
$("#scrapeBtn").addEventListener("click", async () => { await api("/api/scrape", { method: "POST" }); pollStatus(); });

// Theme picker (in Settings): the themes are defined in style.css; the choice is remembered in this browser.
const THEMES = [["graphite", "Graphite"], ["midnight", "Midnight"], ["fjord", "Fjord"], ["espresso", "Espresso"],
  ["forest", "Forest"], ["aubergine", "Aubergine"], ["catppuccin", "Catppuccin Mocha"], ["oled", "OLED Black"]];
const themeSel = $("#themeSel");
themeSel.innerHTML = THEMES.map(([id, name]) => `<option value="${id}">${name}</option>`).join("");
themeSel.value = THEMES.some(([id]) => id === document.documentElement.dataset.theme) ? document.documentElement.dataset.theme : "graphite";
themeSel.addEventListener("change", () => {
  document.documentElement.dataset.theme = themeSel.value;
  try { localStorage.setItem("theme", themeSel.value); } catch (e) {}
});

// --- settings modal (profile.yaml values that are on/off or a single value; each change saves at once)
$("#settingsBtn").addEventListener("click", async () => {
  $("#setDlg").showModal();
  try { renderSettings((await api("/api/settings")).settings); }
  catch (e) { $("#setBody").innerHTML = `<p class="err">Couldn't load settings: ${esc(e.message)}</p>`; }
});

function settingControl(s) {
  const id = `set-${s.path.replace(/\./g, "-")}`;
  if (s.type === "bool")
    return `<label class="switch"><input type="checkbox" id="${id}" ${s.value ? "checked" : ""}><span></span></label>`;
  if (s.type === "time") return `<input type="time" id="${id}" value="${esc(s.value)}" required>`;
  if (s.type === "int")
    return `<input type="number" id="${id}" value="${esc(s.value)}" min="${s.min ?? 0}" max="${s.max ?? ""}" step="${s.step ?? 1}" inputmode="numeric">`;
  return `<input type="text" id="${id}" value="${esc(s.value)}" maxlength="80" class="wide">`;
}

function renderSettings(list) {
  let html = "", group = "";
  for (const s of list) {
    if (s.group !== group) { group = s.group; html += `<h3>${esc(group)}</h3>`; }
    html += `<div class="set-row" data-path="${esc(s.path)}">
      <span class="set-label"><label for="set-${esc(s.path.replace(/\./g, "-"))}">${esc(s.label)}${s.unit ? ` <small class="unit">(${esc(s.unit)})</small>` : ""}</label>
        <span class="info" tabindex="0" data-tip="${esc(s.help)}">i</span></span>
      <span class="set-msg" aria-live="polite"></span>
      <span class="set-ctl">${settingControl(s)}</span></div>`;
  }
  $("#setBody").innerHTML = html;
  for (const s of list) {
    const row = $(`#setBody .set-row[data-path="${s.path}"]`), input = row.querySelector("input");
    let saved = s.value;
    input.addEventListener("change", async () => {
      const value = s.type === "bool" ? input.checked : input.value;
      const msg = row.querySelector(".set-msg");
      msg.className = "set-msg"; msg.textContent = "Saving…";
      try {
        const r = await api("/api/settings", { method: "PUT", body: { path: s.path, value } });
        saved = r.value;
        if (s.type !== "bool") input.value = r.value;
        msg.className = "set-msg ok"; msg.textContent = "Saved";
        setTimeout(() => { if (msg.textContent === "Saved") msg.textContent = ""; }, 2000);
        load();   // header name, daily goal, next run time
      } catch (e) {
        if (s.type === "bool") input.checked = saved; else input.value = saved;
        msg.className = "set-msg error"; msg.textContent = e.message;
      }
    });
  }
}

load().then(pollStatus);
