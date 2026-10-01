import { PresentationScroll, wheelPixels } from "./presentation-scroll.mjs";

const TOPOLOGY_LAYOUT_VERSION = "organic-seed-zones-v7";
const TOPOLOGY_MIN_ZOOM = 0.03;

const appState = {
  selectedView: "home",
  selectedDeviceId: "core-01",
  autoPollTimer: null,
  topologyPositions: {},
  topologyLayoutSignature: "",
  topologyStructure: null,
  topologyCleaning: false,
  topologyLayoutMigrationPending: window.localStorage?.getItem("netwatch.topologyLayoutVersion") !== TOPOLOGY_LAYOUT_VERSION,
  topologyLogicalSize: { width: 1, height: 1 },
  topologyZoom: 1,
  topologyZoomUserSet: false,
  topologyPan: { x: 0, y: 0 },
  draggingNode: null,
  panningMap: null,
  selectedDeviceIndex: 0,
  monitorViewMode: window.localStorage?.getItem("netwatch.monitorViewMode") === "grid" ? "grid" : "carousel",
  deviceCarouselDrag: null,
  deviceCarouselSuppressClick: false,
  historyDeviceId: "",
  historyOverlayOpen: false,
  deviceHistory: null,
  particleAnimation: null,
  snapshot: {
    devices: [],
    links: [],
    alerts: [],
    events: [],
    metric_catalog: [],
    seeds: [],
    runtime: {},
    operational_summary: {},
    device_labels: {},
    settings: { polling: {}, security: {} }
  }
};

const viewMeta = {
  dashboard: ["Current network state", "Dashboard"],
  devices: ["Inventory and interface state", "Devices"],
  topology: ["Seed-based LLDP discovery", "Topology"],
  alerts: ["Static thresholds", "Alerts"],
  settings: ["MVP architecture decisions", "Settings"]
};

const landingRgb = [216, 211, 235];
const mapRgb = [249, 250, 238];
const dashboardRgb = [217, 217, 217];
const patternBreakImages = [
  "sarah-morris-1.png",
  "sarah-morris-2.png",
  "sarah-morris-3.png",
  "sarah-morris-4.png",
  "sarah-morris-5.png"
];
const patternSectionRatio = 1 / 3;
const presentationScrollDurationMs = 2000;
const presentationScroll = new PresentationScroll();
const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
let presentationScrollFrame = 0;
let presentationWrittenTop = window.scrollY;
let presentationVisualFrame = 0;
let presentationLayout = null;
let updateParticleVisibility = () => {};
const presentationStyleValues = new WeakMap();
const presentationParts = {
  shell: document.getElementById("presentationShell"),
  copy: document.querySelector(".landing-copy"),
  credit: document.querySelector(".landing-credit"),
  code: document.querySelector(".landing-code"),
  pattern: document.querySelector(".landing-pattern"),
  particles: document.getElementById("particleOrbCanvas"),
  map: document.getElementById("showcaseMap"),
  mapControls: document.querySelector(".showcase-map-controls"),
  evidence: document.getElementById("topologyEvidencePanel"),
  patternBreak: document.querySelector(".pattern-break img"),
  live: document.querySelector(".dashboard-live"),
  actions: document.querySelector(".dashboard-action-row"),
  summary: document.getElementById("presentationSeedSummary"),
  ops: document.getElementById("dashboardOpsGrid"),
  cards: document.getElementById("presentationDashboardCards"),
  deviceHeader: document.querySelector(".devices-showcase-header"),
  deviceToggle: document.querySelector(".devices-view-toggle"),
  carousel: document.getElementById("devicesCarousel"),
  carouselControls: document.querySelector(".devices-carousel-controls")
};

