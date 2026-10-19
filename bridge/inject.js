/**
 * Pacific Front Helper — page inject (any browser).
 * Paste in DevTools console on the game origin, OR load via bookmarklet from
 * http://127.0.0.1:PORT/inject.js
 *
 * Connects to local helper WS, scrapes API dumps, places ships/mods on board.
 * Manual deploy never starts a fight and this script never calls
 * /api/v2/keypoint/start/ or /keypoint/start/dry/.
 * Automation (explicit user action) may click the on-page 「战斗」 button only.
 * It must not click buy / post / account-settings controls.
 */
(function () {
  "use strict";
  const PF_INJECT_VERSION = "2026-10-19T · v10-lobby-chain";
  console.info("[PFHelper] inject boot", PF_INJECT_VERSION, location.href);

  // Always tear down prior inject so CDP / paste can upgrade the script.
  if (window.__PFHelper) {
    try {
      window.__PFHelper._alive = false;
      if (window.__PFHelper.ws) window.__PFHelper.ws.close();
    } catch (e) {}
    console.info("[PFHelper] replaced previous inject", window.__PFHelper.version || "(old)");
  }

  const PORT = (function () {
    const m = String(document.currentScript && document.currentScript.src || "").match(/[?&]port=(\d+)/);
    if (m) return Number(m[1]);
    // baked by helper when serving
    const baked = "__PF_BRIDGE_PORT__";
    if (/^\d+$/.test(baked)) return Number(baked);
    return 17891;
  })();

  const WS_URL = "ws://127.0.0.1:" + PORT + "/ws";
  const HTTP_SNAPSHOT = "http://127.0.0.1:" + PORT + "/api/snapshot";

  const api = async (path, opts) => {
    opts = opts || {};
    const res = await fetch(path, {
      method: opts.method || "GET",
      credentials: "include",
      headers: opts.body ? { "Content-Type": "application/json" } : undefined,
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(path + " " + res.status + " " + JSON.stringify(data).slice(0, 200));
    return data;
  };

  const unwrap = (d) => (d && typeof d === "object" && d.result != null ? d.result : d);

  function ingestLocalePack(pack, cards, enemies) {
    if (!pack || typeof pack !== "object") return;
    const walk = (obj, prefix) => {
      if (!obj || typeof obj !== "object") return;
      for (const [k, v] of Object.entries(obj)) {
        const key = prefix ? prefix + "." + k : k;
        if (typeof v === "string") {
          const bare = key.replace(/^.*\./, "");
          if (/^card_\d+$/i.test(bare) || /^card_\d+$/i.test(k)) {
            cards[bare.match(/^card_/i) ? bare : k] = v;
          } else if (/^vehicle_\d+$/i.test(bare) || /^enemy_\d+$/i.test(bare)) {
            enemies[bare] = v;
          } else if (/card/i.test(key) && /^card_\d+$/i.test(bare)) {
            cards[bare] = v;
          } else if (/enem|vehicle/i.test(key) && /^(vehicle|enemy)_\d+$/i.test(bare)) {
            enemies[bare] = v;
          }
        } else if (v && typeof v === "object" && (prefix || "").split(".").length < 4) {
          walk(v, key);
        }
      }
    };
    // Direct maps
    if (pack.cards && typeof pack.cards === "object") {
      for (const [k, v] of Object.entries(pack.cards)) {
        if (typeof v === "string") cards[k] = v;
      }
    }
    if (pack.enemies && typeof pack.enemies === "object") {
      for (const [k, v] of Object.entries(pack.enemies)) {
        if (typeof v === "string") enemies[k] = v;
      }
    }
    walk(pack, "");
  }

  function pullLocale() {
    const cards = {};
    const enemies = {};
    try {
      const roots = [
        window.__I18N__,
        window.i18n,
        window.__locale,
        window.__LOCALE__,
        window.__NEXT_DATA__ && window.__NEXT_DATA__.props,
        window.__NEXT_DATA__ && window.__NEXT_DATA__.runtimeConfig,
        window.__NUXT__ && window.__NUXT__.i18n,
        window.__NUXT__ && window.__NUXT__.data,
      ];
      for (const root of roots) {
        if (!root) continue;
        const pack =
          root.messages ||
          root.zh ||
          root["zh-sg"] ||
          root["zh_sg"] ||
          root.locale ||
          root.translations ||
          root;
        ingestLocalePack(pack, cards, enemies);
      }
    } catch (_) {}
    // DOM labels (hand cards / tooltips)
    try {
      document.querySelectorAll("[data-card-code], [data-code], [data-vehicle-code]").forEach((el) => {
        const code =
          el.getAttribute("data-card-code") ||
          el.getAttribute("data-vehicle-code") ||
          el.getAttribute("data-code");
        const label = (el.getAttribute("aria-label") || el.textContent || "")
          .replace(/\s+/g, " ")
          .trim();
        if (!code || !label || label.length >= 48) return;
        if (/^card_/i.test(code)) cards[code] = label;
        else if (/^vehicle_/i.test(code)) enemies[code] = label;
        else cards[code] = label;
      });
    } catch (_) {}
    return { cards, enemies };
  }

  const UUID_RE = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}";

  function parseIdsFromHref(href) {
    let operationId = null;
    let keypointId = null;
    try {
      const u = new URL(href || location.href, location.origin);
      operationId = u.searchParams.get("operationId") || u.searchParams.get("op") || null;
      keypointId = u.searchParams.get("keypointId") || u.searchParams.get("kp") || null;
      const path = u.pathname || "";
      // Real game URL: /zh-sg/operations/{opUuid}/{kpUuid}
      const mOps = path.match(
        new RegExp("operation[s]?\\/(" + UUID_RE + ")\\/(" + UUID_RE + ")", "i")
      );
      if (mOps) {
        operationId = operationId || mOps[1];
        keypointId = keypointId || mOps[2];
      }
      // Legacy: /operation/<uuid>/.../keypoint/<uuid>
      const mLegacy = path.match(
        new RegExp("operation[s]?\\/(" + UUID_RE + ").*keypoint[s]?\\/(" + UUID_RE + ")", "i")
      );
      if (mLegacy) {
        operationId = operationId || mLegacy[1];
        keypointId = keypointId || mLegacy[2];
      }
    } catch (_) {}
    return { operationId, keypointId };
  }

  function pickKeypointFromStages(stages) {
    if (!Array.isArray(stages)) return null;
    for (const st of stages) {
      if (!st || typeof st !== "object") continue;
      const list = Array.isArray(st.keypoints)
        ? st.keypoints
        : Array.isArray(st.keyPoints)
          ? st.keyPoints
          : [];
      for (const kp of list) {
        if (kp && (kp.status === "active" || kp.status === "current" || kp.isCurrent)) {
          return kp.id || null;
        }
      }
    }
    for (const st of stages) {
      if (!st || typeof st !== "object") continue;
      const list = Array.isArray(st.keypoints)
        ? st.keypoints
        : Array.isArray(st.keyPoints)
          ? st.keyPoints
          : [];
      for (const kp of list) {
        if (
          kp &&
          kp.id &&
          kp.status !== "cleared" &&
          kp.status !== "completed" &&
          kp.status !== "finished"
        ) {
          return kp.id;
        }
      }
    }
    return null;
  }

  function resolveIdsFromState(state) {
    // Prefer URL on deployment pages — most reliable for /operations/{op}/{kp}
    const fromUrl = parseIdsFromHref(location.href);
    let operationId = fromUrl.operationId;
    let keypointId = fromUrl.keypointId;

    state = unwrap(state) || {};
    const status = state.status && typeof state.status === "object" ? state.status : state;
    operationId =
      operationId ||
      status.operationId ||
      state.operationId ||
      state.currentOperationId ||
      null;
    keypointId =
      keypointId ||
      status.keypointId ||
      state.keypointId ||
      state.currentKeypointId ||
      null;

    const op = state.operation || state.currentOperation;
    if (op && typeof op === "object") {
      operationId = operationId || op.id;
      keypointId = keypointId || pickKeypointFromStages(op.stages);
    }

    // Traverse operations list (state.result.operations or state.operations)
    const opsList = Array.isArray(state.operations)
      ? state.operations
      : Array.isArray((state.result || {}).operations)
        ? state.result.operations
        : [];
    if (!operationId || !keypointId) {
      for (const item of opsList) {
        if (!item || typeof item !== "object") continue;
        if (item.isStarted && !item.isFinished) {
          operationId = operationId || item.id;
          // keyPoints is often a NUMBER in list summary — only dig stages if array
          if (Array.isArray(item.stages)) {
            keypointId = keypointId || pickKeypointFromStages(item.stages);
          }
          break;
        }
      }
      if (!operationId) {
        for (const item of opsList) {
          if (item && item.id && !item.isFinished) {
            operationId = item.id;
            break;
          }
        }
      }
    }
    return { operationId, keypointId };
  }

  function looksLikeOperation(o) {
    if (!o || typeof o !== "object") return false;
    if (o.isFinished != null || o.isStarted != null) return true;
    if (o.isCompleted != null || o.completed != null || o.isCleared != null) return true;
    if (o.stages || o.keypoints) return true;
    // Hall summaries often store keyPoints as a count, including 0.
    if (typeof o.keyPoints === "number" || Array.isArray(o.keyPoints)) return true;
    const status = String(o.status || o.state || "");
    return /^(completed|cleared|active|current|locked|opened|available|in_progress|pending|done|finished|archived)$/i.test(
      status
    );
  }

  function asOpSummary(o) {
    if (!looksLikeOperation(o)) return null;
    const id = o.id || o.operationId;
    if (!id || typeof id !== "string") return null;
    const status = String(o.status || o.state || "").toLowerCase();
    const completed = !!(
      o.isFinished ||
      o.isCompleted ||
      o.completed ||
      o.isCleared ||
      o.archived ||
      o.isArchived ||
      status === "completed" ||
      status === "cleared" ||
      status === "done" ||
      status === "finished" ||
      status === "archived"
    );
    const started = !!(o.isStarted || o.isCurrent) && !completed;
    const index = o.index != null ? o.index : o.order != null ? o.order : null;
    let phase = "not_started";
    if (completed) phase = "completed";
    else if (started || status === "active" || status === "in_progress" || status === "current") phase = "in_progress";
    return {
      id: id,
      name: o.name || o.title || "",
      status: phase,
      phase: phase,
      completed: completed,
      started: phase === "in_progress",
      isFinished: completed,
      isStarted: phase === "in_progress",
      index: index,
    };
  }

  function collectLobby(state) {
    const found = [];
    const seen = {};
    const un = unwrap(state) || {};
    const buckets = [
      un.operations,
      un.lobby && un.lobby.operations,
      un.event && un.event.operations,
      un.campaign && un.campaign.operations,
      un.status && un.status.operations,
      un.result && un.result.operations,
    ];
    try {
      for (const key of Object.keys(un)) {
        if (/operation/i.test(key) && Array.isArray(un[key])) buckets.push(un[key]);
      }
    } catch (_) {}
    for (const arr of buckets) {
      if (!Array.isArray(arr) || arr.length < 1 || arr.length > 8) continue;
      for (const item of arr) {
        const sum = asOpSummary(item);
        if (!sum || seen[sum.id]) continue;
        seen[sum.id] = true;
        found.push(sum);
      }
    }
    return found;
  }

  const PAY_OR_SETTINGS = /购买|商店|发帖|分享|设置|充值|礼包|通行证|订阅|账号|支付|premium|buy|shop|post|settings|account/i;

  function visibleText(el) {
    return String((el && (el.innerText || el.textContent)) || "")
      .replace(/\s+/g, " ")
      .trim();
  }

  function isBattleLabel(text) {
    const t = String(text || "").trim();
    if (!t || PAY_OR_SETTINGS.test(t)) return false;
    const compact = t.replace(/\s+/g, "");
    if (compact === "战斗" || compact === "开始战斗" || compact === "开战") return true;
    return /^战斗[（(].{0,12}[）)]$/.test(t);
  }

  function findBattleButton() {
    const nodes = document.querySelectorAll("button, [role='button']");
    let fallback = null;
    for (const el of nodes) {
      const t = visibleText(el);
      const aria = (el.getAttribute && el.getAttribute("aria-label")) || "";
      if (PAY_OR_SETTINGS.test(aria)) continue;
      if (!isBattleLabel(t) && !isBattleLabel(aria)) continue;
      const r = el.getBoundingClientRect();
      if (r.width < 2 || r.height < 2) continue;
      if (el.disabled || el.getAttribute("aria-disabled") === "true") continue;
      if (el.tagName === "BUTTON") return el;
      fallback = fallback || el;
    }
    return fallback;
  }

  /**
   * Click only the battle button. Does not call keypoint/start and does not
   * click purchase, post, or settings controls.
   */
  function startBattle() {
    const result = { ok: false, method: "click", error: null, label: null };
    try {
      const btn = findBattleButton();
      if (!btn) {
        result.error = "页面上没有可点的「战斗」按钮（请先停在据点布阵页）";
        return result;
      }
      result.label = visibleText(btn);
      btn.click();
      result.ok = true;
    } catch (e) {
      result.ok = false;
      result.error = String((e && e.message) || e);
    }
    return result;
  }

  function openKeypoint(operationId, keypointId) {
    const result = { ok: false, method: null, error: null, href: location.href };
    try {
      if (!operationId || !keypointId) {
        result.error = "缺少行动或据点 id";
        return result;
      }
      const href = location.href || "";
      if (href.indexOf(operationId) >= 0 && href.indexOf(keypointId) >= 0) {
        result.ok = true;
        result.method = "already";
        return result;
      }
      const anchors = document.querySelectorAll("a[href]");
      for (const a of anchors) {
        const h = a.getAttribute("href") || "";
        const label = visibleText(a);
        if (PAY_OR_SETTINGS.test(label) || PAY_OR_SETTINGS.test(h)) continue;
        if (h.indexOf(keypointId) >= 0 && (h.indexOf(operationId) >= 0 || h.indexOf("/operations/") >= 0)) {
          a.click();
          result.ok = true;
          result.method = "anchor";
          result.href = location.href;
          return result;
        }
      }
      let marked = null;
      try {
        marked = document.querySelector(
          '[data-keypoint-id="' + keypointId + '"], [data-id="' + keypointId + '"]'
        );
      } catch (_) {}
      if (marked) {
        const label = visibleText(marked);
        if (!PAY_OR_SETTINGS.test(label)) {
          const host = marked.closest("a,button,[role='button']") || marked;
          host.click();
          result.ok = true;
          result.method = "data-id";
          result.href = location.href;
          return result;
        }
      }
      const u = new URL(location.href);
      let replaced = u.pathname.replace(
        /\/operations\/[^/]+\/[^/]+/,
        "/operations/" + operationId + "/" + keypointId
      );
      // Hall URL is /zh-sg/operations with no ids. Push the keypoint path;
      // do not assign location.href (a full load drops this inject).
      if (replaced === u.pathname && /\/operations\/?$/i.test(u.pathname)) {
        replaced = u.pathname.replace(/\/?$/, "/") + operationId + "/" + keypointId;
      } else if (replaced === u.pathname && /\/operations\/[^/]+\/?$/i.test(u.pathname)) {
        replaced = u.pathname.replace(/\/?$/, "/") + keypointId;
      }
      if (replaced !== u.pathname) {
        history.pushState({}, "", replaced + u.search + u.hash);
        window.dispatchEvent(new PopStateEvent("popstate"));
        result.ok = true;
        result.method = "history";
        result.href = location.href;
        return result;
      }
      result.error =
        "无法在不刷新页面的情况下切到该据点。请手动点开据点；若整页刷新，需要在 F12 重新粘贴注入脚本。";
    } catch (e) {
      result.ok = false;
      result.error = String((e && e.message) || e);
    }
    return result;
  }

  /**
   * Enter one operation from the hall (/zh-sg/operations) without a full reload.
   * Clicks an in-page link first, then history.pushState + popstate.
   */
  function openOperation(operationId) {
    const result = { ok: false, method: null, error: null, href: location.href };
    try {
      if (!operationId) {
        result.error = "缺少行动 id";
        return result;
      }
      const href = location.href || "";
      if (href.indexOf("/operations/" + operationId) >= 0) {
        result.ok = true;
        result.method = "already";
        return result;
      }
      const anchors = document.querySelectorAll("a[href]");
      for (const a of anchors) {
        const h = a.getAttribute("href") || "";
        const label = visibleText(a);
        if (PAY_OR_SETTINGS.test(label) || PAY_OR_SETTINGS.test(h)) continue;
        if (h.indexOf(operationId) < 0 || h.indexOf("/operations/") < 0) continue;
        a.click();
        result.ok = true;
        result.method = "anchor";
        result.href = location.href;
        return result;
      }
      let marked = null;
      try {
        marked = document.querySelector(
          '[data-operation-id="' + operationId + '"], [data-id="' + operationId + '"]'
        );
      } catch (_) {}
      if (marked) {
        const host = marked.closest("a,button,[role='button']") || marked;
        const label = visibleText(host);
        if (!PAY_OR_SETTINGS.test(label)) {
          host.click();
          result.ok = true;
          result.method = "data-id";
          result.href = location.href;
          return result;
        }
      }
      const u = new URL(location.href);
      let nextPath = u.pathname;
      if (/\/operations\/?$/i.test(u.pathname)) {
        nextPath = u.pathname.replace(/\/?$/, "/") + operationId;
      } else if (/\/operations\/[^/]+\/[^/]+/.test(u.pathname)) {
        nextPath = u.pathname.replace(/\/operations\/[^/]+\/[^/]+/, "/operations/" + operationId);
      } else if (/\/operations\/[^/]+/.test(u.pathname)) {
        nextPath = u.pathname.replace(/\/operations\/[^/]+/, "/operations/" + operationId);
      }
      if (nextPath !== u.pathname) {
        history.pushState({}, "", nextPath + u.search + u.hash);
        window.dispatchEvent(new PopStateEvent("popstate"));
        result.ok = true;
        result.method = "history";
        result.href = location.href;
        return result;
      }
      result.error = "无法在不刷新页面的情况下进入该行动。请手动点开；不要整页刷新。";
    } catch (e) {
      result.ok = false;
      result.error = String((e && e.message) || e);
    }
    return result;
  }

  /** Back to the three-operation hall. SPA only — never location.reload. */
  function returnToLobby() {
    const result = { ok: false, method: null, error: null, href: location.href };
    try {
      const u = new URL(location.href);
      if (/\/operations\/?$/i.test(u.pathname)) {
        result.ok = true;
        result.method = "already";
        return result;
      }
      const anchors = document.querySelectorAll("a[href]");
      for (const a of anchors) {
        const h = a.getAttribute("href") || "";
        const label = visibleText(a);
        if (PAY_OR_SETTINGS.test(label) || PAY_OR_SETTINGS.test(h)) continue;
        if (/\/operations\/?$/.test(h.split("?")[0].split("#")[0])) {
          a.click();
          result.ok = true;
          result.method = "anchor";
          result.href = location.href;
          return result;
        }
      }
      const hall = u.pathname.replace(/\/operations\/.+$/i, "/operations");
      if (hall !== u.pathname && /\/operations$/i.test(hall)) {
        history.pushState({}, "", hall + u.search + u.hash);
        window.dispatchEvent(new PopStateEvent("popstate"));
        result.ok = true;
        result.method = "history";
        result.href = location.href;
        return result;
      }
      result.error = "无法在不刷新页面的情况下回到行动大厅。";
    } catch (e) {
      result.ok = false;
      result.error = String((e && e.message) || e);
    }
    return result;
  }

  async function scrapeAll() {
    const scrapeErrors = [];
    const settings = await api("/api/v2/settings/");
    const inventory = await api("/api/v2/inventory/");
    const state = await api("/api/v2/state/");
    let { operationId, keypointId } = resolveIdsFromState(state);
    let operation = null;
    let keypoint = null;

    if (operationId) {
      try {
        operation = await api("/api/v2/operation/" + operationId + "/");
      } catch (e) {
        const msg = String(e && e.message || e);
        scrapeErrors.push("operation: " + msg);
        console.warn("[PFHelper] operation fetch failed", e);
      }
    }

    // If still no keypointId, dig active/current from operation detail stages
    if (!keypointId && operation) {
      const op = unwrap(operation);
      keypointId = pickKeypointFromStages(op.stages || []);
    }

    if (keypointId) {
      try {
        keypoint = await api("/api/v2/keypoint/" + keypointId + "/");
      } catch (e) {
        const msg = String(e && e.message || e);
        scrapeErrors.push("keypoint: " + msg);
        console.warn("[PFHelper] keypoint fetch failed", e);
      }
    } else {
      scrapeErrors.push("missing keypointId (URL/state/operation)");
    }

    // Fallback: dig keypoint from operation payload
    if (!keypoint && operation && keypointId) {
      const op = unwrap(operation);
      for (const st of op.stages || []) {
        const list = Array.isArray(st.keypoints)
          ? st.keypoints
          : Array.isArray(st.keyPoints)
            ? st.keyPoints
            : [];
        for (const kp of list) {
          if (kp && kp.id === keypointId) {
            keypoint = { result: kp };
            break;
          }
        }
        if (keypoint) break;
      }
    }

    const locale = pullLocale();
    let operations = [];
    try {
      operations = collectLobby(state);
      const opUn = operation ? unwrap(operation) : null;
      if (opUn && opUn.id && !operations.some((o) => o.id === opUn.id)) {
        const sum = asOpSummary(opUn);
        operations.push(
          sum || {
            id: opUn.id,
            name: opUn.name || "",
            status: "active",
            completed: false,
            index: opUn.index != null ? opUn.index : null,
          }
        );
      }
    } catch (e) {
      console.warn("[PFHelper] lobby collect failed", e);
    }
    const snap = {
      type: "snapshot",
      settings,
      inventory,
      state,
      operation,
      keypoint,
      operations,
      locale,
      meta: {
        operationId,
        keypointId,
        href: location.href,
        ts: Date.now(),
        scrapeErrors: scrapeErrors.length ? scrapeErrors : undefined,
      },
    };
    if (!keypoint) {
      console.warn(
        "[PFHelper] snapshot missing keypoint — enemies will be empty. " +
          "ids=",
        { operationId, keypointId },
        "href=",
        location.href,
        "errors=",
        scrapeErrors
      );
    }
    return snap;
  }

  function findReduxStoreAndQc() {
    const app = document.getElementById("app");
    if (!app) return { store: null, qc: null, error: "no #app" };
    const fk = Object.keys(app).find((k) => k.startsWith("__reactContainer") || k.startsWith("__reactFiber"));
    if (!fk) return { store: null, qc: null, error: "no react fiber on #app" };
    const root = app[fk];
    const startFiber = root && (root.current || root);
    let store = null;
    let qc = null;
    const seen = new Set();
    const stack = [startFiber];
    while (stack.length) {
      const f = stack.pop();
      if (!f || seen.has(f)) continue;
      seen.add(f);
      const p = f.memoizedProps || f.pendingProps;
      if (p && p.store && p.store.getState && p.store.dispatch) store = p.store;
      if (p && p.client && p.client.getQueryCache) qc = p.client;
      if (store && qc) break;
      if (f.child) stack.push(f.child);
      if (f.sibling) stack.push(f.sibling);
      // some trees hang state under stateNode
      try {
        const sn = f.stateNode;
        if (sn && sn.store && sn.store.dispatch) store = store || sn.store;
      } catch (_) {}
    }
    if (!store) return { store: null, qc, error: "redux store not found in fiber tree" };
    return { store, qc, error: null };
  }

  function cardCatalog() {
    try {
      const cards =
        (window.defaultState &&
          window.defaultState.settings &&
          window.defaultState.settings.collection &&
          window.defaultState.settings.collection.cards) ||
        null;
      if (Array.isArray(cards) && cards.length) {
        return Object.fromEntries(cards.map((c) => [c.code, c]));
      }
    } catch (_) {}
    return null;
  }

  async function cardCatalogAsync() {
    const local = cardCatalog();
    if (local) return local;
    try {
      const settings = await api("/api/v2/settings/");
      const cards =
        (unwrap(settings).collection && unwrap(settings).collection.cards) ||
        (settings.result && settings.result.collection && settings.result.collection.cards) ||
        [];
      return Object.fromEntries((cards || []).map((c) => [c.code, c]));
    } catch (e) {
      throw new Error("catalog unavailable: " + (e && e.message || e));
    }
  }

  function inventoryHealthMap(qc) {
    const health = {};
    try {
      if (!qc || !qc.getQueryCache) return health;
      const q = qc.getQueryCache().find({ queryKey: ["inventory"] });
      const data = q && q.state && q.state.data;
      const cards =
        (data && data.result && data.result.cards) ||
        (data && data.cards) ||
        [];
      for (const c of cards || []) {
        if (c && c.code) health[c.code] = c.health != null ? c.health : 3;
      }
    } catch (_) {}
    return health;
  }

  function withPower(card, healthVal) {
    const specs = (card && card.specs) || {};
    const power = [0, 1, 2].reduce((s, k) => s + (Number(specs[k]) || 0), 0);
    return Object.assign({}, card, {
      power,
      health: healthVal != null ? healthVal : 3,
    });
  }

  function resolveKeypointId() {
    const fromUrl = parseIdsFromHref(location.href);
    if (fromUrl.keypointId) return fromUrl.keypointId;
    try {
      return location.pathname.split("/").filter(Boolean).pop() || null;
    } catch (_) {
      return null;
    }
  }

  /**
   * Place ally cards (+ optional combat mods) via Redux battleBoard previews.
   * slots: [{code, health?, modifiers?: string[]}|null] length 4
   * Does NOT call start/dry and does NOT click 战斗.
   *
   * Verified order (must apply separately — do not nest mods on card):
   *   1. clear cards  slot_card [null×4] → apply
   *   2. set cards    slot_card [{card}…] → apply
   *   3. set A        slot_modifier_group_a [{modifier:{id,entityId}}|null×4] → apply
   *   4. set B        slot_modifier_group_b … → apply
   * Type 1→A, Type 2→B (adeck.getModifiers()[id].Type). entityId = kpId:id:lane.
   */
  async function placeByRedux(slotsIn) {
    const result = {
      ok: false,
      method: "redux",
      version: PF_INJECT_VERSION,
      placed: [],
      modsPlaced: [],
      modsSkipped: [],
      error: null,
      keypointId: null,
    };
    try {
      const { store, qc, error: findErr } = findReduxStoreAndQc();
      if (!store) {
        result.error = findErr || "no redux store";
        return result;
      }
      const kpId = resolveKeypointId();
      result.keypointId = kpId;
      if (!kpId) {
        result.error = "无法从 URL 解析 keypointId（请在据点布阵页）";
        return result;
      }

      const byCode = await cardCatalogAsync();
      const healthMap = inventoryHealthMap(qc);
      const modTypeMap = modifierTypeMap();
      const raw = Array.isArray(slotsIn) ? slotsIn.slice(0, 4) : [];
      while (raw.length < 4) raw.push(null);

      const previewSlots = [];
      for (let i = 0; i < 4; i++) {
        const s = raw[i];
        if (!s || !s.code) {
          previewSlots.push(null);
          continue;
        }
        const base = byCode[s.code];
        if (!base) {
          result.error = "目录中无此卡: " + s.code;
          return result;
        }
        const h =
          s.health != null
            ? s.health
            : healthMap[s.code] != null
              ? healthMap[s.code]
              : 3;
        const card = withPower(base, h);
        const mods = Array.isArray(s.modifiers) ? s.modifiers.filter(Boolean) : [];
        previewSlots.push({ card });
        result.placed.push({ lane: i, code: s.code, health: h, mods: mods.slice() });
      }

      if (!result.placed.length) {
        result.error = "没有可放的舰船";
        return result;
      }

      // Clear cards first — applyPreviews no-ops when card codes are unchanged.
      store.dispatch({
        type: "battleBoard/setPreviews",
        payload: { type: "slot_card", slots: [null, null, null, null] },
      });
      store.dispatch({
        type: "battleBoard/applyPreviews",
        payload: { keypointId: kpId, dropTargetId: null },
      });

      store.dispatch({
        type: "battleBoard/setPreviews",
        payload: { type: "slot_card", slots: previewSlots },
      });
      store.dispatch({
        type: "battleBoard/applyPreviews",
        payload: { keypointId: kpId, dropTargetId: null },
      });

      const modResult = placeModifiersByRedux(store, kpId, raw, modTypeMap);
      result.modsPlaced = modResult.placed || [];
      result.modsSkipped = modResult.skipped || [];

      result.ok = true;
      result.note =
        "redux place · " +
        result.placed.length +
        " lanes" +
        (result.modsPlaced.length ? " · mods×" + result.modsPlaced.length : "") +
        (result.modsSkipped.length ? " · mods skip×" + result.modsSkipped.length : "");
    } catch (e) {
      result.ok = false;
      result.error = String(e && e.message || e);
    }
    return result;
  }

  function modifierTypeMap() {
    // Prefer adeck.getModifiers()[id].Type (1=A common, 2=B rare).
    // settings.collection.modifiers.type usually matches but is secondary.
    const map = {};
    try {
      const adeck =
        (window.adeck &&
          typeof window.adeck.getModifiers === "function" &&
          window.adeck.getModifiers()) ||
        {};
      for (const [id, info] of Object.entries(adeck)) {
        if (!id || !info) continue;
        const t = Number(info.Type != null ? info.Type : info.type);
        if (t === 1 || t === 2) map[id] = t;
      }
    } catch (_) {}
    try {
      const mods =
        (window.defaultState &&
          window.defaultState.settings &&
          window.defaultState.settings.collection &&
          window.defaultState.settings.collection.modifiers) ||
        [];
      for (const m of mods) {
        if (m && m.id && map[m.id] == null) {
          const t = Number(m.Type != null ? m.Type : m.type);
          if (t === 1 || t === 2) map[m.id] = t;
        }
      }
    } catch (_) {}
    // Hardcoded fallbacks for known rare (group B) ids
    const groupB = {
      focused: 2,
      specialization: 2,
      "sea-dog": 2,
      "fast-repair": 2,
      unstoppable: 2,
    };
    for (const [id, t] of Object.entries(groupB)) {
      if (map[id] == null) map[id] = t;
    }
    return map;
  }

  function placeModifiersByRedux(store, kpId, raw, modTypeMap) {
    // Verified sequence (pf-place-with-mods.js): AFTER cards committed,
    // apply group A then group B separately. slots[i] = {modifier:{id,entityId}}
    // or null (null CLEARS that lane). NEVER nest mods on the card object.
    // Type 1 → slot_modifier_group_a, Type 2 → slot_modifier_group_b.
    // entityId = keypointId + ':' + id + ':' + lane (':0' is fine).
    const out = { placed: [], skipped: [] };
    const groupA = [null, null, null, null];
    const groupB = [null, null, null, null];
    for (let i = 0; i < 4; i++) {
      const s = raw[i];
      const mods = s && Array.isArray(s.modifiers) ? s.modifiers.filter(Boolean) : [];
      if (!mods.length) continue;
      let usedA = false;
      let usedB = false;
      for (const mid of mods) {
        const typ = modTypeMap[mid] != null ? modTypeMap[mid] : 1;
        const entityId = kpId + ":" + mid + ":" + i;
        const payload = { modifier: { id: mid, entityId: entityId } };
        if (typ === 2) {
          if (usedB) {
            out.skipped.push({ lane: i, id: mid, reason: "groupB full" });
            continue;
          }
          groupB[i] = payload;
          usedB = true;
          out.placed.push({ lane: i, id: mid, group: "B", entityId: entityId });
        } else {
          if (usedA) {
            out.skipped.push({ lane: i, id: mid, reason: "groupA full" });
            continue;
          }
          groupA[i] = payload;
          usedA = true;
          out.placed.push({ lane: i, id: mid, group: "A", entityId: entityId });
        }
      }
    }

    // Always apply both groups (null slots clear stale mods). Each setPreviews
    // must be followed immediately by applyPreviews.
    store.dispatch({
      type: "battleBoard/setPreviews",
      payload: { type: "slot_modifier_group_a", slots: groupA },
    });
    store.dispatch({
      type: "battleBoard/applyPreviews",
      payload: { keypointId: kpId, dropTargetId: null },
    });
    store.dispatch({
      type: "battleBoard/setPreviews",
      payload: { type: "slot_modifier_group_b", slots: groupB },
    });
    store.dispatch({
      type: "battleBoard/applyPreviews",
      payload: { keypointId: kpId, dropTargetId: null },
    });
    return out;
  }

  function elementScreenCenter(el) {
    if (!el || !el.getBoundingClientRect) return null;
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return null;
    const chromeTop = Math.max(0, (window.outerHeight || 0) - (window.innerHeight || 0));
    const x = Math.round((window.screenX || 0) + r.left + r.width / 2);
    const y = Math.round((window.screenY || 0) + chromeTop + r.top + r.height / 2);
    return { x, y, w: Math.round(r.width), h: Math.round(r.height) };
  }

  function _attrMatch(el, code) {
    if (!el || !code) return false;
    const attrs = ["data-card-code", "data-code", "data-id", "data-vehicle-code", "data-ship-code", "data-key"];
    for (const a of attrs) {
      const v = el.getAttribute && el.getAttribute(a);
      if (v && (v === code || v.toLowerCase() === String(code).toLowerCase())) return true;
    }
    return false;
  }

  function findFleetCard(code, nameHint) {
    if (!code) return null;
    const sels = [
      "[data-card-code='" + code + "']",
      "[data-code='" + code + "']",
      "[data-id='" + code + "']",
      "[data-vehicle-code='" + code + "']",
      "[data-ship-code='" + code + "']",
    ];
    for (const s of sels) {
      try {
        const el = document.querySelector(s);
        if (el) return el.closest("[draggable='true']") || el;
      } catch (_) {}
    }
    const nodes = [...document.querySelectorAll(
      "[draggable='true'], [data-card-code], [data-code], [data-vehicle-code], " +
      "button, [role='button'], [class*='card'], [class*='Card'], [class*='fleet'], [class*='Fleet']"
    )];
    let hit = nodes.find((el) => _attrMatch(el, code));
    if (hit) return hit.closest("[draggable='true']") || hit;
    if (nameHint) {
      hit = nodes.find((el) => (el.textContent || "").includes(nameHint));
      if (hit) return hit.closest("[draggable='true']") || hit;
    }
    hit = nodes.find((el) => (el.textContent || "").includes(code));
    return hit ? (hit.closest("[draggable='true']") || hit) : null;
  }

  function findLaneSlots() {
    let slots = [...document.querySelectorAll(
      "[data-slot], [data-lane], [data-drop-zone], [data-dropzone], " +
      "[class*='slot'], [class*='Slot'], [class*='lane'], [class*='Lane'], " +
      "[class*='drop'], [class*='Drop'], [class*='setup'], [class*='Setup']"
    )];
    const dropish = slots.filter((el) =>
      /拖|drop|slot|lane|空位|布置|布阵/i.test(
        (el.textContent || "") + " " + (el.className || "") + " " + (el.getAttribute("aria-label") || "")
      )
    );
    if (dropish.length >= 2) slots = dropish;
    slots = slots.filter((el, i, arr) => !arr.some((o, j) => j !== i && o.contains(el)));
    slots.sort((a, b) => {
      const ra = a.getBoundingClientRect();
      const rb = b.getBoundingClientRect();
      if (Math.abs(ra.top - rb.top) > 40) return ra.top - rb.top;
      return ra.left - rb.left;
    });
    return slots;
  }

  function findClearButtons() {
    const btns = [...document.querySelectorAll(
      "button, [role='button'], [class*='remove'], [class*='clear'], [class*='Close'], [class*='close']"
    )];
    const hits = [];
    for (const b of btns) {
      const t = (b.getAttribute("aria-label") || b.textContent || "").trim();
      if (/清空|移除|卸下|清除|撤销/.test(t) || t === "×" || t === "x" || t === "X") {
        const pt = elementScreenCenter(b);
        if (pt) hits.push({ el: b, label: t, at: { x: pt.x, y: pt.y } });
      }
    }
    return hits;
  }

  /** Legacy mouse-drag plan — kept for console debug; not used as primary deploy path. */
  function prepareDeploy(slots, clear) {
    const plan = {
      type: "deploy_plan",
      ok: false,
      version: PF_INJECT_VERSION,
      moves: [],
      wanted: 0,
      lanesFound: 0,
      href: location.href,
    };
    try {
      const wanted = (slots || []).filter(Boolean);
      plan.wanted = wanted.length;
      if (!wanted.length) {
        plan.error = "没有可放的舰船";
        return plan;
      }
      if (clear) {
        const clears = findClearButtons();
        const seen = new Set();
        for (const c of clears.slice(0, 8)) {
          const key = c.at.x + "," + c.at.y;
          if (seen.has(key)) continue;
          seen.add(key);
          plan.moves.push({ action: "click", at: c.at, label: c.label || "clear" });
        }
      }
      const lanes = findLaneSlots();
      plan.lanesFound = lanes.length;
      if (lanes.length < 1) {
        plan.error = "未找到布阵格子（legacy mouse plan）";
        return plan;
      }
      const missing = [];
      for (let i = 0; i < (slots || []).length; i++) {
        const slot = slots[i];
        if (!slot || !slot.code) continue;
        const src = findFleetCard(slot.code, slot.name);
        const dst = lanes[Math.min(i, lanes.length - 1)];
        if (!src || !dst) {
          missing.push(slot.code);
          continue;
        }
        const from = elementScreenCenter(src);
        const to = elementScreenCenter(dst);
        if (!from || !to) {
          missing.push(slot.code);
          continue;
        }
        plan.moves.push({
          action: "drag",
          code: slot.code,
          name: slot.name || slot.code,
          lane: i,
          from: { x: from.x, y: from.y },
          to: { x: to.x, y: to.y },
        });
      }
      if (plan.moves.filter((m) => m.action === "drag").length === 0) {
        plan.error = "legacy plan empty: " + missing.join(",");
        return plan;
      }
      plan.ok = true;
      plan.note = "legacy mouse-drag plan (unused by default)";
    } catch (e) {
      plan.ok = false;
      plan.error = String(e && e.message || e);
    }
    return plan;
  }

  async function deploy(slots, clear) {
    return placeByRedux(slots || []);
  }

  function sleep(ms) {
    return new Promise((r) => setTimeout(r, ms));
  }

  const helper = {
    _alive: true,
    version: PF_INJECT_VERSION,
    port: PORT,
    ws: null,
    _retry: 0,
    reconnect() {
      this.connect();
    },
    connect() {
      try {
        if (this.ws && (this.ws.readyState === 0 || this.ws.readyState === 1)) {
          try { this.ws.close(); } catch (_) {}
        }
      } catch (_) {}
      const ws = new WebSocket(WS_URL);
      this.ws = ws;
      ws.onopen = () => {
        this._retry = 0;
        console.info("[PFHelper] WS connected", WS_URL);
        this.send({ type: "log", message: "页面桥接已连接 " + location.host });
        this.doScrape();
      };
      ws.onclose = () => {
        console.warn("[PFHelper] WS closed; retry soon");
        this._retry += 1;
        const wait = Math.min(15000, 1000 * this._retry);
        setTimeout(() => this.connect(), wait);
      };
      ws.onerror = (e) => console.warn("[PFHelper] WS error", e);
      ws.onmessage = (ev) => {
        let msg;
        try { msg = JSON.parse(ev.data); } catch (_) { return; }
        this.onMessage(msg);
      };
    },
    send(obj) {
      try {
        if (this.ws && this.ws.readyState === 1) {
          this.ws.send(JSON.stringify(obj));
          return true;
        }
      } catch (_) {}
      return false;
    },
    async postHttp(snapshot) {
      try {
        await fetch(HTTP_SNAPSHOT, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(snapshot),
          mode: "cors",
        });
      } catch (e) {
        console.warn("[PFHelper] HTTP snapshot fallback failed", e);
      }
    },
    async doScrape() {
      try {
        const snap = await scrapeAll();
        const ok = this.send(snap);
        if (!ok) await this.postHttp(snap);
        console.info("[PFHelper] snapshot sent", snap.meta);
      } catch (e) {
        const err = String(e && e.message || e);
        this.send({ type: "error", message: "采数失败: " + err });
        console.error("[PFHelper] scrape failed", e);
      }
    },
    async onMessage(msg) {
      if (!msg || !msg.type) return;
      if (msg.type === "scrape" || msg.type === "hello") {
        if (msg.type === "scrape") await this.doScrape();
        return;
      }
      if (msg.type === "prepare_deploy" || msg.type === "deploy") {
        const placed = await placeByRedux(msg.slots || []);
        console.info("[PFHelper] placeByRedux", placed);
        this.send({
          type: "deploy_result",
          ok: !!placed.ok,
          method: "redux",
          error: placed.error || null,
          detail: {
            ok: !!placed.ok,
            method: "redux",
            version: placed.version,
            note: placed.note || (placed.ok ? "redux place" : "redux failed"),
            error: placed.error || null,
            placed: placed.placed || [],
            modsPlaced: placed.modsPlaced || [],
            modsSkipped: placed.modsSkipped || [],
            keypointId: placed.keypointId,
          },
        });
        return;
      }
      // Automation-only. Manual deploy above still does not start a battle.
      if (msg.type === "open_keypoint") {
        const opened = openKeypoint(msg.operationId, msg.keypointId);
        console.info("[PFHelper] openKeypoint", opened);
        this.send({
          type: "auto_result",
          action: "open_keypoint",
          ok: !!opened.ok,
          error: opened.error || null,
          detail: opened,
        });
        return;
      }
      if (msg.type === "start_battle") {
        const started = startBattle();
        console.info("[PFHelper] startBattle", started);
        this.send({
          type: "auto_result",
          action: "start_battle",
          ok: !!started.ok,
          error: started.error || null,
          detail: started,
        });
        return;
      }
      if (msg.type === "open_operation") {
        const opened = openOperation(msg.operationId);
        console.info("[PFHelper] openOperation", opened);
        this.send({
          type: "auto_result",
          action: "open_operation",
          ok: !!opened.ok,
          error: opened.error || null,
          detail: opened,
        });
        return;
      }
      if (msg.type === "return_lobby") {
        const back = returnToLobby();
        console.info("[PFHelper] returnToLobby", back);
        this.send({
          type: "auto_result",
          action: "return_lobby",
          ok: !!back.ok,
          error: back.error || null,
          detail: back,
        });
        return;
      }
      if (msg.type === "fetch_operation") {
        try {
          const data = await api("/api/v2/operation/" + msg.operationId + "/");
          this.send({
            type: "auto_result",
            action: "fetch_operation",
            ok: true,
            operation: unwrap(data),
          });
        } catch (e) {
          this.send({
            type: "auto_result",
            action: "fetch_operation",
            ok: false,
            error: String((e && e.message) || e),
          });
        }
        return;
      }
    },
    scrape: () => helper.doScrape(),
    deploy,
    placeByRedux,
    findReduxStoreAndQc,
    prepareDeploy, // legacy debug only
  };

  window.__PFHelper = helper;
  helper.connect();
  console.info(
    "[PFHelper] ready ·",
    PF_INJECT_VERSION,
    "· port=" + PORT,
    "· manual place does not fight · automation may click 战斗"
  );
})();
