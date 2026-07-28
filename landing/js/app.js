/* ═══════════════════════════════════════════════
   KFT — experience layer
   GSAP · ScrollSmoother · SplitText · interactions
   ═══════════════════════════════════════════════ */
import { TEAMS, FIXTURES, PLAYERS, GWS, GW_COUNT, BRACKET_LABELS } from "./data.js";

const $  = (s, c = document) => c.querySelector(s);
const $$ = (s, c = document) => [...c.querySelectorAll(s)];
const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
const finePointer = matchMedia("(pointer: fine)").matches;

document.documentElement.classList.add("js");
gsap.registerPlugin(ScrollTrigger, ScrollSmoother, ScrollToPlugin, SplitText);

/* ── smooth scroll ─────────────────────────────── */
let smoother = null;
if (!reduced) {
  smoother = ScrollSmoother.create({
    wrapper: "#smooth-wrapper",
    content: "#smooth-content",
    smooth: 1.35,
    effects: true,
    normalizeScroll: true,
  });
}
const scrollToEl = (target) => {
  if (smoother) smoother.scrollTo(target, true, "top 72px");
  else gsap.to(window, { duration: 0.8, scrollTo: { y: target, offsetY: 72 } });
};

/* ── three.js hero (progressive) ───────────────── */
let scene = null;
if (!reduced) {
  import("./scene.js")
    .then((m) => {
      scene = m.initScene($("#heroCanvas"));
      ScrollTrigger.create({
        trigger: "#hero", start: "top top", end: "bottom top", scrub: true,
        onUpdate: (st) => scene && scene.setScroll(st.progress),
      });
    })
    .catch(() => $("#hero").classList.add("hero--fallback"));
}

/* ── preloader ─────────────────────────────────── */
const pre = $("#preloader");
function runPreloader() {
  const state = { p: 0 };
  const tl = gsap.timeline();
  tl.from(".preloader__logo span", { yPercent: 120, opacity: 0, stagger: 0.09, duration: 0.7, ease: "power4.out" })
    .to(state, {
      p: 100, duration: reduced ? 0.1 : 1.7, ease: "power2.inOut",
      onUpdate() {
        $("#prePct").textContent = Math.round(state.p);
        $("#preFill").style.width = state.p + "%";
      },
    }, "-=0.2")
    .to(".preloader__curtain--a", { scaleY: 1, duration: 0.5, ease: "power4.inOut" })
    .to(".preloader__curtain--b", { scaleY: 1, duration: 0.5, ease: "power4.inOut" }, "-=0.32")
    .set(".preloader__inner", { opacity: 0 })
    .to(pre, { yPercent: -100, duration: 0.75, ease: "power4.inOut" })
    .set(pre, { display: "none" })
    .add(heroIntro, "-=0.55");
}

/* ── hero intro ────────────────────────────────── */
function heroIntro() {
  if (reduced) { gsap.set(".hero [data-reveal], .hero__line", { opacity: 1, y: 0 }); return; }
  const tl = gsap.timeline({ defaults: { ease: "power4.out" } });
  $$(".hero__line").forEach((line, i) => {
    gsap.set(line, { opacity: 1 });
    const split = new SplitText(line.querySelector("[data-split]") || line, { type: "chars" });
    tl.from(split.chars, { yPercent: 118, rotateX: -50, stagger: 0.024, duration: 1 }, i * 0.11);
  });
  tl.to(".hero [data-reveal]", { opacity: 1, y: 0, stagger: 0.1, duration: 0.9 }, 0.55)
    .from(".hero__scrollcue", { opacity: 0, y: 14, duration: 0.7 }, 1.1);
}
window.addEventListener("load", runPreloader);
setTimeout(() => { if (!pre.dataset.done) { pre.dataset.done = 1; } }, 4000);