function selectPatternBreakImage() {
  const image = presentationParts.patternBreak;
  if (!image) return;

  let previous = "";
  try {
    previous = window.sessionStorage.getItem("netwatch-pattern-break-image") || "";
  } catch {
    // Storage can be unavailable in private browsing; random selection still works.
  }

  const candidates = patternBreakImages.filter((name) => name !== previous);
  const selected = candidates[Math.floor(Math.random() * candidates.length)];
  image.src = `/assets/media/${selected}`;
  image.dataset.variant = selected;

  try {
    window.sessionStorage.setItem("netwatch-pattern-break-image", selected);
  } catch {
    // Keep the selected image even when session storage is unavailable.
  }
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function interpolateRgb(from, to, progress) {
  return from.map((channel, index) => Math.round(channel + (to[index] - channel) * progress)).join(" ");
}

function interpolateRgbArray(from, to, progress) {
  return from.map((channel, index) => Math.round(channel + (to[index] - channel) * progress));
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function getPresentationLayout() {
  if (presentationLayout) return presentationLayout;
  const viewport = Math.max(1, window.innerHeight);
  const topologyTop = document.getElementById("topologyShowcase")?.offsetTop || viewport;
  const patternTop = document.getElementById("patternBreak")?.offsetTop || viewport * 2;
  const dashboardTop = document.getElementById("presentationDashboard")?.offsetTop || viewport * (2 + patternSectionRatio);
  const devicesTop = document.getElementById("presentationDevices")?.offsetTop || dashboardTop + viewport;
  presentationLayout = {
    viewport, topologyTop, patternTop, dashboardTop, devicesTop,
    maxScroll: Math.max(0, document.documentElement.scrollHeight - viewport)
  };
  return presentationLayout;
}

function getPresentationMetrics() {
  const { viewport, topologyTop, patternTop, dashboardTop, devicesTop } = getPresentationLayout();
  const scroll = window.scrollY;
  const dashboardStart = dashboardTop / viewport;
  const devicesStart = devicesTop / viewport;
  return {
    viewport,
    scroll,
    dashboardStart,
    devicesStart,
    landingProgress: clamp(scroll / topologyTop, 0, 1),
    postTopologyProgress: clamp((scroll - topologyTop) / Math.max(1, patternTop - topologyTop), 0, 1),
    patternProgress: clamp((scroll - topologyTop) / Math.max(1, dashboardTop - topologyTop), 0, 1),
    dashboardProgress: clamp((scroll - viewport * (dashboardStart - 0.28)) / (viewport * 0.28), 0, 1),
    dashboardBgProgress: clamp((scroll - patternTop) / Math.max(1, dashboardTop - patternTop), 0, 1),
    devicesProgress: clamp((scroll - viewport * (devicesStart - 0.34)) / (viewport * 0.34), 0, 1)
  };
}

function setPresentationStyle(element, property, value) {
  if (!element) return;
  let values = presentationStyleValues.get(element);
  if (!values) {
    values = new Map();
    presentationStyleValues.set(element, values);
  }
  if (values.get(property) === value) return;
  values.set(property, value);
  element.style.setProperty(property, value);
}

function schedulePresentationProgress() {
  if (presentationVisualFrame) return;
  presentationVisualFrame = window.requestAnimationFrame(() => {
    presentationVisualFrame = 0;
    updatePresentationProgress();
  });
}

function updatePresentationProgress() {
  const shell = presentationParts.shell;
  if (!shell || !document.body.classList.contains("presentation-mode")) return;
  const {
    landingProgress,
    postTopologyProgress,
    patternProgress,
    dashboardProgress,
    dashboardBgProgress,
    devicesProgress,
    scroll,
    viewport,
    dashboardStart,
    devicesStart
  } = getPresentationMetrics();
  const baseRgb = interpolateRgbArray(landingRgb, mapRgb, landingProgress);
  const stageRgb = interpolateRgb(baseRgb, dashboardRgb, dashboardBgProgress);
  const mapOpacity = Math.min(
    clamp((landingProgress - 0.18) * 1.45, 0, 1),
    clamp(1 - postTopologyProgress * 1.16, 0, 1)
  );
  const particleOpacity = clamp(landingProgress * 0.62, 0, 0.62) * clamp(1 - postTopologyProgress * 1.15, 0, 1);
  const patternOffset = (0.5 - patternProgress) * 92;

  // Only mutate the animated elements. Inherited root variables invalidate the entire inventory.
  const parts = presentationParts;
  const move = (element, opacity, x = 0, y = 0) => {
    setPresentationStyle(element, "opacity", opacity.toFixed(4));
    setPresentationStyle(element, "transform", `translate3d(${x.toFixed(1)}px, ${y.toFixed(1)}px, 0)`);
  };
  setPresentationStyle(shell, "background-color", `rgb(${stageRgb})`);
  setPresentationStyle(document.body, "background-color", `rgb(${stageRgb})`);
  const landingOpacity = clamp(1 - landingProgress * 1.65, 0, 1);
  move(parts.copy, landingOpacity, -landingProgress * 80);
  move(parts.credit, landingOpacity, -landingProgress * 60);
  const codeOpacity = clamp(1 - postTopologyProgress * 1.2, 0, 1);
  setPresentationStyle(parts.code, "opacity", codeOpacity.toFixed(4));
  setPresentationStyle(parts.code, "pointer-events", codeOpacity > 0.08 ? "auto" : "none");
  setPresentationStyle(parts.code, "transform", `translate3d(calc(${landingProgress.toFixed(4)} * var(--pattern-width)), 0, 0)`);
  setPresentationStyle(parts.pattern, "opacity", clamp(1 - landingProgress * 1.2, 0, 1).toFixed(4));
  setPresentationStyle(parts.pattern, "transform", `translate3d(${(landingProgress * 38).toFixed(3)}vw, 0, 0)`);
  move(parts.map, mapOpacity, (1 - landingProgress) * -150);
  move(parts.mapControls, mapOpacity, (1 - landingProgress) * -90);
  move(parts.evidence, mapOpacity, (1 - landingProgress) * -90);
  setPresentationStyle(parts.particles, "opacity", particleOpacity.toFixed(4));
  updateParticleVisibility(particleOpacity > 0.001 && !document.hidden);
  setPresentationStyle(parts.patternBreak, "transform", `translate3d(0, ${patternOffset.toFixed(1)}px, 0) scale(1.06)`);
  move(parts.live, dashboardProgress, (1 - dashboardProgress) * 72);
  for (const element of [parts.actions, parts.summary, parts.ops]) {
    move(element, dashboardProgress, (1 - dashboardProgress) * -86);
  }
  move(parts.cards, dashboardProgress, 0, (1 - dashboardProgress) * 92);
  move(parts.deviceHeader, devicesProgress, 0, (1 - devicesProgress) * 28);
  move(parts.deviceToggle, devicesProgress, 0, (1 - devicesProgress) * 28);
  move(parts.carousel, devicesProgress, 0, (1 - devicesProgress) * 56);
  setPresentationStyle(parts.carouselControls, "opacity", devicesProgress.toFixed(4));
  if (scroll >= viewport * (devicesStart - 0.08)) {
    appState.selectedView = "devices";
  } else if (scroll >= viewport * (dashboardStart - 0.08)) {
    appState.selectedView = "dashboard";
  } else if (landingProgress > 0.55) {
    appState.selectedView = "topology";
  } else {
    appState.selectedView = "home";
  }
}

function enterPresentationMode(view = "home", behavior = "smooth") {
  document.body.classList.add("presentation-mode");
  document.body.classList.toggle("is-home", view === "home");
  appState.selectedView = view;
  renderShowcaseTopology();
  renderPresentationDashboard();
  renderPresentationDevices();
  renderDeviceHistory();
  updatePresentationProgress();
  const topologySection = document.getElementById("topologyShowcase");
  const dashboardSection = document.getElementById("presentationDashboard");
  const devicesSection = document.getElementById("presentationDevices");
  const targets = {
    home: 0,
    topology: topologySection?.offsetTop ?? window.innerHeight,
    dashboard: dashboardSection?.offsetTop ?? window.innerHeight * (2 + patternSectionRatio),
    devices: devicesSection?.offsetTop ?? window.innerHeight * (3 + patternSectionRatio)
  };
  const top = targets[view] ?? 0;
  if (behavior === "smooth") {
    animatePresentationScrollTo(top);
  } else {
    cancelPresentationScroll();
    window.scrollTo({ top, behavior: "auto" });
    window.requestAnimationFrame(updatePresentationProgress);
  }
  if (view === "home" && window.location.hash) {
    history.pushState(null, "", window.location.pathname + window.location.search);
  } else if (view !== "home" && window.location.hash !== `#${view}`) {
    history.pushState(null, "", `#${view}`);
  }
}

function enterAppMode(view) {
  cancelPresentationScroll();
  document.body.classList.remove("presentation-mode", "is-home");
  appState.selectedView = view;
  window.scrollTo({ top: 0, behavior: "auto" });
}

function getPresentationAnchorTops() {
  const { topologyTop, dashboardTop, devicesTop } = getPresentationLayout();
  return [
    { name: "home", top: 0 },
    { name: "topology", top: topologyTop },
    { name: "dashboard", top: dashboardTop },
    { name: "devices", top: devicesTop }
  ];
}

function updatePresentationHashForView(view) {
  if (view === "home") {
    if (window.location.hash) history.replaceState(null, "", window.location.pathname + window.location.search);
    return;
  }
  if (window.location.hash !== `#${view}`) {
    history.replaceState(null, "", `#${view}`);
  }
}

function easePresentationScroll(progress) {
  return progress < 0.5
    ? 4 * progress * progress * progress
    : 1 - Math.pow(-2 * progress + 2, 3) / 2;
}

function animatePresentationScrollTo(top, duration = presentationScrollDurationMs) {
  cancelPresentationScroll();
  const startTop = window.scrollY;
  const distance = top - startTop;

  if (Math.abs(distance) < 1 || duration <= 0 || reducedMotion.matches) {
    window.scrollTo({ top, behavior: "auto" });
    updatePresentationProgress();
    return;
  }

  const startTime = performance.now();
  const step = (now) => {
    const progress = clamp((now - startTime) / duration, 0, 1);
    const eased = easePresentationScroll(progress);
    window.scrollTo({ top: startTop + distance * eased, behavior: "auto" });
    presentationWrittenTop = window.scrollY;
    updatePresentationProgress();
    if (progress < 1) {
      presentationScrollFrame = window.requestAnimationFrame(step);
    } else {
      window.scrollTo({ top, behavior: "auto" });
      updatePresentationProgress();
      presentationScrollFrame = 0;
      presentationScroll.reset(window.scrollY);
    }
  };

  presentationScrollFrame = window.requestAnimationFrame(step);
}

function cancelPresentationScroll() {
  window.cancelAnimationFrame(presentationScrollFrame);
  presentationScrollFrame = 0;
  presentationScroll.reset(window.scrollY);
  presentationWrittenTop = window.scrollY;
}

function canScrollElementInDirection(element, deltaY) {
  if (!element) return false;
  const maxScroll = element.scrollHeight - element.clientHeight;
  if (maxScroll <= 2) return false;
  if (deltaY > 0) return element.scrollTop < maxScroll - 2;
  return element.scrollTop > 2;
}

function getInternalScrollTarget(target, deltaY) {
  for (let element = target; element && element !== document.body; element = element.parentElement) {
    if (/^(auto|scroll)$/.test(getComputedStyle(element).overflowY)
      && canScrollElementInDirection(element, deltaY)) return element;
  }
  return null;
}

function continuePresentationScroll() {
  if (presentationScrollFrame) return;
  let lastTime = performance.now();
  let position = window.scrollY;
  const step = (now) => {
    position = presentationScroll.advance(position, Math.min(now - lastTime, 64), window.innerHeight);
    lastTime = now;
    window.scrollTo({ top: position, behavior: "auto" });
    presentationWrittenTop = window.scrollY;
    updatePresentationProgress();
    if (position !== presentationScroll.target) {
      presentationScrollFrame = window.requestAnimationFrame(step);
    } else {
      presentationScrollFrame = 0;
      const anchor = getPresentationAnchorTops().find((item) => Math.abs(item.top - position) <= 1);
      if (anchor) updatePresentationHashForView(anchor.name);
    }
  };
  presentationScrollFrame = window.requestAnimationFrame(step);
}

function handlePresentationWheel(event) {
  if (!document.body.classList.contains("presentation-mode") || event.defaultPrevented) return;
  if (event.ctrlKey || event.metaKey || event.shiftKey || Math.abs(event.deltaX) > Math.abs(event.deltaY)) return;
  if (!event.deltaY || !event.cancelable) return;
  const target = event.target instanceof Element ? event.target : null;
  const overlayOpen = appState.historyOverlayOpen || document.getElementById("liveSetupModal")?.hidden === false;
  if (overlayOpen || getInternalScrollTarget(target, event.deltaY)) {
    cancelPresentationScroll();
    if (overlayOpen && !getInternalScrollTarget(target, event.deltaY)) event.preventDefault();
    return;
  }
  if (reducedMotion.matches) return;

  event.preventDefault();
  const current = window.scrollY;
  // A manual wheel gesture also interrupts a navigation-button animation.
  if (!presentationScroll.direction) cancelPresentationScroll();
  presentationScroll.move(
    wheelPixels(event, window.innerHeight), current, getPresentationAnchorTops(), window.innerHeight,
    getPresentationLayout().maxScroll
  );
  continuePresentationScroll();
}

function letterizeLandingTitle() {
  const title = document.querySelector('[data-letterize="true"]');
  if (!title || title.dataset.ready === "true") return;
  const lines = Array.from(title.children).map((line) => line.textContent.trim());
  const total = lines.reduce((count, line) => count + Array.from(line).length, 0);
  let index = 0;
  title.textContent = "";

  lines.forEach((line) => {
    const lineEl = document.createElement("span");
    lineEl.className = "title-line";
    Array.from(line).forEach((char) => {
      const charEl = document.createElement("span");
      charEl.className = char === " " ? "letter-space" : "letter";
      charEl.style.setProperty("--i", index);
      charEl.style.setProperty("--total", total);
      charEl.textContent = char === " " ? String.fromCharCode(160) : char;
      lineEl.append(charEl);
      index += 1;
    });
    title.append(lineEl);
  });

  title.dataset.ready = "true";
}

function formatBps(value) {
  if (value === null || value === undefined) return "n/a";
  if (value >= 1_000_000_000) return `${(value / 1_000_000_000).toFixed(1)} Gbps`;
  if (value >= 1_000_000) return `${Math.round(value / 1_000_000)} Mbps`;
  if (value >= 1_000) return `${Math.round(value / 1_000)} Kbps`;
  return `${value} bps`;
}

function formatSpeed(value) {
  if (!value) return "unknown";
  if (value >= 1000) return `${value / 1000} Gbps`;
  return `${value} Mbps`;
}

function statusLabel(status) {
  if (status === "up") return "UP";
  if (status === "down") return "DOWN";
  if (status === "warning") return "WARNING";
  if (status === "pending") return "PENDING";
  if (status === "observed") return "OBSERVED";
  return "UNKNOWN";
}

function stateLabel(value) {
  return value.charAt(0).toUpperCase() + value.slice(1);
}

function getDevices() {
  return appState.snapshot.devices;
}

function getManagedDevices() {
  return getDevices().filter((device) => device.status !== "pending");
}

function getDeviceById(id) {
  return getDevices().find((device) => device.id === id);
}

function getAlertCounts() {
  return appState.snapshot.alerts.reduce(
    (acc, alert) => {
      acc[alert.state] += 1;
      return acc;
    },
    { active: 0, acknowledged: 0, resolved: 0 }
  );
}

async function apiGet(path) {
  const response = await fetch(path, { cache: "no-store" });
  if (!response.ok) throw new Error(`${path} failed with ${response.status}`);
  return response.json();
}

async function apiPost(path) {
  return apiPostJson(path);
}

async function apiPostJson(path, body) {
  const response = await fetch(path, {
    method: "POST",
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined
  });
  if (!response.ok) {
    let detail = `${path} failed with ${response.status}`;
    try {
      const error = await response.json();
      detail = error.detail || detail;
    } catch {
      detail = response.statusText || detail;
    }
    throw new Error(detail);
  }
  return response.json();
}

function isSavedMapLayout(layout) {
  return layout?.source === "saved-map" && Number.isFinite(Number(layout.x)) && Number.isFinite(Number(layout.y));
}

function applySavedTopologyLayouts(positions, devices, ignoredDeviceIds = new Set()) {
  devices.forEach((device) => {
    if (ignoredDeviceIds.has(device.id)) return;
    if (!isSavedMapLayout(device.layout)) return;
    positions[device.id] = {
      x: Number(device.layout.x),
      y: Number(device.layout.y)
    };
  });
}

let topologyLayoutSaveTimer = null;
let topologyLayoutSaveQueue = Promise.resolve();

async function saveTopologyLayout() {
  window.clearTimeout(topologyLayoutSaveTimer);
  const layouts = {};
  getDevices().forEach((device) => {
    const position = appState.topologyPositions[device.id];
    if (!position) return;
    layouts[device.id] = {
      x: Math.round(position.x * 10) / 10,
      y: Math.round(position.y * 10) / 10,
      locked: Boolean(device.layout?.locked)
    };
    device.layout = { ...layouts[device.id], source: "saved-map" };
  });
  if (Object.keys(layouts).length === 0) return;
  // A pending drag save must finish before a newer clean-up layout is written.
  topologyLayoutSaveQueue = topologyLayoutSaveQueue.catch(() => {}).then(() =>
    apiPostJson("/api/topology/layout", { layouts })
  );
  const result = await topologyLayoutSaveQueue;
  if (result.snapshot) appState.snapshot = result.snapshot;
  if (appState.topologyLayoutMigrationPending) {
    window.localStorage?.setItem("netwatch.topologyLayoutVersion", TOPOLOGY_LAYOUT_VERSION);
    appState.topologyLayoutMigrationPending = false;
  }
}

function saveTopologyLayoutSoon() {
  window.clearTimeout(topologyLayoutSaveTimer);
  topologyLayoutSaveTimer = window.setTimeout(async () => {
    try {
      await saveTopologyLayout();
    } catch (error) {
      console.warn("Topology layout save failed", error);
    }
  }, 350);
}

function getPollingSettings() {
  return appState.snapshot.settings?.polling || {};
}

function getSeedSummaryText() {
  const seeds = appState.snapshot.seeds || [];
  const polling = getPollingSettings();
  const runtime = appState.snapshot.runtime || {};
  const mode = appState.snapshot.mode || "mock";
  const auto = polling.backend_auto_poll ? `auto ${polling.backend_interval_seconds || 30}s` : "manual poll";
  const pollerStatus = runtime.external_poller_alive
    ? `poller ${polling.external_poller_status || "running"}`
    : "poller offline";
  if (seeds.length === 0) return `${mode} mode / ${auto}`;
  const loadedCredentials = Number(runtime.seed_credentials_loaded || 0);
  const savedCredentials = Number(runtime.seed_credentials_saved || 0);
  if (mode === "live" && loadedCredentials === 0) {
    return savedCredentials > 0
      ? `${seeds.length} seed saved / credentials pending load`
      : `${seeds.length} seed saved / credentials missing`;
  }
  const ok = seeds.filter((seed) => seed.status === "up").length;
  const credentialSummary = savedCredentials > 0 ? ` / ${loadedCredentials} credentials loaded` : "";
  return `${seeds.length} seed / ${ok} up${credentialSummary} / ${auto} / ${pollerStatus}`;
}

function updateSeedSummary() {
  const summary = document.getElementById("presentationSeedSummary");
  if (summary) summary.textContent = getSeedSummaryText();
}

function updatePollActionButtons(isBusy = false) {
  const polling = getPollingSettings();
  const enabled = Boolean(polling.backend_auto_poll);
  const interval = Number(polling.backend_interval_seconds || 30);
  const label = enabled ? "Stop Poll" : "Run Poll";
  const title = enabled ? `Polling every ${interval}s` : "Start polling every 30 seconds";
  ["presentationRunPoll", "runPollButton"].forEach((id) => {
    const button = document.getElementById(id);
    if (!button) return;
    button.disabled = isBusy;
    button.classList.toggle("is-running", enabled);
    button.setAttribute("aria-pressed", String(enabled));
    button.title = isBusy ? "Polling in progress" : title;
    const labelTarget = button.querySelector("span");
    if (labelTarget) {
      labelTarget.textContent = label;
    } else {
      button.textContent = label;
    }
  });
}

function formatAge(seconds) {
  if (seconds === null || seconds === undefined || Number.isNaN(Number(seconds))) return "n/a";
  const value = Math.max(0, Number(seconds));
  if (value < 60) return `${Math.round(value)}s`;
  if (value < 3600) return `${Math.round(value / 60)}m`;
  if (value < 86400) return `${Math.round(value / 3600)}h`;
  return `${Math.round(value / 86400)}d`;
}

function formatDurationMs(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "n/a";
  const ms = Number(value);
  if (ms < 1000) return `${Math.round(ms)}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  return `${Math.round(ms / 60_000)}m`;
}

function getOperationalSummary() {
  return appState.snapshot.operational_summary || {};
}

function operationalTone(status) {
  if (["ok", "fresh", "strong", "succeeded"].includes(status)) return "ok";
  if (["warning", "stale", "mixed", "partial"].includes(status)) return "warning";
  if (["down", "failed"].includes(status)) return "down";
  return "neutral";
}

function renderProblemRows(problems) {
  if (!problems?.length) {
    return `<li><span class="ops-problem-dot ok"></span><strong>Clear</strong><small>No open operational issue</small></li>`;
  }
  return problems
    .slice(0, 3)
    .map(
      (problem) => `
        <li>
          <span class="ops-problem-dot ${escapeHtml(problem.severity || "info")}"></span>
          <strong>${escapeHtml(problem.title || "Problem")}</strong>
          <small>${escapeHtml(problem.detail || "")}</small>
        </li>
      `
    )
    .join("");
}

function renderDashboardOps() {
  const target = document.getElementById("dashboardOpsGrid");
  if (!target) return;
  const ops = getOperationalSummary();
  const poll = ops.poll_health || {};
  const topology = ops.topology_evidence || {};
  const latest = ops.latest_data || {};
  const problems = ops.problems || [];
  const lastRun = poll.last_run || appState.snapshot.runtime?.last_poll_run || {};
  const pollStatus = poll.poller_alive ? (lastRun.status || poll.poller_status || "running") : "offline";
  const latestStatus = latest.status || "unknown";
  const topologyStatus = topology.status || "empty";
  const problemStatus = problems.length ? problems[0].severity || "warning" : "ok";
  const tiles = [
    {
      label: "Poll Health",
      value: pollStatus,
      status: poll.status || pollStatus,
      meta: `${poll.auto_poll ? `auto ${poll.interval_seconds || 30}s` : "manual"} / ${poll.poller_status || "unknown"}`,
      detail: `last ${formatDurationMs(lastRun.duration_ms)} / ${lastRun.successes ?? 0} ok / ${lastRun.failures ?? 0} failed`
    },
    {
      label: "Topology Evidence",
      value: topology.total_links ? `${topology.score || 0}%` : "n/a",
      status: topologyStatus,
      meta: `${topology.confirmed_links || 0} confirmed / ${topology.pending_links || 0} pending`,
      detail: `${topology.low_confidence_links || 0} weak / ${topology.directness?.direct || 0} direct / ${topology.directness?.["probable-direct"] || 0} probable`
    },
    {
      label: "Latest Data",
      value: latestStatus,
      status: latestStatus,
      meta: `newest ${formatAge(latest.latest_sample_age_seconds)} ago`,
      detail: `${latest.interfaces_with_traffic || 0}/${latest.interfaces_total || 0} interfaces with traffic`
    },
    {
      label: "Problems",
      value: String(problems.length || 0),
      status: problemStatus,
      meta: problems[0]?.title || "no open problems",
      detailHtml: `<ol class="ops-problem-list">${renderProblemRows(problems)}</ol>`
    }
  ];

  target.innerHTML = tiles
    .map(
      (tile) => `
        <article class="ops-tile ${operationalTone(tile.status)}">
          <span>${escapeHtml(tile.label)}</span>
          <strong>${escapeHtml(tile.value)}</strong>
          <small>${escapeHtml(tile.meta)}</small>
          ${tile.detailHtml || `<em>${escapeHtml(tile.detail || "")}</em>`}
        </article>
      `
    )
    .join("");
}

function renderTopologyEvidencePanel() {
  const target = document.getElementById("topologyEvidencePanel");
  if (!target) return;
  const topology = getOperationalSummary().topology_evidence || {};
  const sources = topology.sources || {};
  const sourceText = Object.entries(sources)
    .sort((a, b) => Number(b[1]) - Number(a[1]))
    .slice(0, 3)
    .map(([source, count]) => `${source} ${count}`)
    .join(" / ");
  target.innerHTML = `
    <span>Topology Evidence</span>
    <strong>${topology.total_links ? `${topology.score || 0}%` : "n/a"}</strong>
    <small>${topology.confirmed_links || 0} confirmed / ${topology.pending_links || 0} pending / ${topology.low_confidence_links || 0} weak</small>
    <em>${escapeHtml(sourceText || "waiting for LLDP/FDB evidence")}</em>
  `;
}

function setLiveSetupOpen(open) {
  const modal = document.getElementById("liveSetupModal");
  if (!modal) return;
  modal.hidden = !open;
  if (open) {
    cancelPresentationScroll();
    syncSeedVersionFields();
    document.getElementById("presentationSeedHost")?.focus();
  }
}

async function loadSnapshot() {
  appState.snapshot = await apiGet("/api/snapshot");
  const backendState = document.getElementById("backendState");
  if (backendState) backendState.textContent = "Connected";
  renderAll();
}

function renderKpis() {
  const target = document.getElementById("kpiGrid");
  if (!target) return;
  const managed = getManagedDevices();
  const up = managed.filter((device) => device.status === "up").length;
  const warning = managed.filter((device) => device.status === "warning").length;
  const down = managed.filter((device) => device.status === "down").length;
  const counts = getAlertCounts();
  const pendingLinks = appState.snapshot.links.filter((link) => link.status === "pending").length;
  const kpis = [
    { label: "Managed devices", value: `${up}/${managed.length}`, trend: `${warning} warning, ${down} down` },
    { label: "Active alerts", value: counts.active, trend: `${counts.acknowledged} acknowledged` },
    { label: "LLDP links", value: appState.snapshot.links.length, trend: `${pendingLinks} pending` },
    { label: "Event stream", value: "WS", trend: document.getElementById("eventStreamState")?.textContent || "WebSocket pending" }
  ];

  target.innerHTML = kpis
    .map(
      (kpi) => `
        <article class="kpi-card">
          <div>
            <span class="eyebrow">${kpi.label}</span>
            <div class="kpi-value">${kpi.value}</div>
          </div>
          <span class="kpi-trend">${kpi.trend}</span>
        </article>
      `
    )
    .join("");
}

function renderDashboardAlertDetail() {
  const alerts = appState.snapshot.alerts;
  if (alerts.length === 0) {
    return `
      <section class="dashboard-card-detail dashboard-alert-detail">
        <div class="dashboard-detail-heading">
          <span>Alert queue</span>
          <strong>0</strong>
        </div>
        <p class="dashboard-detail-empty">No active alert details yet.</p>
      </section>
    `;
  }

  return `
    <section class="dashboard-card-detail dashboard-alert-detail" aria-label="Alert details">
      <div class="dashboard-detail-heading">
        <span>Alert queue</span>
        <strong>${alerts.length}</strong>
      </div>
      <div class="dashboard-alert-list">
        ${alerts
          .map((alert) => {
            const device = getDeviceById(alert.device_id);
            const canAck = alert.state === "active";
            const canResolve = alert.state !== "resolved";
            return `
              <article class="dashboard-alert-item">
                <div>
                  <strong>${escapeHtml(alert.title)}</strong>
                  <span>${escapeHtml(device ? device.name : "unknown device")}</span>
                  <p>${escapeHtml(alert.detail)}</p>
                </div>
                <span class="dashboard-alert-state ${escapeHtml(alert.state)}">${escapeHtml(stateLabel(alert.state))}</span>
                <div class="dashboard-alert-actions">
                  <button type="button" data-alert-action="ack" data-alert-id="${escapeHtml(alert.id)}" ${canAck ? "" : "disabled"}>Ack</button>
                  <button type="button" data-alert-action="resolve" data-alert-id="${escapeHtml(alert.id)}" ${canResolve ? "" : "disabled"}>Resolve</button>
                </div>
              </article>
            `;
          })
          .join("")}
      </div>
    </section>
  `;
}

function renderDashboardDeviceDetail() {
  const devices = getDevices();
  if (devices.length === 0) {
    return `
      <section class="dashboard-card-detail dashboard-device-detail">
        <div class="dashboard-detail-heading">
          <span>Discovered devices</span>
          <strong>0</strong>
        </div>
        <p class="dashboard-detail-empty">No devices discovered yet.</p>
      </section>
    `;
  }

  return `
    <section class="dashboard-card-detail dashboard-device-detail" aria-label="Discovered devices">
      <div class="dashboard-detail-heading">
        <span>Discovered devices</span>
        <strong>${devices.length}</strong>
      </div>
      <div class="dashboard-device-list">
        ${devices
          .map(
            (device) => `
              <article class="dashboard-device-item">
                <div>
                  <strong>${escapeHtml(device.name)}</strong>
                  <span>${escapeHtml(device.ip || "unknown")} / ${escapeHtml(device.vendor || "unknown")}</span>
                  <p>${escapeHtml(device.model || "Unknown model")}</p>
                </div>
                <span class="dashboard-device-status ${escapeHtml(device.status || "unknown")}">${escapeHtml(statusLabel(device.status))}</span>
              </article>
            `
          )
          .join("")}
      </div>
    </section>
  `;
}

function renderDashboardEventDetail() {
  const events = appState.snapshot.events.slice(0, 20);
  if (events.length === 0) {
    return `
      <section class="dashboard-card-detail dashboard-event-detail">
        <div class="dashboard-detail-heading">
          <span>Event log</span>
          <strong>0</strong>
        </div>
        <p class="dashboard-detail-empty">No event log entries yet.</p>
      </section>
    `;
  }

  return `
    <section class="dashboard-card-detail dashboard-event-detail" aria-label="Event log">
      <div class="dashboard-detail-heading">
        <span>Event log</span>
        <strong>${events.length}</strong>
      </div>
      <ol class="dashboard-event-log">
        ${events
          .map(
            (event) => `
              <li class="dashboard-event-item">
                <time>${escapeHtml(event.time)}</time>
                <span>${escapeHtml(event.text)}</span>
              </li>
            `
          )
          .join("")}
      </ol>
    </section>
  `;
}

function renderPresentationDashboard() {
  const cardsTarget = document.getElementById("presentationDashboardCards");
  const eventsTarget = document.getElementById("presentationEventList");
  if (!cardsTarget || !eventsTarget) return;
  const activeCard = cardsTarget.querySelector(".dashboard-card.is-interacting");
  const activeTone = activeCard?.dataset.tone;
  const activeReveal = Number(activeCard?.dataset.reveal || 0);
  renderDashboardOps();

  const managed = getManagedDevices();
  const up = managed.filter((device) => device.status === "up").length;
  const warning = managed.filter((device) => device.status === "warning").length;
  const down = managed.filter((device) => device.status === "down").length;
  const counts = getAlertCounts();
  const pendingLinks = appState.snapshot.links.filter((link) => link.status === "pending").length;
  const streamState = document.getElementById("eventStreamState")?.textContent || "WebSocket pending";
  const cards = [
    {
      tone: "black",
      title: "Managed<br>Devices",
      value: `${up} / ${managed.length}`,
      foot: `${warning} warning, ${down} down`,
      icon: "/assets/media/intelligent-iteration-icon.svg",
      bg: "#0c0c0c",
      fg: "#e9e9e9",
      detail: renderDashboardDeviceDetail()
    },
    {
      tone: "teal",
      title: "LLDP<br>Links",
      value: appState.snapshot.links.length,
      foot: `${pendingLinks} pending`,
      icon: "/assets/media/financial-support-icon.svg",
      bg: "#66a3ad",
      fg: "#080808"
    },
    {
      tone: "orange",
      title: "Active<br>Alerts",
      value: counts.active,
      foot: `${counts.acknowledged} acknowledged`,
      icon: "/assets/media/full-stack-support-icon.svg",
      bg: "#f36b42",
      fg: "#080808",
      detail: renderDashboardAlertDetail()
    },
    {
      tone: "neutral",
      title: "Event<br>Stream",
      value: "WS",
      foot: streamState,
      icon: "/assets/media/exponential-foresight-icon.svg",
      bg: "#e8e8e8",
      fg: "#080808",
      detail: renderDashboardEventDetail()
    }
  ];

  cardsTarget.innerHTML = cards
    .map(
      (card) => `
        <article
          class="dashboard-card${card.tone === activeTone ? " is-interacting" : ""}"
          data-tone="${card.tone}"
          data-reveal="${card.tone === activeTone ? activeReveal : 0}"
          style="--card-bg:${card.bg};--card-fg:${card.fg};"
        >
          <div class="dashboard-card-summary">
            <h3 class="dashboard-card-title">${card.title}</h3>
            <img class="dashboard-card-icon" src="${card.icon}" alt="" />
            <strong class="dashboard-card-value">${escapeHtml(card.value)}</strong>
            <span class="dashboard-card-foot">${escapeHtml(card.foot)}</span>
          </div>
          ${card.detail || ""}
        </article>
      `
    )
    .join("");
  bindAlertActionButtons(cardsTarget);
  bindDashboardCardReveal(cardsTarget);

  const events = appState.snapshot.events.length
    ? appState.snapshot.events.slice(0, 20)
    : [{ time: "--:--:--", text: "Waiting for worker events" }];
  eventsTarget.innerHTML = events
    .map((event) => `<li><time>${escapeHtml(event.time)}</time> - ${escapeHtml(event.text)}</li>`)
    .join("");
  updateSeedSummary();
  updatePollActionButtons();
}

function getDeviceSummary(device) {
  const interfaces = device.interfaces || [];
  return interfaces.reduce(
    (acc, iface) => {
      if (iface.admin_status === "up") acc.adminUp += 1;
      if (iface.oper_status === "up") acc.operUp += 1;
      acc.inBps += iface.in_bps || 0;
      acc.outBps += iface.out_bps || 0;
      acc.errors += (iface.in_errors || 0) + (iface.out_errors || 0);
      acc.discards += (iface.in_discards || 0) + (iface.out_discards || 0);
      return acc;
    },
    { adminUp: 0, operUp: 0, inBps: 0, outBps: 0, errors: 0, discards: 0 }
  );
}

function getEndpointTraffic(device) {
  return device?.device_type === "endpoint" && device.observed_traffic ? device.observed_traffic : null;
}

function formatEndpointTraffic(device) {
  const traffic = getEndpointTraffic(device);
  if (!traffic) return "traffic n/a";
  const suffix = traffic.shared_port ? " shared port" : "estimated";
  return `${formatBps(traffic.endpoint_in_bps)} down / ${formatBps(traffic.endpoint_out_bps)} up (${suffix})`;
}

function formatInterfaceTraffic(iface, device) {
  if (device?.device_type === "endpoint" && iface.traffic_source === "switch-port") {
    return `${formatBps(iface.in_bps)} down / ${formatBps(iface.out_bps)} up`;
  }
  return `${formatBps(iface.in_bps)} in / ${formatBps(iface.out_bps)} out`;
}

function normalizeMacKey(value) {
  const text = String(value || "").toLowerCase().replace(/[^a-f0-9]/g, "");
  return text.length === 12 ? text : "";
}

function formatMacKey(value) {
  const key = normalizeMacKey(value);
  if (!key) return "";
  return key.match(/.{1,2}/g).join(":");
}

function isSnmpNamedSwitch(device) {
  if (!device || device.device_type === "endpoint" || device.device_type === "segment") return false;
  if (device.name_source === "mac-label") return false;
  if (device.seed_key || device.lldp_sys_name || device.lldp_mgmt_ip) return true;
  if (String(device.id || "").startsWith("live-")) return true;
  return (device.interfaces || []).length > 0 && Boolean(device.ip && device.ip !== "unknown");
}

function getDeviceMacCandidates(device) {
  const candidates = [
    device?.asset_mac,
    device?.mac,
    device?.observed_mac,
    device?.chassis_id,
    device?.fingerprint,
    ...(device?.observed_macs || [])
  ];
  const seen = new Set();
  return candidates
    .map(formatMacKey)
    .filter((mac) => {
      const key = normalizeMacKey(mac);
      if (!key || seen.has(key)) return false;
      seen.add(key);
      return true;
    });
}

function getDeviceMac(device) {
  return getDeviceMacCandidates(device)[0] || "";
}

function getMacLabelForMac(mac) {
  const key = normalizeMacKey(mac);
  return appState.snapshot.mac_labels?.[key] || {};
}

function getMacLabel(device, mac = "") {
  const key = normalizeMacKey(mac || getDeviceMac(device));
  return appState.snapshot.mac_labels?.[key] || device?.asset_label || {};
}

function getDeviceLabel(device) {
  return appState.snapshot.device_labels?.[device?.id] || device?.device_label || {};
}

function renderMacLabelEditor(device) {
  if (isSnmpNamedSwitch(device)) return "";
  const macs = getDeviceMacCandidates(device);
  const hasMacTarget = macs.length > 0;
  const mac = macs[0] || "";
  const label = hasMacTarget ? getMacLabel(device) : getDeviceLabel(device);
  const macInput = macs.length > 1
    ? `
      <label>
        <span>MAC address</span>
        <select name="mac">
          ${macs
            .map((candidate) => {
              const candidateLabel = getMacLabelForMac(candidate);
              const suffix = candidateLabel.name ? ` - ${candidateLabel.name}` : "";
              return `<option value="${escapeHtml(candidate)}" ${normalizeMacKey(candidate) === normalizeMacKey(mac) ? "selected" : ""}>${escapeHtml(candidate + suffix)}</option>`;
            })
            .join("")}
        </select>
      </label>
    `
    : hasMacTarget
      ? `<input name="mac" type="hidden" value="${escapeHtml(mac)}" />`
      : `<input name="device_id" type="hidden" value="${escapeHtml(device.id || "")}" />`;
  const descriptor = macs.length > 1
    ? `${macs.length} observed MACs available`
    : hasMacTarget
      ? `${mac} follows this asset across switch ports.`
      : "Saved to this observed port-description endpoint; no MAC is available yet.";
  return `
    <form class="mac-label-form" data-mac-label-form data-label-target="${hasMacTarget ? "mac" : "device"}" data-mac="${escapeHtml(mac)}" data-device-id="${escapeHtml(device.id || "")}">
      ${macInput}
      <label>
        <span>Asset name</span>
        <input name="name" type="text" maxlength="160" value="${escapeHtml(label.name || "")}" placeholder="${escapeHtml(device.name || "Endpoint")}" />
      </label>
      <label>
        <span>Description</span>
        <input name="description" type="text" maxlength="400" value="${escapeHtml(label.description || "")}" placeholder="Saved by MAC address" />
      </label>
      <button type="submit">Save MAC</button>
      <small data-mac-label-status>${escapeHtml(descriptor)}</small>
    </form>
  `;
}

function compactList(values, limit = 5) {
  const items = (values || []).filter(Boolean).map(String);
  if (items.length <= limit) return items.join(", ");
  return `${items.slice(0, limit).join(", ")} +${items.length - limit}`;
}

function formatInterfaceVlans(iface) {
  const pvid = iface.vlan_pvid ? `PVID ${iface.vlan_pvid}` : "";
  const tagged = compactList(iface.vlan_tagged || []);
  const untagged = compactList(iface.vlan_untagged || []);
  const parts = [];
  if (pvid) parts.push(pvid);
  if (untagged) parts.push(`untagged ${untagged}`);
  if (tagged) parts.push(`tagged ${tagged}`);
  return parts.join(" / ") || "VLAN n/a";
}

function summarizeDeviceVlans(device) {
  const vlans = new Set();
  (device.interfaces || []).forEach((iface) => {
    (iface.vlans || []).forEach((vlan) => vlans.add(String(vlan)));
    if (iface.vlan_pvid) vlans.add(String(iface.vlan_pvid));
  });
  return compactList([...vlans].sort((a, b) => Number(a) - Number(b)), 8) || "n/a";
}

function renderPresentationDeviceInterfaces(device) {
  const interfaces = device.interfaces || [];
  if (interfaces.length === 0) {
    return `<li class="device-interface-item"><strong>No interfaces discovered</strong><span>SNMP IF-MIB returned no rows</span></li>`;
  }

  return interfaces
    .map(
      (iface) => `
        <li class="device-interface-item">
          <div>
            <strong>${escapeHtml(iface.name || iface.if_descr || "Interface")}</strong>
            <span>${escapeHtml(iface.if_alias || iface.if_descr || "no alias")}</span>
            <span>${escapeHtml(formatInterfaceTraffic(iface, device))}</span>
            <span>${escapeHtml(formatInterfaceVlans(iface))}</span>
            ${iface.traffic_source === "switch-port" ? `<span>${escapeHtml(iface.traffic_note || "estimated from switch port")}</span>` : ""}
            <span>${escapeHtml(String((iface.in_errors || 0) + (iface.out_errors || 0)))} errors / ${escapeHtml(String((iface.in_discards || 0) + (iface.out_discards || 0)))} discards</span>
          </div>
          <span class="device-interface-state ${escapeHtml(iface.oper_status || "unknown")}">${escapeHtml(iface.admin_status || "n/a")} / ${escapeHtml(iface.oper_status || "n/a")}</span>
        </li>
      `
    )
    .join("");
}

function renderPresentationDevices() {
  const section = document.getElementById("presentationDevices");
  const track = document.getElementById("devicesTrack");
  const carousel = document.getElementById("devicesCarousel");
  if (!section || !track || !carousel) return;
  section.classList.toggle("is-grid-view", appState.monitorViewMode === "grid");
  updateMonitorViewControls();

  const devices = getDevices();
  const selectedFromId = devices.findIndex((device) => device.id === appState.selectedDeviceId);
  if (selectedFromId >= 0) {
    appState.selectedDeviceIndex = selectedFromId;
  }
  appState.selectedDeviceIndex = clamp(appState.selectedDeviceIndex, 0, Math.max(0, devices.length - 1));

  if (devices.length === 0) {
    track.innerHTML = `
      <article class="device-card devices-placeholder-card is-active" data-device-card data-device-index="0">
        <strong>No devices yet</strong>
        <span>Add a live seed switch or use mock data to populate the monitor.</span>
      </article>
    `;
    bindDeviceCarousel();
    updateDeviceCarousel();
    return;
  }

  track.innerHTML = devices
    .map((device, index) => {
      const summary = getDeviceSummary(device);
      const interfaces = device.interfaces || [];
      const endpointTraffic = getEndpointTraffic(device);
      const sourceLabel = device.device_type === "endpoint"
        ? `${device.observed_source || "MAC table"}${device.observed_vlan ? ` / VLAN ${device.observed_vlan}` : ""}`
        : device.device_type === "segment"
          ? `${device.observed_mac_count || 0} MACs / ${device.observed_source || "MAC table"}`
        : device.lldp_sys_name
          ? "LLDP neighbor"
          : "SNMP seed";
      const portLabel = device.observed_local_port || device.lldp_local_port || "n/a";
      const trafficLabel = endpointTraffic
        ? `${formatBps(endpointTraffic.endpoint_in_bps)} down<br>${formatBps(endpointTraffic.endpoint_out_bps)} up`
        : `${formatBps(summary.inBps)} in<br>${formatBps(summary.outBps)} out`;
      const trafficSourceLabel = endpointTraffic
        ? `${endpointTraffic.switch_name || "switch"} / ${endpointTraffic.switch_port || portLabel}${endpointTraffic.shared_port ? ` / ${endpointTraffic.shared_endpoint_count} endpoints` : ""}`
        : "direct interface counters";
      const vlanSummary = summarizeDeviceVlans(device);
      const observedMacs = device.observed_macs || [];
      const observedMacList = observedMacs.slice(0, 6).map((mac) => escapeHtml(mac)).join("<br>");
      const observedMacMore = observedMacs.length > 6 ? `<br>+${observedMacs.length - 6}` : "";
      return `
        <article class="device-card" data-device-card data-device-index="${index}" data-device-card-id="${escapeHtml(device.id)}">
          <header class="device-card-head">
            <h3>${escapeHtml(device.name || "Unknown device")}</h3>
            <span class="device-card-status ${escapeHtml(device.status || "unknown")}">${escapeHtml(statusLabel(device.status))}</span>
          </header>
          <div class="device-card-mark" aria-hidden="true">
            <span></span><span></span><span></span><span></span>
          </div>
          <div class="device-card-scroll">
            <div class="device-facts">
              <div class="device-fact"><span>Management IP</span><strong>${escapeHtml(device.ip || "unknown")}</strong></div>
              <div class="device-fact"><span>Vendor</span><strong>${escapeHtml(device.vendor || "unknown")}</strong></div>
              <div class="device-fact"><span>Model</span><strong>${escapeHtml(device.model || "unknown")}</strong></div>
              ${device.description ? `<div class="device-fact"><span>Description</span><strong>${escapeHtml(device.description)}</strong></div>` : ""}
              <div class="device-fact"><span>Source</span><strong>${escapeHtml(sourceLabel)}</strong></div>
              <div class="device-fact"><span>Switch port</span><strong>${escapeHtml(portLabel)}</strong></div>
              ${device.device_type === "segment" ? `<div class="device-fact"><span>Observed MACs</span><strong>${observedMacList}${observedMacMore}</strong></div>` : ""}
              <div class="device-fact"><span>Interfaces</span><strong>${summary.operUp}/${interfaces.length} oper up</strong></div>
              <div class="device-fact"><span>Traffic</span><strong>${trafficLabel}</strong></div>
              <div class="device-fact"><span>VLANs</span><strong>${escapeHtml(vlanSummary)}</strong></div>
              <div class="device-fact"><span>Traffic source</span><strong>${escapeHtml(trafficSourceLabel)}</strong></div>
              <div class="device-fact"><span>Errors</span><strong>${summary.errors} errors<br>${summary.discards} discards</strong></div>
            </div>
            ${renderMacLabelEditor(device)}
            <ol class="device-interface-list">
              ${renderPresentationDeviceInterfaces(device)}
            </ol>
          </div>
          <span class="device-card-foot">${escapeHtml(device.fingerprint || "no fingerprint")}</span>
        </article>
      `;
    })
    .join("");

  bindDeviceCarousel();
  bindMacLabelForms(track);
  updateDeviceCarousel();
}

function updateMonitorViewControls() {
  document.querySelectorAll("[data-monitor-view]").forEach((button) => {
    const isActive = button.dataset.monitorView === appState.monitorViewMode;
    button.classList.toggle("is-active", isActive);
    button.setAttribute("aria-pressed", String(isActive));
  });
}

function setMonitorViewMode(mode) {
  appState.monitorViewMode = mode === "grid" ? "grid" : "carousel";
  try {
    window.localStorage.setItem("netwatch.monitorViewMode", appState.monitorViewMode);
  } catch {
    // Ignore private-mode storage failures; the in-memory setting still works.
  }
  renderPresentationDevices();
  updateDeviceCarousel();
}

function setSelectedDeviceIndex(index) {
  const devices = getDevices();
  if (devices.length === 0) {
    appState.selectedDeviceIndex = 0;
    return;
  }
  appState.selectedDeviceIndex = clamp(index, 0, devices.length - 1);
  appState.selectedDeviceId = devices[appState.selectedDeviceIndex]?.id || "";
  updateDeviceCarousel();
}

function openMonitorForDevice(deviceId, behavior = "smooth") {
  if (!getDeviceById(deviceId)) return;
  appState.selectedDeviceId = deviceId;
  const index = getDevices().findIndex((device) => device.id === deviceId);
  if (index >= 0) appState.selectedDeviceIndex = index;
  renderPresentationDevices();
  updateDeviceCarousel();
  switchView("devices", behavior);
}

function setHistoryOverlayOpen(open, updateHash = true) {
  appState.historyOverlayOpen = open;
  document.body.classList.toggle("history-overlay-open", open);
  if (open) {
    cancelPresentationScroll();
    if (updateHash && window.location.hash !== "#history") {
      history.pushState(null, "", "#history");
    }
  } else if (updateHash && window.location.hash === "#history") {
    history.pushState(null, "", "#devices");
  }
}

async function openDeviceHistory(deviceId, behavior = "smooth") {
  if (!getDeviceById(deviceId)) return;
  appState.selectedDeviceId = deviceId;
  appState.historyDeviceId = deviceId;
  const index = getDevices().findIndex((device) => device.id === deviceId);
  if (index >= 0) appState.selectedDeviceIndex = index;
  enterPresentationMode("devices", behavior);
  try {
    appState.deviceHistory = await apiGet(`/api/devices/${encodeURIComponent(deviceId)}/history`);
  } catch (error) {
    appState.deviceHistory = { error: error.message, device: getDeviceById(deviceId), interfaces: [] };
  }
  renderDeviceHistory();
  setHistoryOverlayOpen(true);
}

function historySampleValue(sample) {
  const inBps = Number(sample?.in_bps || 0);
  const outBps = Number(sample?.out_bps || 0);
  return Math.max(0, inBps, outBps);
}

function historyBuckets(samples, columns = 18) {
  const now = Date.now() / 1000;
  const start = now - 3600;
  const width = 3600 / columns;
  const buckets = Array.from({ length: columns }, () => []);
  samples.forEach((sample) => {
    const ts = Number(sample.ts || 0);
    if (!Number.isFinite(ts)) return;
    const index = clamp(Math.floor((ts - start) / width), 0, columns - 1);
    buckets[index].push(historySampleValue(sample));
  });
  return buckets.map((bucket) => (bucket.length ? Math.max(...bucket) : 0));
}

function renderHistoryGrid(samples, capacity) {
  const columns = 18;
  const rows = 7;
  const values = historyBuckets(samples, columns);
  const maxObserved = Math.max(...values, 1);
  const maxCapacity = Number(capacity || 0) > 0 ? Number(capacity) * 1_000_000 : 0;
  const denominator = Math.max(maxObserved, maxCapacity * 0.18, 1);
  let cells = "";
  for (let row = 0; row < rows; row += 1) {
    for (let column = 0; column < columns; column += 1) {
      const normalized = values[column] / denominator;
      const height = Math.max(values[column] > 0 ? 1 : 0, Math.round(normalized * rows));
      const active = rows - row <= height;
      cells += `<span class="history-pill ${active ? "is-active" : ""}" aria-hidden="true"></span>`;
    }
  }
  return cells;
}

function renderDeviceHistory() {
  const section = document.getElementById("deviceHistory");
  const title = document.getElementById("historyDeviceName");
  const meta = document.getElementById("historyDeviceMeta");
  const list = document.getElementById("historyInterfaceList");
  if (!section || !title || !meta || !list) return;

  const device = appState.deviceHistory?.device || getDeviceById(appState.historyDeviceId || appState.selectedDeviceId);
  if (!device) {
    title.textContent = "Interface History";
    meta.textContent = "";
    list.innerHTML = `<article class="history-empty">No device selected</article>`;
    return;
  }

  title.textContent = device.name || "Interface History";
  meta.textContent = `${device.ip || "unknown"} / ${device.vendor || "unknown"} / ${device.model || "unknown"}`;
  const interfaces = appState.deviceHistory?.interfaces || [];
  if (appState.deviceHistory?.error) {
    list.innerHTML = `<article class="history-empty">${escapeHtml(appState.deviceHistory.error)}</article>`;
    return;
  }
  if (interfaces.length === 0) {
    list.innerHTML = `<article class="history-empty">No interfaces available</article>`;
    return;
  }

  list.innerHTML = interfaces
    .map((iface) => {
      const samples = iface.samples || [];
      const latest = samples[samples.length - 1] || {};
      return `
        <article class="history-interface-card">
          <div class="history-interface-meta">
            <span>${escapeHtml(iface.name || "Interface")}</span>
            <strong>${escapeHtml(formatBps(historySampleValue(latest)))}</strong>
            <small>${escapeHtml(iface.if_alias || "no alias")} / ${escapeHtml(iface.admin_status || "n/a")} ${escapeHtml(iface.oper_status || "n/a")}</small>
          </div>
          <div class="history-pill-grid" aria-label="${escapeHtml(iface.name || "Interface")} last hour traffic">
            ${renderHistoryGrid(samples, iface.if_high_speed)}
          </div>
        </article>
      `;
    })
    .join("");
}

function updateDeviceCarousel(dragOffset = 0) {
  const section = document.getElementById("presentationDevices");
  const carousel = document.getElementById("devicesCarousel");
  const track = document.getElementById("devicesTrack");
  if (!carousel || !track) return;
  const cards = Array.from(track.querySelectorAll("[data-device-card]"));
  const prev = document.getElementById("devicePrevButton");
  const next = document.getElementById("deviceNextButton");
  const isGrid = appState.monitorViewMode === "grid";

  section?.classList.toggle("is-grid-view", isGrid);
  updateMonitorViewControls();

  if (cards.length === 0) {
    if (prev) prev.disabled = true;
    if (next) next.disabled = true;
    return;
  }

  if (isGrid) {
    track.style.transform = "";
    track.classList.remove("is-dragging");
    cards.forEach((card) => card.classList.add("is-active"));
    if (prev) prev.disabled = true;
    if (next) next.disabled = true;
    return;
  }

  appState.selectedDeviceIndex = clamp(appState.selectedDeviceIndex, 0, cards.length - 1);
  const carouselRect = carousel.getBoundingClientRect();
  const cardWidth = cards[0].offsetWidth || cards[0].getBoundingClientRect().width;
  const styles = getComputedStyle(track);
  const gap = Number.parseFloat(styles.columnGap || styles.gap || "0") || 0;
  const baseOffset = carouselRect.width / 2 - cardWidth / 2;
  const x = baseOffset - appState.selectedDeviceIndex * (cardWidth + gap) + dragOffset;
  track.style.transform = `translate3d(${x.toFixed(1)}px, -50%, 0)`;

  cards.forEach((card, index) => {
    card.classList.toggle("is-active", index === appState.selectedDeviceIndex);
  });
  if (prev) prev.disabled = appState.selectedDeviceIndex === 0;
  if (next) next.disabled = appState.selectedDeviceIndex === cards.length - 1;
}

function bindDeviceCarousel() {
  const track = document.getElementById("devicesTrack");
  if (!track || track.dataset.bound === "true") return;
  track.dataset.bound = "true";

  track.addEventListener("pointerdown", (event) => {
    if (appState.monitorViewMode !== "carousel") return;
    if (event.button !== 0) return;
    if (event.target.closest("input, textarea, button, select, label, [data-mac-label-form]")) return;
    appState.deviceCarouselDrag = {
      pointerId: event.pointerId,
      startX: event.clientX,
      currentX: event.clientX,
      moved: false
    };
  });

  track.addEventListener("pointermove", (event) => {
    if (appState.monitorViewMode !== "carousel") return;
    const drag = appState.deviceCarouselDrag;
    if (!drag || drag.pointerId !== event.pointerId) return;
    drag.currentX = event.clientX;
    if (!drag.moved && Math.abs(drag.currentX - drag.startX) > 8) {
      drag.moved = true;
      track.classList.add("is-dragging");
      track.setPointerCapture(event.pointerId);
    }
    if (!drag.moved) return;
    updateDeviceCarousel(drag.currentX - drag.startX);
  });

  function finishDrag(event) {
    if (appState.monitorViewMode !== "carousel") {
      appState.deviceCarouselDrag = null;
      track.classList.remove("is-dragging");
      return;
    }
    const drag = appState.deviceCarouselDrag;
    if (!drag || drag.pointerId !== event.pointerId) return;
    const delta = drag.currentX - drag.startX;
    appState.deviceCarouselSuppressClick = drag.moved || Math.abs(delta) > 8;
    appState.deviceCarouselDrag = null;
    track.classList.remove("is-dragging");
    if (delta < -80) {
      setSelectedDeviceIndex(appState.selectedDeviceIndex + 1);
    } else if (delta > 80) {
      setSelectedDeviceIndex(appState.selectedDeviceIndex - 1);
    } else {
      updateDeviceCarousel();
    }
  }

  track.addEventListener("pointerup", finishDrag);
  track.addEventListener("pointercancel", finishDrag);
  track.addEventListener("click", (event) => {
    if (event.target.closest("input, textarea, button, select, label, [data-mac-label-form]")) return;
    if (appState.deviceCarouselSuppressClick) {
      appState.deviceCarouselSuppressClick = false;
      return;
    }
    const card = event.target.closest("[data-device-card]");
    if (!card || !track.contains(card)) return;
    const index = Number(card.dataset.deviceIndex || 0);
    setSelectedDeviceIndex(index);
    const deviceId = card.dataset.deviceCardId || getDevices()[index]?.id;
    if (deviceId) openDeviceHistory(deviceId);
  });
}

function bindAlertActionButtons(scope = document) {
  scope.querySelectorAll("[data-alert-action]").forEach((button) => {
    if (button.dataset.bound === "true") return;
    button.dataset.bound = "true";
    button.addEventListener("click", async (event) => {
      event.stopPropagation();
      const action = button.dataset.alertAction;
      const alertId = button.dataset.alertId;
      const result = await apiPost(`/api/alerts/${alertId}/${action}`);
      appState.snapshot = result.snapshot;
      renderAll();
    });
  });
}

function bindMacLabelForms(scope = document) {
  scope.querySelectorAll("[data-mac-label-form]").forEach((form) => {
    if (form.dataset.bound === "true") return;
    form.dataset.bound = "true";
    form.addEventListener("click", (event) => event.stopPropagation());
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      event.stopPropagation();
      const status = form.querySelector("[data-mac-label-status]");
      const submit = form.querySelector("button[type='submit']");
      const isDeviceLabel = form.dataset.labelTarget === "device";
      const payload = {
        ...(isDeviceLabel
          ? { device_id: form.elements.device_id?.value || form.dataset.deviceId || "" }
          : { mac: form.elements.mac?.value || form.dataset.mac || "" }),
        name: form.elements.name?.value?.trim() || "",
        description: form.elements.description?.value?.trim() || ""
      };
      if (status) status.textContent = isDeviceLabel ? "Saving device label..." : "Saving MAC label...";
      if (submit) submit.disabled = true;
      try {
        const result = await apiPostJson(isDeviceLabel ? "/api/device-labels" : "/api/mac-labels", payload);
        appState.snapshot = result.snapshot;
        renderAll();
      } catch (error) {
        if (status) status.textContent = error.message;
      } finally {
        if (submit) submit.disabled = false;
      }
    });
  });
}

function getDashboardCardMaxReveal(card) {
  const row = card.closest(".dashboard-card-row");
  if (!row || !card.querySelector(".dashboard-card-detail") || window.matchMedia("(max-width: 980px)").matches) return 0;
  const visibleHeight = row.getBoundingClientRect().height || window.innerHeight * 0.58;
  const renderedHeight = card.getBoundingClientRect().height;
  const contentHeight = Math.max(card.scrollHeight, renderedHeight);
  return Math.max(0, contentHeight - visibleHeight + 36);
}

function setDashboardCardReveal(card, value) {
  const next = clamp(value, 0, getDashboardCardMaxReveal(card));
  card.dataset.reveal = String(Math.round(next));
  card.style.setProperty("--card-reveal-y", `${next.toFixed(1)}px`);
}

function activateDashboardCard(card, scope = document) {
  if (card.classList.contains("is-interacting")) return;
  scope.querySelectorAll(".dashboard-card").forEach((otherCard) => {
    if (otherCard === card) return;
    otherCard.classList.remove("is-interacting");
    setDashboardCardReveal(otherCard, 0);
  });
  card.classList.add("is-interacting");
}

function deactivateDashboardCard(card) {
  if (!card.classList.contains("is-interacting")) return;
  card.classList.remove("is-interacting");
  setDashboardCardReveal(card, 0);
}

function bindDashboardCardReveal(scope = document) {
  scope.querySelectorAll(".dashboard-card").forEach((card) => {
    if (card.dataset.revealBound === "true") return;
    card.dataset.revealBound = "true";
    setDashboardCardReveal(card, Number(card.dataset.reveal || 0));
    card.addEventListener(
      "wheel",
      (event) => {
        if (event.ctrlKey || event.metaKey || event.shiftKey || Math.abs(event.deltaX) > Math.abs(event.deltaY)) return;
        const dashboard = card.closest(".dashboard-showcase");
        if (dashboard && Math.abs(window.scrollY - dashboard.offsetTop) > 2) return;
        activateDashboardCard(card, scope);
        const current = Number(card.dataset.reveal || 0);
        const next = current + wheelPixels(event, window.innerHeight) * 0.7;
        const maxReveal = getDashboardCardMaxReveal(card);
        const bounded = clamp(next, 0, maxReveal);
        if (bounded !== current) {
          cancelPresentationScroll();
          event.preventDefault();
          event.stopPropagation();
          setDashboardCardReveal(card, bounded);
        }
      },
      { passive: false }
    );
  });
  if (scope.dataset.revealScopeBound !== "true") {
    scope.dataset.revealScopeBound = "true";
    scope.addEventListener("pointerover", (event) => {
      const card = event.target.closest(".dashboard-card");
      if (card && scope.contains(card)) activateDashboardCard(card, scope);
    });
    scope.addEventListener("focusin", (event) => {
      const card = event.target.closest(".dashboard-card");
      if (card && scope.contains(card)) activateDashboardCard(card, scope);
    });
    scope.addEventListener("pointerleave", () => {
      scope.querySelectorAll(".dashboard-card").forEach((card) => deactivateDashboardCard(card));
    });
    scope.addEventListener("focusout", (event) => {
      if (!scope.contains(event.relatedTarget) && !scope.matches(":hover")) {
        scope.querySelectorAll(".dashboard-card").forEach((card) => deactivateDashboardCard(card));
      }
    });
  }
}

function renderDashboardDevices() {
  const target = document.getElementById("dashboardDeviceList");
  if (!target) return;
  if (getDevices().length === 0) {
    target.innerHTML = `
      <article class="device-row">
        <div class="device-name">
          <strong>No live devices yet</strong>
          <span>Open Settings and import an SNMP seed switch.</span>
        </div>
      </article>
    `;
    return;
  }
  target.innerHTML = getDevices()
    .map(
      (device) => `
        <article class="device-row">
          <div class="device-name">
            <strong>${device.name}</strong>
            <span>${device.model}</span>
          </div>
          <span>${device.ip}</span>
          <span>${device.vendor}</span>
          <span class="status-pill ${device.status}">${statusLabel(device.status)}</span>
        </article>
      `
    )
    .join("");
}

function renderEvents() {
  const target = document.getElementById("eventList");
  if (!target) return;
  target.innerHTML = appState.snapshot.events
    .map(
      (event) => `
        <li>
          <time>${event.time}</time>
          ${event.text}
        </li>
      `
    )
    .join("");
}

function renderDevicesTable() {
  const table = document.getElementById("devicesTable");
  const detailTitle = document.getElementById("deviceDetailTitle");
  const detail = document.getElementById("deviceDetail");
  if (!table || !detailTitle || !detail) return;
  if (getDevices().length === 0) {
    table.innerHTML = `
      <tr>
        <td colspan="4">No live devices yet. Add a seed switch from Settings.</td>
      </tr>
    `;
    detailTitle.textContent = "No device selected";
    detail.innerHTML = "";
    return;
  }
  table.innerHTML = getDevices()
    .map(
      (device) => `
        <tr class="clickable-row" data-device-id="${device.id}">
          <td><strong>${device.name}</strong><br><span class="muted">${device.model}</span></td>
          <td>${device.ip}</td>
          <td>${device.vendor}</td>
          <td><span class="status-pill ${device.status}">${statusLabel(device.status)}</span></td>
        </tr>
      `
    )
    .join("");

  document.querySelectorAll("[data-device-id]").forEach((row) => {
    row.addEventListener("click", () => {
      appState.selectedDeviceId = row.dataset.deviceId;
      renderDeviceDetail();
    });
  });
}

function renderDeviceDetail() {
  const detailTitle = document.getElementById("deviceDetailTitle");
  const detail = document.getElementById("deviceDetail");
  if (!detailTitle || !detail) return;
  const device = getDeviceById(appState.selectedDeviceId) || getDevices()[0];
  if (!device) return;
  appState.selectedDeviceId = device.id;
  const endpointTraffic = getEndpointTraffic(device);
  detailTitle.textContent = device.name;
  detail.innerHTML = `
    <dl class="settings-list">
      <div><dt>Management IP</dt><dd>${device.ip}</dd></div>
      <div><dt>Vendor</dt><dd>${device.vendor}</dd></div>
      <div><dt>Model</dt><dd>${device.model}</dd></div>
      <div><dt>Fingerprint</dt><dd>${device.fingerprint}</dd></div>
      ${device.description ? `<div><dt>Description</dt><dd>${escapeHtml(device.description)}</dd></div>` : ""}
      ${endpointTraffic ? `<div><dt>Traffic source</dt><dd>${escapeHtml(endpointTraffic.switch_name || "switch")} / ${escapeHtml(endpointTraffic.switch_port || "port")} (${escapeHtml(endpointTraffic.note || "estimated")})</dd></div>` : ""}
    </dl>
    ${renderMacLabelEditor(device)}
    <div class="interface-list">
      ${device.interfaces
        .map(
          (iface) => `
            <article class="interface-item">
              <div>
                <strong>${iface.name}</strong>
                <span class="muted">${iface.if_alias || iface.if_descr || "no alias"}</span>
              </div>
              <span class="status-pill ${iface.admin_status === "up" ? "up" : "neutral"}">admin ${iface.admin_status}</span>
              <span class="status-pill ${iface.oper_status === "up" ? "up" : iface.oper_status === "down" ? "down" : "neutral"}">oper ${iface.oper_status}</span>
              <span class="muted">${escapeHtml(formatInterfaceTraffic(iface, device)).replace(" / ", "<br>")}</span>
              <span class="muted">${escapeHtml(formatInterfaceVlans(iface)).replaceAll(" / ", "<br>")}</span>
              <span class="muted">${iface.in_errors + iface.out_errors} errors<br>${iface.in_discards + iface.out_discards} discards</span>
              <span class="muted">${formatSpeed(iface.if_high_speed)}</span>
            </article>
          `
        )
        .join("")}
    </div>
  `;
  bindMacLabelForms(detail);
}

function renderTopology() {
  const canvas = document.getElementById("topologyCanvas");
  const svg = document.getElementById("linkLayer");
  const nodes = document.getElementById("nodeLayer");
  if (!canvas || !svg || !nodes) return;
  const width = canvas.clientWidth || 900;
  const height = canvas.clientHeight || 560;
  const nodeWidth = 165;
  const nodeHeight = 92;
  const margin = 24;
  const designWidth = 900;
  const designHeight = 540;
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);

  if (getDevices().length === 0) {
    svg.innerHTML = "";
    nodes.innerHTML = `
      <div class="topology-empty">
        <strong>No topology yet</strong>
        <span>Add a live SNMP seed to discover LLDP neighbors.</span>
      </div>
    `;
    return;
  }

  const normalized = new Map(
    getDevices().map((device) => [
      device.id,
      {
        ...device,
        x: margin + (device.layout.x / designWidth) * Math.max(1, width - nodeWidth - margin * 2),
        y: margin + (device.layout.y / designHeight) * Math.max(1, height - nodeHeight - margin * 2)
      }
    ])
  );

  svg.innerHTML = appState.snapshot.links
    .map((link) => {
      const from = normalized.get(link.from);
      const to = normalized.get(link.to);
      if (!from || !to) return "";
      const x1 = from.x + nodeWidth / 2;
      const y1 = from.y + nodeHeight / 2;
      const x2 = to.x + nodeWidth / 2;
      const y2 = to.y + nodeHeight / 2;
      const stroke = link.status === "confirmed" ? "#1c7f5a" : "#a76505";
      const isManagementLink = link.directness === "management" || link.link_type === "management";
      const dash = link.line_style === "solid" || link.status === "confirmed" ? "0" : "8 7";
      const opacity = Math.max(0.35, Math.min(1, Number(link.confidence || 45) / 100));
      const label = `${isManagementLink ? "MGMT · " : ""}${[link.local_port, link.remote_port].filter(Boolean).join(" -> ")}`;
      const labelSvg = label
        ? `<text x="${(x1 + x2) / 2}" y="${(y1 + y2) / 2 - 8}" class="link-label">${escapeHtml(label)}</text>`
        : "";
      return `<g data-link-type="${isManagementLink ? "management" : "network"}"><title>${escapeHtml(topologyLinkTitle(link))}</title><line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="${isManagementLink ? "#d45d39" : stroke}" stroke-width="3" stroke-opacity="${opacity}" stroke-dasharray="${dash}" stroke-linecap="round" />${labelSvg}</g>`;
    })
    .join("");

  nodes.innerHTML = [...normalized.values()]
    .map(
      (device) => `
        <button class="topology-node ${device.status}" type="button" style="left:${device.x}px;top:${device.y}px" data-node-device="${device.id}">
          <span class="node-title">
            ${device.name}
            <span class="status-pill ${device.status}">${statusLabel(device.status)}</span>
          </span>
          <span class="node-meta">${device.ip}<br>${device.model}</span>
        </button>
      `
    )
    .join("");

  document.querySelectorAll("[data-node-device]").forEach((node) => {
    node.addEventListener("click", () => {
      appState.selectedDeviceId = node.dataset.nodeDevice;
      switchView("devices");
      renderDeviceDetail();
    });
  });
}

