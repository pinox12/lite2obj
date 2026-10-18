/**
 * Visor Three.js de lite2obj.
 *
 * Todo el esquemático llega como **un solo búfer binario**: posiciones, normales, UVs e
 * índices van seguidos y el JSON sólo indica el desplazamiento de cada parte, así que aquí
 * se crean los `BufferAttribute` sin copiar ni recorrer nada.
 *
 * Los triángulos vienen ya ordenados por modo de transparencia, de modo que basta con un
 * `addGroup` por modo y un array de tres materiales: opaco, *cutout* (alfa 0/1: hojas,
 * césped, barandillas) y translúcido (cristal, agua).
 */

import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

const $ = (id) => document.getElementById(id);

const ui = {
  schematics: $('schematics'),
  reload: $('reload'),
  open: $('open'),
  upload: $('upload'),
  dropzone: $('dropzone'),
  cull: $('cull'),
  tint: $('tint'),
  packs: $('packs'),
  stats: $('stats'),
  statsSection: $('stats-section'),
  topBlocks: $('top-blocks'),
  exportSection: $('export-section'),
  exportObj: $('export-obj'),
  exportAtlas: $('export-atlas'),
  health: $('health'),
  overlay: $('overlay'),
  hud: $('hud'),
  viewport: $('viewport'),
};

const state = {
  meshId: null,
  meta: null,
  geometry: null,
  materials: [],
  texture: null,
  packList: [],
};

/* ------------------------------------------------------------------ escena */

const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
renderer.setSize(ui.viewport.clientWidth, ui.viewport.clientHeight);
renderer.outputColorSpace = THREE.SRGBColorSpace;
ui.viewport.appendChild(renderer.domElement);

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x0f1114);

const camera = new THREE.PerspectiveCamera(
  55,
  ui.viewport.clientWidth / ui.viewport.clientHeight,
  0.05,
  4000,
);
camera.position.set(24, 20, 24);

const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.dampingFactor = 0.12;
controls.screenSpacePanning = false;
controls.maxPolarAngle = Math.PI * 0.495;

// Iluminación "de bloque": casi todo ambiente y dos direccionales suaves que sólo sirven
// para que dos caras vecinas no se vean iguales (como el sombreado fijo de Minecraft).
scene.add(new THREE.AmbientLight(0xffffff, 0.78));
const keyLight = new THREE.DirectionalLight(0xffffff, 0.42);
keyLight.position.set(0.45, 1, 0.3);
scene.add(keyLight);
const fillLight = new THREE.DirectionalLight(0xffffff, 0.22);
fillLight.position.set(-0.6, 0.35, -0.55);
scene.add(fillLight);

const group = new THREE.Group();
scene.add(group);

let grid = null;

function updateGrid(size) {
  if (grid) {
    group.remove(grid);
    grid.geometry.dispose();
    grid.material.dispose();
  }
  const span = Math.max(size[0], size[2], 1) * 1.5;
  grid = new THREE.GridHelper(Math.ceil(span), Math.ceil(span), 0x3a4150, 0x272c36);
  grid.position.set(size[0] / 2, 0, size[2] / 2);
  grid.material.depthWrite = false;
  group.add(grid);
}

function onResize() {
  const width = ui.viewport.clientWidth;
  const height = ui.viewport.clientHeight;
  if (!width || !height) return;
  camera.aspect = width / height;
  camera.updateProjectionMatrix();
  renderer.setSize(width, height);
}
window.addEventListener('resize', onResize);

renderer.setAnimationLoop(() => {
  controls.update();
  renderer.render(scene, camera);
});

/* ----------------------------------------------------------------------- UI */

let messageTimer = null;

function showMessage(text, { error = false, sticky = false } = {}) {
  let node = ui.overlay.querySelector('.message');
  if (!node) {
    node = document.createElement('div');
    ui.overlay.appendChild(node);
  }
  node.className = `message${error ? ' error' : ''}`;
  node.textContent = text;
  if (messageTimer) clearTimeout(messageTimer);
  if (!sticky) {
    messageTimer = setTimeout(() => node.classList.add('hidden'), 4000);
  }
}

