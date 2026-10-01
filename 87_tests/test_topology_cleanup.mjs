import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { PresentationScroll } from "../web/presentation-scroll.mjs";

const source = readFileSync(new URL("../web/app.js", import.meta.url), "utf8");
// Load the actual UI functions without starting the application's network timers.
const definitions = source.slice(source.indexOf("const TOPOLOGY_LAYOUT_VERSION"), source.lastIndexOf("\nselectPatternBreakImage();"));
const copy = (value) => JSON.parse(JSON.stringify(value));

function fixture() {
  const devices = ["sternpunkt", "rack105", "rack201"].map((id) => ({ id, name: id, status: "up" }));
  const links = ["rack105", "rack201"].map((to) => ({ from: "sternpunkt", to, status: "confirmed" }));
  for (const parent of [...devices]) {
    for (let n = 0; n < 10; n++) {
      const id = `${parent.id}-${n}`;
      devices.push({ id, name: id, device_type: n === 0 ? "segment" : "endpoint", status: "observed" });
      links.push({ from: parent.id, to: id, status: "confirmed" });
    }
  }
  devices.push({ id: "candidate", status: "pending" }, { id: "orphan", device_type: "endpoint", status: "observed" });
  links.push({ from: "rack201", to: "candidate", status: "pending" });
  devices.forEach((device) => { device.layout = { x: 5000, y: 4000, source: "saved-map", locked: false }; });
  return { devices, links };
}

function harness(snapshot = fixture(), post) {
  const elements = new Map();
  const timers = new Map();
  const requests = [];
  let timerId = 0;
  const getElement = (id) => {
    if (!elements.has(id)) elements.set(id, {
      clientWidth: 1400, clientHeight: 900, innerHTML: "", textContent: "",
      style: { setProperty() {} },
      setAttribute(name, value) { this[name] = value; },
      removeAttribute(name) { delete this[name]; }
    });
    return elements.get(id);
  };
  const context = vm.createContext({
    PresentationScroll,
    console: { warn() {} },
    requestAnimationFrame() {},
    window: {
      innerWidth: 1600, innerHeight: 900, scrollY: 900,
      localStorage: { getItem: () => "organic-seed-zones-v7", setItem() {} },
      matchMedia: () => ({ matches: false }),
      clearTimeout: (id) => timers.delete(id),
      setTimeout: (fn) => { timers.set(++timerId, fn); return timerId; }
    },
    document: { getElementById: getElement, querySelector: () => null },
    async fetch(path, options) {
      const body = JSON.parse(options.body);
      requests.push({ path, body });
      if (post) await post(body);
      return { ok: true, json: async () => ({}) };
    }
  });
  vm.runInContext(definitions, context);
  vm.runInContext("renderTopologyEvidencePanel = () => {}; bindShowcaseDrag = () => {}; bindShowcasePan = () => {};", context);
  const state = vm.runInContext("appState", context);
  state.snapshot = { ...state.snapshot, ...copy(snapshot) };
  return { context, state, elements, requests, timers, render: context.renderShowcaseTopology, clean: context.cleanUpShowcaseTopology };
}

test("clean up replaces manual positions, preserves inventory, groups endpoints and saves once", async () => {
  const h = harness();
  const inventory = copy(h.state.snapshot);
  h.render();
  const manual = copy(h.state.topologyPositions);
  h.state.topologyZoomUserSet = true;
  h.state.topologyZoom = 1.5;
  h.state.topologyPan = { x: -2500, y: -2500 };
  await h.clean();
  assert.notDeepEqual(copy(h.state.topologyPositions), manual);
  assert.equal(h.state.topologyZoomUserSet, false);
  assert.ok(h.state.topologyZoom < 1.5);
  assert.deepEqual(copy(h.state.snapshot.links), inventory.links);
  assert.equal(h.requests.length, 1);
  assert.equal(h.requests[0].path, "/api/topology/layout");
  assert.equal(Object.keys(h.requests[0].body.layouts).length, inventory.devices.length);
  const distance = (a, b) => Math.hypot(a.x - b.x, a.y - b.y);
  for (const device of inventory.devices) {
    const position = h.state.topologyPositions[device.id];
    assert.ok(Number.isFinite(position.x) && Number.isFinite(position.y));
    assert.notDeepEqual(copy(position), manual[device.id]);
    if (!device.id.startsWith("rack105-")) continue;
    assert.ok(distance(position, h.state.topologyPositions.rack105) < distance(position, h.state.topologyPositions.rack201));
  }
  assert.equal(h.elements.get("showcaseLayoutStatus").textContent, "Layout saved");
  assert.equal(h.elements.get("showcaseCleanUp").disabled, false);
});