function topologyDeviceLabel(deviceMap, id) {
  const device = deviceMap.get(id);
  return `${device?.name || id} ${device?.ip || ""}`.toLowerCase();
}

function compareTopologyDeviceIds(deviceMap) {
  return (a, b) => topologyDeviceLabel(deviceMap, a).localeCompare(topologyDeviceLabel(deviceMap, b));
}

function getTopologySignature(devices, structure) {
  const layoutVersion = "diagram-v3-seed-zones";
  const nodePart = devices
    .map((device) => `${device.id}:${structure.roleById.get(device.id) || "unknown"}`)
    .sort()
    .join("|");
  const parentPart = [...structure.parentByChild.entries()]
    .map(([childId, parentId]) => `${childId}>${parentId}`)
    .sort()
    .join("|");
  return `${layoutVersion}::${nodePart}::${parentPart}::${structure.linkPairs.join("|")}`;
}

function getValidTopologyLinks(devices, links) {
  const ids = new Set(devices.map((device) => device.id));
  return links.filter((link) => ids.has(link.from) && ids.has(link.to) && link.from !== link.to);
}

function topologyLinkPriority(link) {
  const confidence = Number(link.confidence || 0);
  const statusBonus = link.status === "confirmed" ? 120 : link.status === "pending" ? -20 : 0;
  const directBonus = link.directness === "direct" ? 80 : link.directness === "probable-direct" ? 45 : 0;
  return confidence + statusBonus + directBonus;
}

