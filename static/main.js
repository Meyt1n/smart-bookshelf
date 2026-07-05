const getById = (id) => document.getElementById(id);
const logBox = getById("log_box");
const chatBox = getById("chat_box");
const chatScrollContainer = chatBox ? chatBox.parentElement : null;
const activeAudioPlayers = new Set();
const SHELF_AUTO_REFRESH_MS = 2000;
const SHELF_WATCH_LOCAL_DUPLICATE_MS = 6000;
let _shelfRefreshInFlight = false;
let _shelfSignature = "";
const _localShelfEventSuppressions = new Map();

// 今日操作计数（声明在顶部，供 log() 使用）
let _todayOps = Number(sessionStorage.getItem("_todayOps") || "0");
function bumpOps() {
  _todayOps++;
  sessionStorage.setItem("_todayOps", String(_todayOps));
}

// ===== 唤醒音效（用 Web Audio API 合成，无需音频文件）=====
function playWakeSound() {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    // 两个音符：低→高，清脆提示音
    [[880, 0, 0.12], [1320, 0.13, 0.18]].forEach(([freq, start, end]) => {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.connect(gain); gain.connect(ctx.destination);
      osc.frequency.value = freq;
      osc.type = "sine";
      gain.gain.setValueAtTime(0, ctx.currentTime + start);
      gain.gain.linearRampToValueAtTime(0.18, ctx.currentTime + start + 0.03);
      gain.gain.linearRampToValueAtTime(0, ctx.currentTime + end);
      osc.start(ctx.currentTime + start);
      osc.stop(ctx.currentTime + end + 0.05);
    });
  } catch (_) {}
}

function playSleepSound() {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    // 高→低，告别音
    [[1320, 0, 0.12], [880, 0.13, 0.22]].forEach(([freq, start, end]) => {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.connect(gain); gain.connect(ctx.destination);
      osc.frequency.value = freq;
      osc.type = "sine";
      gain.gain.setValueAtTime(0, ctx.currentTime + start);
      gain.gain.linearRampToValueAtTime(0.15, ctx.currentTime + start + 0.03);
      gain.gain.linearRampToValueAtTime(0, ctx.currentTime + end);
      osc.start(ctx.currentTime + start);
      osc.stop(ctx.currentTime + end + 0.05);
    });
  } catch (_) {}
}

function showWakeOverlay() {
  const el = getById("wake-overlay");
  if (el) el.classList.remove("hidden");
}

function hideWakeOverlay() {
  const el = getById("wake-overlay");
  if (el) el.classList.add("hidden");
}

const TXT = {
  user: "\u4f60",
  userVoice: "\u4f60\uff08\u8bed\u97f3\u6307\u4ee4\uff09",
  bot: "\u5c0f\u71d5",
  sys: "\u7cfb\u7edf",
  welcome: "\u6b22\u8fce\u56de\u5bb6\uff01\u4eca\u5929\u60f3\u8bfb\u70b9\u4ec0\u4e48\u4e66\u5462\uff1f"
};

const MODULE_IDLE_RESET_MS = 2600;
const MODULE_ACTIONS = {
  uv: {
    buttonId: "btn-uv-sterilize",
    statusId: "uv-status",
    startLog: "\u7d2b\u5916\u7ebf\u6d88\u6bd2\u6a21\u5757\u5df2\u542f\u52a8",
    endLog: "\u7d2b\u5916\u7ebf\u6d88\u6bd2\u6d41\u7a0b\u5df2\u5b8c\u6210",
    reply: "\u7d2b\u5916\u7ebf\u6d88\u6bd2\u5df2\u5b8c\u6210\uff0c\u4e66\u67dc\u73af\u5883\u5df2\u66f4\u65b0\u3002",
    duration: 2600,
  },
  laminate: {
    buttonId: "btn-book-laminate",
    statusId: "laminate-status",
    startLog: "\u5851\u5c01\u4e66\u7c4d\u6a21\u5757\u5df2\u542f\u52a8",
    endLog: "\u5851\u5c01\u4fdd\u62a4\u6d41\u7a0b\u5df2\u5b8c\u6210",
    reply: "\u5851\u5c01\u4e66\u7c4d\u5df2\u5b8c\u6210\uff0c\u53ef\u7ee7\u7eed\u5904\u7406\u4e0b\u4e00\u672c\u56fe\u4e66\u3002",
    duration: 3000,
  },
};

function setModuleStatus(statusId, state, label) {
  const badge = getById(statusId);
  if (!badge) return;
  badge.className = `module-status-badge ${state}`;
  badge.textContent = label;
}

function updateClimateMetric(id, value) {
  const el = getById(id);
  if (!el) return;
  if (value === "--") {
    el.textContent = "--";
    return;
  }
  const num = Number(value);
  el.textContent = Number.isFinite(num) ? String(Math.round(num)) : "--";
}

const HAIGANG_DISTRICT_COORDS = {
  latitude: 39.9427,
  longitude: 119.5996,
};

function normalizeClimateError(error) {
  return error?.message || "\u6682\u65f6\u65e0\u6cd5\u83b7\u53d6\u79e6\u7687\u5c9b\u6d77\u6e2f\u533a\u6e29\u6e7f\u5ea6\u3002";
}

function runModuleAction(moduleKey) {
  const config = MODULE_ACTIONS[moduleKey];
  if (!config) return;
  const btn = getById(config.buttonId);
  if (!btn || btn.disabled) return;

  btn.disabled = true;
  btn.classList.add("is-running");
  setModuleStatus(config.statusId, "running", "\u8fd0\u884c\u4e2d");
  log(config.startLog);

  window.setTimeout(() => {
    btn.disabled = false;
    btn.classList.remove("is-running");
    setModuleStatus(config.statusId, "done", "\u5df2\u5b8c\u6210");
    bumpOps();
    log(config.endLog);
    chat(TXT.bot, config.reply);

    if (typeof Swal !== "undefined") {
      Swal.fire({
        toast: true,
        position: "top-end",
        icon: "success",
        title: config.reply,
        showConfirmButton: false,
        timer: 2200,
        background: "#fefae0"
      });
    }

    window.setTimeout(() => {
      if (!btn.disabled) {
        setModuleStatus(config.statusId, "idle", "\u5f85\u673a");
      }
    }, MODULE_IDLE_RESET_MS);
  }, config.duration);
}

async function loadClimate() {
  try {
    const weatherUrl = new URL("https://api.open-meteo.com/v1/forecast");
    weatherUrl.searchParams.set("latitude", HAIGANG_DISTRICT_COORDS.latitude.toFixed(4));
    weatherUrl.searchParams.set("longitude", HAIGANG_DISTRICT_COORDS.longitude.toFixed(4));
    weatherUrl.searchParams.set("current", "temperature_2m,relative_humidity_2m");
    weatherUrl.searchParams.set("timezone", "Asia/Shanghai");

    const response = await fetch(weatherUrl.toString());
    if (!response.ok) {
      throw new Error("\u5929\u6c14\u670d\u52a1\u6682\u65f6\u4e0d\u53ef\u7528\uff0c\u8bf7\u7a0d\u540e\u518d\u8bd5\u3002");
    }

    const payload = await response.json();
    const current = payload?.current || {};
    const temperature = Number(current.temperature_2m);
    const humidity = Number(current.relative_humidity_2m);

    if (!Number.isFinite(temperature) || !Number.isFinite(humidity)) {
      throw new Error("\u8fd4\u56de\u7684\u5929\u6c14\u6570\u636e\u4e0d\u5b8c\u6574\u3002");
    }

    updateClimateMetric("climate-temp", temperature);
    updateClimateMetric("climate-humidity", humidity);
  } catch (error) {
    updateClimateMetric("climate-temp", "--");
    updateClimateMetric("climate-humidity", "--");
    console.warn("Climate load failed:", normalizeClimateError(error));
  }
}

function log(msg) {
  const t = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const li = document.createElement("li");
  li.className = "log-item animate-fade-in";
  li.innerHTML = `<span class="log-time">${t}</span><span class="log-text">${msg}</span>`;
  logBox.prepend(li);
}

function scrollChatToBottom(force = false) {
  if (!chatScrollContainer) return;

  const run = () => {
    const distanceToBottom =
      chatScrollContainer.scrollHeight - chatScrollContainer.clientHeight - chatScrollContainer.scrollTop;
    if (force || distanceToBottom < 80) {
      chatScrollContainer.scrollTop = chatScrollContainer.scrollHeight;
    }
  };

  run();
  requestAnimationFrame(run);
}

