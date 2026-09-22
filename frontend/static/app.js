"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const state = { reports: { a: null, b: null }, diff: null, config: null };

// ------------------------------------------------------------------ utilitaires
function esc(v) {
  return String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function fmt(v, unit = "") {
  if (v === null || v === undefined) return "–";
  if (typeof v === "number") {
    const s = Math.abs(v) >= 1000 ? Math.round(v).toLocaleString("fr-FR") : v.toLocaleString("fr-FR", { maximumFractionDigits: 1 });
    return s + (unit ? (unit === "%" ? " %" : " " + unit) : "");
  }
  return esc(v);
}

// higherIsBetter = null : écart affiché sans couleur (ni bon ni mauvais en soi).
function signed(v, unit = "", higherIsBetter = true) {
  if (v === null || v === undefined || v === 0) return `<span class="muted">${v === 0 ? "=" : "–"}</span>`;
  const cls = higherIsBetter === null ? "" : (v > 0) === higherIsBetter ? "pos" : "neg";
  return `<span class="${cls}">${v > 0 ? "+" : ""}${fmt(v, unit)}</span>`;
}

function showAlert(el, msg) {
  el.innerHTML = esc(msg);
  el.classList.toggle("hidden", !msg);
}

async function api(path, body) {
  const opts = body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  };
  let res;
  try {
    res = await fetch(path, opts);
  } catch {
    throw Object.assign(new Error("Le serveur local ne répond pas. Est-ce que `flask run` tourne toujours ?"), { code: "network" });
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw Object.assign(new Error(data.error || `Erreur HTTP ${res.status}`), { code: data.code });
  return data;
}

function table(el, head, rows) {
  el.innerHTML = `<thead><tr>${head.map((h) => `<th>${h}</th>`).join("")}</tr></thead>` +
    `<tbody>${rows.length ? rows.join("") : `<tr><td colspan="${head.length}" class="muted">Aucune donnée</td></tr>`}</tbody>`;
}

// ------------------------------------------------------------------ configuration
async function refreshConfig() {
  try {
    state.config = await api("/api/config");
  } catch (e) {
    showAlert($("#global-alert"), e.message);
    return;
  }
  const c = state.config;
  $("#wcl-usage").textContent = `WCL : ${c.wcl_calls_last_hour}/${c.wcl_hourly_budget} req. cette heure`;
  $("#model-name").textContent = c.model;
  if (!c.wcl_configured) {
    showAlert($("#global-alert"), "Clé Warcraft Logs manquante : ajoute WCL_API_KEY dans le fichier .env puis relance le serveur.");
  }
}

function openConfig(message = "") {
  $("#config-msg").textContent = message;
  $("#config-msg").className = "small" + (message ? " worse" : "");
  $("#config-modal").classList.remove("hidden");
  $("#anthropic-key").focus();
}

$("#open-config").addEventListener("click", () => openConfig());
$("#close-config").addEventListener("click", () => $("#config-modal").classList.add("hidden"));
$("#config-modal").addEventListener("click", (e) => { if (e.target.id === "config-modal") e.currentTarget.classList.add("hidden"); });

$("#config-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const msg = $("#config-msg");
  msg.className = "small muted";
  msg.innerHTML = '<span class="spinner"></span>Vérification de la clé…';
  try {
    const r = await api("/api/config/anthropic", { api_key: $("#anthropic-key").value.trim() });
    $("#anthropic-key").value = "";
    msg.className = "small better";
    msg.textContent = r.warning || "Clé enregistrée dans .env ✔";
    await refreshConfig();
    if (state.diff) runAi();
    setTimeout(() => $("#config-modal").classList.add("hidden"), 1200);
  } catch (err) {
    msg.className = "small worse";
    msg.textContent = err.message;
  }
});