function topologyLinkTitle(link) {
  const confidence = Number(link.confidence || 0);
  const evidence = link.evidence || "No evidence";
  const decision = link.topology_decision || "";
  const directness = link.directness || "unknown";
  return `${evidence} / ${confidence}% ${link.confidence_label || ""} / ${directness}${decision ? ` / ${decision}` : ""}`;
}

function buildTopologyAdjacency(devices, links) {
  const adjacency = new Map(devices.map((device) => [device.id, new Set()]));
  getValidTopologyLinks(devices, links)
    .slice()
    .sort((a, b) => topologyLinkPriority(b) - topologyLinkPriority(a))
    .forEach((link) => {
    adjacency.get(link.from)?.add(link.to);
    adjacency.get(link.to)?.add(link.from);
  });
  return adjacency;
}

function getTopologyComponents(devices, adjacency, deviceMap) {
  const visited = new Set();
  const components = [];
  const ids = devices.map((device) => device.id).sort(compareTopologyDeviceIds(deviceMap));

  ids.forEach((id) => {
    if (visited.has(id)) return;
    const stack = [id];
    const component = [];
    visited.add(id);
    while (stack.length) {
      const current = stack.pop();
      component.push(current);
      [...(adjacency.get(current) || [])]
        .sort(compareTopologyDeviceIds(deviceMap))
        .forEach((next) => {
          if (visited.has(next)) return;
          visited.add(next);
          stack.push(next);
        });
    }
    components.push(component.sort(compareTopologyDeviceIds(deviceMap)));
  });

  return components.sort((a, b) => b.length - a.length || compareTopologyDeviceIds(deviceMap)(a[0], b[0]));
}