/* ── custom cursor ─────────────────────────────── */
if (finePointer && !reduced) {
  document.body.classList.add("has-cursor");
  const dot = $("#cursorDot"), ring = $("#cursorRing"), label = $("#cursorLabel"), cur = $("#cursor");
  const dx = gsap.quickTo(dot, "x", { duration: 0.08 }), dy = gsap.quickTo(dot, "y", { duration: 0.08 });
  const rx = gsap.quickTo(ring, "x", { duration: 0.35, ease: "power3" }), ry = gsap.quickTo(ring, "y", { duration: 0.35, ease: "power3" });
  window.addEventListener("pointermove", (e) => { dx(e.clientX); dy(e.clientY); rx(e.clientX); ry(e.clientY); }, { passive: true });
  window.addEventListener("pointerdown", () => cur.classList.add("cursor--down"));
  window.addEventListener("pointerup", () => cur.classList.remove("cursor--down"));
  document.addEventListener("pointerover", (e) => {
    const t = e.target.closest("[data-cursor], a, button, .chip, .tcell, .rbar__track, th");
    if (t) { cur.classList.add("cursor--hover"); label.textContent = t.dataset ? (t.dataset.cursor || "") : ""; }
    else cur.classList.remove("cursor--hover");
  });
}

/* ── nav behaviour ─────────────────────────────── */
const nav = $("#nav");
let lastY = 0;
ScrollTrigger.create({
  start: 0, end: "max",
  onUpdate(self) {
    const y = self.scroll();
    nav.classList.toggle("is-scrolled", y > 40);
    nav.classList.toggle("is-hidden", y > 500 && y > lastY && !$("#menu").classList.contains("is-open"));
    lastY = y;
    gsap.set("#progressBar", { scaleX: self.progress });
  },
});

/* active section highlight */
$$("main section[id]").forEach((sec) => {
  ScrollTrigger.create({
    trigger: sec, start: "top 45%", end: "bottom 45%",
    onToggle(self) {
      if (!self.isActive) return;
      $$(".nav__link").forEach((l) => l.classList.toggle("is-active", l.getAttribute("href") === "#" + sec.id));
    },
  });
});

/* anchor scrolling */
document.addEventListener("click", (e) => {
  const a = e.target.closest("[data-scroll]");
  if (!a) return;
  const target = $(a.getAttribute("href"));
  if (!target) return;
  e.preventDefault();
  closeMenu();
  scrollToEl(target);
});

/* card click-through (toolkit deck) */
$$("[data-goto]").forEach((card) => card.addEventListener("click", () => {
  const t = $(card.dataset.goto);
  if (t) scrollToEl(t);
}));

/* mobile menu */
const burger = $("#burger"), menu = $("#menu");
function closeMenu() {
  menu.classList.remove("is-open"); burger.classList.remove("is-open");
  burger.setAttribute("aria-expanded", "false"); menu.setAttribute("aria-hidden", "true");
}
burger.addEventListener("click", () => {
  const open = menu.classList.toggle("is-open");
  burger.classList.toggle("is-open", open);
  burger.setAttribute("aria-expanded", String(open));
  menu.setAttribute("aria-hidden", String(!open));
  if (open) gsap.from(".menu__links a", { x: -30, opacity: 0, stagger: 0.06, duration: 0.5, ease: "power3.out", delay: 0.25 });
});

/* ── scroll reveals ────────────────────────────── */
$$("[data-reveal]").forEach((el) => {
  if (el.closest(".hero")) return;
  gsap.to(el, {
    opacity: 1, y: 0, duration: 1, ease: "power4.out",
    scrollTrigger: { trigger: el, start: "top 88%" },
  });
});
$$("[data-split]").forEach((el) => {
  if (el.closest(".hero")) return;
  const split = new SplitText(el, { type: "words" });
  gsap.from(split.words, {
    yPercent: 60, opacity: 0, stagger: 0.05, duration: 0.9, ease: "power4.out",
    scrollTrigger: { trigger: el, start: "top 86%" },
  });
});

/* ── counters ──────────────────────────────────── */
$$("[data-count]").forEach((el) => {
  const end = +el.dataset.count;
  const obj = { v: 0 };
  ScrollTrigger.create({
    trigger: el, start: "top 90%", once: true,
    onEnter: () => gsap.to(obj, {
      v: end, duration: 1.8, ease: "power2.out",
      onUpdate: () => (el.textContent = Math.round(obj.v).toLocaleString()),
    }),
  });
});