// ------------------------------------------------------------------ étape 1 : chargement
$("#links-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = $("#load-btn");
  btn.disabled = true;
  btn.textContent = "Chargement…";
  showAlert($("#global-alert"), "");
  $("#results").classList.add("hidden");
  try {
    const [a, b] = await Promise.all([
      api("/api/report", { url: $("#url-a").value }).catch((err) => { throw new Error("Lien A : " + err.message); }),
      api("/api/report", { url: $("#url-b").value }).catch((err) => { throw new Error("Lien B : " + err.message); }),
    ]);
    state.reports = { a, b };
    setupPickers();
    $("#select-card").classList.remove("hidden");
  } catch (err) {
    showAlert($("#global-alert"), err.message);
    $("#select-card").classList.add("hidden");
  } finally {
    btn.disabled = false;
    btn.textContent = "Charger les rapports";
    refreshConfig();
  }
});

// ------------------------------------------------------------------ étape 2 : clés et joueurs
const picker = (side) => $(`.picker[data-side="${side}"]`);

function setupPickers() {
  for (const side of ["a", "b"]) {
    const rep = state.reports[side];
    const p = picker(side);
    $(".report-title", p).textContent = rep.title || rep.code;
    $(".fight-select", p).innerHTML = rep.fights
      .map((f) => `<option value="${f.id}" ${f.id === rep.selectedFight ? "selected" : ""}>${esc(f.label)}</option>`)
      .join("");
  }
  refreshPlayers();
}

function playersInFight(side) {
  const rep = state.reports[side];
  const fid = Number($(".fight-select", picker(side)).value);
  return rep.players.filter((pl) => pl.fights.includes(fid));
}

function refreshPlayers() {
  const pa = playersInFight("a");
  const pb = playersInFight("b");
  const specsB = new Set(pb.map((p) => p.specKey));
  const commonA = pa.filter((p) => specsB.has(p.specKey));
  const selA = $(".player-select", picker("a"));
  const previousA = selA.value;

  if (!commonA.length) {
    selA.innerHTML = "";
    $(".player-select", picker("b")).innerHTML = "";
    showAlert($("#match-alert"), "Aucun joueur ne partage la même classe et spécialisation entre ces deux clés. Choisis d'autres clés ou d'autres rapports.");
    $("#analyze-btn").disabled = true;
    return;
  }
  showAlert($("#match-alert"), "");
  $("#analyze-btn").disabled = false;
  selA.innerHTML = commonA.map((p) => `<option value="${p.id}">${esc(p.label)}</option>`).join("");
  if (commonA.some((p) => String(p.id) === previousA)) selA.value = previousA;
  refreshPlayersB();
}

function refreshPlayersB() {
  const idA = Number($(".player-select", picker("a")).value);
  const playerA = state.reports.a.players.find((p) => p.id === idA);
  const matches = playersInFight("b").filter((p) => playerA && p.specKey === playerA.specKey);
  $(".player-select", picker("b")).innerHTML = matches.map((p) => `<option value="${p.id}">${esc(p.label)}</option>`).join("");
}

for (const side of ["a", "b"]) {
  $(".fight-select", picker(side)).addEventListener("change", refreshPlayers);
}
$(".player-select", picker("a")).addEventListener("change", refreshPlayersB);

// ------------------------------------------------------------------ analyse
$("#analyze-btn").addEventListener("click", async () => {
  const side = (s) => ({
    code: state.reports[s].code,
    fight: Number($(".fight-select", picker(s)).value),
    player: Number($(".player-select", picker(s)).value),
  });
  const btn = $("#analyze-btn");
  btn.disabled = true;
  showAlert($("#global-alert"), "");
  $("#results").classList.add("hidden");
  $("#progress-card").classList.remove("hidden");
  setProgress(0, "Démarrage de l'analyse…");
  try {
    const { job } = await api("/api/analyze", {
      a: side("a"), b: side("b"), gap_threshold_s: Number($("#gap-threshold").value),
    });
    const result = await pollJob(job);
    state.diff = result.diff;
    renderResults(result.diff);
    runAi();
  } catch (err) {
    showAlert($("#global-alert"), err.message);
  } finally {
    btn.disabled = false;
    $("#progress-card").classList.add("hidden");
    refreshConfig();
  }
});