function showSpinner(visible) {
  const existing = ui.overlay.querySelector('.spinner');
  if (visible && !existing) {
    ui.overlay.appendChild(Object.assign(document.createElement('div'), { className: 'spinner' }));
  } else if (!visible && existing) {
    existing.remove();
  }
}

const numberFormat = new Intl.NumberFormat('es-ES');

async function api(path, options) {
  const response = await fetch(path, options);
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (body?.detail) detail = typeof body.detail === 'string' ? body.detail : detail;
    } catch {
      /* la respuesta no era JSON */
    }
    throw new Error(detail);
  }
  return response;
}

async function loadHealth() {
  try {
    const body = await (await api('/api/health')).json();
    ui.health.textContent = body.client_jar ? 'listo' : 'sin client jar detectable';
  } catch (error) {
    ui.health.textContent = 'servidor no disponible';
  }
}

async function loadPacks() {
  const body = await (await api('/api/packs')).json();
  state.packList = body.packs;
  ui.packs.innerHTML = '';
  for (const pack of body.packs) {
    const option = document.createElement('option');
    option.value = pack.name;
    option.textContent = pack.name;
    ui.packs.appendChild(option);
  }
}

async function loadSchematics() {
  const body = await (await api('/api/schematics')).json();
  ui.schematics.innerHTML = '';
  for (const item of body.schematics) {
    const option = document.createElement('option');
    option.value = item.path;
    option.textContent = item.name;
    ui.schematics.appendChild(option);
  }
  ui.open.disabled = body.schematics.length === 0;
  if (!body.schematics.length) {
    showMessage('No se encontraron .litematic en el disco: sube uno.', { error: true, sticky: true });
  }
  return body.schematics.length;
}

function selectedPacks() {
  return Array.from(ui.packs.selectedOptions).map((option) => option.value);
}

function describeStats(meta) {
  const stats = meta.stats;
  const rows = [
    ['Caras', stats.quads],
    ['Triángulos', stats.triangles],
    ['Vértices', stats.vertices],
    ['Bloques distintos', stats.distinct_blocks],
    ['Sprites', stats.sprites],
    ['Atlas', `${meta.atlas.width} × ${meta.atlas.height}`],
    ['Tamaño', `${meta.size[0]} × ${meta.size[1]} × ${meta.size[2]}`],
  ];
  ui.stats.innerHTML = '';
  for (const [label, value] of rows) {
    ui.stats.insertAdjacentHTML('beforeend', `<dt>${label}</dt>`);
    const text = typeof value === 'number' ? numberFormat.format(value) : value;
    ui.stats.insertAdjacentHTML('beforeend', `<dd>${text}</dd>`);
  }

  const top = stats.top_blocks ?? [];
  const max = top.length ? top[0][1] : 1;
  ui.topBlocks.innerHTML = '';
  for (const [name, count] of top) {
    const label = name.replace(/^minecraft:/, '');
    ui.topBlocks.insertAdjacentHTML(
      'beforeend',
      `<li style="--w:${Math.round((count / max) * 100)}%">
         <span>${label}</span><span class="count">${numberFormat.format(count)}</span>
       </li>`,
    );
  }
  ui.statsSection.hidden = false;
  ui.exportSection.hidden = false;
}

/* ---------------------------------------------------------------- geometría */

function makeMaterial(texture, mode) {
  const common = { map: texture, side: THREE.FrontSide };
  if (mode === 'cutout') {
    return new THREE.MeshLambertMaterial({ ...common, side: THREE.DoubleSide, alphaTest: 0.5 });
  }
  if (mode === 'translucent') {
    return new THREE.MeshLambertMaterial({
      ...common,
      side: THREE.DoubleSide,
      transparent: true,
      depthWrite: false,
      alphaTest: 0.02,
    });
  }
  return new THREE.MeshLambertMaterial(common);
}