/* ── marquee (velocity-reactive) ───────────────── */
if (!reduced) {
  const marq = gsap.to("#marqueeTrack", { xPercent: -50, ease: "none", duration: 22, repeat: -1 });
  ScrollTrigger.create({
    start: 0, end: "max",
    onUpdate(self) {
      const v = gsap.utils.clamp(-4, 4, self.getVelocity() / 300);
      gsap.to(marq, { timeScale: 1 + Math.abs(v), duration: 0.4, overwrite: true });
    },
  });
}

/* ── toolkit horizontal pin ────────────────────── */
if (!reduced && innerWidth > 860) {
  const track = $("#toolkitTrack");
  const getX = () => -(track.scrollWidth - innerWidth + 64);
  gsap.to(track, {
    x: getX, ease: "none",
    scrollTrigger: {
      trigger: "#toolkitPin", start: "top top", end: () => "+=" + (track.scrollWidth - innerWidth + 400),
      pin: true, scrub: 1, invalidateOnRefresh: true,
    },
  });
  gsap.from(".tool-card", {
    y: 60, opacity: 0, stagger: 0.08, duration: 0.9, ease: "power3.out",
    scrollTrigger: { trigger: "#toolkit", start: "top 70%" },
  });
}

/* ── magnetic elements ─────────────────────────── */
if (finePointer && !reduced) {
  $$("[data-magnetic]").forEach((el) => {
    const xTo = gsap.quickTo(el, "x", { duration: 0.4, ease: "power3" });
    const yTo = gsap.quickTo(el, "y", { duration: 0.4, ease: "power3" });
    el.addEventListener("pointermove", (e) => {
      const r = el.getBoundingClientRect();
      xTo((e.clientX - r.left - r.width / 2) * 0.35);
      yTo((e.clientY - r.top - r.height / 2) * 0.35);
    });
    el.addEventListener("pointerleave", () => { xTo(0); yTo(0); });
  });

  /* 3d tilt + glow-follow */
  $$("[data-tilt]").forEach((card) => {
    card.addEventListener("pointermove", (e) => {
      const r = card.getBoundingClientRect();
      const px = (e.clientX - r.left) / r.width, py = (e.clientY - r.top) / r.height;
      card.style.setProperty("--mx", px * 100 + "%");
      card.style.setProperty("--my", py * 100 + "%");
      gsap.to(card, { rotateY: (px - 0.5) * 8, rotateX: (0.5 - py) * 8, transformPerspective: 800, duration: 0.5 });
    });
    card.addEventListener("pointerleave", () => gsap.to(card, { rotateX: 0, rotateY: 0, duration: 0.7, ease: "elastic.out(1,0.5)" }));
  });
}

/* ═══════════════════════════════════════════════
   TOOL 1 — xGI RADAR
   ═══════════════════════════════════════════════ */
const radar = { gwIdx: 0, metric: "xgi", pos: "ALL" };
const radarChart = $("#radarChart"), radarTip = $("#radarTip");

function metricVal(p, i) {
  if (radar.metric === "xg") return p.xg[i];
  if (radar.metric === "xa") return p.xa[i];
  return +(p.xg[i] + p.xa[i]).toFixed(2);
}