function setProgress(frac, msg) {
  $("#progress-bar").style.width = `${Math.round(frac * 100)}%`;
  $("#progress-msg").textContent = msg;
}

async function pollJob(id) {
  for (;;) {
    const job = await api(`/api/jobs/${id}`);
    setProgress(job.progress, job.message);
    if (job.status === "done") return job.result;
    if (job.status === "error") throw new Error(job.error);
    await new Promise((r) => setTimeout(r, 800));
  }
}

// ------------------------------------------------------------------ rendu des résultats
function renderResults(d) {
  const c = d.context;
  const who = (k) => {
    const m = c[k];
    const lvl = m.keystone_level ? ` +${m.keystone_level}` : "";
    return `<div><span class="tag tag-${k.toLowerCase()}">${k}</span> <span class="who">${esc(m.player)}</span>
      <span class="muted">· ${esc(m.fight_name)}${lvl} · ${fmtTime(m.duration_s)}${m.timed === false ? " · non timée" : ""}</span></div>`;
  };
  const warnings = [];
  if (!c.same_dungeon) warnings.push("Les deux clés ne portent pas sur le même donjon : la comparaison est moins fiable.");
  for (const k of ["A", "B"]) if (c[k].truncated) warnings.push(`Joueur ${k} : trop d'événements, les données ont été tronquées.`);
  $("#context").innerHTML = `<div><strong>${esc(c.spec)}</strong></div>${who("A")}${who("B")}` +
    `<div class="muted">Écart de DPS : <strong>${fmt(c.dps_gap_pct, "%")}</strong> · joueur le moins performant : <strong>${c.weaker_player}</strong></div>` +
    warnings.map((w) => `<div class="alert warn" style="width:100%">${esc(w)}</div>`).join("");

  // Métriques clés
  const nA = `<span class="col-a">${esc(c.A.player)}</span>`;
  const nB = `<span class="col-b">${esc(c.B.player)}</span>`;
  table($("#headline-table"), ["Métrique", nA, nB, "Écart (B − A)"],
    d.headline.map((h) => {
      const cellA = `<td class="num ${h.better === "A" ? "better" : ""}">${fmt(h.a, h.unit)}</td>`;
      const cellB = `<td class="num ${h.better === "B" ? "better" : ""}">${fmt(h.b, h.unit)}</td>`;
      const rel = h.rel_pct !== null && h.rel_pct !== undefined && h.unit !== "%" ? ` <span class="muted small">(${h.rel_pct > 0 ? "+" : ""}${fmt(h.rel_pct)} %)</span>` : "";
      const diff = h.better === null && h.diff !== null ? fmt(h.diff, h.unit) : signed(h.diff, h.unit, (h.better === "B") === (h.diff > 0));
      return `<tr><td>${esc(h.label)}</td>${cellA}${cellB}<td class="num">${diff}${rel}</td></tr>`;
    }));

  // Sorts
  const abilityRows = d.abilities.map((r) =>
    `<tr><td>${esc(r.name)}</td><td class="num">${r.a_count}</td><td class="num">${r.b_count}</td>` +
    `<td class="num col-a">${fmt(r.a_per_min)}</td><td class="num col-b">${fmt(r.b_per_min)}</td>` +
    `<td class="num">${signed(r.diff_per_min, "", null)}</td>` +
    `<td class="num">${r.rel_pct === null ? '<span class="muted">nouveau</span>' : fmt(r.rel_pct, "%")}</td></tr>`);
  const head = ["Sort", `Casts ${nA}`, `Casts ${nB}`, `/min ${nA}`, `/min ${nB}`, "Écart /min", "Écart %"];
  const showAbilities = (all) => table($("#abilities-table"), head, all ? abilityRows : abilityRows.slice(0, 15));
  showAbilities(false);
  const more = $("#abilities-more");
  more.classList.toggle("hidden", abilityRows.length <= 15);
  more.textContent = "Tout afficher";
  more.onclick = () => {
    const expand = more.textContent === "Tout afficher";
    showAbilities(expand);
    more.textContent = expand ? "Réduire" : "Tout afficher";
  };

  // Debuffs
  table($("#debuffs-table"), ["Debuff", nA, nB, "Écart", `Applic. ${nA} / ${nB}`],
    d.debuffs.map((r) => `<tr><td>${esc(r.name)}</td><td class="num col-a">${fmt(r.a_uptime, "%")}</td>` +
      `<td class="num col-b">${fmt(r.b_uptime, "%")}</td><td class="num">${r.a_uptime === null || r.b_uptime === null ? "–" : signed(r.diff, "%")}</td>` +
      `<td class="num muted">${r.a_applications} / ${r.b_applications}</td></tr>`));

  // Ressources
  table($("#resources-table"), ["Ressource", `Gaspillé ${nA}`, `Gaspillé ${nB}`, `% gaspi ${nA} / ${nB}`, `Casts ressource pleine ${nA} / ${nB}`],
    d.resources.map((r) => `<tr><td>${esc(r.resource)}</td>` +
      `<td class="num col-a">${fmt(r.a_wasted)}</td><td class="num col-b">${fmt(r.b_wasted)}</td>` +
      `<td class="num">${fmt(r.a_waste_pct, "%")} / ${fmt(r.b_waste_pct, "%")}</td>` +
      `<td class="num">${fmt(r.a_casts_at_cap_pct, "%")} / ${fmt(r.b_casts_at_cap_pct, "%")}</td></tr>`));

  // Temps mort
  const gapCol = (k) => {
    const g = d.gaps[k];
    return `<div><h3><span class="tag tag-${k.toLowerCase()}">${k}</span> ${fmt(g.total_s, "s")} · ${g.count} trous</h3>
      <p class="small muted">Trous les plus longs</p>
      <ul class="plain small">${g.longest.map((x) => `<li>${esc(x.at)} — ${fmt(x.s, "s")} après ${esc(x.after)}</li>`).join("") || "<li>Aucun</li>"}</ul>
      <p class="small muted">Sorts suivis d'un trou</p>
      <ul class="plain small">${g.after_spell.map((x) => `<li>${esc(x.spell)} : ${x.count}× (${fmt(x.total_s, "s")})</li>`).join("") || "<li>Aucun</li>"}</ul></div>`;
  };
  $("#gaps-box").innerHTML = `<p class="small muted">Seuil : ${fmt(c.gap_threshold_s, "s")}. Un trou après un sort canalisé peut être normal.</p>` +
    `<div class="side-by-side">${gapCol("A")}${gapCol("B")}</div>`;

  // Dégâts
  table($("#damage-table"), ["Sort", `% ${nA}`, `% ${nB}`, "Écart"],
    d.damage_breakdown.map((r) => `<tr><td>${esc(r.name)}</td><td class="num col-a">${fmt(r.a_share, "%")}</td>` +
      `<td class="num col-b">${fmt(r.b_share, "%")}</td><td class="num">${signed(r.diff, "%", null)}</td></tr>`));

  // Boss
  const seq = (list) => `<div class="seq">${(list || []).map((s) => `<span>${esc(s)}</span>`).join("")}</div>`;
  const bossSide = (k, b) => b
    ? `<div><h3><span class="tag tag-${k.toLowerCase()}">${k}</span> ${esc(b.duration)} · ${fmt(b.boss_dps_player_only)} DPS boss · ${fmt(b.casts_per_min)} casts/min</h3>${seq(b.opener)}</div>`
    : `<div><h3><span class="tag tag-${k.toLowerCase()}">${k}</span> <span class="muted">non rencontré</span></h3></div>`;
  $("#bosses-box").innerHTML = (d.bosses.length
    ? d.bosses.map((b) => `<div class="boss"><strong>${esc(b.boss)}</strong><div class="side-by-side">${bossSide("A", b.a)}${bossSide("B", b.b)}</div></div>`).join("")
    : '<p class="muted">Aucun boss détecté.</p>') +
    `<div class="boss"><strong>Ouverture du premier pack</strong><div class="side-by-side">` +
    `<div><h3><span class="tag tag-a">A</span></h3>${seq(d.first_pull_opener.A)}</div>` +
    `<div><h3><span class="tag tag-b">B</span></h3>${seq(d.first_pull_opener.B)}</div></div></div>`;

  // Buffs
  table($("#buffs-table"), ["Buff", `Uptime ${nA}`, `Uptime ${nB}`, "Écart"],
    d.buffs.map((r) => `<tr><td>${esc(r.name)}</td><td class="num col-a">${fmt(r.a_uptime, "%")}</td>` +
      `<td class="num col-b">${fmt(r.b_uptime, "%")}</td><td class="num">${signed(r.diff, "%", null)}</td></tr>`));

  $("#copy-msg").className = "small muted";
  $("#copy-msg").innerHTML = 'Sans clé API : copie le résumé, puis colle-le dans une nouvelle conversation sur <a href="https://claude.ai/new" target="_blank" rel="noopener">claude.ai</a>.';
  $("#results").classList.remove("hidden");
  $("#context").scrollIntoView({ behavior: "smooth" });
}