function chat(role, text, isAudio = false) {
  const div = document.createElement("div");
  div.className = `message-bubble ${role === TXT.bot ? "ai" : "user"} animate-slide-in`;
  const icon = role === TXT.bot ? '<i class="fa-solid fa-robot ai-avatar"></i>' : "";
  const audioIcon = isAudio ? '<i class="fa-solid fa-microphone-lines voice-indicator"></i> ' : "";
  const sender = role === TXT.bot ? "" : role;

  div.innerHTML = `
    ${role === TXT.bot ? icon : ""}
    <div class="message-content">
      ${sender ? `<div class="message-sender">${sender}</div>` : ""}
      <div class="message-text">${audioIcon}${String(text || "").replace(/\n/g, "<br>")}</div>
    </div>
  `;
  chatBox.appendChild(div);
  scrollChatToBottom(true);
}

if (chatBox && chatScrollContainer) {
  if (typeof MutationObserver !== "undefined") {
    const chatObserver = new MutationObserver(() => scrollChatToBottom());
    chatObserver.observe(chatBox, { childList: true, subtree: true, characterData: true });
  }

  if (typeof ResizeObserver !== "undefined") {
    const chatResizeObserver = new ResizeObserver(() => scrollChatToBottom());
    chatResizeObserver.observe(chatBox);
  }

  window.addEventListener("resize", () => scrollChatToBottom());
}

async function readJsonEnvelope(response) {
  const raw = await response.text();
  let envelope;

  try {
    envelope = raw ? JSON.parse(raw) : null;
  } catch (_) {
    const snippet = raw ? raw.slice(0, 300) : "";
    throw new Error(snippet || "接口返回了非 JSON 响应");
  }

  if (!envelope || typeof envelope !== "object" || typeof envelope.ok !== "boolean" || !("data" in envelope)) {
    throw new Error("接口返回格式不正确");
  }

  return envelope;
}

async function fetchJsonEnvelope(url, options) {
  try {
    const response = await fetch(url, options);
    return readJsonEnvelope(response);
  } catch (error) {
    const detail = normalizeErrorMessage(error, "network error");
    throw new Error(`接口无法连接: ${url} (${detail})`);
  }
}

function requireEnvelopeSuccess(envelope, fallbackMessage) {
  if (!envelope.ok) {
    throw new Error(envelope.message || fallbackMessage || "请求失败");
  }

  return {
    data: envelope.data,
    message: envelope.message || "",
  };
}

function normalizeErrorMessage(error, fallback = "未知错误") {
  if (typeof error === "string" && error.trim()) {
    return error.trim();
  }
  if (error && typeof error.message === "string" && error.message.trim()) {
    return error.message.trim();
  }
  if (error != null) {
    const rendered = String(error).trim();
    if (rendered && rendered !== "[object Object]") {
      return rendered;
    }
  }
  return fallback;
}

async function dispatchPreparedShelfAction(prepared) {
  return dispatchPreparedShelfActionStandalone(prepared);
}

// Optional standalone mode: skip the local bridge and commit directly to Flask.
async function dispatchPreparedShelfActionStandalone(prepared) {
  const commitRequest = prepared?.commit_request;

  if (!commitRequest) {
    throw new Error("服务器没有返回可提交的动作信息");
  }

  const commitEnvelope = await fetchJsonEnvelope("/api/motion/commit", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(commitRequest),
  });
  const { data, message } = requireEnvelopeSuccess(commitEnvelope, "同步书架状态失败");
  return {
    result: data || {},
    message,
  };
}

function getReplyText(payload, fallback = "") {
  return payload?.ai_reply || payload?.reply || fallback;
}

function getShelfWatchEventText(payload) {
  const action = payload?.intent || payload?.commit_request?.action || "";
  if (action !== "store" && action !== "take") return "";
  const picked = payload?.picked || {};
  const title = picked.title || payload?.commit_request?.title || "";
  const cid = picked.cid ?? payload?.commit_request?.cid;
  if (!title) return "";
  const actionText = action === "store" ? "已存入" : "已取出";
  const suffix = cid != null ? `（${cid}号格）` : "";
  return `${actionText}《${title}》${suffix}`;
}

function rememberLocalShelfWatchEvent(payload) {
  const text = getShelfWatchEventText(payload);
  if (!text) return;
  _localShelfEventSuppressions.set(text, Date.now() + SHELF_WATCH_LOCAL_DUPLICATE_MS);
}

function shouldSuppressShelfWatchEvent(ev) {
  const now = Date.now();
  for (const [text, expiresAt] of _localShelfEventSuppressions) {
    if (expiresAt <= now) {
      _localShelfEventSuppressions.delete(text);
    }
  }
  const text = String(ev?.op_text || ev?.text || "");
  const expiresAt = _localShelfEventSuppressions.get(text);
  if (!expiresAt || expiresAt <= now) return false;
  _localShelfEventSuppressions.delete(text);
  return true;
}

async function playTtsReply(text) {
  const reply = String(text || "").trim();
  if (!reply) return;

  try {
    const ttsEnvelope = await fetchJsonEnvelope("/api/tts_say", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: reply }),
    });
    const { data } = requireEnvelopeSuccess(ttsEnvelope, "语音播报失败");
    if (data?.audio_b64) {
      return playBase64Audio(data.audio_b64, data.audio_format || "mp3");
    }
    console.warn("TTS response returned no audio payload.");
  } catch (error) {
    console.warn("TTS playback failed:", error);
  }

  return false;
}

async function refreshShelfViewsAfterMutation() {
  await loadShelf({ fresh: true });
  Promise.resolve(loadAiInsight({ fresh: true })).catch((error) => {
    console.warn("AI insight refresh after mutation failed:", error);
  });
}

async function finalizeShelfAction(prepared, options = {}) {
  const {
    fallbackReply = "",
    fallbackLog = "操作完成",
    wantAudio = true,
    showChat = true,
  } = options;
  const { result: committed, message } = await dispatchPreparedShelfAction(prepared);
  const resolved = { ...(prepared || {}), ...(committed || {}) };
  rememberLocalShelfWatchEvent(resolved);
  const reply = getReplyText(committed, getReplyText(prepared, fallbackReply));

  log(message || resolved.msg || fallbackLog);
  await refreshShelfViewsAfterMutation();

  if (reply && showChat) {
    chat(TXT.bot, reply);
  }
  if (reply && wantAudio) {
    await playTtsReply(reply);
  }

  return {
    result: resolved,
    message,
    reply,
  };
}

async function loadShelf(options = {}) {
  const grid = getById("shelf_grid");
  try {
    const url = options?.fresh ? `/api/compartments?t=${Date.now()}` : "/api/compartments";
    const envelope = await fetchJsonEnvelope(url, { cache: "no-store" });
    const { data } = requireEnvelopeSuccess(envelope, "书架加载失败");
    const compartments = Array.isArray(data) ? data : [];
    const nextSignature = JSON.stringify(
      compartments.map((it) => [it.cid, it.status, it.book || ""])
    );
    if (options?.silent && _shelfSignature && nextSignature === _shelfSignature) {
      return false;
    }
    _shelfSignature = nextSignature;

    grid.innerHTML = "";
    let used = 0;

    compartments.forEach((it) => {
      const cell = document.createElement("div");
      cell.className = `shelf-cell animate-scale-in ${it.book ? "occupied" : "free"}`;
      if (it.book) {
        used++;
        cell.innerHTML = `
          <div class="cell-icon"><i class="fa-solid fa-book"></i></div>
          <div class="cell-title">${it.book}</div>
          <div class="cell-cid">${it.cid} \u53f7\u683c</div>
        `;
        cell.onclick = () => takeBook(it.cid, it.book);
        cell.title = `\u70b9\u51fb\u53d6\u51fa\u300a${it.book}\u300b`;
      } else {
        cell.innerHTML = `
          <div class="cell-icon free"><i class="fa-regular fa-square-plus"></i></div>
          <div class="cell-title">\u7a7a\u95f2</div>
          <div class="cell-cid">${it.cid} \u53f7\u683c</div>
        `;
      }
      grid.appendChild(cell);
    });

    const total = compartments.length, free = total - used;
    getById("stats_bar").innerHTML = `
      <span class="pill pill-total"><i class="fa-solid fa-warehouse"></i> \u603b\u8ba1: ${total}</span>
      <span class="pill pill-used"><i class="fa-solid fa-book"></i> \u5df2\u5b58: ${used}</span>
      <span class="pill pill-free"><i class="fa-regular fa-square"></i> \u7a7a\u95f2: ${free}</span>
    `;
    // 更新左侧统计环
    const pct = total > 0 ? Math.round(used / total * 100) : 0;
    const circumference = 2 * Math.PI * 30; // r=30
    const fill = (pct / 100) * circumference;
    const ringFill = getById("ring-fill");
    if (ringFill) ringFill.setAttribute("stroke-dasharray", `${fill.toFixed(1)} ${circumference.toFixed(1)}`);
    const ringPct = getById("ring-pct");
    if (ringPct) ringPct.textContent = pct + "%";
    const statTotal = getById("stat-total"); if (statTotal) statTotal.textContent = total;
    const statUsed  = getById("stat-used");  if (statUsed)  statUsed.textContent  = used;
    const statFree  = getById("stat-free");  if (statFree)  statFree.textContent  = free;
    // 缓存供弹窗使用
    window._shelfData = compartments;
    return true;
  } catch (e) {
    if (!options?.silent) {
      grid.innerHTML = `<div class="error-placeholder">\u52a0\u8f7d\u5931\u8d25: ${e.message}</div>`;
    }
    return false;
  }
}