function renderRadar(animate = true) {
  const i = radar.gwIdx;
  $("#gwLabel").textContent = "GW " + GWS[i];
  $("#gwPrev").disabled = i === 0;
  $("#gwNext").disabled = i === GW_COUNT - 1;

  const pool = PLAYERS
    .filter((p) => radar.pos === "ALL" || p.pos === radar.pos)
    .sort((a, b) => metricVal(b, i) - metricVal(a, i))
    .slice(0, 14);
  const max = Math.max(...pool.map((p) => metricVal(p, i)), 0.1);

  radarChart.innerHTML = pool.map((p) => {
    const g = p.xg[i], a = p.xa[i], v = metricVal(p, i);
    const gw = (radar.metric === "xa" ? 0 : (g / max) * 100);
    const aw = (radar.metric === "xg" ? 0 : (a / max) * 100);
    const fx = FIXTURES[p.team][i];
    return `<div class="rbar" data-id="${p.id}">
      <div class="rbar__name"><b>${p.name}</b><span>${p.team} · ${p.pos} · £${p.price.toFixed(1)}</span></div>
      <div class="rbar__track" data-tt="<b>${p.name}</b> — ${fx.home ? "vs" : "@"} ${fx.opp}<br><span class='tt-xg'>xG ${g.toFixed(2)}</span> · <span class='tt-xa'>xA ${a.toFixed(2)}</span> · xGI ${(g + a).toFixed(2)}">
        <span class="rbar__xg" style="width:0%" data-w="${radar.metric === "xa" ? 0 : gw}"></span>
        <span class="rbar__xa" style="left:0;width:0%" data-l="${radar.metric === "xa" ? 0 : gw}" data-w="${aw}"></span>
      </div>
      <div class="rbar__val">${v.toFixed(2)}</div>
    </div>`;
  }).join("");

  $$(".rbar__xa", radarChart).forEach((el) => (el.style.left = el.dataset.l + "%"));
  const bars = $$(".rbar__xg, .rbar__xa", radarChart);
  if (animate && !reduced) {
    gsap.to(bars, { width: (idx, el) => el.dataset.w + "%", duration: 0.9, ease: "power4.out", stagger: 0.02 });
    gsap.from($$(".rbar", radarChart), { opacity: 0, x: -14, stagger: 0.025, duration: 0.45, ease: "power2.out", clearProps: "opacity,transform" });
  } else {
    bars.forEach((el) => (el.style.width = el.dataset.w + "%"));
  }
}

$("#gwPrev").addEventListener("click", () => { radar.gwIdx = Math.max(0, radar.gwIdx - 1); renderRadar(); });
$("#gwNext").addEventListener("click", () => { radar.gwIdx = Math.min(GW_COUNT - 1, radar.gwIdx + 1); renderRadar(); });
function chipGroup(sel, key, cb) {
  $(sel).addEventListener("click", (e) => {
    const b = e.target.closest(".chip"); if (!b) return;
    $$(".chip", $(sel)).forEach((c) => c.classList.remove("is-active"));
    b.classList.add("is-active");
    cb(b.dataset[key]);
  });
}
chipGroup("#radarMetric", "metric", (v) => { radar.metric = v; renderRadar(); });
chipGroup("#radarPos", "pos", (v) => { radar.pos = v; renderRadar(); });

/* tooltip */
radarChart.addEventListener("pointermove", (e) => {
  const t = e.target.closest("[data-tt]");
  if (!t) { radarTip.classList.remove("is-on"); return; }
  radarTip.innerHTML = t.dataset.tt;
  radarTip.classList.add("is-on");
  radarTip.style.left = e.clientX + "px";
  radarTip.style.top = e.clientY + "px";
});
radarChart.addEventListener("pointerleave", () => radarTip.classList.remove("is-on"));
ScrollTrigger.create({ trigger: "#radar", start: "top 70%", once: true, onEnter: () => renderRadar(true) });
renderRadar(false);

/* ═══════════════════════════════════════════════
   TOOL 2 — xPTS TABLE
   ═══════════════════════════════════════════════ */
const tState = { key: "total", asc: false };
const HEADS = [
  ["player", "Player"], ["price", "£"],
  ...GWS.map((gw, i) => ["gw" + i, "GW" + gw]),
  ["total", "Total"], ["mean", "Avg"],
];
function tVal(p, key) {
  if (key === "player") return p.name;
  if (key === "price") return p.price;
  if (key === "total") return p.total;
  if (key === "mean") return p.mean;
  return p.xpts[+key.slice(2)];
}
function renderTable(animate = false) {
  $("#xtableHead").innerHTML = "<tr>" + HEADS.map(([k, l]) =>
    `<th data-key="${k}" class="${tState.key === k ? "is-sorted" + (tState.asc ? " asc" : "") : ""}" data-cursor="sort">${l}</th>`
  ).join("") + "</tr>";

  const rows = [...PLAYERS].sort((a, b) => {
    const va = tVal(a, tState.key), vb = tVal(b, tState.key);
    const cmp = typeof va === "string" ? va.localeCompare(vb) : va - vb;
    return tState.asc ? cmp : -cmp;
  }).slice(0, 15);

  $("#xtableBody").innerHTML = rows.map((p) => {
    const best = Math.max(...p.xpts);
    return `<tr>
      <td><span class="pcell"><i class="pcell__dot" style="background:${TEAMS[p.team].color}"></i><span><b>${p.name}</b><span>${p.team} · ${p.pos}</span></span></span></td>
      <td>£${p.price.toFixed(1)}</td>
      ${p.xpts.map((v) => `<td class="${v === best ? "hi" : ""}">${v.toFixed(1)}</td>`).join("")}
      <td class="tot">${p.total.toFixed(1)}</td>
      <td>${p.mean.toFixed(1)}</td>
    </tr>`;
  }).join("");

  if (animate && !reduced) {
    gsap.from("#xtableBody tr", { opacity: 0, y: 16, stagger: 0.035, duration: 0.5, ease: "power2.out", clearProps: "all" });
  }
}
$("#xtableHead").addEventListener("click", (e) => {
  const th = e.target.closest("th"); if (!th) return;
  const k = th.dataset.key;
  if (tState.key === k) tState.asc = !tState.asc;
  else { tState.key = k; tState.asc = k === "player"; }
  renderTable(true);
});
ScrollTrigger.create({ trigger: "#projections", start: "top 70%", once: true, onEnter: () => renderTable(true) });
renderTable(false);

