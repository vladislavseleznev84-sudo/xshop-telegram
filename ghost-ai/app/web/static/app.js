(() => {
  "use strict";
  const view = document.getElementById("view");
  const pill = document.getElementById("pill");
  const toastEl = document.getElementById("toast");
  let tab = "overview";

  // DOM helper. Text always goes through textContent -> user-controlled names can never inject markup.
  const h = (tag, props = {}, ...kids) => {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(props)) {
      if (k === "class") el.className = v;
      else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else if (v !== false && v != null) el.setAttribute(k, v === true ? "" : v);
    }
    for (const kid of kids.flat()) if (kid != null) el.append(kid.nodeType ? kid : document.createTextNode(kid));
    return el;
  };
  const svg = (d) => { const s = document.createElementNS("http://www.w3.org/2000/svg", "svg"); s.setAttribute("viewBox", "0 0 24 24"); const p = document.createElementNS("http://www.w3.org/2000/svg", "path"); p.setAttribute("d", d); s.append(p); return s; };

  let toastTimer;
  const toast = (msg) => { toastEl.textContent = msg; toastEl.classList.add("show"); clearTimeout(toastTimer); toastTimer = setTimeout(() => toastEl.classList.remove("show"), 2200); };

  async function api(path, method = "GET", body) {
    const res = await fetch(path, {
      method, credentials: "same-origin",
      headers: { "X-Requested-With": "ghost", ...(body ? { "Content-Type": "application/json" } : {}) },
      body: body ? JSON.stringify(body) : undefined,
    });
    if (res.status === 401) { showLogin(); throw new Error("401"); }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw Object.assign(new Error("http"), { data });
    return data;
  }

  function showLogin() {
    pill.textContent = "нет доступа"; pill.className = "pill";
    view.replaceChildren(h("div", { class: "card" },
      h("p", { class: "h" }, "Вход"),
      h("p", {}, "Откройте панель по одноразовой ссылке: отправьте боту команду /panel в Telegram и перейдите по ссылке.")));
  }

  const KIND = {
    incoming: "Входящее", reply_sent: "Ответ отправлен", draft_sent: "Черновик вам", notice_sent: "Запрос согласия",
    consent_granted: "Согласие дано", consent_declined: "Отказ", consent_revoked: "Согласие отозвано",
    memory_forgotten: "Память удалена", needs_owner: "Нужен вы", ai_error: "Ошибка Claude", send_failed: "Не удалось отправить",
    skipped_paused: "Пропуск: пауза", skipped_not_allowed: "Пропуск: чат не разрешён", skipped_no_rights: "Пропуск: нет прав",
    takeover_skip: "Вы ответили сами", rate_limited: "Лимит ответов", repeat_blocked: "Повтор заблокирован",
    flood_dropped: "Флуд отброшен", chat_enabled: "Чат разрешён", chat_disabled: "Чат выключен",
  };
  const CONSENT = { none: "не спрашивали", pending: "ждём ответа", granted: "согласен", declined: "отказал", revoked: "отозвал" };
  const fmtTime = (iso) => new Date(iso).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });

  async function renderOverview() {
    const o = await api("/api/overview");
    pill.textContent = o.paused ? "пауза" : (o.mode === "live" ? "live" : "shadow");
    pill.className = "pill " + (o.paused ? "off" : "on");

    const power = h("button", { class: "power" + (o.paused ? "" : " on"), "aria-label": "Включить или выключить автоответы",
      onclick: async () => { await api(o.paused ? "/api/resume" : "/api/pause", "POST"); toast(o.paused ? "Автоответы включены" : "Остановлено"); render(); } },
      svg("M12 3v8M6.3 6.6a8 8 0 1 0 11.4 0"));
    const warns = [];
    if (!o.claude_configured) warns.push("ANTHROPIC_API_KEY не задан — ответы невозможны.");
    if (!o.connections.length) warns.push("Нет Business-подключения: Telegram → Настройки → Business → Чат-боты.");
    if (o.connections.some((c) => !c.can_reply)) warns.push("У бота нет права отвечать на сообщения.");

    view.replaceChildren(
      ...warns.map((w) => h("div", { class: "warn-box" }, w)),
      h("div", { class: "card kill" },
        h("div", { class: "txt" }, h("p", { class: "h" }, "Автоответы"),
          h("div", { class: "big" }, o.paused ? "Остановлены" : "Работают"),
          h("div", { class: "muted small" }, o.mode === "live" ? "Режим live: ответы уходят собеседникам" : "Режим shadow: черновики приходят только вам")),
        power),
      h("div", { class: "card" }, h("p", { class: "h" }, "Последние 24 часа"),
        h("div", { class: "grid" },
          ...[["Входящих", o.day.incoming], ["Ответов", o.day.replies], ["Черновиков", o.day.drafts], ["Нужны вы", o.day.needs_owner]]
            .map(([l, v]) => h("div", { class: "stat" }, h("b", {}, String(v)), h("span", {}, l))))),
      h("div", { class: "card" }, h("p", { class: "h" }, "Чаты"),
        h("div", { class: "grid" },
          ...[["Всего", o.chats.total], ["Разрешено", o.chats.allowed], ["Согласились", o.chats.granted], ["Токены вх/исх", `${o.tokens.input}/${o.tokens.output}`]]
            .map(([l, v]) => h("div", { class: "stat" }, h("b", {}, String(v)), h("span", {}, l))))),
      h("div", { class: "card" }, h("p", { class: "h" }, "События (без текста переписок)"),
        o.recent.length ? o.recent.map((e) => h("div", { class: "ev" }, h("b", {}, KIND[e.kind] || e.kind), h("span", {}, (e.chat ? "#" + e.chat + " · " : "") + fmtTime(e.ts))))
          : h("div", { class: "empty" }, "Пока пусто")));
  }

  async function renderChats() {
    pill.className = pill.className; // keep
    const chats = await api("/api/chats");
    view.replaceChildren(h("div", { class: "card" },
      h("p", { class: "h" }, "Чаты"),
      h("p", { class: "muted small" }, "Ассистент отвечает только в чатах с включённым переключателем и только после согласия собеседника."),
      chats.length ? chats.map((c) => h("div", { class: "row" },
        h("div", { class: "main" }, h("div", { class: "name" }, c.name),
          h("span", { class: "badge " + c.consent }, "согласие: " + (CONSENT[c.consent] || c.consent))),
        h("button", { class: "btn ghost danger", onclick: async () => { if (confirm("Удалить память о «" + c.name + "»?")) { await api(`/api/chats/${c.id}/forget`, "POST"); toast("Память удалена"); } } }, "Забыть"),
        h("label", { class: "switch" },
          h("input", { type: "checkbox", checked: c.enabled, "aria-label": "Разрешить ответы",
            onchange: async (e) => { await api(`/api/chats/${c.id}/enabled`, "POST", { enabled: e.target.checked }); toast(e.target.checked ? "Чат разрешён" : "Чат выключен"); } }),
          h("i")))) : h("div", { class: "empty" }, "Чатов пока нет. Они появятся после первого входящего сообщения.")));
  }

  async function renderSettings() {
    const st = await api("/api/settings");
    let mode = st.mode;
    const num = (key, label, min, max) => h("div", { class: "field" }, h("label", { for: key }, label),
      h("input", { id: key, type: "number", inputmode: "numeric", min, max, value: st[key] }));
    const modeSeg = h("div", { class: "seg" });
    const drawSeg = () => modeSeg.replaceChildren(...["shadow", "live"].map((m) => h("button", { type: "button", class: m === mode ? "active" : "", onclick: () => { mode = m; drawSeg(); } }, m === "shadow" ? "Shadow (черновики вам)" : "Live (отправка)")));
    drawSeg();
    const style = h("textarea", { id: "style_prompt" }); style.value = st.style_prompt;
    view.replaceChildren(h("div", { class: "card" }, h("p", { class: "h" }, "Настройки"),
      h("div", { class: "field" }, h("label", {}, "Режим"), modeSeg,
        h("span", { class: "muted small" }, "Live отправляет ответы собеседникам от вашего имени. Включайте после проверки в shadow.")),
      h("div", { class: "field" }, h("label", { for: "style_prompt" }, "Стиль общения"), style),
      num("takeover_minutes", "Молчать после вашего ручного сообщения (мин)", 0, 1440),
      num("debounce_seconds", "Ждать, пока собеседник допишет (сек)", 0, 60),
      num("max_replies_per_hour", "Лимит ответов на чат в час", 1, 200),
      num("retention_days", "Хранить переписку (дней)", 1, 365),
      h("button", { class: "btn primary", onclick: async () => {
        const body = { mode, style_prompt: style.value };
        for (const k of ["takeover_minutes", "debounce_seconds", "max_replies_per_hour", "retention_days"]) body[k] = Number(document.getElementById(k).value);
        try { await api("/api/settings", "POST", body); toast("Сохранено"); } catch (e) { toast("Ошибка: " + JSON.stringify(e.data?.errors || {})); }
      } }, "Сохранить"),
      h("button", { class: "btn ghost", onclick: async () => { await api("/api/logout", "POST"); showLogin(); } }, "Выйти")));
  }

  const VIEWS = { overview: renderOverview, chats: renderChats, settings: renderSettings };
  async function render() { try { await VIEWS[tab](); } catch (e) { if (e.message !== "401") toast("Ошибка загрузки"); } }
  document.querySelectorAll(".tab").forEach((b) => b.addEventListener("click", () => {
    tab = b.dataset.tab; document.querySelectorAll(".tab").forEach((x) => x.classList.toggle("active", x === b)); render();
  }));
  setInterval(() => { if (tab === "overview" && !document.hidden) render(); }, 15000);
  render();
})();