function getTopologyChainOrder(component, adjacency, deviceMap) {
  const compare = compareTopologyDeviceIds(deviceMap);
  const endpoints = component.filter((id) => (adjacency.get(id)?.size || 0) <= 1).sort(compare);
  const start = endpoints[0] || [...component].sort(compare)[0];
  const order = [];
  const visited = new Set();
  let current = start;
  let previous = "";

  while (current && !visited.has(current)) {
    order.push(current);
    visited.add(current);
    const next = [...(adjacency.get(current) || [])]
      .filter((id) => id !== previous && !visited.has(id))
      .sort(compare)[0];
    previous = current;
    current = next;
  }

  component
    .filter((id) => !visited.has(id))
    .sort(compare)
    .forEach((id) => order.push(id));
  return order;
}

function getTopologyBfsLevels(root, component, adjacency, deviceMap) {
  const compare = compareTopologyDeviceIds(deviceMap);
  const componentIds = new Set(component);
  const visited = new Set([root]);
  const queue = [{ id: root, level: 0 }];
  const levels = [];

  while (queue.length) {
    const current = queue.shift();
    levels[current.level] ||= [];
    levels[current.level].push(current.id);
    [...(adjacency.get(current.id) || [])]
      .filter((id) => componentIds.has(id) && !visited.has(id))
      .sort(compare)
      .forEach((id) => {
        visited.add(id);
        queue.push({ id, level: current.level + 1 });
      });
  }

  component
    .filter((id) => !visited.has(id))
    .sort(compare)
    .forEach((id) => {
      levels[levels.length] = [id];
    });

  return levels.map((level) => level.sort(compare));
}

function normalizeRelativePositions(items, cardWidth, cardHeight) {
  const minX = Math.min(...items.map((item) => item.x));
  const minY = Math.min(...items.map((item) => item.y));
  const normalized = items.map((item) => ({ id: item.id, x: item.x - minX, y: item.y - minY }));
  const maxX = Math.max(...normalized.map((item) => item.x));
  const maxY = Math.max(...normalized.map((item) => item.y));
  return {
    items: normalized,
    width: maxX + cardWidth,
    height: maxY + cardHeight
  };
}

function layoutTopologyGrid(ids, spacingX, spacingY, maxRows, cardWidth, cardHeight) {
  return normalizeRelativePositions(
    ids.map((id, index) => ({
      id,
      x: Math.floor(index / maxRows) * spacingX,
      y: (index % maxRows) * spacingY
    })),
    cardWidth,
    cardHeight
  );
}

function layoutTopologyComponent(component, adjacency, deviceMap, spacingX, spacingY, maxRows, cardWidth, cardHeight) {
  const compare = compareTopologyDeviceIds(deviceMap);
  if (component.length === 1) {
    return normalizeRelativePositions([{ id: component[0], x: 0, y: 0 }], cardWidth, cardHeight);
  }

  const degrees = component.map((id) => ({ id, degree: adjacency.get(id)?.size || 0 }));
  const maxDegree = Math.max(...degrees.map((item) => item.degree));
  if (maxDegree <= 2) {
    return layoutTopologyGrid(getTopologyChainOrder(component, adjacency, deviceMap), spacingX, spacingY, maxRows, cardWidth, cardHeight);
  }

  const root = degrees
    .sort((a, b) => b.degree - a.degree || compare(a.id, b.id))[0]
    .id;
  const levels = getTopologyBfsLevels(root, component, adjacency, deviceMap);
  const items = [];
  let column = 0;

  levels.forEach((level, levelIndex) => {
    for (let offset = 0; offset < level.length; offset += maxRows) {
      const chunk = level.slice(offset, offset + maxRows);
      chunk.forEach((id, row) => {
        items.push({
          id,
          x: column * spacingX,
          y: row * spacingY
        });
      });
      column += 1;
    }
    if (levelIndex === 0 && levels[1]?.length) {
      const rootItem = items.find((item) => item.id === root);
      const visibleRows = Math.min(maxRows, levels[1].length);
      if (rootItem) rootItem.y = ((visibleRows - 1) * spacingY) / 2;
    }
  });

  return normalizeRelativePositions(items, cardWidth, cardHeight);
}

function isTopologyEndpoint(device) {
  return device?.device_type === "endpoint" || String(device?.id || "").startsWith("endpoint-");
}

function isTopologySegment(device) {
  return device?.device_type === "segment" || String(device?.id || "").startsWith("segment-");
}

function isTopologyPeripheral(device) {
  return isTopologyEndpoint(device) || isTopologySegment(device) || device?.status === "pending";
}

function getTopologyParentMap(devices, links, deviceMap) {
  const ids = new Set(devices.map((device) => device.id));
  const parentByChild = new Map();
  const childrenByParent = new Map();

  getValidTopologyLinks(devices, links)
    .slice()
    .sort((a, b) => topologyLinkPriority(b) - topologyLinkPriority(a))
    .forEach((link) => {
    if (!ids.has(link.from) || !ids.has(link.to)) return;
    const from = deviceMap.get(link.from);
    const to = deviceMap.get(link.to);
    const fromPeripheral = isTopologyPeripheral(from);
    const toPeripheral = isTopologyPeripheral(to);
    if (fromPeripheral === toPeripheral) return;
    const parentId = fromPeripheral ? link.to : link.from;
    const childId = fromPeripheral ? link.from : link.to;
    if (parentByChild.has(childId)) return;
    parentByChild.set(childId, parentId);
    childrenByParent.set(parentId, [...(childrenByParent.get(parentId) || []), childId]);
  });

  childrenByParent.forEach((children, parentId) => {
    childrenByParent.set(parentId, children.sort(compareTopologyDeviceIds(deviceMap)));
  });

  return { parentByChild, childrenByParent };
}

function topologyLayoutRole(device) {
  if (isTopologyPeripheral(device)) return "peripheral";
  return device?.status === "pending" ? "candidate" : "seed";
}

function getTopologyStructure(devices, links) {
  const deviceMap = new Map(devices.map((device) => [device.id, device]));
  const { parentByChild, childrenByParent } = getTopologyParentMap(devices, links, deviceMap);
  const roleById = new Map(devices.map((device) => [device.id, topologyLayoutRole(device)]));
  const linkPairs = getValidTopologyLinks(devices, links)
    .map((link) => [link.from, link.to].sort().join("~"))
    .sort();
  return { roleById, parentByChild, childrenByParent, linkPairs };
}

function getAffectedTopologyLayoutIds(previous, current) {
  if (!previous) return new Set();
  const affected = new Set();
  const affectedParents = new Set();

  current.roleById.forEach((role, id) => {
    const previousRole = previous.roleById.get(id);
    const parentId = current.parentByChild.get(id);
    const previousParentId = previous.parentByChild.get(id);

    if (!previousRole || previousRole !== role) {
      affected.add(id);
      if (role === "seed") affectedParents.add(id);
    }
    if (parentId !== previousParentId) {
      affected.add(id);
      if (parentId) affectedParents.add(parentId);
      if (previousParentId) affectedParents.add(previousParentId);
    }
  });

  previous.roleById.forEach((_, id) => {
    if (current.roleById.has(id)) return;
    const previousParentId = previous.parentByChild.get(id);
    if (previousParentId) affectedParents.add(previousParentId);
  });

  affectedParents.forEach((parentId) => {
    (current.childrenByParent.get(parentId) || []).forEach((id) => affected.add(id));
    (previous.childrenByParent.get(parentId) || []).forEach((id) => {
      if (current.roleById.has(id)) affected.add(id);
    });
  });

  return affected;
}

function getRenderableTopologyLinks(devices, links) {
  const deviceMap = new Map(devices.map((device) => [device.id, device]));
  const { parentByChild } = getTopologyParentMap(devices, links, deviceMap);
  return getValidTopologyLinks(devices, links).filter((link) => {
    const from = deviceMap.get(link.from);
    const to = deviceMap.get(link.to);
    const fromPeripheral = isTopologyPeripheral(from);
    const toPeripheral = isTopologyPeripheral(to);
    if (fromPeripheral === toPeripheral) return true;
    const parentId = fromPeripheral ? link.to : link.from;
    const childId = fromPeripheral ? link.from : link.to;
    return parentByChild.get(childId) === parentId;
  });
}

function getCoreTopologyLevels(coreIds, links, deviceMap) {
  const coreSet = new Set(coreIds);
  const compare = compareTopologyDeviceIds(deviceMap);
  const coreAdjacency = new Map(coreIds.map((id) => [id, new Set()]));
  getValidTopologyLinks([...coreSet].map((id) => deviceMap.get(id)).filter(Boolean), links).forEach((link) => {
    if (coreSet.has(link.from) && coreSet.has(link.to)) {
      coreAdjacency.get(link.from)?.add(link.to);
      coreAdjacency.get(link.to)?.add(link.from);
    }
  });

  const root = coreIds
    .map((id) => ({ id, degree: coreAdjacency.get(id)?.size || 0 }))
    .sort((a, b) => b.degree - a.degree || compare(a.id, b.id))[0]?.id || coreIds[0];
  const levels = getTopologyBfsLevels(root, coreIds, coreAdjacency, deviceMap);
  return { root, levels };
}

function getTopologyRootId(coreIds, adjacency, deviceMap) {
  const sternpunkt = coreIds
    .filter((id) => `${deviceMap.get(id)?.name || ""} ${deviceMap.get(id)?.ip || ""}`.toLowerCase().includes("sternpunkt"))
    .map((id) => ({ id, degree: adjacency.get(id)?.size || 0 }))
    .sort((a, b) => b.degree - a.degree || compareTopologyDeviceIds(deviceMap)(a.id, b.id))[0]?.id;
  if (sternpunkt) return sternpunkt;
  return coreIds
    .map((id) => ({ id, degree: adjacency.get(id)?.size || 0 }))
    .sort((a, b) => b.degree - a.degree || compareTopologyDeviceIds(deviceMap)(a.id, b.id))[0]?.id || coreIds[0];
}

function getCoreTree(rootId, coreIds, links, deviceMap) {
  const coreSet = new Set(coreIds);
  const adjacency = new Map(coreIds.map((id) => [id, new Set()]));
  getValidTopologyLinks(coreIds.map((id) => deviceMap.get(id)).filter(Boolean), links).forEach((link) => {
    if (coreSet.has(link.from) && coreSet.has(link.to)) {
      adjacency.get(link.from)?.add(link.to);
      adjacency.get(link.to)?.add(link.from);
    }
  });

  const compare = compareTopologyDeviceIds(deviceMap);
  const parentByCore = new Map();
  const levels = [[rootId]];
  const visited = new Set([rootId]);
  let queue = [{ id: rootId, level: 0 }];
  while (queue.length) {
    const current = queue.shift();
    [...(adjacency.get(current.id) || [])]
      .filter((id) => !visited.has(id))
      .sort(compare)
      .forEach((id) => {
        visited.add(id);
        parentByCore.set(id, current.id);
        levels[current.level + 1] ||= [];
        levels[current.level + 1].push(id);
        queue.push({ id, level: current.level + 1 });
      });
  }

  coreIds
    .filter((id) => !visited.has(id))
    .sort(compare)
    .forEach((id) => {
      levels[1] ||= [];
      levels[1].push(id);
      parentByCore.set(id, rootId);
    });

  return { adjacency, parentByCore, levels: levels.map((level) => level.sort(compare)) };
}

function zoneVector(index, total) {
  const presets = {
    1: [{ x: 1, y: 0 }],
    2: [{ x: -1, y: 0 }, { x: 1, y: 0 }],
    3: [{ x: -1, y: 0 }, { x: 1, y: 0 }, { x: 0, y: 1 }],
    4: [{ x: -1, y: 0 }, { x: 1, y: 0 }, { x: 0, y: -1 }, { x: 0, y: 1 }],
    5: [{ x: -1, y: 0 }, { x: 1, y: 0 }, { x: 0, y: -1 }, { x: 0, y: 1 }, { x: 1, y: 1 }],
    6: [{ x: -1, y: 0 }, { x: 1, y: 0 }, { x: 0, y: -1 }, { x: 0, y: 1 }, { x: -1, y: 1 }, { x: 1, y: 1 }]
  };
  if (presets[total]?.[index]) return presets[total][index];
  const angle = -Math.PI / 2 + (index / Math.max(1, total)) * Math.PI * 2;
  return { x: Math.cos(angle), y: Math.sin(angle) };
}

function radialVector(index, total, phase = -Math.PI / 2) {
  const angle = phase + (index / Math.max(1, total)) * Math.PI * 2;
  return { x: Math.cos(angle), y: Math.sin(angle) };
}

function normalizedVector(vector, fallback = { x: 1, y: 0 }) {
  const length = Math.hypot(vector.x, vector.y);
  if (length < 0.001) return fallback;
  return { x: vector.x / length, y: vector.y / length };
}

function placeSatelliteGroup(children, parent, side, positions, cursor, options) {
  const { cardHeight, satelliteX, satelliteY, deviceMap } = options;
  if (!children.length || !parent) return;
  const rows = children.length;
  const groupHeight = (rows - 1) * satelliteY + cardHeight;
  const startY = Math.max(cursor.top, parent.y + cardHeight / 2 - groupHeight / 2);
  const direction = side === "left" ? -1 : 1;
  const ordered = [...children].sort(compareTopologyDeviceIds(deviceMap));

  ordered.forEach((id, index) => {
    positions[id] = {
      x: parent.x + direction * satelliteX,
      y: startY + index * satelliteY
    };
  });
}

function placeEndpointZone(children, parentId, rootId, positions, options) {
  const { rootCenter, cardWidth, cardHeight, endpointRadius, endpointSpread, deviceMap } = options;
  const parent = positions[parentId];
  if (!parent || !children.length) return;
  const ordered = [...children].sort(compareTopologyDeviceIds(deviceMap));
  const parentCenter = { x: parent.x + cardWidth / 2, y: parent.y + cardHeight / 2 };
  const away = normalizedVector(
    parentId === rootId
      ? { x: 0, y: 1 }
      : { x: parentCenter.x - rootCenter.x, y: parentCenter.y - rootCenter.y },
    { x: 0, y: 1 }
  );
  const baseAngle = Math.atan2(away.y, away.x);
  const maxPerRing = 8;
  for (let offset = 0; offset < ordered.length; offset += maxPerRing) {
    const ring = Math.floor(offset / maxPerRing);
    const group = ordered.slice(offset, offset + maxPerRing);
    const spread = Math.min(Math.PI * 1.48, endpointSpread + group.length * 0.09);
    const step = group.length > 1 ? spread / (group.length - 1) : 0;
    const radius = endpointRadius + ring * (cardHeight * 0.9);
    group.forEach((id, index) => {
      const angle = baseAngle - spread / 2 + step * index;
      positions[id] = {
        x: parentCenter.x + Math.cos(angle) * radius - cardWidth / 2,
        y: parentCenter.y + Math.sin(angle) * radius - cardHeight / 2
      };
    });
  }
}