/* ═══════════════════════════════════════════════
   TOOL 3 — POINTS RANGE
   ═══════════════════════════════════════════════ */
const picks = [...PLAYERS].sort((a, b) => b.total - a.total).slice(0, 9);
let rangeSel = picks[0];
$("#rangePicker").innerHTML = picks.map((p, i) =>
  `<button class="chip ${i === 0 ? "is-active" : ""}" data-pid="${p.id}">${p.name.split(" ").pop()}</button>`
).join("");
$("#rangeBars").innerHTML = BRACKET_LABELS.map((l) =>
  `<div class="rcol"><span class="rcol__pct">0%</span><div class="rcol__bar" style="height:2%"></div><span class="rcol__lab">${l}</span></div>`
).join("");

const CIRC = 2 * Math.PI * 52;
function renderRange(animate = true) {
  const p = rangeSel;
  const maxB = Math.max(...p.brackets);
  $$(".rcol", $("#rangeBars")).forEach((col, i) => {
    const v = p.brackets[i];
    const h = Math.max(2, (v / maxB) * 100);
    const bar = $(".rcol__bar", col), pct = $(".rcol__pct", col);
    if (animate && !reduced) {
      gsap.to(bar, { height: h + "%", duration: 0.9, ease: "elastic.out(1,0.75)", delay: i * 0.05 });
      const o = { v: +pct.textContent.replace("%", "") || 0 };
      gsap.to(o, { v, duration: 0.7, onUpdate: () => (pct.textContent = o.v.toFixed(1) + "%") });
    } else { bar.style.height = h + "%"; pct.textContent = v.toFixed(1) + "%"; }
  });

  const set = (id, v) => ($(id).textContent = v);
  set("#mFloor", p.floor); set("#mMed", p.median); set("#mCeil", p.ceil);

  const dial = (circle, txtId, val) => {
    const off = CIRC * (1 - val / 100);
    if (animate && !reduced) {
      gsap.to(circle, { strokeDashoffset: off, duration: 1.1, ease: "power3.out" });
      const o = { v: 0 };
      gsap.to(o, { v: val, duration: 1.1, onUpdate: () => ($(txtId).innerHTML = o.v.toFixed(0) + "<i>%</i>") });
    } else { circle.style.strokeDashoffset = off; $(txtId).innerHTML = val.toFixed(0) + "<i>%</i>"; }
  };
  dial($("#dialHaul"), "#haulPct", p.haul);
  dial($("#dialBlank"), "#blankPct", p.blank);

  const mo = { v: +$("#meanPts").textContent || 0 };
  if (animate && !reduced) gsap.to(mo, { v: p.mean, duration: 0.8, onUpdate: () => ($("#meanPts").textContent = mo.v.toFixed(1)) });
  else $("#meanPts").textContent = p.mean.toFixed(1);
}
$("#rangePicker").addEventListener("click", (e) => {
  const b = e.target.closest(".chip"); if (!b) return;
  $$(".chip", $("#rangePicker")).forEach((c) => c.classList.remove("is-active"));
  b.classList.add("is-active");
  rangeSel = PLAYERS.find((p) => p.id === +b.dataset.pid);
  renderRange(true);
});
ScrollTrigger.create({ trigger: "#range", start: "top 65%", once: true, onEnter: () => renderRange(true) });