function disposeCurrent() {
  if (!state.geometry) return;
  group.remove(...group.children.filter((child) => child.userData.mesh));
  state.geometry.dispose();
  for (const material of state.materials) material.dispose();
  if (state.texture) state.texture.dispose();
  state.geometry = null;
  state.materials = [];
  state.texture = null;
}

function partByName(parts, name) {
  return parts.find((part) => part.name === name);
}

async function buildMesh(result) {
  const { mesh_id: meshId, meta } = result;
  state.meshId = meshId;
  state.meta = meta;

  const [buffer, texture] = await Promise.all([
    (await api(`/api/mesh/${meshId}/buffer`)).arrayBuffer(),
    new THREE.TextureLoader().loadAsync(`/api/mesh/${meshId}/atlas.png`),
  ]);

  texture.flipY = false; // nuestras UV tienen v hacia abajo, como la imagen
  texture.magFilter = THREE.NearestFilter;
  texture.minFilter = THREE.NearestMipmapNearestFilter;
  texture.generateMipmaps = true;
  texture.colorSpace = THREE.SRGBColorSpace;

  const parts = meta.parts;
  const position = partByName(parts, 'position');
  const normal = partByName(parts, 'normal');
  const uv = partByName(parts, 'uv');
  const index = partByName(parts, 'index');

  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute(
    'position',
    new THREE.BufferAttribute(
      new Float32Array(buffer, position.offset, position.count * position.components),
      position.components,
    ),
  );
  geometry.setAttribute(
    'normal',
    new THREE.BufferAttribute(
      new Float32Array(buffer, normal.offset, normal.count * normal.components),
      normal.components,
    ),
  );
  geometry.setAttribute(
    'uv',
    new THREE.BufferAttribute(new Float32Array(buffer, uv.offset, uv.count * uv.components), 2),
  );
  geometry.setIndex(
    new THREE.BufferAttribute(new Uint32Array(buffer, index.offset, index.count), 1),
  );

  const modes = ['opaque', 'cutout', 'translucent'];
  const materials = modes.map((mode) => makeMaterial(texture, mode));
  for (const entry of meta.groups) {
    const slot = modes.indexOf(entry.mode);
    if (slot >= 0) geometry.addGroup(entry.start * 3, entry.count * 3, slot);
  }
  geometry.computeBoundingSphere();

  disposeCurrent();
  state.geometry = geometry;
  state.materials = materials;
  state.texture = texture;

  const mesh = new THREE.Mesh(geometry, materials);
  mesh.userData.mesh = true;
  group.add(mesh);

  updateGrid(meta.size);
  frameCamera(meta.size);
  describeStats(meta);
}

function frameCamera(size) {
  const radius = Math.max(Math.hypot(size[0], size[1], size[2]) * 0.5, 1);
  const center = new THREE.Vector3(size[0] / 2, size[1] / 2, size[2] / 2);
  controls.target.copy(center);
  const distance = radius * 2.2;
  camera.position
    .copy(center)
    .add(new THREE.Vector3(distance * 0.65, distance * 0.5, distance * 0.65));
  camera.near = Math.max(radius / 200, 0.02);
  camera.far = radius * 60;
  camera.updateProjectionMatrix();
  controls.minDistance = radius * 0.05;
  controls.maxDistance = radius * 12;
  controls.update();
}

/* -------------------------------------------------------------------- carga */

function renderRequest(extra = {}) {
  return {
    path: null,
    upload_id: null,
    packs: selectedPacks(),
    cull: ui.cull.checked,
    tint: ui.tint.checked,
    ...extra,
  };
}

