/*
 * Copyright (c) 2026 CNCKitchen (Stefan Hermann) and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 */

const params = new URLSearchParams(window.location.search);
const hosted = params.get('fusion') === '1';

if (hosted) {
  const jobId = params.get('job');
  const token = params.get('token');
  const sourceName = params.get('source') || 'FusionBody.stl';

  const badge = document.createElement('span');
  badge.className = 'fusion-credit';
  badge.textContent = 'Fusion integration by Extrusion Therapy';
  document.querySelector('.logo')?.appendChild(badge);

  const status = document.createElement('div');
  status.className = 'fusion-status visible';
  status.setAttribute('role', 'status');
  status.setAttribute('aria-live', 'polite');
  status.textContent = 'Loading selected Fusion body…';
  document.body.appendChild(status);

  let hideTimer;
  async function saveBinary(buffer, filename, mime) {
    clearTimeout(hideTimer);
    status.classList.remove('error');
    status.textContent = `Preparing ${filename}…`;
    status.classList.add('visible');
    const query = new URLSearchParams({token, filename});
    try {
      const response = await fetch(`/api/jobs/${jobId}/output?${query}`, {
        method: 'POST',
        headers: {'Content-Type': mime || 'application/octet-stream'},
        body: buffer,
      });
      if (!response.ok) {
        const detail = await response.text();
        throw new Error(`Fusion export bridge failed: ${detail}`);
      }
      const output = await response.json();
      if (!window.adsk || typeof window.adsk.fusionSendData !== 'function') {
        throw new Error('The Fusion palette bridge is unavailable.');
      }
      const rawResult = await window.adsk.fusionSendData(
        'saveOutput',
        JSON.stringify(output),
      );
      const result = typeof rawResult === 'string' ? JSON.parse(rawResult) : rawResult;
      if (!['saved', 'cancelled'].includes(result?.status)) throw new Error(result?.message || 'Fusion could not save the export.');
      status.textContent = result.status === 'saved' ? 'Textured file saved.' : 'Save cancelled.';
      hideTimer = setTimeout(() => status.classList.remove('visible'), 2200);
      return result;
    } catch (error) {
      status.textContent = error.message || 'Fusion could not save the export. Try exporting again.';
      status.classList.add('error', 'visible');
      throw error;
    }
  }

  window.bumpMeshFusionBridge = {saveBinary};

  async function loadSelectedBody() {
    try {
      if (!window.bumpMeshHost) {
        await new Promise((resolve) => {
          // Slow loading can still recover; the timer gives guidance, not a false abort.
          const timer = setTimeout(() => {
            status.textContent = 'BumpMesh is taking longer to load. Check your internet connection; if it stays here, close this panel and open BumpMesh again.';
          }, 30000);
          window.addEventListener('bumpmesh-host-ready', () => {
            clearTimeout(timer);
            status.textContent = 'Loading selected Fusion body…';
            resolve();
          }, {once: true});
        });
      }
      const query = new URLSearchParams({token});
      const response = await fetch(`/api/jobs/${jobId}/source?${query}`);
      if (!response.ok) throw new Error(`Fusion body transfer failed (${response.status}).`);
      const blob = await response.blob();
      const file = new File([blob], sourceName, {type: 'model/stl'});
      if (!window.bumpMeshHost || typeof window.bumpMeshHost.loadModelFile !== 'function') {
        throw new Error('BumpMesh did not expose its model loader.');
      }
      await window.bumpMeshHost.loadModelFile(file);
      if (typeof window.bumpMeshHost.initializeFusionSurfaceSelection !== 'function') {
        throw new Error('BumpMesh did not expose its Fusion surface-selection hook.');
      }
      window.bumpMeshHost.initializeFusionSurfaceSelection();
      status.classList.remove('visible');
    } catch (error) {
      status.textContent = error.message;
      status.classList.add('error');
      console.error(error);
    }
  }

  loadSelectedBody();
}