function getTopologyLayoutNodeSize(id, deviceMap, cardWidth, cardHeight) {
  const device = deviceMap.get(id);
  if (isTopologyEndpoint(device)) {
    return { width: cardWidth * 0.78, height: cardHeight * 0.62 };
  }
  if (isTopologySegment(device) || device?.status === "pending") {
    return { width: cardWidth * 0.86, height: cardHeight * 0.62 };
  }
  return { width: cardWidth, height: cardHeight };
}

function getTopologyBounds(positions, ids, deviceMap, cardWidth, cardHeight) {
  return ids.reduce(
    (acc, id) => {
      const pos = positions[id];
      if (!pos) return acc;
      const size = getTopologyLayoutNodeSize(id, deviceMap, cardWidth, cardHeight);
      acc.minX = Math.min(acc.minX, pos.x);
      acc.minY = Math.min(acc.minY, pos.y);
      acc.maxX = Math.max(acc.maxX, pos.x + size.width);
      acc.maxY = Math.max(acc.maxY, pos.y + size.height);
      return acc;
    },
    { minX: Number.POSITIVE_INFINITY, minY: Number.POSITIVE_INFINITY, maxX: 0, maxY: 0 }
  );
}

function computeShowcaseTopologyLayout(devices, links, width, height, cardWidth, cardHeight, options = {}) {
  const deviceMap = new Map(devices.map((device) => [device.id, device]));
  const validLinks = getValidTopologyLinks(devices, links);
  const ignoredSavedLayoutIds = options.ignoredSavedLayoutIds || new Set();
  const margin = 240;
  const baseCoreRadius = clamp(Math.min(width, height) * 1.25, 900, 1260);
  const endpointRadius = clamp(cardWidth + 180, 400, 470);
  const endpointSpread = Math.PI * 1.08;
  const positions = {};

  if (validLinks.length === 0) {
    const logicalWidthBase = Math.max(width * 2.15, baseCoreRadius * 2 + endpointRadius * 2 + cardWidth + margin * 2);
    const logicalHeightBase = Math.max(height * 2.05, baseCoreRadius * 2 + endpointRadius * 2 + cardHeight + margin * 2);
    const sortedIds = devices.map((device) => device.id).sort(compareTopologyDeviceIds(deviceMap));
    const grid = layoutTopologyGrid(sortedIds, endpointRadius, cardHeight + 74, Math.max(2, Math.ceil(Math.sqrt(sortedIds.length))), cardWidth, cardHeight);
    grid.items.forEach((item) => {
      positions[item.id] = { x: margin + item.x, y: margin + item.y };
    });
    return {
      positions,
      width: Math.max(logicalWidthBase, margin + grid.width + margin),
      height: Math.max(logicalHeightBase, margin + grid.height + margin)
    };
  }

  const { parentByChild, childrenByParent } = getTopologyParentMap(devices, links, deviceMap);
  const coreDevices = devices.filter((device) => !isTopologyPeripheral(device));
  const peripheralWithoutParent = devices.filter((device) => isTopologyPeripheral(device) && !parentByChild.has(device.id));
  const coreIds = coreDevices.map((device) => device.id).sort(compareTopologyDeviceIds(deviceMap));
  const coreAdjacency = buildTopologyAdjacency(coreDevices, validLinks.filter((link) => {
    const from = deviceMap.get(link.from);
    const to = deviceMap.get(link.to);
    return from && to && !isTopologyPeripheral(from) && !isTopologyPeripheral(to);
  }));

  const rootId = getTopologyRootId(coreIds, coreAdjacency, deviceMap);
  const { parentByCore, levels } = getCoreTree(rootId, coreIds, validLinks, deviceMap);
  const firstLevel = (levels[1] || []).sort(compareTopologyDeviceIds(deviceMap));
  const largestSatelliteGroup = Math.max(1, ...[...childrenByParent.values()].map((children) => new Set(children).size));
  const satelliteRings = Math.max(1, Math.ceil(largestSatelliteGroup / 8));
  const outerSatelliteRadius = endpointRadius + (satelliteRings - 1) * cardHeight * 0.9;
  const desiredZoneSpacing = outerSatelliteRadius * 2 + cardWidth * 0.86;
  const radialSpacing = firstLevel.length > 1
    ? desiredZoneSpacing / (2 * Math.sin(Math.PI / firstLevel.length))
    : baseCoreRadius;
  const coreRadius = Math.max(baseCoreRadius, radialSpacing);
  const treeStepX = Math.max(clamp(cardWidth + 640, 860, 1040), desiredZoneSpacing);
  const treeStepY = Math.max(clamp(cardHeight + 500, 760, 980), desiredZoneSpacing);
  const logicalWidthBase = Math.max(width * 2.15, coreRadius * 2 + endpointRadius * 2 + cardWidth + margin * 2);
  const logicalHeightBase = Math.max(height * 2.05, coreRadius * 2 + endpointRadius * 2 + cardHeight + margin * 2);
  const rootCenter = { x: logicalWidthBase / 2, y: logicalHeightBase / 2 };
  positions[rootId] = { x: rootCenter.x - cardWidth / 2, y: rootCenter.y - cardHeight / 2 };
  const zoneByCore = new Map([[rootId, { x: 0, y: 0 }]]);

  firstLevel.forEach((id, index) => {
    const vector = firstLevel.length <= 6 ? zoneVector(index, firstLevel.length) : radialVector(index, firstLevel.length);
    zoneByCore.set(id, vector);
    positions[id] = {
      x: rootCenter.x + vector.x * coreRadius - cardWidth / 2,
      y: rootCenter.y + vector.y * coreRadius - cardHeight / 2
    };
  });

  levels.slice(2).forEach((level, levelIndex) => {
    const siblingsByParent = new Map();
    level.forEach((id) => {
      const parentId = parentByCore.get(id) || rootId;
      siblingsByParent.set(parentId, [...(siblingsByParent.get(parentId) || []), id]);
    });
    siblingsByParent.forEach((siblings, parentId) => {
      const parent = positions[parentId] || positions[rootId];
      const inherited = normalizedVector(zoneByCore.get(parentId) || { x: 1, y: 0 });
      const tangent = { x: -inherited.y, y: inherited.x };
      const offsetStart = -((siblings.length - 1) * treeStepY) / 2;
      siblings.sort(compareTopologyDeviceIds(deviceMap)).forEach((id, index) => {
        zoneByCore.set(id, inherited);
        positions[id] = {
          x: parent.x + inherited.x * treeStepX + tangent.x * (offsetStart + index * treeStepY),
          y: parent.y + inherited.y * treeStepX + tangent.y * (offsetStart + index * treeStepY)
        };
      });
    });
    level.forEach((id) => {
      if (positions[id]) return;
      const parentId = parentByCore.get(id) || rootId;
      const parent = positions[parentId] || positions[rootId];
      const vector = normalizedVector(zoneByCore.get(parentId) || { x: 1, y: 0 });
      zoneByCore.set(id, vector);
      positions[id] = {
        x: parent.x + vector.x * treeStepX,
        y: parent.y + vector.y * treeStepX
      };
    });
  });

  coreIds
    .filter((id) => !positions[id])
    .forEach((id, index) => {
      const vector = zoneVector(index, Math.max(1, coreIds.length));
      zoneByCore.set(id, vector);
      positions[id] = {
        x: rootCenter.x + vector.x * coreRadius - cardWidth / 2,
        y: rootCenter.y + vector.y * coreRadius - cardHeight / 2
      };
    });

  coreIds.forEach((parentId) => {
    placeEndpointZone(childrenByParent.get(parentId) || [], parentId, rootId, positions, {
      rootCenter,
      cardWidth,
      cardHeight,
      endpointRadius,
      endpointSpread,
      deviceMap
    });
  });

  if (peripheralWithoutParent.length) {
    const orphanIds = peripheralWithoutParent.map((device) => device.id).sort(compareTopologyDeviceIds(deviceMap));
    const grid = layoutTopologyGrid(orphanIds, endpointRadius, cardHeight + 56, Math.max(2, Math.ceil(Math.sqrt(orphanIds.length))), cardWidth, cardHeight);
    grid.items.forEach((item) => {
      positions[item.id] = { x: margin + item.x, y: logicalHeightBase + item.y };
    });
  }

  const allPositionedIds = devices.map((device) => device.id).filter((id) => positions[id]);
  applySavedTopologyLayouts(positions, devices, ignoredSavedLayoutIds);
  const bounds = getTopologyBounds(positions, allPositionedIds, deviceMap, cardWidth, cardHeight);
  if (bounds.minX < margin) {
    const shift = margin - bounds.minX;
    allPositionedIds.forEach((id) => {
      positions[id].x += shift;
    });
    bounds.maxX += shift;
  }
  if (bounds.minY < margin) {
    const shift = margin - bounds.minY;
    allPositionedIds.forEach((id) => {
      positions[id].y += shift;
    });
    bounds.maxY += shift;
  }

  return {
    positions,
    width: Math.max(logicalWidthBase, bounds.maxX + margin),
    height: Math.max(logicalHeightBase, bounds.maxY + margin),
    focus: positions[rootId]
      ? { x: positions[rootId].x + cardWidth / 2, y: positions[rootId].y + cardHeight / 2 }
      : rootCenter
  };
}

function fitShowcaseZoom(logicalWidth, logicalHeight, stageWidth, stageHeight) {
  const fit = Math.min(1, (stageWidth - 8) / Math.max(1, logicalWidth), (stageHeight - 8) / Math.max(1, logicalHeight));
  return clamp(fit, TOPOLOGY_MIN_ZOOM, 1);
}

function updateShowcaseZoomControls() {
  const cleanUp = document.getElementById("showcaseCleanUp");
  if (cleanUp) cleanUp.disabled = appState.topologyCleaning || getDevices().length === 0;
  const zoomIn = document.getElementById("showcaseZoomIn");
  const zoomOut = document.getElementById("showcaseZoomOut");
  if (!zoomIn || !zoomOut) return;
  zoomIn.disabled = appState.topologyZoom >= 1.75;
  zoomOut.disabled = appState.topologyZoom <= TOPOLOGY_MIN_ZOOM;
}

function clampShowcasePan(pan = appState.topologyPan) {
  const stage = document.getElementById("showcaseMap");
  if (!stage) return { x: 0, y: 0 };
  const zoom = appState.topologyZoom || 1;
  const logicalWidth = appState.topologyLogicalSize.width || stage.clientWidth || 1;
  const logicalHeight = appState.topologyLogicalSize.height || stage.clientHeight || 1;
  const scaledWidth = logicalWidth * zoom;
  const scaledHeight = logicalHeight * zoom;
  const slack = 180;
  const minX = scaledWidth <= stage.clientWidth ? stage.clientWidth - scaledWidth - slack : stage.clientWidth - scaledWidth;
  const minY = scaledHeight <= stage.clientHeight ? stage.clientHeight - scaledHeight - slack : stage.clientHeight - scaledHeight;
  const maxX = scaledWidth <= stage.clientWidth ? slack : 0;
  const maxY = scaledHeight <= stage.clientHeight ? slack : 0;
  return {
    x: clamp(pan.x, minX, maxX),
    y: clamp(pan.y, minY, maxY)
  };
}

function applyShowcasePan() {
  const stage = document.getElementById("showcaseMap");
  if (!stage) return;
  appState.topologyPan = clampShowcasePan(appState.topologyPan);
  stage.style.setProperty("--topology-pan-x", `${appState.topologyPan.x.toFixed(1)}px`);
  stage.style.setProperty("--topology-pan-y", `${appState.topologyPan.y.toFixed(1)}px`);
  requestAnimationFrame(renderShowcaseLinks);
}

function centerShowcaseOn(point) {
  const stage = document.getElementById("showcaseMap");
  if (!stage || !point) return;
  const zoom = appState.topologyZoom || 1;
  appState.topologyPan = {
    x: stage.clientWidth / 2 - point.x * zoom,
    y: stage.clientHeight / 2 - point.y * zoom
  };
  applyShowcasePan();
}

function applyShowcaseZoom() {
  const stage = document.getElementById("showcaseMap");
  if (stage) stage.style.setProperty("--topology-zoom", appState.topologyZoom.toFixed(3));
  applyShowcasePan();
  updateShowcaseZoomControls();
  requestAnimationFrame(renderShowcaseLinks);
}

function setShowcaseZoom(value, userSet = true) {
  appState.topologyZoom = clamp(value, TOPOLOGY_MIN_ZOOM, 1.75);
  if (userSet) appState.topologyZoomUserSet = true;
  applyShowcaseZoom();
}

function getAppliedShowcaseZoom() {
  const stage = document.getElementById("showcaseMap");
  const applied = Number.parseFloat(getComputedStyle(stage).getPropertyValue("--topology-zoom"));
  return Number.isFinite(applied) ? applied : appState.topologyZoom || 1;
}

function logicalPointFromClient(event, stageRect, zoom) {
  return {
    x: (event.clientX - stageRect.left - appState.topologyPan.x) / zoom,
    y: (event.clientY - stageRect.top - appState.topologyPan.y) / zoom
  };
}

function defaultShowcasePosition(device, index, total, width, height, cardWidth, cardHeight) {
  if (total === 2) {
    const presets = [
      { x: width * 0.14, y: height * 0.24 },
      { x: width * 0.52, y: height * 0.14 }
    ];
    return {
      x: clamp(presets[index].x, 32, Math.max(32, width - cardWidth - 36)),
      y: clamp(presets[index].y, 126, Math.max(126, height - cardHeight - 42))
    };
  }

  const layout = device.layout || {};
  if (typeof layout.x === "number" && typeof layout.y === "number") {
    const x = 56 + (layout.x / 900) * Math.max(1, width - cardWidth - 150);
    const y = 138 + (layout.y / 540) * Math.max(1, height - cardHeight - 260);
    return {
      x: clamp(x, 32, Math.max(32, width - cardWidth - 36)),
      y: clamp(y, 126, Math.max(126, height - cardHeight - 42))
    };
  }

  const span = Math.max(1, total - 1);
  const x = width * (0.14 + (index / span) * 0.48);
  const y = height * (0.22 + (index % 3) * 0.18);
  return {
    x: clamp(x, 32, Math.max(32, width - cardWidth - 36)),
    y: clamp(y, 126, Math.max(126, height - cardHeight - 42))
  };
}

function getLogicalNodeBox(node, stageRect, zoom) {
  const rect = node.getBoundingClientRect();
  return {
    left: (rect.left - stageRect.left - appState.topologyPan.x) / zoom,
    top: (rect.top - stageRect.top - appState.topologyPan.y) / zoom,
    width: rect.width / zoom,
    height: rect.height / zoom,
    get right() {
      return this.left + this.width;
    },
    get bottom() {
      return this.top + this.height;
    },
    get cx() {
      return this.left + this.width / 2;
    },
    get cy() {
      return this.top + this.height / 2;
    }
  };
}

function getStableLogicalNodeBox(node) {
  const position = appState.topologyPositions[node.dataset.showcaseNode] || {
    x: Number.parseFloat(node.style.left) || 0,
    y: Number.parseFloat(node.style.top) || 0
  };
  return {
    left: position.x,
    top: position.y,
    width: node.offsetWidth,
    height: node.offsetHeight,
    get right() {
      return this.left + this.width;
    },
    get bottom() {
      return this.top + this.height;
    },
    get cx() {
      return this.left + this.width / 2;
    },
    get cy() {
      return this.top + this.height / 2;
    }
  };
}

function getEdgePoint(box, targetBox) {
  const dx = targetBox.cx - box.cx;
  const dy = targetBox.cy - box.cy;
  if (Math.abs(dx) >= Math.abs(dy)) {
    return {
      x: dx >= 0 ? box.right : box.left,
      y: clamp(targetBox.cy, box.top + 24, box.bottom - 24)
    };
  }
  return {
    x: clamp(targetBox.cx, box.left + 28, box.right - 28),
    y: dy >= 0 ? box.bottom : box.top
  };
}

function showcaseLinkPoints(fromPoint, toPoint) {
  const dx = Math.abs(toPoint.x - fromPoint.x);
  const dy = Math.abs(toPoint.y - fromPoint.y);
  if (dx >= dy) {
    const midX = (fromPoint.x + toPoint.x) / 2;
    return [fromPoint, { x: midX, y: fromPoint.y }, { x: midX, y: toPoint.y }, toPoint];
  }
  const midY = (fromPoint.y + toPoint.y) / 2;
  return [fromPoint, { x: fromPoint.x, y: midY }, { x: toPoint.x, y: midY }, toPoint];
}

function pointsToSvgPath(points) {
  return points.map((point, index) => `${index === 0 ? "M" : "L"} ${point.x} ${point.y}`).join(" ");
}

function pathScore(points, fromId, toId, boxesById) {
  let hits = 0;
  let length = 0;
  for (let index = 0; index < points.length - 1; index += 1) {
    const a = points[index];
    const b = points[index + 1];
    const segmentLength = Math.abs(b.x - a.x) + Math.abs(b.y - a.y);
    length += segmentLength;
    const steps = Math.max(3, Math.ceil(segmentLength / 28));
    for (let step = 1; step < steps; step += 1) {
      const p = {
        x: a.x + ((b.x - a.x) * step) / steps,
        y: a.y + ((b.y - a.y) * step) / steps
      };
      for (const [id, box] of boxesById) {
        if (id === fromId || id === toId) continue;
        if (p.x > box.left + 8 && p.x < box.right - 8 && p.y > box.top + 8 && p.y < box.bottom - 8) {
          hits += 1;
          break;
        }
      }
    }
  }
  return hits * 1_000_000 + length;
}

