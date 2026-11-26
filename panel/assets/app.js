// Radarban panel feed: loads news, polls every 20 s for new items, client-side filters.
(function () {
  "use strict";
  const feed = document.getElementById("feed");
  const olderBtn = document.getElementById("older");
  const live = document.getElementById("live");
  const q = document.getElementById("q"), lineSel = document.getElementById("line"),
        sigSel = document.getElementById("sig"), impSel = document.getElementById("imp"),
        sound = document.getElementById("sound");
  const FA = (s) => String(s).replace(/\d/g, (d) => "۰۱۲۳۴۵۶۷۸۹"[d]);
  const DIR = { up: "صعودی", down: "نزولی", neutral: "خنثی", none: "بدون سیگنال" };
  let kind = "card", items = [], maxId = 0, minId = 0, lines = new Set(), timer = null;

  try { sound.checked = localStorage.getItem("rb_sound") === "1"; } catch (e) {}
  sound.addEventListener("change", () => { try { localStorage.setItem("rb_sound", sound.checked ? "1" : "0"); } catch (e) {} });

  function ago(ts) {
    const m = Math.max(0, Math.round((Date.now() / 1000 - ts) / 60));
    if (m < 1) return "همین الان";
    if (m < 60) return FA(m) + " دقیقه پیش";
    const h = Math.floor(m / 60);
    if (h < 24) return FA(h) + " ساعت پیش";
    return FA(Math.floor(h / 24)) + " روز پیش";
  }

  function beep() {
    if (!sound.checked) return;
    try {
      const c = new (window.AudioContext || window.webkitAudioContext)(), o = c.createOscillator(), g = c.createGain();
      o.connect(g); g.connect(c.destination); o.frequency.value = 880; g.gain.value = 0.05;
      o.start(); o.stop(c.currentTime + 0.15);
    } catch (e) {}
  }

  function el(it) {
    const d = document.createElement("article");
    d.className = "card" + (it.reply_to_event ? " update" : "") + (it.kind === "review" ? " review" : "");
    if (it.event_id) d.id = it.event_id;
    const imp = it.importance ?? 0;
    const band = imp > 80 ? "b5" : imp > 60 ? "b4" : imp > 40 ? "b3" : imp > 20 ? "b2" : "b1";
    let head = '<div class="meta">';
    if (it.code) head += '<span class="code">کد ' + FA(it.code) + "</span>";
    if (it.line) head += '<span class="line">' + escapeHtml(it.line) + "</span>";
    if (it.kind === "card") head += '<span class="imp ' + band + '">اهمیت ' + FA(imp) + "</span>";
    if (it.sig_dir && it.sig_dir !== "none") head += '<span class="sig ' + it.sig_dir + '">' + escapeHtml(it.sig_label || "") + " " + DIR[it.sig_dir] + "</span>";
    if (it.reply_to_event) head += '<a class="parent" href="#' + encodeURIComponent(it.reply_to_event) + '">به‌روزرسانی خبر قبلی ↩</a>';
    head += '<span class="ago" data-ts="' + it.created_ts + '">' + ago(it.created_ts) + "</span></div>";
    d.innerHTML = head + '<div class="body">' + it.html + "</div>"; // html is sanitized server-side
    d.dataset.search = (String(it.code || "") + " " + (it.title || "") + " " + (it.sig_label || "") + " " + d.textContent).toLowerCase();
    d.dataset.line = it.line || "";
    d.dataset.dir = it.sig_dir || "";
    d.dataset.imp = imp;
    return d;
  }

  function escapeHtml(s) { return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]); }

  function visible(d) {
    const s = q.value.trim().toLowerCase().replace(/[۰-۹]/g, (x) => "۰۱۲۳۴۵۶۷۸۹".indexOf(x));
    if (s && !d.dataset.search.includes(s)) return false;
    if (lineSel.value && d.dataset.line !== lineSel.value) return false;
    const sg = sigSel.value;
    if (sg === "dir" && !["up", "down"].includes(d.dataset.dir)) return false;
    if ((sg === "up" || sg === "down") && d.dataset.dir !== sg) return false;
    if (+d.dataset.imp < +impSel.value) return false;
    return true;
  }

  const linkbar = document.getElementById("linkbar");
  function showLink(l) {
    live.classList.toggle("off", l.state !== "ok");
    if (l.state === "ok") { linkbar.hidden = true; return; }
    const last = l.last_item_ts ? ago(l.last_item_ts) : "—";
    linkbar.className = "linkbar " + l.state;
    linkbar.textContent = l.state === "down"
      ? "⚠️ ارتباط با سرور خبر فعلاً قطع است (احتمالاً اختلال اینترنت بین‌الملل). آخرین خبر: " + last +
        ". خبرها در صف می‌مانند و بعد از وصل شدن، به ترتیب و خودکار اینجا می‌آیند."
      : "⏳ دریافت خبر با تأخیر انجام می‌شود. آخرین خبر: " + last;
    linkbar.hidden = false;
  }

  function applyFilters() { feed.querySelectorAll(".card").forEach((d) => { d.hidden = !visible(d); }); }
  [q, lineSel, sigSel, impSel].forEach((x) => x.addEventListener("input", applyFilters));

  function addLines(list) {
    list.forEach((it) => { if (it.line && !lines.has(it.line)) { lines.add(it.line); const o = document.createElement("option"); o.value = o.textContent = it.line; lineSel.appendChild(o); } });
  }

  async function get(params) {
    const r = await fetch("api/feed.php?" + new URLSearchParams({ kind, ...params }), { credentials: "same-origin", cache: "no-store" });
    if (r.status === 401) { location.href = "login.php"; return null; }
    if (r.status === 403) { location.reload(); return null; }
    const j = await r.json();
    if (j.link) showLink(j.link);
    return j.ok ? j.items : null;
  }

  async function load() {
    feed.innerHTML = '<p class="muted center">در حال بارگذاری…</p>';
    const list = await get({ limit: 40 });
    feed.innerHTML = "";
    if (!list) return;
    if (!list.length) feed.innerHTML = '<p class="muted center">هنوز خبری نیامده است.</p>';
    list.forEach((it) => feed.appendChild(el(it)));
    addLines(list);
    maxId = list.length ? list[0].id : 0;
    minId = list.length ? list[list.length - 1].id : 0;
    olderBtn.hidden = list.length < 40;
    applyFilters();
  }

  async function poll() {
    try {
      const list = await get({ after: maxId, limit: 100 });
      if (!list) live.classList.add("off");
      if (list && list.length) {
        feed.querySelector(".center")?.remove();
        list.forEach((it) => { const d = el(it); d.classList.add("new"); feed.prepend(d); });
        maxId = list[list.length - 1].id;
        addLines(list);
        applyFilters();
        beep();
        if (document.hidden) document.title = "(" + FA(list.length) + ") " + document.title.replace(/^\(\S+\) /, "");
      }
    } catch (e) { live.classList.add("off"); }
    feed.querySelectorAll(".ago").forEach((a) => { a.textContent = ago(+a.dataset.ts); });
  }

  olderBtn.addEventListener("click", async () => {
    const list = await get({ before: minId, limit: 40 });
    if (!list) return;
    list.forEach((it) => feed.appendChild(el(it)));
    addLines(list);
    if (list.length) minId = list[list.length - 1].id;
    olderBtn.hidden = list.length < 40;
    applyFilters();
  });

  document.querySelectorAll(".tab").forEach((b) => b.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((x) => x.classList.toggle("on", x === b));
    kind = b.dataset.kind;
    document.getElementById("filters").hidden = kind !== "card";
    load();
  }));
  document.addEventListener("visibilitychange", () => { if (!document.hidden) document.title = document.title.replace(/^\(\S+\) /, ""); });

  load().then(() => { timer = setInterval(poll, 20000); });
})();