async function render(body) {
  showSpinner(true);
  try {
    const response = await api('/api/render', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    await buildMesh(await response.json());
    updateHud();
    if (body.path) {
      history.replaceState(null, '', `?path=${encodeURIComponent(body.path)}`);
    }
  } catch (error) {
    showMessage(`No se pudo renderizar: ${error.message}`, { error: true });
  } finally {
    showSpinner(false);
  }
}

function updateHud() {
  if (!state.meta) {
    ui.hud.textContent = '';
    return;
  }
  const top = (state.meta.stats.top_blocks ?? []).slice(0, 1).map(([name]) => name);
  ui.hud.textContent = `${numberFormat.format(state.meta.stats.triangles)} triángulos`;
  if (top.length) ui.hud.textContent += `\n${top[0].replace(/^minecraft:/, '')}`;
}

async function uploadAndRender(file) {
  showSpinner(true);
  try {
    const form = new FormData();
    form.append('file', file, file.name);
    const uploaded = await (await api('/api/schematics', { method: 'POST', body: form })).json();
    await render(renderRequest({ upload_id: uploaded.upload_id }));
    showMessage(`${uploaded.name}: ${uploaded.bytes} bytes`);
  } catch (error) {
    showMessage(`Fallo al subir: ${error.message}`, { error: true });
  } finally {
    showSpinner(false);
  }
}

/* -------------------------------------------------------------------- eventos */

ui.reload.addEventListener('click', async () => {
  try {
    await loadSchematics();
    await loadPacks();
    showMessage('Listas actualizadas');
  } catch (error) {
    showMessage(error.message, { error: true });
  }
});

ui.open.addEventListener('click', () => {
  const path = ui.schematics.value;
  if (path) render(renderRequest({ path }));
});

ui.schematics.addEventListener('dblclick', () => ui.open.click());

for (const control of [ui.cull, ui.tint]) {
  control.addEventListener('change', () => {
    if (state.meta && ui.schematics.value) render(renderRequest({ path: ui.schematics.value }));
  });
}

ui.packs.addEventListener('change', () => {
  if (state.meta && ui.schematics.value) render(renderRequest({ path: ui.schematics.value }));
});

ui.upload.addEventListener('change', () => {
  const file = ui.upload.files?.[0];
  if (file) uploadAndRender(file);
});

for (const event of ['dragenter', 'dragover']) {
  ui.dropzone.addEventListener(event, (e) => {
    e.preventDefault();
    ui.dropzone.classList.add('dragging');
  });
}
for (const event of ['dragleave', 'drop']) {
  ui.dropzone.addEventListener(event, () => ui.dropzone.classList.remove('dragging'));
}
ui.dropzone.addEventListener('drop', (e) => {
  e.preventDefault();
  const file = e.dataTransfer?.files?.[0];
  if (file) uploadAndRender(file);
});

ui.exportObj.addEventListener('click', () => {
  if (state.meshId) window.location.assign(`/api/mesh/${state.meshId}/export.obj`);
});
ui.exportAtlas.addEventListener('click', () => {
  if (state.meshId) window.location.assign(`/api/mesh/${state.meshId}/atlas.png`);
});

window.addEventListener('keydown', (event) => {
  if (event.key === 'r' || event.key === 'R') frameCamera(state.meta?.size ?? [16, 16, 16]);
});

const observer = new ResizeObserver(onResize);
observer.observe(ui.viewport);

/* ------------------------------------------------------------------ arranque */

(async function start() {
  try {
    await Promise.all([loadHealth(), loadPacks()]);
    await loadSchematics();
    const requested = new URLSearchParams(window.location.search).get('path');
    const option = requested
      ? Array.from(ui.schematics.options).find((item) => item.value === requested)
      : ui.schematics.querySelector('option');
    if (option) {
      ui.schematics.value = option.value;
      await render(renderRequest({ path: option.value }));
    }
  } catch (error) {
    showMessage(`Error de arranque: ${error.message}`, { error: true, sticky: true });
  }
})();

// Expuesto para depurar desde la consola del navegador.
window.lite2obj = { scene, camera, controls, renderer, state };
