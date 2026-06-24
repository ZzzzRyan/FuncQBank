/* FuncQBank — client logic: KaTeX typesetting, practice flow, admin preview */
(function () {
  "use strict";

  var CSRF = (document.querySelector('meta[name="csrf-token"]') || {}).content || "";

  function typeset(el) {
    if (!el || typeof renderMathInElement !== "function") return;
    try {
      renderMathInElement(el, {
        delimiters: [
          { left: "$$", right: "$$", display: true },
          { left: "$", right: "$", display: false },
          { left: "\\(", right: "\\)", display: false },
          { left: "\\[", right: "\\]", display: true },
        ],
        throwOnError: false,
        ignoredClasses: ["raw-dump"],
      });
    } catch (e) { /* noop */ }
  }

  function post(url, body) {
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": CSRF },
      body: JSON.stringify(body),
    }).then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; });
  }

  var THEME_ICONS = {
    light: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="4.2"/><g stroke-linecap="round"><path d="M12 2.5v3"/><path d="M12 18.5v3"/><path d="M2.5 12h3"/><path d="M18.5 12h3"/><path d="M4.9 4.9l2.1 2.1"/><path d="M17 17l2.1 2.1"/><path d="M19.1 4.9L17 7"/><path d="M7 17l-2.1 2.1"/></g></svg>',
    dark: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 13.2A8 8 0 1 1 10.8 4a6.4 6.4 0 0 0 9.2 9.2z"/></svg>',
    system: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8.2" fill="none"/><path d="M12 3.8a8.2 8.2 0 0 0 0 16.4z" stroke="none"/></svg>',
  };
  var THEME_LABELS = { light: "浅色", dark: "深色", system: "跟随系统" };

  function preferredTheme() {
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }

  function applyTheme(mode) {
    var resolved = mode === "dark" || (mode === "system" && preferredTheme() === "dark") ? "dark" : "light";
    document.documentElement.dataset.themeMode = mode;
    document.documentElement.dataset.theme = resolved;
    var btn = document.getElementById("theme-toggle");
    if (btn) {
      btn.innerHTML = THEME_ICONS[mode] || THEME_ICONS.system;
      btn.title = "主题：" + (THEME_LABELS[mode] || "跟随系统") + "（点击切换）";
      btn.setAttribute("aria-label", "主题：" + (THEME_LABELS[mode] || "跟随系统"));
      btn.dataset.mode = mode;
    }
  }

  function initThemeToggle() {
    var btn = document.getElementById("theme-toggle");
    var mode = localStorage.getItem("theme-mode") || "system";
    applyTheme(mode);
    if (!btn) return;
    btn.addEventListener("click", function () {
      var current = localStorage.getItem("theme-mode") || "system";
      var next = current === "system" ? "light" : (current === "light" ? "dark" : "system");
      localStorage.setItem("theme-mode", next);
      applyTheme(next);
    });
    if (window.matchMedia) {
      var mq = window.matchMedia("(prefers-color-scheme: dark)");
      var sync = function () {
        var current = localStorage.getItem("theme-mode") || "system";
        if (current === "system") applyTheme(current);
      };
      if (mq.addEventListener) mq.addEventListener("change", sync);
      else if (mq.addListener) mq.addListener(sync);
    }
  }

  function initUserMenu() {
    var menu = document.getElementById("usermenu");
    var btn = document.getElementById("usermenu-btn");
    if (!menu || !btn) return;
    function close() { menu.classList.remove("open"); btn.setAttribute("aria-expanded", "false"); }
    function toggle() {
      var open = menu.classList.toggle("open");
      btn.setAttribute("aria-expanded", open ? "true" : "false");
    }
    btn.addEventListener("click", function (e) { e.stopPropagation(); toggle(); });
    document.addEventListener("click", function (e) { if (!menu.contains(e.target)) close(); });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape") close(); });
  }

  function setEq(a, b) {
    if (a.size !== b.size) return false;
    var ok = true;
    a.forEach(function (x) { if (!b.has(x)) ok = false; });
    return ok;
  }

  // ---------------- Practice ----------------
  function initPractice() {
    var dataEl = document.getElementById("qdata");
    if (!dataEl) return;
    var questions = JSON.parse(dataEl.textContent || "[]");
    if (!questions.length) return;
    var states = JSON.parse((document.getElementById("sdata") || {}).textContent || "{}");
    var pageData = JSON.parse((document.getElementById("pdata") || {}).textContent || "{}");
    var isWrongBook = !!pageData.is_wrong_book;
    var masterThreshold = pageData.master_threshold || 2;
    var nextScopeUrl = pageData.next_scope_url || "";
    var completionEnabled = !!pageData.completion_enabled;
    var initialStates = {};
    Object.keys(states || {}).forEach(function (qid) {
      var s = states[qid] || {};
      initialStates[qid] = {
        wrong: !!s.wrong,
        mastered: !!s.mastered,
        correct_count: s.correct_count || 0,
        seen: s.seen || 0,
      };
    });

    var sheet = document.getElementById("qsheet");
    var gridnav = document.getElementById("gridnav");
    var posEl = document.getElementById("pos");
    var answeredEl = document.getElementById("answered");
    var wrongEl = document.getElementById("wrongcount");
    var progbar = document.getElementById("progbar");
    var btnNext = document.getElementById("btn-next");
    var btnPrev = document.getElementById("btn-prev");
    var kbdHint = document.getElementById("kbd-hint");
    var summaryTemplate = document.getElementById("section-summary-template");

    var idx = 0;
    var summaryShown = false;
    var rt = questions.map(function () { return { sel: new Set(), revealed: false, result: null }; });

    function curRtState(i) {
      // colour for the answer-card grid — this run only (cumulative state lives on the home page)
      if (rt[i].result === "correct") return "correct";
      if (rt[i].result === "incorrect") return "wrong";
      if (rt[i].revealed) return "seen";
      return "";
    }

    function answeredCount() {
      var n = 0;
      for (var i = 0; i < questions.length; i++) { if (rt[i].revealed) n++; }
      return n;
    }

    function wrongCount() {
      var n = 0;
      for (var i = 0; i < questions.length; i++) { if (rt[i].result === "incorrect") n++; }
      return n;
    }

    function newWrongCount() {
      var n = 0;
      for (var i = 0; i < questions.length; i++) { if (rt[i].newWrong) n++; }
      return n;
    }

    function improvedCount() {
      var n = 0;
      for (var i = 0; i < questions.length; i++) { if (rt[i].masteryImproved) n++; }
      return n;
    }

    function summaryStats() {
      return {
        done: answeredCount(),
        total: questions.length,
        wrong: wrongCount(),
        newWrong: newWrongCount(),
        improved: improvedCount(),
      };
    }

    function updateNavText() {
      if (!btnNext || !btnPrev) return;
      if (summaryShown) {
        btnNext.textContent = nextScopeUrl ? "下一节 →" : "返回题库";
        btnPrev.textContent = "← 回看本节";
        if (kbdHint) kbdHint.textContent = "回车继续 · ← 回看最后一题";
        return;
      }
      btnPrev.textContent = "← 上一题";
      if (idx >= questions.length - 1 && completionEnabled) btnNext.textContent = "查看小结 →";
      else if (idx >= questions.length - 1 && nextScopeUrl) btnNext.textContent = "下一节 →";
      else btnNext.textContent = "下一题 →";
      if (kbdHint) kbdHint.textContent = "← → 切题 · 数字键选择 · 回车揭示";
    }

    function updateProgress() {
      if (summaryShown) posEl.textContent = "本节小结";
      else posEl.textContent = "第 " + (idx + 1) + " 题 / 共 " + questions.length + " 题";
      var a = answeredCount();
      answeredEl.textContent = "已答 " + a + " / " + questions.length;
      if (wrongEl) { var w = wrongCount(); wrongEl.textContent = "本次错 " + w; wrongEl.classList.toggle("has", w > 0); }
      progbar.style.width = Math.round((a / questions.length) * 100) + "%";
      updateNavText();
    }

    function buildGrid() {
      gridnav.innerHTML = "";
      questions.forEach(function (q, i) {
        var b = document.createElement("button");
        b.type = "button";
        b.textContent = (i + 1);
        var cls = curRtState(i);
        if (cls) b.classList.add(cls);
        if (!summaryShown && i === idx) b.classList.add("cur");
        b.addEventListener("click", function () { summaryShown = false; idx = i; render(); });
        gridnav.appendChild(b);
      });
    }

    function activeWrongOf(q) {
      var s = states[q.id];
      return s && s.wrong && !s.mastered;
    }

    function masteredOf(q) {
      var s = states[q.id];
      return s && s.mastered;
    }

    function correctStreakOf(q) {
      var s = states[q.id];
      return s ? (s.correct_count || 0) : 0;
    }

    function summaryTone(stats) {
      if (stats.done < stats.total) return "本节已完成 " + stats.done + " / " + stats.total + " 题，未完成的题可以回看补上。";
      if (stats.wrong === 0) return "这一节本次没有错题，保持这个节奏。";
      if (stats.newWrong > 0) return "有 " + stats.newWrong + " 道是这次新错，优先回看这些题。";
      return "本次错题都不是新错，按错题本继续复习即可。";
    }

    function renderSummary() {
      summaryShown = true;
      var stats = summaryStats();
      var titleEl = document.querySelector(".practice-head .title");
      var title = titleEl ? titleEl.textContent : "本节";
      var nextHref = nextScopeUrl || "/";
      var nextLabel = nextScopeUrl ? "下一节 →" : "返回题库";
      var summaryNode = summaryTemplate.content.firstElementChild.cloneNode(true);

      summaryNode.querySelector('[data-summary="title"]').textContent = title;
      summaryNode.querySelector('[data-summary="lead"]').textContent = summaryTone(stats);
      summaryNode.querySelector('[data-summary="done"]').textContent = stats.done;
      summaryNode.querySelector('[data-summary="total"]').textContent = "共 " + stats.total + " 题";
      summaryNode.querySelector('[data-summary="wrong"]').textContent = stats.wrong;
      summaryNode.querySelector('[data-summary="new-wrong"]').textContent = stats.newWrong;
      summaryNode.querySelector('[data-summary="improved"]').textContent = stats.improved;
      summaryNode.querySelector('[data-summary="bar"]').style.width = Math.round((stats.done / stats.total) * 100) + "%";
      var next = summaryNode.querySelector('[data-summary="next"]');
      next.href = nextHref;
      next.textContent = nextLabel;

      sheet.classList.remove("revealed");
      sheet.classList.add("summary-sheet");
      sheet.textContent = "";
      sheet.appendChild(summaryNode);
      var review = document.getElementById("summary-review");
      if (review) review.addEventListener("click", function () { summaryShown = false; idx = Math.max(0, questions.length - 1); render(); });
      updateProgress();
      buildGrid();
      window.scrollTo({ top: 0, behavior: "smooth" });
    }

    function render() {
      summaryShown = false;
      var q = questions[idx];
      var st = rt[idx];
      var isJudge = q.type === "judge";
      var typeLabel = { single: "单选题", multiple: "多选题", judge: "判断题" }[q.type] || q.type;

      var h = "";
      h += '<div class="qmeta">';
      h += '<span class="num">第 ' + (idx + 1) + ' 题</span>';
      h += '<span class="badge ' + q.type + '">' + typeLabel + "</span>";
      if (q.points) h += '<span class="points">' + q.points + " 分</span>";
      if (q.section_code) h += '<span class="points">· ' + q.section_code + " " + q.section_title + "</span>";
      if (isWrongBook) {
        h += st.pendingRemoval
          ? '<span class="master-progress done">自动移出错题本</span>'
          : '<span class="master-progress">连续答对 ' + correctStreakOf(q) + ' / ' + masterThreshold + '</span>';
        h += '<button class="btn btn-sm btn-mastered" id="mark-mastered" type="button">移出错题</button>';
      }
      h += "</div>";

      h += '<div class="stem">' + q.stem_html + "</div>";

      if (isJudge) {
        h += '<div class="judge" id="judge">';
        ["正确", "错误"].forEach(function (v) {
          h += '<button type="button" data-v="' + v + '"><span class="ic">' + (v === "正确" ? "✓" : "✗") + "</span>" + v + "</button>";
        });
        h += "</div>";
      } else {
        h += '<div class="options ' + q.type + '" id="options">';
        q.options.forEach(function (o) {
          h += '<div class="option" data-label="' + o.label + '"><span class="lab">' + o.label + '</span><span class="otext">' + o.text_html + '</span><span class="mark"></span></div>';
        });
        h += "</div>";
      }

      h += '<div class="actions" id="actions"></div>';
      h += '<div id="reveal-area"></div>';
      sheet.classList.remove("revealed");
      sheet.classList.remove("summary-sheet");
      sheet.innerHTML = h;

      // restore selection visuals
      if (!isJudge) {
        sheet.querySelectorAll(".option").forEach(function (el) {
          if (st.sel.has(el.dataset.label)) {
            el.classList.add("selected");
            if (q.type === "multiple") el.querySelector(".lab").textContent = "✓";
          }
          el.addEventListener("click", function () { if (!st.revealed) toggleOption(el); });
        });
      } else {
        sheet.querySelectorAll("#judge button").forEach(function (el) {
          if (st.sel.has(el.dataset.v)) el.classList.add("selected");
          el.addEventListener("click", function () { if (!st.revealed) { st.sel = new Set([el.dataset.v]); grade(); } });
        });
      }

      var masterBtn = document.getElementById("mark-mastered");
      if (masterBtn) masterBtn.addEventListener("click", markMastered);
      renderActions();
      if (st.revealed) applyReveal();
      typeset(sheet);
      updateProgress();
      buildGrid();
    }

    function setMultipleSelected(el, selected) {
      el.classList.toggle("selected", selected);
      var labEl = el.querySelector(".lab");
      if (labEl) labEl.textContent = selected ? "✓" : el.dataset.label;
    }

    function toggleOption(el) {
      var q = questions[idx], st = rt[idx], lab = el.dataset.label;
      if (q.type === "single") {
        st.sel = new Set([lab]);
        sheet.querySelectorAll(".option").forEach(function (o) { o.classList.toggle("selected", o.dataset.label === lab); });
      } else {
        if (st.sel.has(lab)) { st.sel.delete(lab); setMultipleSelected(el, false); }
        else { st.sel.add(lab); setMultipleSelected(el, true); }
      }
      renderActions();
    }

    function renderActions() {
      var st = rt[idx], q = questions[idx], a = document.getElementById("actions");
      if (!a) return;
      if (st.revealed) {
        var isLast = idx >= questions.length - 1;
        var label = isLast && completionEnabled ? "查看小结 →" : (isLast && nextScopeUrl ? "下一节 →" : "下一题 →");
        a.innerHTML = '<button class="btn btn-primary" id="act-next" type="button">' + label + '</button>';
        var n = document.getElementById("act-next"); if (n) n.addEventListener("click", next);
        return;
      }
      if (q.type === "judge") { a.innerHTML = '<button class="btn btn-ghost btn-sm" id="act-reveal" type="button">直接看答案</button>'; }
      else {
        var dis = st.sel.size === 0 ? "disabled" : "";
        a.innerHTML = '<button class="btn btn-primary" id="act-submit" ' + dis + ' type="button">提交答案</button>' +
                      '<button class="btn btn-ghost btn-sm" id="act-reveal" type="button">直接看答案</button>';
        var s = document.getElementById("act-submit"); if (s) s.addEventListener("click", grade);
      }
      var r = document.getElementById("act-reveal"); if (r) r.addEventListener("click", revealOnly);
    }

    function answerSet(q) { return new Set(q.answer || []); }

    function grade() {
      var q = questions[idx], st = rt[idx];
      if (st.sel.size === 0) return;
      var correct = setEq(st.sel, answerSet(q));
      st.result = correct ? "correct" : "incorrect";
      st.revealed = true;
      var before = initialStates[q.id] || {};
      st.newWrong = st.result === "incorrect" && !(before.wrong && !before.mastered);
      st.masteryImproved = st.result === "correct" && before.wrong && !before.mastered;
      post("/api/attempt", { question_id: q.id, result: st.result }).then(function (res) {
        if (res) {
          var beforeCount = before.correct_count || 0;
          states[q.id] = states[q.id] || {};
          states[q.id].wrong = res.wrong;
          states[q.id].mastered = res.mastered;
          states[q.id].correct_count = res.correct_count || 0;
          states[q.id].last_result = st.result;
          states[q.id].seen = 1;
          st.masteryImproved = st.result === "correct" &&
            ((states[q.id].correct_count || 0) > beforeCount || (!before.mastered && res.mastered));
          if (isWrongBook && st.result === "correct") {
            if (!activeWrongOf(q)) st.pendingRemoval = true;
            render();
          } else if (summaryShown) {
            renderSummary();
          } else {
            buildGrid();
          }
        }
      });
      applyReveal();
      renderActions();
      updateProgress();
    }

    function revealOnly() {
      var q = questions[idx], st = rt[idx];
      st.revealed = true; st.result = null;
      post("/api/attempt", { question_id: q.id, result: "revealed" }).then(function () {
        states[q.id] = states[q.id] || {}; states[q.id].seen = 1; buildGrid();
      });
      applyReveal();
      renderActions();
      updateProgress();
    }

    function applyReveal() {
      var q = questions[idx], st = rt[idx];
      var ans = answerSet(q);
      sheet.classList.add("revealed");
      if (q.type === "judge") {
        sheet.querySelectorAll("#judge button").forEach(function (el) {
          var v = el.dataset.v;
          if (ans.has(v)) el.classList.add("correct");
          else if (st.sel.has(v)) el.classList.add("wrong");
        });
        var jd = document.getElementById("judge"); if (jd) jd.style.pointerEvents = "none";
      } else {
        sheet.querySelectorAll(".option").forEach(function (el) {
          var lab = el.dataset.label;
          el.classList.remove("selected");
          var mk = el.querySelector(".mark");
          if (ans.has(lab)) { el.classList.add("correct"); if (mk) mk.textContent = "✓"; }
          else if (st.sel.has(lab)) { el.classList.add("wrong"); if (mk) mk.textContent = "✗"; }
          if (q.type === "multiple") {
            var labEl = el.querySelector(".lab");
            if (labEl) labEl.textContent = lab;
          }
        });
        var op = document.getElementById("options"); if (op) op.style.pointerEvents = "none";
      }
      // reveal box
      var rv = document.getElementById("reveal-area");
      var html = '<div class="reveal">';
      if (st.result === "correct") html += '<span class="verdict ok">✓ 回答正确</span>';
      else if (st.result === "incorrect") html += '<span class="verdict no">✗ 回答错误</span>';
      html += '<div class="answer-line">正确答案：<b>' + (q.answer_raw || (q.answer || []).join("")) + "</b></div>";
      if (q.explanation_html) html += '<div class="explanation"><div class="h">解析</div>' + q.explanation_html + "</div>";
      html += "</div>";
      rv.innerHTML = html;
      typeset(rv);
    }

    function markMastered() {
      var q = questions[idx];
      states[q.id] = states[q.id] || {};
      states[q.id].mastered = true;
      states[q.id].wrong = false;
      states[q.id].correct_count = masterThreshold;
      post("/api/state", { question_id: q.id, mastered: true }).then(function () {
        if (isWrongBook) removeCurrentWrong();
      });
    }

    function removeCurrentWrong() {
      questions.splice(idx, 1);
      rt.splice(idx, 1);
      if (idx >= questions.length) idx = Math.max(0, questions.length - 1);
      if (!questions.length) {
        window.location.reload();
        return;
      }
      render();
    }

    function next() {
      if (summaryShown) { window.location.href = nextScopeUrl || "/"; return; }
      if (isWrongBook && rt[idx] && rt[idx].pendingRemoval) { removeCurrentWrong(); return; }
      if (idx < questions.length - 1) { idx++; render(); }
      else if (completionEnabled) { renderSummary(); }
      else if (nextScopeUrl) { window.location.href = nextScopeUrl; }
    }

    function prev() {
      if (summaryShown) { summaryShown = false; idx = Math.max(0, questions.length - 1); render(); return; }
      if (isWrongBook && rt[idx] && rt[idx].pendingRemoval) { removeCurrentWrong(); return; }
      if (idx > 0) { idx--; render(); }
    }

    btnNext.addEventListener("click", next);
    btnPrev.addEventListener("click", prev);
    document.getElementById("btn-grid").addEventListener("click", function () { gridnav.classList.toggle("hidden"); });

    document.addEventListener("keydown", function (e) {
      if (e.target && /INPUT|TEXTAREA|SELECT/.test(e.target.tagName)) return;
      if (summaryShown) {
        if (e.key === "Enter" || e.key === "ArrowRight") { next(); e.preventDefault(); }
        else if (e.key === "ArrowLeft") { prev(); e.preventDefault(); }
        return;
      }
      var q = questions[idx], st = rt[idx];
      if (e.key === "ArrowRight") { next(); e.preventDefault(); }
      else if (e.key === "ArrowLeft") { prev(); e.preventDefault(); }
      else if (e.key === "Enter") {
        if (st.revealed) next();
        else if (q.type !== "judge" && st.sel.size) grade();
        else revealOnly();
        e.preventDefault();
      } else if ((e.key === "m" || e.key === "M") && isWrongBook) { markMastered(); }
      else if (/^[1-9]$/.test(e.key) && !st.revealed) {
        if (q.type === "judge") { var v = e.key === "1" ? "正确" : (e.key === "2" ? "错误" : null); if (v) { st.sel = new Set([v]); grade(); } }
        else { var i = parseInt(e.key, 10) - 1; if (i < q.options.length) { var el = sheet.querySelectorAll(".option")[i]; if (el) toggleOption(el); } }
      }
    });

    render();
  }

  // ---------------- Admin: live full-question preview + safe re-extract ----------------
  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#x27;");
  }

  function nonmath(s) {
    return escapeHtml(s).replace(/\*\*([\s\S]+?)\*\*/g, "<strong>$1</strong>").replace(/\n/g, "<br>");
  }

  function richToHtml(text) {
    if (!text) return "";
    var re = /\$\$[\s\S]+?\$\$|\$[\s\S]+?\$/g, out = "", last = 0, m;
    while ((m = re.exec(text))) { out += nonmath(text.slice(last, m.index)) + escapeHtml(m[0]); last = re.lastIndex; }
    return out + nonmath(text.slice(last));
  }

  function initAdminPreview() {
    var form = document.getElementById("edit-form");
    if (!form) return;
    var preview = document.getElementById("q-preview");
    var optsEl = document.getElementById("opts");
    var typeSel = document.getElementById("type");
    var optsField = document.getElementById("opts-field");

    function val(id) { var e = document.getElementById(id); return e ? e.value : ""; }
    function setVal(id, v) { var e = document.getElementById(id); if (e) e.value = (v == null ? "" : v); }

    function readOpts() {
      var arr = [];
      optsEl.querySelectorAll(".opt-row").forEach(function (r) {
        var l = r.querySelector(".lab").value.trim();
        var t = r.querySelector('input[name="opt_text"]').value;
        if (l || t) arr.push({ label: l, text: t });
      });
      return arr;
    }

    function addOptRow(label, text) {
      var row = document.createElement("div");
      row.className = "opt-row";
      row.innerHTML = '<input class="lab" type="text" name="opt_label"><input type="text" name="opt_text"><button class="btn btn-sm btn-danger opt-del" type="button">×</button>';
      row.querySelector(".lab").value = label || "";
      row.querySelector('input[name="opt_text"]').value = text || "";
      optsEl.appendChild(row);
    }

    function toggleOpts() { if (optsField) optsField.style.display = (typeSel.value === "judge") ? "none" : ""; }

    function buildPreview() {
      var type = val("type"), stem = val("stem"), answer = val("answer").trim();
      var ansraw = val("answer_raw").trim(), points = val("points").trim(), expl = val("explanation");
      var typeLabel = { single: "单选题", multiple: "多选题", judge: "判断题" }[type] || type;
      var h = '<div class="qmeta"><span class="badge ' + type + '">' + typeLabel + "</span>" + (points ? '<span class="points">' + escapeHtml(points) + " 分</span>" : "") + "</div>";
      h += '<div class="stem">' + (richToHtml(stem) || '<span class="muted">（题干为空）</span>') + "</div>";
      if (type === "judge") {
        h += '<div class="judge">';
        ["正确", "错误"].forEach(function (v) {
          h += '<button type="button" class="' + (answer === v ? "correct" : "") + '" data-v="' + v + '"><span class="ic">' + (v === "正确" ? "✓" : "✗") + "</span>" + v + "</button>";
        });
        h += "</div>";
      } else {
        var ansset = answer.toUpperCase().split("");
        h += '<div class="options ' + type + ' revealed">';
        readOpts().forEach(function (o) {
          var corr = ansset.indexOf((o.label || "").toUpperCase()) >= 0;
          h += '<div class="option' + (corr ? " correct" : "") + '"><span class="lab">' + escapeHtml(o.label) + '</span><span class="otext">' + richToHtml(o.text) + '</span><span class="mark">' + (corr ? "✓" : "") + "</span></div>";
        });
        h += "</div>";
      }
      h += '<div class="reveal"><div class="answer-line">正确答案：<b>' + escapeHtml(ansraw || answer || "—") + "</b></div>";
      if (expl.trim()) h += '<div class="explanation"><div class="h">解析</div>' + richToHtml(expl) + "</div>";
      h += "</div>";
      preview.innerHTML = h;
      typeset(preview);
    }

    form.addEventListener("input", buildPreview);
    form.addEventListener("change", buildPreview);
    optsEl.addEventListener("click", function (e) {
      if (e.target && e.target.classList.contains("opt-del")) { e.target.closest(".opt-row").remove(); buildPreview(); }
    });
    var addBtn = document.getElementById("add-opt");
    if (addBtn) addBtn.addEventListener("click", function () { addOptRow("", ""); buildPreview(); });
    if (typeSel) typeSel.addEventListener("change", toggleOpts);

    var reBtn = document.getElementById("reextract-btn");
    var notice = document.getElementById("reextract-notice");
    if (reBtn) reBtn.addEventListener("click", function () {
      var orig = reBtn.textContent;
      reBtn.disabled = true; reBtn.textContent = "识别中…";
      notice.innerHTML = '<div class="alert info">正在用大模型重新识别，请稍候…（不会自动保存）</div>';
      post(reBtn.dataset.url, {}).then(function (res) {
        reBtn.disabled = false; reBtn.textContent = orig;
        if (res && res.ok) {
          var f = res.fields;
          setVal("type", f.type); setVal("stem", f.stem); setVal("answer", f.answer);
          setVal("answer_raw", f.answer_raw); setVal("points", f.points); setVal("explanation", f.explanation);
          optsEl.innerHTML = "";
          (f.options || []).forEach(function (o) { addOptRow(o.label, o.text); });
          toggleOpts(); buildPreview();
          var flags = (f.auto_flags && f.auto_flags.length) ? " 自动检查：" + f.auto_flags.join("；") : "";
          notice.innerHTML = '<div class="alert info">已载入新的识别结果，请<b>检查无误后点「保存」</b>（当前尚未保存，未保存即离开则丢弃）。' + flags + "</div>";
        } else {
          notice.innerHTML = '<div class="alert err">' + ((res && res.error) || "识别失败，未改动任何数据，可重试。") + "</div>";
        }
      });
    });

    toggleOpts();
    buildPreview();
  }

  document.addEventListener("DOMContentLoaded", function () {
    initThemeToggle();
    initUserMenu();
    if (document.getElementById("qdata")) initPractice();
    else typeset(document.body);
    initAdminPreview();
  });
})();