async function refreshShelfIfChanged() {
  if (_shelfRefreshInFlight || document.hidden) return;
  _shelfRefreshInFlight = true;
  try {
    await loadShelf({ fresh: true, silent: true });
  } finally {
    _shelfRefreshInFlight = false;
  }
}

function startShelfAutoRefresh() {
  window.setInterval(refreshShelfIfChanged, SHELF_AUTO_REFRESH_MS);
  window.addEventListener("focus", refreshShelfIfChanged);
  window.addEventListener("pageshow", refreshShelfIfChanged);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) {
      refreshShelfIfChanged();
    }
  });
}

async function loadAiInsight(options = {}) {
  const box = getById("ai_insight_box");
  if (!box) return;
  try {
    const url = options?.fresh ? `/api/ai_insight?t=${Date.now()}` : "/api/ai_insight";
    const envelope = await fetchJsonEnvelope(url, { cache: "no-store" });
    const { data } = requireEnvelopeSuccess(envelope, "AI insight 加载失败");
    box.innerHTML = data?.insight || "";
  } catch (_) {
    box.innerText = "\u9986\u957f\u6682\u65f6\u65e0\u6cd5\u8fde\u63a5\u5230\u5927\u8111\u3002";
  }
}

async function storeByOcr(fromText, isAudio = false, opts = {}) {
  const options = opts || {};
  const source = options.source || "ui";
  const wantAudio = Boolean(options.wantAudio);
  const skipChat = Boolean(options.skipChat);
  const scanSpine = options.scanSpine ?? Boolean(getById("scan-spine")?.checked);
  const scanHint = scanSpine
    ? "\u8bf7\u5c06\u4e66\u810a\u5bf9\u51c6\u6444\u50cf\u5934\uff0c\u7cfb\u7edf\u5c06\u81ea\u52a8\u62cd\u7167"
    : "\u8bf7\u5c06\u5c01\u9762\u4e66\u540d\u548c\u4f5c\u8005\u533a\u57df\u5bf9\u51c6\u6444\u50cf\u5934\uff0c\u7cfb\u7edf\u5c06\u81ea\u52a8\u62cd\u7167";
  if (!skipChat) {
    chat(isAudio ? TXT.userVoice : TXT.user, fromText, isAudio);
  }
  log(scanSpine ? "\u51c6\u5907\u6253\u5f00\u6444\u50cf\u5934\u8fdb\u884c\u4e66\u810a\u5b58\u4e66\u626b\u63cf..." : "\u51c6\u5907\u6253\u5f00\u6444\u50cf\u5934\u8fdb\u884c\u5c01\u9762\u5b58\u4e66\u626b\u63cf...");

  const btnStore = getById("btn-store");
  const oldBtn = btnStore ? btnStore.innerHTML : "";
  if (btnStore) {
    btnStore.disabled = true;
    btnStore.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> \u6253\u5f00\u6444\u50cf\u5934...';
  }

  let stream = null;
  let capturedBlob = null;
  let preparedFromScan = null;

  const openBrowserCamera = async (video) => {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      throw new Error("当前浏览器不支持摄像头访问，请使用支持摄像头的浏览器。");
    }
    stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: { ideal: "environment" } },
      audio: false,
    });
    video.srcObject = stream;
    video.muted = true;
    video.playsInline = true;
    await video.play();
    await new Promise((resolve) => setTimeout(resolve, 600));
  };

  const captureBrowserCameraFrame = async () => {
    const video = document.createElement("video");
    video.autoplay = true;
    await openBrowserCamera(video);
    const width = video.videoWidth || 1280;
    const height = video.videoHeight || 720;
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext("2d");
    ctx.drawImage(video, 0, 0, width, height);
    return new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.92));
  };

  const buildOcrParams = () => {
    const params = new URLSearchParams({ source });
    if (wantAudio) params.set("audio", "1");
    if (scanSpine) params.set("scan_spine", "1");
    return params;
  };

  const ingestCaptureBlob = async (blob) => {
    const form = new FormData();
    form.append("image", blob, "capture.jpg");
    const url = `/api/ocr/ingest?${buildOcrParams().toString()}`;
    const envelope = await fetchJsonEnvelope(url, { method: "POST", body: form });
    const { data } = requireEnvelopeSuccess(envelope, "\u5b58\u4e66\u5931\u8d25");
    return data || {};
  };

  try {
    if (typeof Swal === "undefined") {
      // Fallback: no modal, auto-capture a frame.
      capturedBlob = await captureBrowserCameraFrame();
    } else {
    const autoCapture = () =>
      new Promise((resolve) => {
        let closed = false;
        let resolved = false;
        let scanTimer = null;
        const finish = (prepared) => {
          if (resolved) return;
          resolved = true;
          closed = true;
          resolve(prepared || null);
          Swal.close();
        };
        const cancel = () => {
          if (resolved) return;
          resolved = true;
          closed = true;
          resolve(null);
        };

        Swal.fire({
          title: "\u81ea\u52a8\u626b\u63cf\u4e2d",
          html: `
            <div style="display:flex;flex-direction:column;gap:10px;align-items:center;">
              <div style="position:relative;width:100%;max-width:420px;aspect-ratio:16/9;">
                <video id="camera_stream" autoplay playsinline muted style="width:100%;height:100%;object-fit:fill;border-radius:12px;border:1px solid #ddd;"></video>
                <canvas id="yolo_roi_overlay" style="position:absolute;inset:0;width:100%;height:100%;pointer-events:none;"></canvas>
              </div>
              <div id="yolo_roi_status" style="font-size:12px;color:#8e735b;">YOLO \u6b63\u5728\u5b9a\u4f4d ROI...</div>
              <div style="font-size:12px;color:#666;">${scanHint}</div>
            </div>
          `,
          showCancelButton: true,
          showConfirmButton: false,
          cancelButtonText: "\u53d6\u6d88",
          background: "#fefae0",
          didOpen: async () => {
            const video = document.getElementById("camera_stream");
            const overlay = document.getElementById("yolo_roi_overlay");
            const roiStatus = document.getElementById("yolo_roi_status");
            try {
              if (video) {
                await openBrowserCamera(video);
              }
            } catch (error) {
              if (roiStatus) {
                roiStatus.textContent = error?.message || "浏览器摄像头打开失败";
              }
              return;
            }

            const width = 640;
            const height = 360;
            const canvas = document.createElement("canvas");
            canvas.width = width;
            canvas.height = height;
            const ctx = canvas.getContext("2d");
            let roiBusy = false;
            let ingestBusy = false;
            let lastRoiAt = 0;
            let lastIngestAt = 0;
            let lastBoxesCount = 0;

            const clearRoiBoxes = () => {
              if (!overlay) return;
              const rect = overlay.getBoundingClientRect();
              overlay.width = Math.max(1, Math.round(rect.width));
              overlay.height = Math.max(1, Math.round(rect.height));
              const overlayCtx = overlay.getContext("2d");
              overlayCtx.clearRect(0, 0, overlay.width, overlay.height);
            };

            const drawRoiBoxes = (payload) => {
              if (!overlay) return;
              const rect = overlay.getBoundingClientRect();
              overlay.width = Math.max(1, Math.round(rect.width));
              overlay.height = Math.max(1, Math.round(rect.height));
              const overlayCtx = overlay.getContext("2d");
              overlayCtx.clearRect(0, 0, overlay.width, overlay.height);
              const boxes = Array.isArray(payload?.boxes) ? payload.boxes : [];
              lastBoxesCount = boxes.length;
              if (!boxes.length) {
                if (roiStatus) {
                  roiStatus.textContent = scanSpine ? "\u672a\u68c0\u6d4b\u5230\u4e66\u810a\u6846" : "\u672a\u68c0\u6d4b\u5230\u5c01\u9762\u6807\u9898/\u4f5c\u8005\u6846";
                }
                return;
              }

              const imageWidth = payload.image_width || width;
              const imageHeight = payload.image_height || height;
              const sx = overlay.width / imageWidth;
              const sy = overlay.height / imageHeight;
              overlayCtx.lineWidth = 2;
              overlayCtx.font = "12px sans-serif";
              overlayCtx.textBaseline = "top";

              boxes.forEach((box) => {
                const x = box.x1 * sx;
                const y = box.y1 * sy;
                const w = (box.x2 - box.x1) * sx;
                const h = (box.y2 - box.y1) * sy;
                const label = `${box.cls_name || "ROI"} ${Math.round((box.conf || 0) * 100)}%`;
                overlayCtx.strokeStyle = scanSpine ? "#2a9d8f" : "#d35f3f";
                overlayCtx.fillStyle = overlayCtx.strokeStyle;
                overlayCtx.strokeRect(x, y, w, h);
                const labelWidth = Math.min(overlayCtx.measureText(label).width + 8, overlay.width - x);
                const labelY = Math.max(0, y - 18);
                overlayCtx.fillRect(x, labelY, labelWidth, 18);
                overlayCtx.fillStyle = "#fff";
                overlayCtx.fillText(label, x + 4, labelY + 3);
              });

              if (roiStatus) {
                roiStatus.textContent = `YOLO \u5df2\u68c0\u6d4b\u5230 ${boxes.length} \u4e2a ROI`;
              }
            };

            const requestRoiBoxes = (blob) => {
              if (closed || roiBusy || ingestBusy || !blob) return;
              roiBusy = true;
              const form = new FormData();
              form.append("image", blob, "preview.jpg");
              const roiParams = new URLSearchParams();
              if (scanSpine) roiParams.set("scan_spine", "1");
              const query = roiParams.toString();
              fetchJsonEnvelope(`/api/ocr/rois${query ? `?${query}` : ""}`, { method: "POST", body: form })
                .then((envelope) => {
                  if (closed) return;
                  const { data } = requireEnvelopeSuccess(envelope, "YOLO ROI \u68c0\u6d4b\u5931\u8d25");
                  drawRoiBoxes(data || {});
                })
                .catch((err) => {
                  if (!closed && roiStatus) {
                    roiStatus.textContent = err?.message || "YOLO ROI \u68c0\u6d4b\u5931\u8d25";
                    clearRoiBoxes();
                  }
                })
                .finally(() => {
                  roiBusy = false;
                });
            };

            const requestStoreFromFrame = (blob) => {
              if (closed || ingestBusy || !blob) return;
              ingestBusy = true;
              if (roiStatus) {
                roiStatus.textContent = "\u68c0\u6d4b\u5230 ROI\uff0c\u6b63\u5728 OCR \u8bc6\u522b...";
              }
              ingestCaptureBlob(blob)
                .then((prepared) => {
                  if (closed) return;
                  finish(prepared);
                })
                .catch((err) => {
                  if (!closed && roiStatus) {
                    const message = err?.message || "\u672a\u8bc6\u522b\u5230\u6587\u5b57";
                    roiStatus.textContent = `${message}\uff0c\u8bf7\u8c03\u6574\u89d2\u5ea6\u6216\u5149\u7ebf\uff0c\u7ee7\u7eed\u626b\u63cf\u4e2d...`;
                  }
                })
                .finally(() => {
                  ingestBusy = false;
                });
            };

            scanTimer = setInterval(() => {
              if (closed) {
                clearInterval(scanTimer);
                return;
              }
              const cameraReady = video instanceof HTMLVideoElement
                ? video.readyState >= 2
                : Boolean(video?.naturalWidth > 0);
              if (!video || !cameraReady) return;
              ctx.drawImage(video, 0, 0, width, height);
              const now = Date.now();
              if (now - lastRoiAt >= 700) {
                lastRoiAt = now;
                canvas.toBlob((blob) => requestRoiBoxes(blob), "image/jpeg", 0.72);
              }
              if (lastBoxesCount > 0 && now - lastIngestAt >= 1800) {
                lastIngestAt = now;
                canvas.toBlob((blob) => requestStoreFromFrame(blob), "image/jpeg", 0.92);
              }
            }, 250);
          },
          willClose: () => {
            closed = true;
            if (scanTimer) {
              clearInterval(scanTimer);
            }
            const camera = document.getElementById("camera_stream");
            if (camera) {
              camera.srcObject = null;
              camera.removeAttribute("src");
            }
            if (stream) {
              stream.getTracks().forEach((t) => t.stop());
            }
            cancel();
          }
        });
      });

    preparedFromScan = await autoCapture();
    }
    if (!capturedBlob && !preparedFromScan) {
      return;
    }

    const prepared = preparedFromScan || await ingestCaptureBlob(capturedBlob);
    await finalizeShelfAction(prepared, {
      fallbackReply: "好的，已完成存书。",
      fallbackLog: "存书完成",
      wantAudio: true,
    });
  } catch (e) {
    log(`\u5b58\u4e66\u5931\u8d25: ${e.message}`);
    if (typeof Swal !== "undefined") {
      Swal.fire({
        toast: true,
        position: "top-end",
        icon: "error",
        title: e.message || "\u5b58\u4e66\u5931\u8d25",
        showConfirmButton: false,
        timer: 2200,
        background: "#fefae0"
      });
    }
  } finally {
    if (btnStore) {
      btnStore.disabled = false;
      btnStore.innerHTML = oldBtn;
    }
    if (stream) {
      stream.getTracks().forEach((t) => t.stop());
    }
  }
}