function chooseShowcaseLinkPoints(fromPoint, toPoint, fromId, toId, boxesById) {
  const boxes = [...boxesById.values()];
  const minX = Math.min(...boxes.map((box) => box.left));
  const maxX = Math.max(...boxes.map((box) => box.right));
  const minY = Math.min(...boxes.map((box) => box.top));
  const maxY = Math.max(...boxes.map((box) => box.bottom));
  const xLanes = [Math.min(fromPoint.x, toPoint.x) - 72, Math.max(fromPoint.x, toPoint.x) + 72, minX - 76, maxX + 76];
  const yLanes = [Math.min(fromPoint.y, toPoint.y) - 72, Math.max(fromPoint.y, toPoint.y) + 72, minY - 72, maxY + 72];
  const candidates = [
    showcaseLinkPoints(fromPoint, toPoint),
    ...xLanes.map((x) => [fromPoint, { x, y: fromPoint.y }, { x, y: toPoint.y }, toPoint]),
    ...yLanes.map((y) => [fromPoint, { x: fromPoint.x, y }, { x: toPoint.x, y }, toPoint])
  ];
  return candidates
    .map((points) => ({ points, score: pathScore(points, fromId, toId, boxesById) }))
    .sort((a, b) => a.score - b.score)[0].points;
}

function renderShowcaseLinks() {
  const stage = document.getElementById("showcaseMap");
  const plane = document.getElementById("showcaseMapPlane");
  const svg = document.getElementById("showcaseLinkLayer");
  if (!stage || !plane || !svg) return;

  const logicalWidth = appState.topologyLogicalSize.width || stage.clientWidth || 1;
  const logicalHeight = appState.topologyLogicalSize.height || stage.clientHeight || 1;
  const devices = getDevices();
  const linksToRender = getRenderableTopologyLinks(devices, appState.snapshot.links || []);
  svg.setAttribute("viewBox", `0 0 ${logicalWidth} ${logicalHeight}`);
  svg.setAttribute("width", String(logicalWidth));
  svg.setAttribute("height", String(logicalHeight));
  svg.innerHTML = linksToRender
    .map((link) => {
      const from = document.querySelector(`[data-showcase-node="${CSS.escape(link.from)}"]`);
      const to = document.querySelector(`[data-showcase-node="${CSS.escape(link.to)}"]`);
      if (!from || !to) return "";
      const fromBox = getStableLogicalNodeBox(from);
      const toBox = getStableLogicalNodeBox(to);
      const fromPoint = getEdgePoint(fromBox, toBox);
      const toPoint = getEdgePoint(toBox, fromBox);
      const isManagementLink = link.directness === "management" || link.link_type === "management";
      const dash = link.line_style === "solid" || link.status === "confirmed" ? "0" : "8 7";
      const opacity = Math.max(0.35, Math.min(1, Number(link.confidence || 45) / 100));
      const label = `${isManagementLink ? "MGMT · " : ""}${[link.local_port, link.remote_port].filter(Boolean).join(" -> ")}`;
      const labelSvg = label
        ? `<text x="${(fromPoint.x + toPoint.x) / 2}" y="${(fromPoint.y + toPoint.y) / 2 - 7}" class="showcase-link-label">${escapeHtml(label)}</text>`
        : "";
      return `<g data-link-from="${escapeHtml(link.from)}" data-link-to="${escapeHtml(link.to)}" data-link-type="${isManagementLink ? "management" : "network"}"><title>${escapeHtml(topologyLinkTitle(link))}</title><line x1="${fromPoint.x}" y1="${fromPoint.y}" x2="${toPoint.x}" y2="${toPoint.y}" stroke="${isManagementLink ? "#d45d39" : "#263746"}" stroke-width="${isManagementLink ? "3" : "2"}" stroke-opacity="${opacity}" stroke-dasharray="${dash}" stroke-linecap="round" />${labelSvg}</g>`;
    })
    .join("");
}

function bindShowcaseDrag() {
  document.querySelectorAll("[data-showcase-node]").forEach((node) => {
    node.addEventListener("pointerdown", (event) => {
      if (event.button !== 0) return;
      const stage = document.getElementById("showcaseMap");
      if (!stage) return;
      const nodeRect = node.getBoundingClientRect();
      const zoom = appState.topologyZoom || 1;
      appState.draggingNode = {
        id: node.dataset.showcaseNode,
        pointerId: event.pointerId,
        startX: event.clientX,
        startY: event.clientY,
        moved: false,
        offsetX: (event.clientX - nodeRect.left) / zoom,
        offsetY: (event.clientY - nodeRect.top) / zoom,
        width: node.offsetWidth,
        height: node.offsetHeight
      };
      node.classList.add("is-dragging");
      node.setPointerCapture(event.pointerId);
    });

    node.addEventListener("pointermove", (event) => {
      const drag = appState.draggingNode;
      if (!drag || drag.pointerId !== event.pointerId || drag.id !== node.dataset.showcaseNode) return;
      if (Math.hypot(event.clientX - drag.startX, event.clientY - drag.startY) > 6) {
        drag.moved = true;
      }
      if (!drag.moved) return;
      const stage = document.getElementById("showcaseMap");
      const stageRect = stage.getBoundingClientRect();
      const zoom = appState.topologyZoom || 1;
      const logicalWidth = appState.topologyLogicalSize.width || stageRect.width / zoom;
      const logicalHeight = appState.topologyLogicalSize.height || stageRect.height / zoom;
      const point = logicalPointFromClient(event, stageRect, zoom);
      const x = clamp(point.x - drag.offsetX, 24, logicalWidth - drag.width - 24);
      const y = clamp(point.y - drag.offsetY, 112, logicalHeight - drag.height - 24);
      appState.topologyPositions[drag.id] = { x, y };
      node.style.left = `${x}px`;
      node.style.top = `${y}px`;
      renderShowcaseLinks();
    });

    node.addEventListener("pointerup", (event) => {
      const drag = appState.draggingNode;
      if (drag?.pointerId === event.pointerId) {
        node.classList.remove("is-dragging");
        appState.draggingNode = null;
        if (drag.moved) {
          saveTopologyLayoutSoon();
        } else if (drag.id) {
          openMonitorForDevice(drag.id);
        }
      }
    });

    node.addEventListener("pointercancel", () => {
      node.classList.remove("is-dragging");
      appState.draggingNode = null;
    });
  });
}

function bindShowcasePan() {
  const stage = document.getElementById("showcaseMap");
  if (!stage || stage.dataset.panBound === "true") return;
  stage.dataset.panBound = "true";

  stage.addEventListener("pointerdown", (event) => {
    if (event.button !== 0 || !event.metaKey || event.target.closest("[data-showcase-node]")) return;
    event.preventDefault();
    appState.panningMap = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      panX: appState.topologyPan.x,
      panY: appState.topologyPan.y
    };
    stage.classList.add("is-panning");
    stage.setPointerCapture(event.pointerId);
  });

  stage.addEventListener("pointermove", (event) => {
    const pan = appState.panningMap;
    if (!pan || pan.pointerId !== event.pointerId) return;
    event.preventDefault();
    appState.topologyPan = {
      x: pan.panX + event.clientX - pan.startX,
      y: pan.panY + event.clientY - pan.startY
    };
    applyShowcasePan();
  });

  const stopPan = (event) => {
    if (!appState.panningMap || appState.panningMap.pointerId !== event.pointerId) return;
    appState.panningMap = null;
    stage.classList.remove("is-panning");
    applyShowcasePan();
  };

  stage.addEventListener("pointerup", stopPan);
  stage.addEventListener("pointercancel", stopPan);

  window.addEventListener("keydown", (event) => {
    if (event.metaKey) stage.classList.add("is-pan-ready");
  });
  window.addEventListener("keyup", (event) => {
    if (!event.metaKey) stage.classList.remove("is-pan-ready");
  });
}

async function cleanUpShowcaseTopology() {
  if (appState.topologyCleaning || getDevices().length === 0) return;
  const button = document.getElementById("showcaseCleanUp");
  const status = document.getElementById("showcaseLayoutStatus");
  appState.topologyCleaning = true;
  button?.setAttribute("aria-busy", "true");
  updateShowcaseZoomControls();
  if (status) status.textContent = "Saving layout...";
  try {
    renderShowcaseTopology({ resetLayout: true });
    await saveTopologyLayout();
    if (status) status.textContent = "Layout saved";
  } catch (error) {
    if (status) status.textContent = "Layout not saved. Try again.";
    console.warn("Topology clean up failed", error);
  } finally {
    appState.topologyCleaning = false;
    button?.removeAttribute("aria-busy");
    updateShowcaseZoomControls();
  }
}

function renderShowcaseTopology({ resetLayout = false } = {}) {
  const stage = document.getElementById("showcaseMap");
  const plane = document.getElementById("showcaseMapPlane");
  const nodes = document.getElementById("showcaseNodeLayer");
  const svg = document.getElementById("showcaseLinkLayer");
  if (!stage || !plane || !nodes || !svg) return;
  renderTopologyEvidencePanel();

  const width = stage.clientWidth || window.innerWidth;
  const height = stage.clientHeight || window.innerHeight;
  const cardWidth = clamp(window.innerWidth * 0.15, 220, 288);
  const cardHeight = 260;
  const devices = getDevices();
  const links = appState.snapshot.links || [];
  const structure = getTopologyStructure(devices, links);
  const signature = getTopologySignature(devices, structure);
  const structureChanged = signature !== appState.topologyLayoutSignature;
  const affectedLayoutIds = structureChanged
    ? getAffectedTopologyLayoutIds(appState.topologyStructure, structure)
    : new Set();
  const migrationLayoutIds = appState.topologyLayoutMigrationPending || resetLayout
    ? new Set(devices.map((device) => device.id))
    : new Set();
  const ignoredSavedLayoutIds = new Set([...affectedLayoutIds, ...migrationLayoutIds]);
  let shouldCenterOnFocus = false;

  if (devices.length === 0) {
    svg.innerHTML = "";
    plane.style.width = "100%";
    plane.style.height = "100%";
    appState.topologyLogicalSize = { width: Math.max(1, width), height: Math.max(1, height) };
    appState.topologyLayoutSignature = signature;
    appState.topologyStructure = structure;
    appState.topologyPositions = {};
    appState.topologyZoomUserSet = false;
    appState.topologyPan = { x: 0, y: 0 };
    shouldCenterOnFocus = true;
    setShowcaseZoom(1, false);
    nodes.innerHTML = `
      <div class="showcase-empty">
        <strong>No topology yet</strong>
        <span>Add a live SNMP seed to discover LLDP neighbors.</span>
      </div>
    `;
    return;
  }

  const layout = computeShowcaseTopologyLayout(devices, links, width, height, cardWidth, cardHeight, {
    ignoredSavedLayoutIds
  });
  const hadLayout = Boolean(appState.topologyLayoutSignature);
  if (structureChanged || resetLayout) {
    const previousPositions = appState.topologyPositions || {};
    appState.topologyPositions = devices.reduce((positions, device) => {
      positions[device.id] = resetLayout || affectedLayoutIds.has(device.id)
        ? layout.positions[device.id]
        : previousPositions[device.id] || layout.positions[device.id];
      return positions;
    }, {});
    appState.topologyLayoutSignature = signature;
    appState.topologyStructure = structure;
    if (!hadLayout || resetLayout) {
      appState.topologyZoomUserSet = false;
      appState.topologyPan = { x: 0, y: 0 };
      shouldCenterOnFocus = true;
    }
  } else {
    devices.forEach((device) => {
      appState.topologyPositions[device.id] ||= layout.positions[device.id];
    });
  }

  appState.topologyLogicalSize = { width: layout.width, height: layout.height };
  plane.style.width = `${layout.width}px`;
  plane.style.height = `${layout.height}px`;
  nodes.style.width = `${layout.width}px`;
  nodes.style.height = `${layout.height}px`;
  if (!appState.topologyZoomUserSet) {
    setShowcaseZoom(fitShowcaseZoom(layout.width, layout.height, width, height), false);
    if (shouldCenterOnFocus) centerShowcaseOn({ x: layout.width / 2, y: layout.height / 2 });
  } else {
    applyShowcaseZoom();
  }

  nodes.innerHTML = devices
    .map((device, index) => {
      const saved = appState.topologyPositions[device.id];
      const pos = saved || layout.positions[device.id] || defaultShowcasePosition(device, index, devices.length, width, height, cardWidth, cardHeight);
      appState.topologyPositions[device.id] = pos;
      const status = device.status || "unknown";
      const model = device.model || "Unknown";
      const endpointTraffic = getEndpointTraffic(device);
      const source = device.device_type === "endpoint"
        ? device.mac || device.fingerprint
        : device.device_type === "segment"
          ? `${device.observed_mac_count || 0} MACs`
          : device.ip;
      const metaLines = device.device_type === "endpoint"
        ? [
            source || "unknown",
            device.observed_local_port || device.vendor || "observed endpoint",
            endpointTraffic ? formatEndpointTraffic(device) : (device.vendor || model)
          ]
        : device.device_type === "segment"
          ? [
              source || "multi-MAC",
              device.observed_local_port || "shared switch port",
              device.observed_local_port_alias || model
            ]
        : [source || "unknown", device.vendor || "Unknown", model];
      const kindClass = isTopologySegment(device)
        ? "is-segment"
        : isTopologyEndpoint(device)
          ? "is-endpoint"
          : device.status === "pending"
            ? "is-pending"
            : "is-seed";
      return `
        <button class="showcase-node ${status} ${kindClass}" type="button" style="left:${pos.x}px;top:${pos.y}px" data-showcase-node="${device.id}">
          <span class="showcase-node-title">${escapeHtml(device.name || "Unknown")}</span>
          <span class="showcase-status ${status}">${statusLabel(status)}</span>
          <span class="showcase-meta">${metaLines.map((line) => escapeHtml(line)).join("<br>")}</span>
        </button>
      `;
    })
    .join("");

  bindShowcaseDrag();
  bindShowcasePan();
  requestAnimationFrame(renderShowcaseLinks);
  if (!resetLayout && ((hadLayout && affectedLayoutIds.size > 0) || appState.topologyLayoutMigrationPending)) {
    saveTopologyLayoutSoon();
  }
}

function startParticleOrb() {
  const canvas = document.getElementById("particleOrbCanvas");
  if (!canvas || appState.particleAnimation) return;
  const context = canvas.getContext("2d");
  if (!context) return;
  const isSafari = /^((?!chrome|android).)*safari/i.test(navigator.userAgent);
  const particleCount = isSafari ? 240 : 300;
  const depthBuckets = Array.from({ length: 6 }, () => []);
  const particles = Array.from({ length: particleCount }, (_, index) => {
    // Fibonacci distribution avoids visible bands without randomizing on every refresh.
    const y = 1 - ((index + 0.5) / particleCount) * 2;
    const radial = Math.sqrt(Math.max(0, 1 - y * y));
    const theta = index * Math.PI * (3 - Math.sqrt(5));
    const shell = 0.38 + (((index * 73) % particleCount) / particleCount) * 0.62;
    return {
      x: Math.cos(theta) * radial * shell,
      y: y * shell,
      z: Math.sin(theta) * radial * shell,
      screenX: 0,
      screenY: 0,
      size: 1,
      bucket: 0
    };
  });
  let visible = false;
  let cssWidth = 1;
  let cssHeight = 1;
  let resizePending = true;
  let lastDraw = -Infinity;
  const targetFrameMs = 1000 / 30;

  canvas.dataset.renderer = "particle-orb-canvas";
  canvas.dataset.particleCount = String(particleCount);
  canvas.dataset.targetFps = "30";

  function requestDraw() {
    if (visible && !document.hidden && !appState.particleAnimation) {
      appState.particleAnimation = window.requestAnimationFrame(draw);
    }
  }

  updateParticleVisibility = (nextVisible) => {
    if (visible === nextVisible) return;
    visible = nextVisible;
    if (visible) {
      lastDraw = -Infinity;
      requestDraw();
    } else {
      window.cancelAnimationFrame(appState.particleAnimation);
      appState.particleAnimation = null;
    }
  };

  new ResizeObserver(([entry]) => {
    cssWidth = Math.max(1, entry.contentRect.width);
    cssHeight = Math.max(1, entry.contentRect.height);
    resizePending = true;
    requestDraw();
  }).observe(canvas);
  reducedMotion.addEventListener("change", requestDraw);

  function draw(time) {
    appState.particleAnimation = null;
    if (!visible || document.hidden) return;
    if (!resizePending && !reducedMotion.matches && time - lastDraw < targetFrameMs - 1) {
      requestDraw();
      return;
    }
    lastDraw = time;
    const scale = Math.min(window.devicePixelRatio || 1, isSafari ? 1.25 : 1.5);
    if (resizePending) {
      canvas.width = Math.round(cssWidth * scale);
      canvas.height = Math.round(cssHeight * scale);
      resizePending = false;
    }
    context.setTransform(scale, 0, 0, scale, 0, 0);
    context.clearRect(0, 0, cssWidth, cssHeight);
    const t = reducedMotion.matches ? 0 : time * (Math.PI * 2 / 18000);
    const sinY = Math.sin(t);
    const cosY = Math.cos(t);
    const sinX = Math.sin(t * 0.43 + 0.6);
    const cosX = Math.cos(t * 0.43 + 0.6);
    const centerX = cssWidth * 0.48;
    const centerY = cssHeight * 0.53;
    const radiusX = Math.max(cssWidth * 0.52, cssHeight * 0.6);
    const radiusY = cssHeight * 0.64;

    depthBuckets.forEach((bucket) => { bucket.length = 0; });
    for (const particle of particles) {
      const rotatedX = particle.x * cosY - particle.z * sinY;
      const rotatedZ = particle.x * sinY + particle.z * cosY;
      const rotatedY = particle.y * cosX - rotatedZ * sinX;
      const depthZ = particle.y * sinX + rotatedZ * cosX;
      const perspective = 1 / (1.34 - depthZ * 0.34);
      particle.screenX = centerX + rotatedX * radiusX * perspective;
      particle.screenY = centerY + rotatedY * radiusY * perspective;
      particle.size = 0.7 + perspective * 1.25;
      particle.bucket = Math.max(0, Math.min(depthBuckets.length - 1, Math.floor((depthZ + 1) * 3)));
      depthBuckets[particle.bucket].push(particle);
    }

    depthBuckets.forEach((bucket, bucketIndex) => {
      const alpha = 0.09 + bucketIndex * 0.032;
      context.fillStyle = `rgba(63, 66, 67, ${alpha})`;
      context.beginPath();
      for (const particle of bucket) {
        if (
          particle.screenX < -8 || particle.screenX > cssWidth + 8
          || particle.screenY < -8 || particle.screenY > cssHeight + 8
        ) continue;
        context.moveTo(particle.screenX + particle.size, particle.screenY);
        context.arc(particle.screenX, particle.screenY, particle.size, 0, Math.PI * 2);
      }
      context.fill();
    });
    if (!reducedMotion.matches) requestDraw();
  }
}