test("ordinary refresh preserves manual placement; cleanup is stable when repeated and after reload", async () => {
  const h = harness();
  h.render();
  h.state.topologyPositions.rack105 = { x: 3456, y: 2345 };
  h.render();
  assert.deepEqual(copy(h.state.topologyPositions.rack105), { x: 3456, y: 2345 });
  await h.clean();
  const clean = copy(h.state.topologyPositions);
  h.render();
  assert.deepEqual(copy(h.state.topologyPositions), clean);
  await h.clean();
  assert.deepEqual(copy(h.state.topologyPositions), clean);
  const reloaded = harness(h.state.snapshot);
  reloaded.render();
  for (const [id, position] of Object.entries(clean)) {
    assert.ok(Math.abs(reloaded.state.topologyPositions[id].x - position.x) <= 0.1);
    assert.ok(Math.abs(reloaded.state.topologyPositions[id].y - position.y) <= 0.1);
  }
});

test("cleanup serializes behind in-flight drag saves and discards pending debounce", async () => {
  let finishDrag;
  const drag = new Promise((resolve) => { finishDrag = resolve; });
  let calls = 0;
  const h = harness(undefined, async () => { if (++calls === 1) await drag; });
  h.render();
  const saving = h.context.saveTopologyLayout();
  await new Promise((resolve) => setImmediate(resolve));
  h.context.saveTopologyLayoutSoon();
  assert.equal(h.timers.size, 1);
  const cleaning = h.clean();
  await h.clean();
  assert.equal(h.elements.get("showcaseCleanUp").disabled, true);
  assert.equal(h.requests.length, 1);
  assert.equal(h.timers.size, 0);
  finishDrag();
  await saving;
  await cleaning;
  assert.equal(h.requests.length, 2);
  assert.notDeepEqual(h.requests[0].body, h.requests[1].body);
  assert.equal(h.elements.get("showcaseCleanUp").disabled, false);
});

test("failed saves are reported, release the button and permit retry", async () => {
  let fail = true;
  const h = harness(undefined, () => { if (fail) throw new Error("offline"); });
  h.render();
  await h.clean();
  assert.equal(h.elements.get("showcaseLayoutStatus").textContent, "Layout not saved. Try again.");
  assert.equal(h.elements.get("showcaseCleanUp").disabled, false);
  assert.equal(h.state.topologyCleaning, false);
  fail = false;
  await h.clean();
  assert.equal(h.elements.get("showcaseLayoutStatus").textContent, "Layout saved");
});

test("empty maps disable cleanup without saving; disconnected devices are still arranged", async () => {
  const empty = harness({ devices: [], links: [] });
  empty.render();
  await empty.clean();
  assert.equal(empty.elements.get("showcaseCleanUp").disabled, true);
  assert.equal(empty.requests.length, 0);
  const isolated = harness({ devices: fixture().devices.slice(0, 3), links: [] });
  isolated.render();
  await isolated.clean();
  assert.equal(Object.keys(isolated.state.topologyPositions).length, 3);
  assert.equal(isolated.requests.length, 1);
});

test("cleanup fits a large map inside the available viewport, including below the old zoom limit", async () => {
  const h = harness();
  const stage = h.elements.get("showcaseMap");
  stage.clientWidth = 390;
  stage.clientHeight = 844;
  await h.clean();
  const { width, height } = h.state.topologyLogicalSize;
  const zoom = h.state.topologyZoom;
  assert.ok(zoom < 0.2);
  assert.ok(width * zoom <= stage.clientWidth);
  assert.ok(height * zoom <= stage.clientHeight);
  assert.ok(h.state.topologyPan.x >= 0);
  assert.ok(h.state.topologyPan.y >= 0);
  assert.ok(h.state.topologyPan.x + width * zoom <= stage.clientWidth);
  assert.ok(h.state.topologyPan.y + height * zoom <= stage.clientHeight);
});