async function takeBook(cid, title) {
  try {
    const result = await Swal.fire({
      title: "\u786e\u8ba4\u53d6\u4e66",
      html: `\u786e\u5b9a\u53d6\u51fa <b>${cid}\u53f7\u683c</b> \u7684\u300a${title}\u300b\u5417\uff1f`,
      icon: "question",
      showCancelButton: true,
      confirmButtonColor: "#8e735b",
      cancelButtonColor: "#a3b18a",
      confirmButtonText: "\u786e\u8ba4",
      cancelButtonText: "\u53d6\u6d88",
      background: "#fefae0",
      color: "#555"
    });

    if (!result.isConfirmed) return;

    const envelope = await fetchJsonEnvelope("/api/take", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cid, title })
    });
    const { data } = requireEnvelopeSuccess(envelope, "\u53d6\u4e66\u5931\u8d25");
    const prepared = data || {};
    await finalizeShelfAction(prepared, {
      fallbackReply: `已为你取出《${title}》`,
      fallbackLog: "\u53d6\u4e66\u5b8c\u6210",
      wantAudio: true,
    });
  } catch (e) {
    log(`\u53d6\u4e66\u5931\u8d25: ${e.message}`);
  }
}

async function takeByTextIntent(text, isAudio = false, opts = {}) {
  const skipChat = Boolean(opts?.skipChat);
  const wantAudio = "wantAudio" in opts ? Boolean(opts.wantAudio) : isAudio;
  if (!skipChat) {
    chat(isAudio ? TXT.userVoice : TXT.user, text, isAudio);
  }
  try {
    const envelope = await fetchJsonEnvelope("/api/take_by_text", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text })
    });
    const { data } = requireEnvelopeSuccess(envelope, "\u53d6\u4e66\u5931\u8d25");
    const prepared = data || {};
    await finalizeShelfAction(prepared, {
      fallbackReply: prepared?.picked ? `已为你取出《${prepared.picked.title}》` : "已执行取书指令",
      fallbackLog: "\u53d6\u4e66\u5b8c\u6210",
      wantAudio,
    });
  } catch (e) {
    log(e.message || "\u53d6\u4e66\u6307\u4ee4\u6267\u884c\u5931\u8d25");
  }
}

getById("btn-store").onclick = () => storeByOcr("\u5e2e\u6211\u5b58\u4e66");

getById("btn-search").onclick = async function () {
  const input = getById("search_input");
  const kw = input.value.trim();
  if (!kw) return;

  const envelope = await fetchJsonEnvelope("/api/compartments");
  const { data } = requireEnvelopeSuccess(envelope, "书架加载失败");
  const compartments = Array.isArray(data) ? data : [];
  const hit = compartments.find((i) => i.book && i.book.includes(kw));
  if (hit) takeBook(hit.cid, hit.book);
  input.value = "";
};

const uvModuleBtn = getById("btn-uv-sterilize");
if (uvModuleBtn) {
  uvModuleBtn.onclick = () => runModuleAction("uv");
}

const laminateModuleBtn = getById("btn-book-laminate");
if (laminateModuleBtn) {
  laminateModuleBtn.onclick = () => runModuleAction("laminate");
}

function isStoreIntent(text) {
  const t = String(text || "");
  // 至少要有"书"字，避免"归还你的好意"这种被误判
  const keywords = ["存书", "放回书", "归还书", "还书", "我要存书", "帮我存书", "上架"];
  return keywords.some((k) => t.includes(k));
}

