const assert = require('node:assert/strict');
const {test} = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const bridgeCode = fs.readFileSync(path.join(root, 'web/js/fusionBridge.js'), 'utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));

function harness({ready = true, saved = {status: 'saved'}, uploadOK = true, fetchImpl} = {}) {
  const elements = [], timers = new Map(), listeners = new Map();
  const fetchCalls = [];
  let timerId = 0, loaded = 0;
  function element() {
    const classes = new Set();
    return {isConnected: false, attributes: {}, textContent: '',
      setAttribute(key, value) {this.attributes[key] = value;},
      classList: {add(...names) {names.forEach(n => classes.add(n));},
        remove(...names) {names.forEach(n => classes.delete(n));}, contains(n) {return classes.has(n);}},
      remove() {this.isConnected = false;}};
  }
  const host = {async loadModelFile() {loaded++;}, initializeFusionSurfaceSelection() {}};
  const window = {location: {search: '?fusion=1&job=test&token=test'},
    addEventListener(name, callback) {listeners.set(name, callback);},
    adsk: {async fusionSendData() {return typeof saved === 'function' ? saved() : JSON.stringify(saved);}}};
  if (ready) window.bumpMeshHost = host;
  class TestFile { constructor(parts, name, options) {this.parts = parts; this.name = name; this.type = options?.type;} }
  const context = {window, URLSearchParams, File: TestFile, console,
    setTimeout(callback, delay) {timers.set(++timerId, {callback, delay}); return timerId;},
    clearTimeout(id) {timers.delete(id);},
    document: {createElement() {const node = element(); elements.push(node); return node;},
      querySelector() {return {appendChild() {}};}, body: {appendChild(node) {node.isConnected = true;}}},
    async fetch(url, options) {
      fetchCalls.push({url, options});
      if (fetchImpl) return fetchImpl(url, options);
      if (url.startsWith('/api/jobs/') && options) {
        return {ok: uploadOK, json: async () => ({}), text: async () => 'interrupted'};
      }
      if (url.startsWith('/api/jobs/')) return {ok: true, blob: async () => ({})};
      if (url.startsWith('/api/textures/') && !options) return {ok: true, blob: async () => ({png: true})};
      if (options?.method === 'POST') return {ok: true, json: async () => ({texture: {id: 'Map.png', name: 'Map'}})};
      if (options?.method === 'DELETE') return {ok: true, json: async () => ({status: 'deleted'})};
      return {ok: true, json: async () => ({textures: [{id: 'Map.png', name: 'Map'}]})};
    }};
  vm.runInNewContext(bridgeCode, context);
  return {window, status: elements[1], timers, loaded: () => loaded,
    fetchCalls,
    ready() {window.bumpMeshHost = host; listeners.get('bumpmesh-host-ready')();}};
}

test('status remains attached and saved/cancelled results propagate', async () => {
  for (const status of ['saved', 'cancelled']) {
    const h = harness({saved: {status}});
    await tick();
    assert.equal(h.status.isConnected, true);
    assert.equal(h.status.attributes['aria-live'], 'polite');
    const result = await h.window.bumpMeshFusionBridge.saveBinary(new Uint8Array([1]), 'part.stl');
    assert.equal(result.status, status);
    assert.equal(h.status.textContent, status === 'saved' ? 'Textured file saved.' : 'Save cancelled.');
    assert.equal(h.status.isConnected, true);
  }
});

test('save stays pending until native save finishes', async () => {
  let finish;
  const h = harness({saved: () => new Promise(resolve => {finish = resolve;})});
  await tick();
  let complete = false;
  const saving = h.window.bumpMeshFusionBridge.saveBinary(new Uint8Array([1]), 'part.3mf').then(() => {complete = true;});
  await tick();
  assert.equal(complete, false);
  finish(JSON.stringify({status: 'saved'}));
  await saving;
  assert.equal(complete, true);
});

test('failed upload and native errors are visible and reject', async () => {
  for (const options of [{uploadOK: false}, {saved: {status: 'error', message: 'Disk full'}}, {saved: {}}]) {
    const h = harness(options);
    await tick();
    await assert.rejects(h.window.bumpMeshFusionBridge.saveBinary(new Uint8Array([1]), 'part.stl'));
    assert.equal(h.status.classList.contains('error'), true);
    assert.equal(h.status.classList.contains('visible'), true);
  }
});

test('My Maps bridge lists, loads, saves, and deletes authenticated local textures', async () => {
  const h = harness();
  await tick();
  const bridge = h.window.bumpMeshFusionBridge;
  const maps = await bridge.listUserTextures();
  assert.equal(maps[0].id, 'Map.png');
  const file = await bridge.loadUserTexture(maps[0]);
  assert.equal(file.name, 'Map.png');
  assert.equal(file.type, 'image/png');
  const savedMap = await bridge.saveUserTexture({png: true}, 'New Map.jpg');
  assert.equal(savedMap.id, 'Map.png');
  await bridge.deleteUserTexture('Map.png');

  const mapCalls = h.fetchCalls.filter(call => call.url.startsWith('/api/textures'));
  assert.equal(mapCalls.length, 4);
  assert.match(mapCalls[0].url, /token=test/);
  assert.match(mapCalls[1].url, /\/api\/textures\/Map.png\?/);
  assert.equal(mapCalls[2].options.method, 'POST');
  assert.equal(mapCalls[2].options.headers['Content-Type'], 'image/png');
  assert.match(mapCalls[2].url, /filename=New\+Map.jpg/);
  assert.equal(mapCalls[3].options.method, 'DELETE');
});

test('My Maps bridge surfaces server errors', async () => {
  const h = harness({fetchImpl: async url => {
    if (url.startsWith('/api/jobs/')) return {ok: true, blob: async () => ({})};
    return {ok: false, json: async () => ({error: 'Texture folder unavailable'})};
  }});
  await tick();
  await assert.rejects(
    h.window.bumpMeshFusionBridge.listUserTextures(),
    /Texture folder unavailable/,
  );
});

test('Fusion custom-map uploads are automatically persisted', () => {
  const main = fs.readFileSync(path.join(root, 'web/js/main.js'), 'utf8');
  assert.match(
    main,
    /textureInput\.addEventListener\('change',[\s\S]*if \(FUSION_HOSTED\) await _saveCustomMapEntry\(activeMapEntry\)/,
  );
  assert.match(main, /customMapSaveBtn\.addEventListener\('click',[\s\S]*_saveCustomMapEntry\(_lastCustomMap\)/);
  assert.match(
    main,
    /await _refreshUserMaps\(\);[\s\S]*if \(_lastCustomMap === entry\)[\s\S]*_lastCustomMap = null;[\s\S]*_hideCustomMapThumb\(\)/,
  );
});

test('surface selection controls sit directly after the map picker', () => {
  const html = fs.readFileSync(path.join(root, 'web/index.html'), 'utf8');
  const mapPicker = html.indexOf('<!-- Displacement Map -->');
  const surfaceSelection = html.indexOf('<!-- Surface Selection -->');
  const projection = html.indexOf('<!-- Projection -->');
  const displacement = html.indexOf('<!-- Displacement -->');
  const textureSmoothing = html.indexOf('id="texture-smoothing"');
  assert.ok(mapPicker >= 0 && surfaceSelection > mapPicker && projection > surfaceSelection);
  assert.ok(textureSmoothing > displacement, 'Texture Smoothing belongs to the global Displacement section');
  for (const id of ['excl-section-heading', 'excl-mode-exclude', 'excl-mode-include', 'excl-brush-btn', 'excl-bucket-btn']) {
    assert.equal((html.match(new RegExp(`id="${id}"`, 'g')) || []).length, 1);
  }
});

test('slow startup gives guidance but still recovers', async () => {
  const h = harness({ready: false});
  const timeout = [...h.timers.values()].find(timer => timer.delay === 30000);
  timeout.callback();
  assert.match(h.status.textContent, /internet connection/);
  assert.equal(h.loaded(), 0);
  h.ready();
  await tick();
  assert.equal(h.loaded(), 1);
  assert.equal(h.status.classList.contains('visible'), false);
});

test('exportSTL returns the native save promise and preserves binary output', async () => {
  const code = fs.readFileSync(path.join(root, 'web/js/exporter.js'), 'utf8')
    .replace(/^import .*;\n/gm, '').replace(/export function/g, 'function');
  let savedBuffer, resolve;
  const expected = new Promise(r => {resolve = r;});
  const context = {globalThis: {bumpMeshFusionBridge: {saveBinary(buffer) {savedBuffer = buffer; return expected;}}}};
  vm.createContext(context);
  vm.runInContext(code, context);
  const geometry = {attributes: {position: {array: new Float32Array([0,0,0, 1,0,0, 0,1,0])}}};
  assert.equal(context.exportSTL(geometry), expected);
  assert.equal(savedBuffer.byteLength, 134);
  assert.equal(new DataView(savedBuffer).getUint32(80, true), 1);
  resolve({status: 'saved'});
  await expected;
  const main = fs.readFileSync(path.join(root, 'web/js/main.js'), 'utf8');
  assert.match(main, /saveResult = await exportSTL\(/);
  assert.match(main, /saveResult = await export3MF\(/);
  assert.match(main, /saveResult\?\.status === 'cancelled'/);
});