function renderAlerts() {
  const target = document.getElementById("alertList");
  if (!target) return;
  if (appState.snapshot.alerts.length === 0) {
    target.innerHTML = `
      <article class="alert-item">
        <div>
          <strong>No active alerts</strong>
          <p class="muted">Live SNMP import will create basic admin-up/oper-down alerts.</p>
        </div>
      </article>
    `;
    return;
  }
  target.innerHTML = appState.snapshot.alerts
    .map((alert) => {
      const device = getDeviceById(alert.device_id);
      const canAck = alert.state === "active";
      const canResolve = alert.state !== "resolved";
      return `
        <article class="alert-item">
          <div>
            <strong>${alert.title}</strong>
            <p class="muted">${device ? device.name : "unknown device"} - ${alert.detail}</p>
          </div>
          <span class="status-pill ${alert.state}">${stateLabel(alert.state)}</span>
          <div class="alert-actions">
            <button class="small-button" type="button" data-alert-action="ack" data-alert-id="${alert.id}" ${canAck ? "" : "disabled"}>Ack</button>
            <button class="small-button" type="button" data-alert-action="resolve" data-alert-id="${alert.id}" ${canResolve ? "" : "disabled"}>Resolve</button>
          </div>
        </article>
      `;
    })
    .join("");

  bindAlertActionButtons(target);
}

function renderSettings() {
  const polling = appState.snapshot.settings.polling || {};
  const security = appState.snapshot.settings.security || {};
  const pollingSettings = document.getElementById("pollingSettings");
  const securitySettings = document.getElementById("securitySettings");
  const metricCatalog = document.getElementById("metricCatalog");
  const liveModeBadge = document.getElementById("liveModeBadge");
  if (pollingSettings) {
    pollingSettings.innerHTML = Object.entries(polling)
      .map(([key, value]) => `<div><dt>${key.replaceAll("_", " ")}</dt><dd>${value}</dd></div>`)
      .join("");
  }
  if (securitySettings) {
    securitySettings.innerHTML = Object.entries(security)
      .map(([key, value]) => `<div><dt>${key.replaceAll("_", " ")}</dt><dd>${value}</dd></div>`)
      .join("");
  }
  if (metricCatalog) {
    const catalogMetrics = appState.snapshot.metric_catalog_details?.metrics || [];
    const metricNames = appState.snapshot.metric_catalog || [];
    const chips = catalogMetrics.length
      ? catalogMetrics.map((metric) => ({
          name: metric.name,
          title: `${metric.target_type || "target"} / ${metric.value_type || "value"} / ${metric.unit || "unit"}`
        }))
      : metricNames.map((name) => ({ name, title: name }));
    metricCatalog.innerHTML = chips
      .map((metric) => `<span class="metric-chip" title="${escapeHtml(metric.title)}">${escapeHtml(metric.name)}</span>`)
      .join("");
  }
  if (liveModeBadge) {
    liveModeBadge.textContent = `${appState.snapshot.mode || "mock"} mode`;
    liveModeBadge.className = `status-pill ${appState.snapshot.mode === "live" ? "up" : "neutral"}`;
  }
  const backendPoll = Boolean(polling.backend_auto_poll);
  const interval = Number(polling.backend_interval_seconds || 30);
  const legacyToggle = document.getElementById("autoPollToggle");
  const presentationToggle = document.getElementById("presentationBackendPollToggle");
  const presentationInterval = document.getElementById("presentationPollInterval");
  if (legacyToggle) legacyToggle.checked = backendPoll;
  if (presentationToggle) presentationToggle.checked = backendPoll;
  if (presentationInterval) presentationInterval.value = String(interval);
  updateSeedSummary();
  updatePollActionButtons();
  syncSeedVersionFields();
}

function switchView(view, behavior = "smooth") {
  if (view !== "history") {
    setHistoryOverlayOpen(false, false);
  }

  if (view === "history") {
    const deviceId = appState.historyDeviceId || appState.selectedDeviceId || getDevices()[0]?.id;
    if (deviceId) openDeviceHistory(deviceId, behavior);
    return;
  }

  if (view === "home") {
    enterPresentationMode("home", behavior);
    return;
  }

  if (view === "topology") {
    enterPresentationMode("topology", behavior);
    return;
  }

  if (view === "dashboard") {
    enterPresentationMode("dashboard", behavior);
    return;
  }

  if (view === "devices") {
    enterPresentationMode("devices", behavior);
    return;
  }

  enterPresentationMode("dashboard", behavior);
}

async function runPoll() {
  const result = await apiPost("/api/poll");
  appState.snapshot = result.snapshot;
  renderAll();
}

async function toggleBackendPoll() {
  const polling = getPollingSettings();
  const enabled = Boolean(polling.backend_auto_poll);
  const interval = Number(polling.backend_interval_seconds || 30) || 30;
  updatePollActionButtons(true);
  try {
    if (enabled) {
      await setBackendPolling(false, interval);
      return;
    }
    await runPoll();
    await setBackendPolling(true, 30);
  } finally {
    updatePollActionButtons(false);
  }
}

async function runDiscovery() {
  const result = await apiPost("/api/discovery");
  appState.snapshot = result.snapshot;
  renderAll();
  switchView("topology");
}

async function clearLiveInventory(resultId = "seedResult") {
  stopAutoPoll();
  const result = await apiPost("/api/live/clear");
  appState.snapshot = result.snapshot;
  appState.selectedDeviceId = "";
  const resultEl = document.getElementById(resultId);
  if (resultEl) {
    resultEl.className = "seed-result success";
    resultEl.textContent = "Live data and saved seed credentials cleared. Add a seed switch to start discovery.";
  }
  renderAll();
}

async function setBackendPolling(enabled, intervalSeconds = 30) {
  const result = await apiPostJson("/api/polling", {
    enabled,
    interval_seconds: Number(intervalSeconds || 30)
  });
  appState.snapshot = result.snapshot;
  renderAll();
  return result;
}

async function submitSeed(event) {
  event.preventDefault();
  const resultEl = document.getElementById("seedResult");
  const submit = document.getElementById("seedSubmitButton");
  const payload = {
    host: document.getElementById("seedHost").value.trim(),
    port: Number(document.getElementById("seedPort").value || 161),
    version: document.getElementById("seedVersion").value,
    community: document.getElementById("seedCommunity").value,
    username: document.getElementById("seedUsername").value,
    auth_key: document.getElementById("seedAuthKey").value,
    priv_key: document.getElementById("seedPrivKey").value,
    auth_protocol: document.getElementById("seedAuthProtocol").value,
    priv_protocol: document.getElementById("seedPrivProtocol").value
  };
  resultEl.className = "seed-result";
  resultEl.textContent = "Testing SNMP and reading IF-MIB/LLDP-MIB...";
  submit.disabled = true;
  try {
    const result = await apiPostJson("/api/live/seed", payload);
    appState.snapshot = result.snapshot;
    appState.selectedDeviceId = result.snapshot.devices[0]?.id || "";
    resultEl.className = "seed-result success";
    if (result.counts.interfaces === 0) {
      resultEl.className = "seed-result error";
      resultEl.textContent = `SNMP system works, but IF-MIB returned 0 interfaces. Check that the read-only view includes IF-MIB/ifTable/ifXTable.`;
    } else {
      resultEl.textContent = `Imported ${result.system.sys_name || payload.host}: ${result.counts.interfaces} interfaces, ${result.counts.lldp_candidates} LLDP candidates. Run poll again to calculate bps rates.`;
    }
    renderAll();
    await setBackendPolling(document.getElementById("autoPollToggle").checked, 30);
    switchView("devices");
  } catch (error) {
    resultEl.className = "seed-result error";
    resultEl.textContent = `SNMP test failed: ${error.message}`;
  } finally {
    submit.disabled = false;
  }
}

async function submitPresentationSeed(event) {
  event.preventDefault();
  const resultEl = document.getElementById("presentationSeedResult");
  const submit = document.getElementById("presentationSeedSubmitButton");
  const payload = {
    host: document.getElementById("presentationSeedHost").value.trim(),
    port: Number(document.getElementById("presentationSeedPort").value || 161),
    version: document.getElementById("presentationSeedVersion").value,
    community: document.getElementById("presentationSeedCommunity").value,
    username: document.getElementById("presentationSeedUsername").value,
    auth_key: document.getElementById("presentationSeedAuthKey").value,
    priv_key: document.getElementById("presentationSeedPrivKey").value,
    auth_protocol: document.getElementById("presentationSeedAuthProtocol").value,
    priv_protocol: document.getElementById("presentationSeedPrivProtocol").value
  };
  resultEl.className = "seed-result";
  resultEl.textContent = "Testing SNMP and reading IF-MIB/LLDP-MIB...";
  submit.disabled = true;
  try {
    const result = await apiPostJson("/api/live/seed", payload);
    appState.snapshot = result.snapshot;
    appState.selectedDeviceId = result.snapshot.devices[0]?.id || "";
    resultEl.className = result.counts.interfaces === 0 ? "seed-result error" : "seed-result success";
    resultEl.textContent = result.counts.interfaces === 0
      ? "SNMP system works, but IF-MIB returned 0 interfaces."
      : `Imported ${result.system.sys_name || payload.host}: ${result.counts.interfaces} interfaces, ${result.counts.lldp_candidates} LLDP candidates.`;
    renderAll();
    await setBackendPolling(
      document.getElementById("presentationBackendPollToggle").checked,
      Number(document.getElementById("presentationPollInterval").value || 30)
    );
    setLiveSetupOpen(false);
    switchView("devices");
  } catch (error) {
    resultEl.className = "seed-result error";
    resultEl.textContent = `SNMP test failed: ${error.message}`;
  } finally {
    submit.disabled = false;
  }
}

function startAutoPoll() {
  stopAutoPoll();
  const toggle = document.getElementById("autoPollToggle");
  if (!toggle || !toggle.checked) return;
  appState.autoPollTimer = window.setInterval(async () => {
    if ((appState.snapshot.mode || "mock") !== "live") return;
    try {
      await runPoll();
    } catch (error) {
      const streamState = document.getElementById("eventStreamState");
      if (streamState) streamState.textContent = `Poll failed: ${error.message}`;
    }
  }, 30000);
}

function stopAutoPoll() {
  if (appState.autoPollTimer) {
    window.clearInterval(appState.autoPollTimer);
    appState.autoPollTimer = null;
  }
}

function startSnapshotRefresh() {
  if (appState.snapshotRefreshTimer) {
    window.clearInterval(appState.snapshotRefreshTimer);
  }
  appState.snapshotRefreshTimer = window.setInterval(async () => {
    try {
      await loadSnapshot();
    } catch (error) {
      const streamState = document.getElementById("eventStreamState");
      if (streamState) streamState.textContent = `Refresh failed: ${error.message}`;
    }
  }, 10000);
}

function syncSeedVersionFields() {
  const version = document.getElementById("seedVersion")?.value || "2c";
  document.querySelectorAll(".v2-field").forEach((field) => {
    field.style.display = version === "2c" ? "grid" : "none";
  });
  document.querySelectorAll(".v3-field").forEach((field) => {
    field.style.display = version === "3" ? "grid" : "none";
  });
  const presentationVersion = document.getElementById("presentationSeedVersion")?.value || "2c";
  document.querySelectorAll(".presentation-v2-field").forEach((field) => {
    field.style.display = presentationVersion === "2c" ? "grid" : "none";
  });
  document.querySelectorAll(".presentation-v3-field").forEach((field) => {
    field.style.display = presentationVersion === "3" ? "grid" : "none";
  });
}

function renderAll() {
  renderKpis();
  renderDashboardDevices();
  renderEvents();
  renderDevicesTable();
  renderDeviceDetail();
  renderTopology();
  renderShowcaseTopology();
  renderTopologyEvidencePanel();
  renderDashboardOps();
  renderPresentationDashboard();
  renderPresentationDevices();
  renderDeviceHistory();
  renderAlerts();
  renderSettings();
}

function bindEvents() {
  document.querySelectorAll(".nav-item").forEach((item) => {
    item.addEventListener("click", () => switchView(item.dataset.view));
  });
  document.querySelectorAll("[data-view-target]").forEach((item) => {
    item.addEventListener("click", () => switchView(item.dataset.viewTarget));
  });
  document.getElementById("runPollButton")?.addEventListener("click", toggleBackendPoll);
  document.getElementById("runDiscoveryButton")?.addEventListener("click", runDiscovery);
  document.getElementById("presentationRunPoll")?.addEventListener("click", toggleBackendPoll);
  document.getElementById("showcaseZoomIn")?.addEventListener("click", () => setShowcaseZoom(getAppliedShowcaseZoom() + 0.14));
  document.getElementById("showcaseZoomOut")?.addEventListener("click", () => setShowcaseZoom(getAppliedShowcaseZoom() - 0.14));
  document.getElementById("showcaseCleanUp")?.addEventListener("click", cleanUpShowcaseTopology);
  document.getElementById("devicePrevButton")?.addEventListener("click", () => setSelectedDeviceIndex(appState.selectedDeviceIndex - 1));
  document.getElementById("deviceNextButton")?.addEventListener("click", () => setSelectedDeviceIndex(appState.selectedDeviceIndex + 1));
  document.querySelectorAll("[data-monitor-view]").forEach((button) => {
    button.addEventListener("click", () => setMonitorViewMode(button.dataset.monitorView));
  });
  document.getElementById("historyBackButton")?.addEventListener("click", () => setHistoryOverlayOpen(false));
  document.getElementById("presentationSetupButton")?.addEventListener("click", () => setLiveSetupOpen(true));
  document.getElementById("presentationClearAllButton")?.addEventListener("click", () => clearLiveInventory());
  document.getElementById("liveSetupClose")?.addEventListener("click", () => setLiveSetupOpen(false));
  document.getElementById("liveSetupBackdrop")?.addEventListener("click", () => setLiveSetupOpen(false));
  document.getElementById("presentationClearLiveButton")?.addEventListener("click", () => clearLiveInventory("presentationSeedResult"));
  document.getElementById("presentationSeedForm")?.addEventListener("submit", submitPresentationSeed);
  document.getElementById("presentationSeedVersion")?.addEventListener("change", syncSeedVersionFields);
  document.getElementById("presentationBackendPollToggle")?.addEventListener("change", async () => {
    await setBackendPolling(
      document.getElementById("presentationBackendPollToggle").checked,
      Number(document.getElementById("presentationPollInterval").value || 30)
    );
  });
  document.getElementById("presentationPollInterval")?.addEventListener("change", async () => {
    if (document.getElementById("presentationBackendPollToggle").checked) {
      await setBackendPolling(true, Number(document.getElementById("presentationPollInterval").value || 30));
    }
  });
  document.getElementById("clearLiveButton")?.addEventListener("click", () => clearLiveInventory("seedResult"));
  document.getElementById("seedForm")?.addEventListener("submit", submitSeed);
  document.getElementById("seedVersion")?.addEventListener("change", syncSeedVersionFields);
  document.getElementById("autoPollToggle")?.addEventListener("change", async () => {
    await setBackendPolling(document.getElementById("autoPollToggle").checked, 30);
  });
  window.addEventListener("resize", () => {
    cancelPresentationScroll();
    presentationLayout = null;
    renderTopology();
    renderShowcaseTopology();
    updateDeviceCarousel();
    updatePresentationProgress();
  });
  window.addEventListener("scroll", () => {
    if (Math.abs(window.scrollY - presentationWrittenTop) > 2) cancelPresentationScroll();
    if (!presentationScrollFrame) schedulePresentationProgress();
  }, { passive: true });
  const layoutObserver = new ResizeObserver(() => {
    presentationLayout = null;
    schedulePresentationProgress();
  });
  document.querySelectorAll(".landing-view, .map-showcase, .pattern-break, .dashboard-showcase, .devices-showcase")
    .forEach((section) => layoutObserver.observe(section));
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      cancelPresentationScroll();
      updateParticleVisibility(false);
    } else {
      schedulePresentationProgress();
    }
  });
  window.addEventListener("wheel", handlePresentationWheel, { passive: false });
  window.addEventListener("pointerdown", cancelPresentationScroll, { passive: true });
  window.addEventListener("touchstart", cancelPresentationScroll, { passive: true });
  reducedMotion.addEventListener("change", cancelPresentationScroll);
  window.addEventListener("hashchange", () => {
    const view = window.location.hash.replace("#", "");
    if (view === "history") {
      switchView("history", "auto");
    } else {
      setHistoryOverlayOpen(false, false);
      switchView(viewMeta[view] ? view : "home");
    }
  });
  window.addEventListener("keydown", (event) => {
    if (["ArrowDown", "ArrowUp", "PageDown", "PageUp", "Home", "End", " "].includes(event.key)) {
      cancelPresentationScroll();
    }
    if (event.key === "Escape" && appState.historyOverlayOpen) {
      setHistoryOverlayOpen(false);
    }
  });
}

function connectEvents() {
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  const socket = new WebSocket(`${protocol}://${window.location.host}/ws/events`);
  socket.addEventListener("open", () => {
    const streamState = document.getElementById("eventStreamState");
    if (streamState) streamState.textContent = "WebSocket connected";
    renderKpis();
    renderPresentationDashboard();
  });
  socket.addEventListener("message", async () => {
    await loadSnapshot();
  });
  socket.addEventListener("close", () => {
    const streamState = document.getElementById("eventStreamState");
    if (streamState) streamState.textContent = "WebSocket closed";
    renderKpis();
    renderPresentationDashboard();
  });
}

selectPatternBreakImage();
letterizeLandingTitle();
bindEvents();
startParticleOrb();
updatePresentationProgress();
loadSnapshot().then(() => {
  connectEvents();
  startSnapshotRefresh();
  const view = window.location.hash.replace("#", "");
  if (view === "history") {
    switchView("history", "auto");
  } else if (viewMeta[view]) {
    switchView(view, "auto");
  } else {
    enterPresentationMode("home", "auto");
  }
}).catch((error) => {
  const backendState = document.getElementById("backendState");
  const streamState = document.getElementById("eventStreamState");
  if (backendState) backendState.textContent = "Backend unavailable";
  if (streamState) streamState.textContent = error.message;
});