function isTakeIntent(text) {
  const t = String(text || "");
  // 必须同时含有"书"或书名相关词，避免"背景介绍"等被误判
  const actionWords = ["取书", "拿书", "借书", "我要取", "帮我取", "帮我拿", "我想拿"];
  const hasAction = actionWords.some((k) => t.includes(k));
  // 纯动作词（没有书名线索）且文字很短才触发，避免聊天句子被误判
  if (!hasAction) return false;
  // "帮我取"后面必须还有内容（书名），不能只是感叹词
  if ((t.includes("帮我取") || t.includes("我要取")) && t.length < 5) return false;
  return true;
}

async function sendTextMessage() {
  const input = getById("chat_input");
  const text = input.value.trim();
  if (!text) return;
  input.value = "";

  if (isStoreIntent(text)) {
    await storeByOcr(text, false);
    return;
  }

  if (isTakeIntent(text)) {
    await takeByTextIntent(text, false, { wantAudio: true });
    return;
  }

  chat(TXT.user, text);
  try {
    const envelope = await fetchJsonEnvelope("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text })
    });
    const { data } = requireEnvelopeSuccess(envelope, "聊天失败");
    if (data?.reply) {
      chat(TXT.bot, data.reply);
      if (data?.audio_b64) {
        await playBase64Audio(data.audio_b64, data.audio_format || "mp3");
      } else {
        await playTtsReply(data.reply);
      }
    }
  } catch (_) {
    chat(TXT.sys, "\u8fde\u63a5 AI \u670d\u52a1\u5931\u8d25\uff0c\u8bf7\u7a0d\u540e\u518d\u8bd5\u3002");
  }
}

getById("chat_input").onkeydown = (e) => {
  if (e.key === "Enter") sendTextMessage();
};
getById("btn-send-text").onclick = sendTextMessage;

// Browser mic loop (requires user gesture once).
let micStream = null;
let micActive = false;
let micSending = false;
const WAKE_WINDOW_MS = 15000;
let wakeActiveUntil = 0;
let _sleepTimer = null;          // 30秒无操作自动休眠计时器
const SLEEP_AFTER_MS = WAKE_WINDOW_MS;    // 15秒无语音后退出唤醒态

function isWakeActive() {
  return Date.now() < wakeActiveUntil;
}

function setWakeActive() {
  const wasActive = isWakeActive();
  wakeActiveUntil = Date.now() + WAKE_WINDOW_MS;
  updateMicStatus();
  scheduleSleep();
  if (!wasActive) {
    // 首次唤醒才播音效和显示动画
    playWakeSound();
    showWakeOverlay();
  }
}

function clearWakeActive() {
  const wasActive = isWakeActive();
  wakeActiveUntil = 0;
  if (_sleepTimer) { clearTimeout(_sleepTimer); _sleepTimer = null; }
  updateMicStatus();
  if (wasActive) playSleepSound();
  hideWakeOverlay();
}

function scheduleSleep() {
  // 清除上一个倒计时，重新开始
  if (_sleepTimer) clearTimeout(_sleepTimer);
  _sleepTimer = setTimeout(async () => {
    if (!isWakeActive()) return;  // 已经手动退出了，不重复处理
    // 1. 请求服务端生成"再见主人"语音
    try {
      const envelope = await fetchJsonEnvelope("/api/tts_say", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: "再见主人，有需要再叫我哦" })
      });
      const { data } = requireEnvelopeSuccess(envelope, "语音播报失败");
      if (data?.audio_b64) {
        playBase64Audio(data.audio_b64, data.audio_format || "mp3");
      }
    } catch (_) {}
    // 2. 聊天框显示告别
    chat(TXT.bot, "再见主人，有需要再叫我哦～");
    // 3. 重置唤醒状态
    clearWakeActive();
  }, SLEEP_AFTER_MS);
}

function updateMicStatus() {
  const status = getById("mic-status");
  const wave = getById("mic-wave");
  const wakeInd = getById("wake-indicator");
  const btn = getById("btn-mic");

  if (!micActive) {
    if (status) status.textContent = "未启用麦克风";
    if (wave) wave.classList.add("hidden");
    if (wakeInd) wakeInd.classList.add("hidden");
    if (btn) { btn.classList.remove("mic-on"); btn.innerHTML = '<i class="fa-solid fa-microphone-slash"></i> 启用麦克风'; }
    return;
  }

  if (wave) wave.classList.remove("hidden");
  if (btn) { btn.classList.add("mic-on"); btn.innerHTML = '<i class="fa-solid fa-microphone"></i> 麦克风监听中'; }

  if (isWakeActive()) {
    if (status) status.textContent = "";
    if (wakeInd) wakeInd.classList.remove("hidden");
  } else {
    if (status) status.textContent = '说"小燕小燕"唤醒助手';
    if (wakeInd) wakeInd.classList.add("hidden");
  }
}

async function startMicLoop() {
  if (micActive) return;
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    log("当前浏览器不支持麦克风访问");
    return;
  }

  try {
    micStream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
  } catch (e) {
    log(`麦克风权限失败: ${e.message}`);
    return;
  }

  micActive = true;
  clearWakeActive();

  const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  const source = audioCtx.createMediaStreamSource(micStream);
  const processor = audioCtx.createScriptProcessor(4096, 1, 1);
  source.connect(processor);
  processor.connect(audioCtx.destination);

  let buffers = [];
  let speaking = false;
  let silenceMs = 0;
  let lastChunkTs = performance.now();
  let flushing = false;   // 新增：防止重复flush的锁

  const flush = async (chunks) => {
    if (!chunks || chunks.length === 0) return;
    if (flushing || micSending) return;   // 双重锁，任何一个在发送中都直接丢弃
    const minSamples = audioCtx.sampleRate * 0.4;
    const totalSamples = chunks.reduce((s, b) => s + b.length, 0);
    if (totalSamples < minSamples) return;
    flushing = true;
    try {
      const wavBlob = encodeWav(chunks, audioCtx.sampleRate);
      const form = new FormData();
      form.append("audio", wavBlob, "audio.wav");
      const mode = isWakeActive() ? "command" : "wake";
      // 把当前书架书名传给后端，辅助ASR识别
      try {
        const shelfEnvelope = await fetchJsonEnvelope(`/api/compartments?t=${Date.now()}`, { cache: "no-store" });
        const { data: shelfData } = requireEnvelopeSuccess(shelfEnvelope, "书架加载失败");
        const titles = (Array.isArray(shelfData) ? shelfData : []).filter(i => i.book).map(i => i.book).join(",");
        if (titles) form.append("hints_extra", titles);
      } catch (_) {}
      const envelope = await fetchJsonEnvelope(`/api/voice/ingest?source=web&mode=${mode}`, { method: "POST", body: form });
      const result = envelope.data || {};

      if (result.ignore) return;

      if (result.wake && result.intent === "wake") {
        setWakeActive();
        if (result.reply) {
          chat(TXT.bot, result.reply);
          if (result.audio_b64) {
            playBase64Audio(result.audio_b64, result.audio_format || "mp3");
          }
        }
        return;
      }

      if (result.need_image) {
        setWakeActive();
        if (result.reply) {
          chat(TXT.bot, result.reply);
          if (result.audio_b64) {
            playBase64Audio(result.audio_b64, result.audio_format || "mp3");
          }
        }
        await storeByOcr(result.text || "帮我存书", true, { source: "web", wantAudio: true, skipChat: true });
        return;
      }

      if (!envelope.ok) {
        const message = (envelope.message || "").trim();
        if (message && message !== "no speech detected") {
          log(message);
        }
        return;
      }

      if (result.text) {
        chat(TXT.userVoice, result.text, true);
        // 检测语音里是否有切换用户意图
        if (window._onVoiceResult) window._onVoiceResult(result.text);
      }
      let resolvedResult = result;
      let operationMessage = envelope.message || "";

      if (result.dispatch_request && result.commit_request) {
        const finalized = await finalizeShelfAction(result, {
          fallbackReply: getReplyText(result, ""),
          fallbackLog: operationMessage || "操作完成",
          wantAudio: true,
        });
        resolvedResult = finalized.result;
        if (resolvedResult.intent && resolvedResult.intent !== "wake") {
          setWakeActive();
        }
        return;
      }

      // msg 是操作结果（存书/取书），进操作日志
      // reply/ai_reply 是 AI 的话术，进聊天框
      let operationLogged = false;
      if (operationMessage && operationMessage !== resolvedResult.reply) {
        log(operationMessage);
        operationLogged = true;
      }

      // ai_reply 是 AI 人格化回复 → 聊天框
      // msg 是操作结果（如"已为你取出..."）→ 日志，不进聊天框
      const aiSpeech = resolvedResult.ai_reply || (resolvedResult.intent === "chat" ? resolvedResult.reply : null);
      const opResult = (resolvedResult.intent === "store" || resolvedResult.intent === "take") ? (operationMessage || resolvedResult.reply) : null;
      if (opResult && !operationLogged) log(opResult);
      if (aiSpeech) {
        chat(TXT.bot, aiSpeech);
        if (resolvedResult.audio_b64) {
          playBase64Audio(resolvedResult.audio_b64, resolvedResult.audio_format || "mp3");
        } else if (resolvedResult.dispatch_request && resolvedResult.commit_request) {
          await playTtsReply(aiSpeech);
        }
      } else if (!opResult && resolvedResult.reply) {
        // 兜底：既不是操作结果也没有ai_reply，才直接显示reply
        chat(TXT.bot, resolvedResult.reply);
        if (resolvedResult.audio_b64) {
          playBase64Audio(resolvedResult.audio_b64, resolvedResult.audio_format || "mp3");
        } else if (resolvedResult.dispatch_request && resolvedResult.commit_request) {
          await playTtsReply(resolvedResult.reply);
        }
      }

      if (resolvedResult.intent && resolvedResult.intent !== "wake") {
        setWakeActive();
        // 语音指令执行后立刻刷新书架
        if (resolvedResult.intent === "store" || resolvedResult.intent === "take") {
          await refreshShelfViewsAfterMutation();
        }
      }
    } catch (e) {
      console.error("Voice upload failed:", e);
      log(`语音上传失败: ${normalizeErrorMessage(e)}`);
    } finally {
      micSending = false;
      flushing = false;
    }
  };

  processor.onaudioprocess = (event) => {
    const input = event.inputBuffer.getChannelData(0);
    const now = performance.now();
    const rms = Math.sqrt(input.reduce((sum, v) => sum + v * v, 0) / input.length);
    const threshold = 0.015;
    const chunkMs = now - lastChunkTs;
    lastChunkTs = now;

    if (rms > threshold) {
      speaking = true;
      silenceMs = 0;
      buffers.push(new Float32Array(input));  // 只在说话时累积
    } else if (speaking) {
      silenceMs += chunkMs;
      buffers.push(new Float32Array(input));  // 静音过渡期也纳入（保留尾音）

      const silenceLimit = isWakeActive() ? 1200 : 800;
      if (silenceMs > silenceLimit) {
        speaking = false;
        silenceMs = 0;
        const toSend = buffers.splice(0);     // 原子取走并清空
        flush(toSend);                        // 传入本次录音数据
      }
    }
    // 非说话、非过渡期：什么都不做，buffers保持空
  };
}