function fmtTime(s) {
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

// ------------------------------------------------------------------ analyse Claude
function renderSetup(message) {
  $("#ai-box").innerHTML = `<div class="setup">
    <p>${message ? `<span class="worse">${esc(message)}</span><br>` : ""}
    L'analyse texte nécessite une clé API Anthropic. Les métriques ci-dessous restent disponibles sans elle.</p>
    <button class="btn primary" type="button" id="setup-btn">Configurer la clé Anthropic</button></div>`;
  $("#setup-btn").addEventListener("click", () => openConfig(message));
}

async function runAi() {
  if (!state.diff) return;
  if (state.config && !state.config.anthropic_configured) return renderSetup("");
  const box = $("#ai-box");
  box.innerHTML = '<p class="muted"><span class="spinner"></span>Claude analyse les différences de rotation… (30 s à 1 min)</p>';
  try {
    const r = await api("/api/ai", { diff: state.diff });
    const html = window.marked && window.DOMPurify
      ? DOMPurify.sanitize(marked.parse(r.markdown))
      : `<pre style="white-space:pre-wrap">${esc(r.markdown)}</pre>`;
    box.innerHTML = `<div class="ai-content">${html}</div>
      <p class="muted small">Modèle : ${esc(r.model)} · ${r.usage.input_tokens} tokens en entrée, ${r.usage.output_tokens} en sortie
      · <a href="#" id="ai-retry">relancer l'analyse</a></p>`;
  } catch (err) {
    if (["missing_key", "invalid_key", "no_credit"].includes(err.code)) return renderSetup(err.message);
    box.innerHTML = `<div class="alert error">${esc(err.message)}</div><a href="#" id="ai-retry">Réessayer</a>`;
  }
  $("#ai-retry")?.addEventListener("click", (e) => { e.preventDefault(); runAi(); });
}

// ------------------------------------------------------------------ copie manuelle pour claude.ai
async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    // Repli si l'API presse-papiers est refusée par le navigateur.
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    ta.remove();
    return ok;
  }
}

$("#copy-prompt").addEventListener("click", async () => {
  const msg = $("#copy-msg");
  if (!state.diff) return;
  try {
    const { text } = await api("/api/ai/prompt", { diff: state.diff });
    if (!(await copyText(text))) throw new Error("Le navigateur a refusé l'accès au presse-papiers.");
    msg.className = "small better";
    msg.innerHTML = 'Copié ✔ Ouvre <a href="https://claude.ai/new" target="_blank" rel="noopener">claude.ai</a>, colle (Ctrl+V) dans une nouvelle conversation et envoie.';
  } catch (err) {
    msg.className = "small worse";
    msg.textContent = err.message;
  }
});

refreshConfig();