/* ═══════════════════════════════════════════════
   TOOL 4 — FIXTURE TICKER
   ═══════════════════════════════════════════════ */
let tickSort = "az", tickFocus = null;
const grid = $("#tickerGrid");
function teamRun(code) { return FIXTURES[code].reduce((s, f) => s + f.fdr, 0); }

function renderTicker(animate = false) {
  let codes = Object.keys(TEAMS);
  if (tickSort === "az") codes.sort((a, b) => TEAMS[a].name.localeCompare(TEAMS[b].name));
  if (tickSort === "easy") codes.sort((a, b) => teamRun(a) - teamRun(b));
  if (tickSort === "hard") codes.sort((a, b) => teamRun(b) - teamRun(a));

  grid.innerHTML =
    `<div class="trow trow--head"><span>Club</span>${GWS.map((g) => `<span>GW${g}</span>`).join("")}<span>Σ FDR</span></div>` +
    codes.map((c) => `<div class="trow ${tickFocus === c ? "is-focus" : ""}" data-team="${c}" data-cursor="focus">
      <span class="trow__team"><i class="trow__badge" style="background:${TEAMS[c].color}"></i><b>${c}</b></span>
      ${FIXTURES[c].map((f) => `<span class="tcell tcell--${f.fdr}"><b>${f.opp}</b><span>${f.home ? "HOME" : "AWAY"}</span></span>`).join("")}
      <span class="trow__sum">${teamRun(c)}</span>
    </div>`).join("");
  grid.classList.toggle("has-focus", !!tickFocus);

  if (animate && !reduced) {
    gsap.from(".trow:not(.trow--head)", { opacity: 0, y: 12, stagger: 0.03, duration: 0.4, ease: "power2.out", clearProps: "all" });
  }
}
chipGroup("#tickerSort", "tsort", (v) => { tickSort = v; renderTicker(true); });
grid.addEventListener("click", (e) => {
  const row = e.target.closest(".trow[data-team]"); if (!row) return;
  tickFocus = tickFocus === row.dataset.team ? null : row.dataset.team;
  renderTicker();
});
ScrollTrigger.create({ trigger: "#ticker", start: "top 70%", once: true, onEnter: () => renderTicker(true) });
renderTicker(false);

/* ═══════════════════════════════════════════════
   NEWSLETTER + FOOTER
   ═══════════════════════════════════════════════ */
$("#newsForm").addEventListener("submit", (e) => {
  e.preventDefault();
  const input = $("#newsEmail");
  if (!input.value || !input.checkValidity()) {
    gsap.fromTo(".news__field", { x: -8 }, { x: 0, duration: 0.5, ease: "elastic.out(1,0.3)" });
    input.focus();
    return;
  }
  $("#newsOk").textContent = "You're in! First treat lands before the next deadline. 🍬";
  input.value = "";
  if (!reduced) {
    const r = $(".news__field").getBoundingClientRect();
    const colors = ["#FF5F1F", "#00FF87", "#38BDF8", "#F2F5F7"];
    for (let i = 0; i < 34; i++) {
      const c = document.createElement("i");
      c.className = "confetti";
      c.style.background = colors[i % colors.length];
      c.style.left = r.left + r.width / 2 + "px";
      c.style.top = r.top + "px";
      document.body.appendChild(c);
      gsap.to(c, {
        x: gsap.utils.random(-260, 260), y: gsap.utils.random(-320, 60),
        rotation: gsap.utils.random(-540, 540), opacity: 0,
        duration: gsap.utils.random(0.9, 1.7), ease: "power2.out",
        onComplete: () => c.remove(),
      });
    }
  }
});

$("#year").textContent = new Date().getFullYear();
$("#toTop").addEventListener("click", () => scrollToEl("#hero"));

/* reduced-motion safety: show everything */
if (reduced) gsap.set("[data-reveal], .hero__line", { opacity: 1, y: 0, clearProps: "transform" });