function encodeWav(chunks, sampleRate) {
  let length = 0;
  chunks.forEach((c) => (length += c.length));
  const buffer = new ArrayBuffer(44 + length * 2);
  const view = new DataView(buffer);

  const writeString = (offset, str) => {
    for (let i = 0; i < str.length; i++) view.setUint8(offset + i, str.charCodeAt(i));
  };

  writeString(0, "RIFF");
  view.setUint32(4, 36 + length * 2, true);
  writeString(8, "WAVE");
  writeString(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeString(36, "data");
  view.setUint32(40, length * 2, true);

  let offset = 44;
  chunks.forEach((chunk) => {
    for (let i = 0; i < chunk.length; i++) {
      const s = Math.max(-1, Math.min(1, chunk[i]));
      view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true);
      offset += 2;
    }
  });
  return new Blob([view], { type: "audio/wav" });
}

function playBase64Audio(b64, fmt) {
  if (!b64) return Promise.resolve(false);
  const byteCharacters = atob(b64);
  const byteNumbers = new Array(byteCharacters.length);
  for (let i = 0; i < byteCharacters.length; i++) {
    byteNumbers[i] = byteCharacters.charCodeAt(i);
  }
  const byteArray = new Uint8Array(byteNumbers);
  const blob = new Blob([byteArray], { type: fmt === "wav" ? "audio/wav" : "audio/mpeg" });
  const url = URL.createObjectURL(blob);
  const audio = new Audio(url);
  activeAudioPlayers.add(audio);

  const cleanup = () => {
    activeAudioPlayers.delete(audio);
    URL.revokeObjectURL(url);
  };

  audio.onended = cleanup;
  audio.onerror = cleanup;

  const playPromise = audio.play();
  if (!playPromise || typeof playPromise.then !== "function") {
    return Promise.resolve(true);
  }

  return playPromise
    .then(() => true)
    .catch((error) => {
      cleanup();
      console.warn("Audio playback failed:", error);
      return false;
    });
}

const micBtn = getById("btn-mic");
if (micBtn) {
  micBtn.onclick = async () => {
    micBtn.disabled = true;
    micBtn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> 启用中...';
    await startMicLoop();
    micBtn.disabled = false;
    updateMicStatus();
  };
}

loadShelf();
startShelfAutoRefresh();
loadAiInsight();
loadClimate();
setInterval(loadClimate, 300000);
setTimeout(() => chat(TXT.bot, TXT.welcome), 500);

// ===== SSE 实时推送（替代轮询）=====
function connectVoiceStream() {
  const evtSource = new EventSource("/api/voice_stream");

  evtSource.onmessage = async (e) => {
    let ev;
    try { ev = JSON.parse(e.data); } catch (_) { return; }
    if (ev.type === "connected") return;  // 心跳包忽略

    // 和之前轮询一样的分流逻辑
    if (ev.role === "log") {
      log(ev.text);
    } else if (ev.role === "user") {
      chat(TXT.userVoice, ev.text, true);
    } else if (ev.role === "assistant") {
      const isShelfWatchEvent = ev.source === "shelf_watch";
      const shouldSuppressShelfWatch = isShelfWatchEvent && shouldSuppressShelfWatchEvent(ev);
      const isOpResult = /已为你取出|已存入|存入|取出|书柜里没有|书柜已满/.test(ev.text);
      const shouldRefreshShelf = isShelfWatchEvent || isOpResult;
      const replyText = String(ev.text || "").trim();

      if (shouldSuppressShelfWatch) {
        await refreshShelfViewsAfterMutation();
        return;
      }

      if (replyText) {
        chat(TXT.bot, replyText);
      }
      if (shouldRefreshShelf) {
        log(ev.op_text || replyText);
        await refreshShelfViewsAfterMutation();
        if (isShelfWatchEvent && replyText && replyText !== ev.op_text) {
          await playTtsReply(replyText);
        }
      }
    }
  };

  evtSource.onerror = () => {
    // 断线后 3 秒自动重连
    evtSource.close();
    setTimeout(connectVoiceStream, 3000);
  };
}

connectVoiceStream();

// 每秒检查唤醒窗口是否自然到期，及时更新界面
setInterval(() => {
  if (!micActive) return;
  const wasShowing = getById("wake-overlay") && !getById("wake-overlay").classList.contains("hidden");
  if (wasShowing && !isWakeActive()) {
    // 唤醒窗口自然到期，清理界面（不播告别音，scheduleSleep 会处理）
    hideWakeOverlay();
    updateMicStatus();
  }
}, 1000);

// ===== 今日操作计数（已在顶部声明）=====

// ===== 统计弹窗 =====
async function openStatsModal() {
  const modal = document.getElementById("stats-modal");
  if (!modal) return;
  modal.classList.remove("hidden");

  const data = window._shelfData || [];
  const used = data.filter(i => i.book).length;
  const total = data.length;
  const pct = total > 0 ? Math.round(used / total * 100) : 0;

  const el = (id, val) => { const e = document.getElementById(id); if (e) e.textContent = val; };
  el("rpt-total", total);
  el("rpt-used",  used);
  el("rpt-pct",   pct + "%");
  el("rpt-ops",   _todayOps);

  const listEl = document.getElementById("rpt-booklist");
  if (listEl) {
    const books = data.filter(i => i.book);
    if (books.length === 0) {
      listEl.innerHTML = '<span class="book-tag-empty">书柜暂无藏书</span>';
    } else {
      listEl.innerHTML = books.map(i =>
        `<span class="book-tag"><i class="fa-solid fa-book" style="font-size:10px;margin-right:4px;opacity:.6"></i>${i.book}</span>`
      ).join("");
    }
  }

  const insightEl = document.getElementById("rpt-insight");
  if (insightEl) {
    insightEl.textContent = "加载中...";
    try {
      const envelope = await fetchJsonEnvelope("/api/ai_insight");
      const { data } = requireEnvelopeSuccess(envelope, "AI insight 加载失败");
      insightEl.textContent = data?.insight || "馆长暂时没有留言～";
    } catch (_) {
      insightEl.textContent = "馆长暂时无法连线，稍后再试。";
    }
  }
}

const _btnStatsDetail = document.getElementById("btn-stats-detail");
if (_btnStatsDetail) _btnStatsDetail.onclick = openStatsModal;

const _btnCloseModal = document.getElementById("btn-close-modal");
if (_btnCloseModal) _btnCloseModal.onclick = () => {
  const modal = document.getElementById("stats-modal");
  if (modal) modal.classList.add("hidden");
};

const _statsModal = document.getElementById("stats-modal");
if (_statsModal) _statsModal.onclick = (e) => {
  if (e.target === _statsModal) _statsModal.classList.add("hidden");
};

// ══════════════════════════════════════════
// 多用户体系
// ══════════════════════════════════════════

let _authState = null;
let _currentUser = null;
let _allUsers = [];

// 角色中文映射
const ROLE_LABEL = { parent: "家长", child: "孩子" };
const ROLE_COLOR = { parent: "#7c5c3e", child: "#9aad82" };
const SYSTEM_ROLE_LABEL = { admin: "管理员", user: "普通用户" };

const THEME_LABEL = {
  warm: "家庭温馨",
  slate: "科技炫酷",
  rose: "粉嫩甜心",
  forest: "森林清新",
  ocean: "海洋清爽",
  golden: "日落暖橙"
};

// 用户主题（沿用旧 key，视觉升级为主题风格）
const USER_THEMES = {
  warm: {
    accent: "#8b5e3c",
    accentH: "#6d452c",
    accent2: "#b7c79e",
    accent2H: "#9fb687",
    accentGlow: "rgba(139,94,60,.25)",
    bg: "#f7f1e8",
    bg2: "#efe4d4",
    card: "#fff9f1",
    line: "#e2d5c2",
    line2: "#cfbea6",
    text: "#3e2c20",
    text2: "#6b513f",
    muted: "#9c826c",
    freeBg: "#eef2e8",
    freeText: "#7a8f67",
    occupied: "#b65f2b",
    occupiedH: "#9f4f1f",
    brandGrad: "linear-gradient(135deg, #8b5e3c 0%, #6d452c 100%)",
    bgGrad: "radial-gradient(1200px 800px at 10% -10%, rgba(255,214,170,.35), transparent 60%), radial-gradient(900px 700px at 100% 10%, rgba(183,199,158,.25), transparent 55%)",
    cardGrad: "linear-gradient(180deg, rgba(255,255,255,.75) 0%, rgba(255,255,255,.15) 100%)"
  },
  slate: {
    accent: "#28d7ff",
    accentH: "#00a8d4",
    accent2: "#8b5cff",
    accent2H: "#6e47d9",
    accentGlow: "rgba(40,215,255,.3)",
    bg: "#e8f2ff",
    bg2: "#d6e8ff",
    card: "#f6f9ff",
    line: "#c5d8f2",
    line2: "#a9c1e6",
    text: "#1d2b44",
    text2: "#3a4b6a",
    muted: "#6b7f9d",
    freeBg: "#eef4ff",
    freeText: "#5a6f8f",
    occupied: "#2b7cff",
    occupiedH: "#1f65d4",
    brandGrad: "linear-gradient(135deg, #0f172a 0%, #2b7cff 55%, #28d7ff 100%)",
    bgGrad: "radial-gradient(900px 600px at 20% -10%, rgba(40,215,255,.35), transparent 60%), radial-gradient(900px 600px at 100% 0%, rgba(139,92,255,.25), transparent 55%)",
    cardGrad: "linear-gradient(180deg, rgba(255,255,255,.85) 0%, rgba(255,255,255,.35) 100%)"
  },
  rose: {
    accent: "#ff7aa2",
    accentH: "#e95b89",
    accent2: "#ffb3c7",
    accent2H: "#ff98b7",
    accentGlow: "rgba(255,122,162,.3)",
    bg: "#fff0f6",
    bg2: "#ffe1ee",
    card: "#fff7fb",
    line: "#f2c7d6",
    line2: "#e7b2c6",
    text: "#4a2b35",
    text2: "#6b3f4f",
    muted: "#9a6a7b",
    freeBg: "#fff4f8",
    freeText: "#a3576e",
    occupied: "#d95778",
    occupiedH: "#bf4767",
    brandGrad: "linear-gradient(135deg, #ff7aa2 0%, #e95b89 100%)",
    bgGrad: "radial-gradient(1200px 700px at 10% -10%, rgba(255,174,197,.4), transparent 60%), radial-gradient(800px 600px at 100% 0%, rgba(255,214,226,.4), transparent 55%)",
    cardGrad: "linear-gradient(180deg, rgba(255,255,255,.85) 0%, rgba(255,255,255,.25) 100%)"
  },
  forest: {
    accent: "#4a7c59",
    accentH: "#2f5b3d",
    accent2: "#9aad82",
    accent2H: "#849669",
    accentGlow: "rgba(74,124,89,.28)",
    bg: "#f0f5f1",
    bg2: "#e2ede5",
    card: "#f8fcf9",
    line: "#cfe0d6",
    line2: "#b3c7bb",
    text: "#2d3c33",
    text2: "#4d5c53",
    muted: "#7a8b80",
    freeBg: "#ecf3ee",
    freeText: "#6f8b78",
    occupied: "#5a7f4a",
    occupiedH: "#46663a",
    brandGrad: "linear-gradient(135deg, #4a7c59 0%, #2f5b3d 100%)",
    bgGrad: "radial-gradient(1200px 800px at 10% -10%, rgba(168,204,182,.35), transparent 60%), radial-gradient(800px 600px at 100% 0%, rgba(122,170,137,.28), transparent 55%)",
    cardGrad: "linear-gradient(180deg, rgba(255,255,255,.8) 0%, rgba(255,255,255,.2) 100%)"
  },
  ocean: {
    accent: "#2d6a9f",
    accentH: "#1e4f7a",
    accent2: "#5aa7c2",
    accent2H: "#4a93ae",
    accentGlow: "rgba(45,106,159,.28)",
    bg: "#eef5fb",
    bg2: "#ddebf6",
    card: "#f6fafc",
    line: "#c4d9ea",
    line2: "#aac3d8",
    text: "#1e3346",
    text2: "#3a4f63",
    muted: "#6b8297",
    freeBg: "#edf6fb",
    freeText: "#5a7c93",
    occupied: "#2f6ea6",
    occupiedH: "#235b8c",
    brandGrad: "linear-gradient(135deg, #2d6a9f 0%, #1e4f7a 100%)",
    bgGrad: "radial-gradient(1100px 700px at 10% -10%, rgba(137,190,216,.35), transparent 60%), radial-gradient(900px 600px at 100% 0%, rgba(86,167,194,.25), transparent 55%)",
    cardGrad: "linear-gradient(180deg, rgba(255,255,255,.8) 0%, rgba(255,255,255,.2) 100%)"
  },
  golden: {
    accent: "#f28c28",
    accentH: "#d47012",
    accent2: "#ffb703",
    accent2H: "#f59f00",
    accentGlow: "rgba(242,140,40,.3)",
    bg: "#fff3e5",
    bg2: "#ffe7cf",
    card: "#fff9f0",
    line: "#f0d1ad",
    line2: "#e3be94",
    text: "#4a341c",
    text2: "#6b4b2b",
    muted: "#9c7a57",
    freeBg: "#fff6ea",
    freeText: "#9a6a3f",
    occupied: "#d47012",
    occupiedH: "#b85c0d",
    brandGrad: "linear-gradient(135deg, #f28c28 0%, #d47012 100%)",
    bgGrad: "radial-gradient(1200px 700px at 10% -10%, rgba(255,200,120,.45), transparent 60%), radial-gradient(800px 600px at 100% 0%, rgba(255,183,3,.25), transparent 55%)",
    cardGrad: "linear-gradient(180deg, rgba(255,255,255,.85) 0%, rgba(255,255,255,.25) 100%)"
  }
};

function applyUserTheme(colorKey) {
  const theme = USER_THEMES[colorKey] || USER_THEMES.warm;
  const root = document.documentElement;
  const set = (k, v) => root.style.setProperty(k, v);
  set("--accent", theme.accent);
  set("--accent-h", theme.accentH);
  set("--accent-2", theme.accent2);
  set("--accent-2h", theme.accent2H);
  set("--accent-glow", theme.accentGlow);
  set("--bg", theme.bg);
  set("--bg-2", theme.bg2);
  set("--card", theme.card);
  set("--line", theme.line);
  set("--line-2", theme.line2);
  set("--text", theme.text);
  set("--text-2", theme.text2);
  set("--muted", theme.muted);
  set("--free-bg", theme.freeBg);
  set("--free-text", theme.freeText);
  set("--occupied", theme.occupied);
  set("--occupied-h", theme.occupiedH);
  set("--brand-grad", theme.brandGrad);
  set("--bg-grad", theme.bgGrad);
  set("--card-grad", theme.cardGrad);
}

// ── 加载当前身份 ──
async function loadCurrentUser() {
  try {
    const envelope = await fetchJsonEnvelope("/api/auth/me");
    const { data } = requireEnvelopeSuccess(envelope, "加载当前身份失败");
    _authState = data || null;
    _currentUser = data?.user || null;
    renderCurrentUser();
    await loadAllUsers();
  } catch (_) {
    window.location.href = "/";
  }
}

function renderCurrentUser() {
  if (!_currentUser) return;
  const avatar = document.getElementById("current-avatar");
  const name   = document.getElementById("current-name");
  const role   = document.getElementById("current-role");
  const meta   = document.getElementById("current-account-meta");
  const familyMeta = document.getElementById("user-family-meta");
  const manageBtn = document.getElementById("btn-manage-users");
  const systemRole = _authState?.account?.system_role || "user";
  const familyName = _currentUser.family_name || _authState?.cabinet?.family_name || "未加入家庭";
  if (avatar) avatar.textContent = _currentUser.avatar || "👤";
  if (name)   name.textContent   = _currentUser.name || "-";
  if (role) {
    role.textContent = `${ROLE_LABEL[_currentUser.role] || _currentUser.role} · ${SYSTEM_ROLE_LABEL[systemRole] || systemRole}`;
    role.style.color = ROLE_COLOR[_currentUser.role] || "var(--muted)";
  }
  if (meta) meta.textContent = `${familyName} · @${_authState?.account?.username || "-"}`;
  if (familyMeta) {
    familyMeta.innerHTML = `
      <div class="user-chip user-chip-active" style="cursor:default;">
        <span class="chip-avatar"><i class="fa-solid fa-house"></i></span>
        <span class="chip-name">${familyName}</span>
      </div>
    `;
  }
  if (manageBtn) manageBtn.classList.toggle("hidden", systemRole !== "admin");
  // 应用该用户的专属色调
  if (_currentUser.color) applyUserTheme(_currentUser.color);
  // 同步到右侧小燕面板
  const descEl = document.querySelector(".assistant-desc");
  if (descEl) descEl.textContent = `正在为 ${_currentUser.name} 服务`;
}

// ── 加载当前家庭成员（管理员专用） ──
async function loadAllUsers() {
  if ((_authState?.account?.system_role || "user") !== "admin") {
    _allUsers = [];
    renderUserManageList();
    return;
  }
  try {
    const envelope = await fetchJsonEnvelope("/api/users");
    const { data } = requireEnvelopeSuccess(envelope, "加载成员列表失败");
    _allUsers = Array.isArray(data) ? data : [];
    renderUserManageList();
  } catch (_) {}
}

// ── 用户管理弹窗 ──
function renderUserManageList() {
  const box = document.getElementById("user-list-manage");
  if (!box) return;
  if ((_authState?.account?.system_role || "user") !== "admin") {
    box.innerHTML = '<div style="color:var(--muted);font-size:13px;">仅管理员可以管理家庭角色。</div>';
    return;
  }
  if (!_allUsers.length) {
    box.innerHTML = '<div style="color:var(--muted);font-size:13px;">当前家庭还没有成员。</div>';
    return;
  }
  box.innerHTML = _allUsers.map(u => {
    const theme = USER_THEMES[u.color] || USER_THEMES.warm;
    return `
    <div class="user-manage-row">
      <div class="chip-avatar" style="font-size:22px;width:36px;height:36px;border-radius:50%;
           background:${theme.bg2};border:2px solid ${theme.accent};
           display:flex;align-items:center;justify-content:center;">${u.avatar || "👤"}</div>
      <div style="flex:1;">
        <div style="font-weight:600;font-size:14px;">${u.name}</div>
        <div style="font-size:12px;display:flex;align-items:center;gap:5px;">
          <span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${theme.accent};"></span>
          <span style="color:var(--muted);">${ROLE_LABEL[u.role] || u.role}</span>
          <span style="color:var(--muted);">·</span>
          <span style="color:var(--muted);">${THEME_LABEL[u.color] || "默认主题"}</span>
        </div>
      </div>
      <div style="display:flex;align-items:center;gap:8px;">
        <select id="role-select-${u.id}" class="input-field" style="width:92px;flex:none;padding:8px 10px;">
          <option value="parent" ${u.role === "parent" ? "selected" : ""}>家长</option>
          <option value="child" ${u.role === "child" ? "selected" : ""}>孩子</option>
        </select>
        <button class="btn-text-sm" onclick="saveUserRole(${u.id})">保存</button>
      </div>
    </div>`;
  }).join("");
}

async function saveUserRole(uid) {
  const select = document.getElementById(`role-select-${uid}`);
  const role = select?.value || "child";
  try {
    await requireEnvelopeSuccess(
      await fetchJsonEnvelope(`/api/users/${uid}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ role })
      }),
      "更新家庭角色失败"
    );
    if (typeof Swal !== "undefined") {
      Swal.fire({
        toast: true,
        position: "top-end",
        icon: "success",
        title: "角色已更新",
        showConfirmButton: false,
        timer: 1800,
        background: "#fefae0"
      });
    }
    await loadCurrentUser();
  } catch (e) {
    if (typeof Swal !== "undefined") {
      Swal.fire({
        toast: true,
        position: "top-end",
        icon: "error",
        title: e.message || "更新失败",
        showConfirmButton: false,
        timer: 2200,
        background: "#fefae0"
      });
    }
  }
}

window.saveUserRole = saveUserRole;

async function logoutCurrentSession() {
  try {
    await requireEnvelopeSuccess(
      await fetchJsonEnvelope("/api/auth/logout", { method: "POST" }),
      "退出登录失败"
    );
  } catch (_) {}
  window.location.href = "/";
}

// 主题选择交互
let _selectedTheme = "warm";
function syncThemePicker(themeKey) {
  const key = themeKey || "warm";
  _selectedTheme = key;
  document.querySelectorAll(".theme-option").forEach((s) => s.classList.toggle("active", s.dataset.theme === key));
}
document.addEventListener("click", (e) => {
  const option = e.target.closest(".theme-option");
  if (!option) return;
  document.querySelectorAll(".theme-option").forEach(s => s.classList.remove("active"));
  option.classList.add("active");
  _selectedTheme = option.dataset.theme || "warm";
  applyUserTheme(_selectedTheme);
});

// 添加新用户
const _btnAddUser = document.getElementById("btn-add-user");
if (_btnAddUser) _btnAddUser.onclick = async () => {
  const name   = (document.getElementById("new-user-name")?.value || "").trim();
  const role   = document.getElementById("new-user-role")?.value || "child";
  const avatar = (document.getElementById("new-user-avatar")?.value || "").trim() ||
                 (role === "parent" ? "👨" : "🧒");
  const color  = _selectedTheme || "warm";
  if (!name) { Swal.fire({ icon: "warning", title: "请输入姓名", timer: 1500, showConfirmButton: false }); return; }
  await requireEnvelopeSuccess(
    await fetchJsonEnvelope("/api/users", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, role, avatar, color })
    }),
    "新增成员失败"
  );
  document.getElementById("new-user-name").value = "";
  document.getElementById("new-user-avatar").value = "";
  syncThemePicker("warm");
  await loadAllUsers();
};

// 打开/关闭管理弹窗
const _btnManageUsers = document.getElementById("btn-manage-users");
if (_btnManageUsers) _btnManageUsers.onclick = () => {
  document.getElementById("user-modal")?.classList.remove("hidden");
};
const _btnLogout = document.getElementById("btn-logout");
if (_btnLogout) _btnLogout.onclick = () => {
  logoutCurrentSession();
};
const _btnCloseUserModal = document.getElementById("btn-close-user-modal");
if (_btnCloseUserModal) _btnCloseUserModal.onclick = () => {
  document.getElementById("user-modal")?.classList.add("hidden");
};
const _userModal = document.getElementById("user-modal");
if (_userModal) _userModal.onclick = (e) => {
  if (e.target === _userModal) _userModal.classList.add("hidden");
};

// ── 语音识别用户切换（"我是爸爸" / "切换到孩子"）──
function detectUserSwitchIntent(text) {
  return null;
}

// 在 flush 函数处理结果后注入切换逻辑
// 原有的 flush 完成后，在 SSE 事件里也可以触发
// 这里用一个全局 hook，在 voice ingest 返回后调用
window._onVoiceResult = function(text) {
  void text;
};

// ── 初始化 ──
loadCurrentUser();
