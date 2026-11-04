import { useEffect, useRef, useState } from 'react';
import {
  AgXToneMapping, Box3, Box3Helper, Color, Group,
  HemisphereLight, Mesh, MeshStandardMaterial, PerspectiveCamera, PlaneGeometry,
  PMREMGenerator, PointLight, Raycaster, Scene, Vector2, Vector3, WebGLRenderer,
  SRGBColorSpace, PCFShadowMap, Material, Texture,
} from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { PointerLockControls } from 'three/addons/controls/PointerLockControls.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';
import type { Camera, Entity } from '../api/contracts';
import { entityMatrix, fittedFrame, toCanonicalPoint, toThreePoint, verticalFov } from './coordinates';
import { canStandAt, findWalkSpawn } from './navigation';
import { applyMaterials, componentName, prepareMaterials, surfaceOf } from './materials';
import { PreviewLights } from './lighting';
import type { CameraDraft, SceneViewportProps } from './types';
import './viewport.css';

type AssetEntry = { root: Group; url: string | null; version: number; entity: Entity };
type ViewportStatus = { loading: number; missingActors: number; failed: string[]; fps: number | null; triangles: number; locked: boolean };

function disposeObject(root: Group | Scene) {
  const materials = new Set<Material>();
  const textures = new Set<Texture>();
  root.traverse((object) => {
    if (!(object instanceof Mesh)) return;
    object.geometry.dispose();
    for (const material of Array.isArray(object.material) ? object.material : [object.material]) {
      materials.add(material);
      for (const value of Object.values(material)) if (value instanceof Texture) textures.add(value);
    }
  });
  materials.forEach((material) => material.dispose());
  textures.forEach((texture) => texture.dispose());
}

function isTyping(target: EventTarget | null) {
  return target instanceof HTMLElement && (!!target.closest('input, textarea, select, [contenteditable="true"]') || target.closest('video'));
}

function keyCode(event: KeyboardEvent) {
  // Virtual keyboards and CDP clients may supply key without the physical code.
  return event.code || ({ w: 'KeyW', a: 'KeyA', s: 'KeyS', d: 'KeyD', shift: 'ShiftLeft' } as Record<string, string>)[event.key.toLowerCase()] || event.key;
}

export function SceneViewport(props: SceneViewportProps) {
  const host = useRef<HTMLDivElement>(null);
  const live = useRef(props);
  const bridge = useRef<{ synchronize: () => void; frameSelection: () => void } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<ViewportStatus>({ loading: 0, missingActors: 0, failed: [], fps: null, triangles: 0, locked: false });
  live.current = props;

  useEffect(() => {
    const container = host.current;
    if (!container) return;
    let renderer: WebGLRenderer;
    try {
      renderer = new WebGLRenderer({ antialias: true, alpha: false, preserveDrawingBuffer: true });
    } catch {
      setError('WebGL could not start. The real set requires a browser with hardware acceleration.');
      return;
    }
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.75));
    renderer.outputColorSpace = SRGBColorSpace;
    renderer.toneMapping = AgXToneMapping;
    renderer.toneMappingExposure = 1;
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = PCFShadowMap;
    renderer.domElement.tabIndex = 0;
    renderer.domElement.setAttribute('aria-label', 'Three dimensional film set. Drag to orbit; use Walk to enter.');
    container.appendChild(renderer.domElement);
    const world = new Scene();
    world.background = new Color('#282c2e');
    const environment = new RoomEnvironment();
    const pmrem = new PMREMGenerator(renderer);
    const envTarget = pmrem.fromScene(environment, .06);
    world.environment = envTarget.texture;
    world.environmentIntensity = .18;
    environment.dispose();
    pmrem.dispose();
    const hemi = new HemisphereLight('#c2cfe0', '#827058', .12);
    world.add(hemi);
    const lights = new PreviewLights(); world.add(lights.root);
    const bounce = new PointLight('#ffe3bd', 6, 12, 2); bounce.position.set(-2, 2.3, 1); world.add(bounce);
    // Explicit temporary floor only; missing actors are never replaced by proxy people.
    const floor = new Mesh(new PlaneGeometry(10, 6.2), new MeshStandardMaterial({ color: '#8a7760', roughness: .85 }));
    floor.rotation.x = -Math.PI / 2; floor.position.y = -.003; floor.receiveShadow = true; world.add(floor);
    const camera = new PerspectiveCamera(45, 16 / 9, .05, 100);
    camera.position.set(-3.7, 1.55, 1.3);
    const orbit = new OrbitControls(camera, renderer.domElement);
    orbit.target.set(2.2, 1, -.15); orbit.enableDamping = true; orbit.dampingFactor = .12;
    orbit.minDistance = .15; orbit.maxDistance = 12;
    orbit.maxPolarAngle = Math.PI * .49; orbit.minPolarAngle = .05;
    const walk = new PointerLockControls(camera, renderer.domElement);
    walk.minPolarAngle = .12; walk.maxPolarAngle = Math.PI - .12;
    const selectedBox = new Box3Helper(new Box3(), new Color('#de8f52'));
    selectedBox.visible = false; selectedBox.renderOrder = 10;
    (selectedBox.material as Material).depthTest = false; world.add(selectedBox);
    const loader = new GLTFLoader();
    const entries = new Map<string, AssetEntry>();
    const pending = new Set<string>(); const failures = new Map<string, string>();
    const keys = new Set<string>(); const raycaster = new Raycaster();
    let disposed = false; let raf = 0; let width = 1; let height = 1;
    let visible = false; let submittedFrames = 0;
    let cameraKey = ''; let currentMode = live.current.mode;
    let lens = { focal_length_mm: 32, sensor_width_mm: 36, aspect_ratio: 16 / 9, near: .05, far: 100, depth_of_field: false, aperture_fstop: 4, focus_distance_m: null as number | null };
    let lastTime = performance.now(); let statusTime = lastTime; let frames = 0;
    let draftTimeout: ReturnType<typeof setTimeout> | undefined;
    let pointerStart: { x: number; y: number } | null = null;
    let collisionBounds: Box3[] = [];
    let walkAvailable = true;
    const frameDurations: number[] = [];

    function refreshSpatialState() {
      world.updateMatrixWorld(true);
      floor.visible = ![...entries.values()].some((entry) => entry.entity.kind === 'room' && entry.entity.visible && entry.root.children.length > 0);
      collisionBounds = [];
      for (const entry of entries.values()) {
        const e = entry.entity;
        if (!e.visible || e.kind === 'room' || e.kind === 'light' || (e.kind === 'prop' && e.dimensions[2] < .3) || !entry.root.children.length) continue;
        collisionBounds.push(new Box3().setFromObject(entry.root).expandByScalar(.18));
      }
    }

    function emitDraft() {
      if (disposed) return;
      const target = currentMode === 'walk'
        ? camera.position.clone().add(camera.getWorldDirection(new Vector3()).multiplyScalar(3))
        : orbit.target;
      const draft: CameraDraft = { ...lens, position: toCanonicalPoint(camera.position), target: toCanonicalPoint(target) };
      live.current.onCameraDraft(draft);
    }
    function queueDraft() {
      if (draftTimeout) clearTimeout(draftTimeout);
      draftTimeout = setTimeout(emitDraft, 180);
    }
    function setCamera(record: Camera) {
      lens = { focal_length_mm: record.focal_length_mm, sensor_width_mm: record.sensor_width_mm, aspect_ratio: record.aspect_ratio, near: record.near, far: record.far, depth_of_field: record.depth_of_field ?? false, aperture_fstop: record.aperture_fstop ?? 4, focus_distance_m: record.focus_distance_m ?? null };
      camera.position.copy(toThreePoint(record.position)); orbit.target.copy(toThreePoint(record.target));
      camera.near = lens.near; camera.far = lens.far;
      camera.lookAt(orbit.target); updateProjection(); orbit.update(); queueDraft();
    }
    function updateProjection() {
      // Lens remains horizontal-fit. Camera mode letterboxes, preserving exact export pixels.
      camera.aspect = currentMode === 'camera' ? lens.aspect_ratio : width / height;
      camera.fov = verticalFov({ ...lens, aspect_ratio: camera.aspect });
      camera.updateProjectionMatrix();
    }
    function applyMaterial(entry: AssetEntry) {
      applyMaterials(entry.root, entry.entity, live.current.scene?.room?.design);
    }
    function refreshSelection() {
      const entry = live.current.selectedEntityId ? entries.get(live.current.selectedEntityId) : undefined;
      selectedBox.visible = !!entry && entry.root.children.length > 0 && entry.entity.kind !== 'room';
      if (selectedBox.visible && entry) { selectedBox.box.setFromObject(entry.root); selectedBox.updateMatrixWorld(true); }
    }
    function synchronize() {
      const p = live.current;
      const wanted = new Set(p.scene?.entities.map((entity) => entity.id) ?? []);
      for (const [id, entry] of entries) {
        if (!wanted.has(id)) { world.remove(entry.root); disposeObject(entry.root); entries.delete(id); failures.delete(id); pending.delete(id); }
      }
      renderer.toneMappingExposure = 2 ** (p.scene?.room?.design?.exposure ?? 0);
      lights.synchronize(p.scene?.entities ?? [], p.scene?.room ?? { width: 10, depth: 6.2 });
      for (const entity of p.scene?.entities ?? []) {
        if (entity.kind === 'light') continue;
        let entry = entries.get(entity.id);
        if (!entry) { entry = { root: new Group(), url: null, version: 0, entity }; entries.set(entity.id, entry); world.add(entry.root); }
        entry.entity = entity; entry.root.name = entity.name; entry.root.userData.entityId = entity.id;
        entry.root.matrixAutoUpdate = false; entry.root.matrix.copy(entityMatrix(entity)); entry.root.matrixWorldNeedsUpdate = true;
        entry.root.visible = entity.visible;
        const url = entity.visible ? entity.asset_url ?? null : null;
        if (url !== entry.url) {
          disposeObject(entry.root); entry.root.clear(); entry.url = url; entry.version++;
          failures.delete(entity.id); pending.delete(entity.id);
          if (url) {
            const version = entry.version; const requestedEntry = entry;
            pending.add(entity.id);
            loader.load(url, (gltf) => {
              if (disposed || entries.get(entity.id) !== requestedEntry || version !== requestedEntry.version) { disposeObject(gltf.scene); return; }
              gltf.scene.traverse((obj) => {
                if (obj instanceof Mesh) {
                  // A point preview inside the six-bulb fixture otherwise projects
                  // a false solid star. Cycles retains the real fixture occlusion
                  // with its finite source; the realtime rig is an approximation.
                  const fixture = entity.kind === 'room' && /^(chandelier arm|chandelier stem|bulb socket|warm bulb)$/.test(componentName(obj));
                  obj.castShadow = !fixture; obj.receiveShadow = true; obj.userData.entityId = entity.id;
                }
              });
              prepareMaterials(gltf.scene, Math.min(8, renderer.capabilities.getMaxAnisotropy()));
              requestedEntry.root.add(gltf.scene); pending.delete(entity.id); applyMaterial(requestedEntry); refreshSpatialState(); refreshSelection();
            }, undefined, () => {
              if (disposed || entries.get(entity.id) !== requestedEntry || version !== requestedEntry.version) return;
              pending.delete(entity.id); failures.set(entity.id, entity.name);
            });
          }
        }
        applyMaterial(entry);
      }
      refreshSpatialState();
      const enteringCamera = p.mode !== currentMode && p.mode === 'camera';
      if (p.mode !== currentMode) {
        const leavingWalk = currentMode === 'walk';
        if (walk.isLocked) walk.unlock();
        currentMode = p.mode; keys.clear();
        if (currentMode === 'walk') {
          const spawn = findWalkSpawn(camera.position, p.scene?.room ?? { width: 10, depth: 6.2, height: 3.15 }, collisionBounds);
          walkAvailable = !!spawn;
          if (spawn) { camera.position.copy(spawn); setError(null); }
          else setError('No clear walking position is available. Choose another view or use Orbit.');
        }
        else if (leavingWalk) { orbit.target.copy(camera.position).add(camera.getWorldDirection(new Vector3()).multiplyScalar(3)); }
        updateProjection(); queueDraft();
      }
      orbit.enabled = currentMode === 'orbit';
      const active = p.cameraOverride ?? p.scene?.cameras.find((c) => c.id === p.activeCameraId) ?? p.scene?.cameras[0];
      const nextKey = active ? JSON.stringify(active) : '';
      if ((nextKey !== cameraKey || enteringCamera) && active) { cameraKey = nextKey; setCamera(active); }
      refreshSelection();
    }
    function frameSelection() {
      const entry = live.current.selectedEntityId ? entries.get(live.current.selectedEntityId) : undefined;
      if (!entry || entry.root.children.length === 0) return;
      const bounds = new Box3().setFromObject(entry.root);
      const center = bounds.getCenter(new Vector3()); const size = bounds.getSize(new Vector3()).length();
      const away = camera.position.clone().sub(orbit.target).normalize();
      orbit.target.copy(center); camera.position.copy(center).addScaledVector(away, Math.max(.4, size * 1.35));
      if (currentMode !== 'orbit') {
        if (walk.isLocked) walk.unlock();
        currentMode = 'orbit'; orbit.enabled = true; keys.clear(); updateProjection();
        live.current.onModeChange('orbit');
      }
      camera.lookAt(center); orbit.update(); emitDraft();
    }
    bridge.current = { synchronize, frameSelection };
    const resize = new ResizeObserver(() => {
      const bounds = container.getBoundingClientRect(); visible = bounds.width > 0 && bounds.height > 0;
      width = Math.max(1, bounds.width); height = Math.max(1, bounds.height);
      renderer.setSize(width, height); updateProjection();
    });
    resize.observe(container);
    const onPointerDown = (event: PointerEvent) => { pointerStart = { x: event.clientX, y: event.clientY }; };
    const onPointerUp = (event: PointerEvent) => {
      if (currentMode === 'walk') {
        if (!walk.isLocked && walkAvailable) { try { walk.lock(); } catch { setError('Pointer capture was denied. Use Orbit mode to navigate.'); } }
        return;
      }
      if (!pointerStart || Math.hypot(event.clientX - pointerStart.x, event.clientY - pointerStart.y) > 5) return;
      const bounds = renderer.domElement.getBoundingClientRect();
      const frame = currentMode === 'camera' ? fittedFrame(width, height, lens.aspect_ratio) : { x: 0, y: 0, width, height };
      const x = event.clientX - bounds.left - frame.x; const y = event.clientY - bounds.top - frame.y;
      if (x < 0 || y < 0 || x > frame.width || y > frame.height) return;
      raycaster.setFromCamera(new Vector2(x / frame.width * 2 - 1, 1 - y / frame.height * 2), camera);
      const hit = raycaster.intersectObjects([...entries.values()].map((e) => e.root), true)[0];
      if (hit) {
        let obj = hit.object; while (!obj.userData.entityId && obj.parent) obj = obj.parent;
        if (obj.userData.entityId) {
          live.current.onSelectEntity(obj.userData.entityId);
          if (entries.get(obj.userData.entityId)?.entity.kind === 'room') {
            const surface = surfaceOf(hit.object); if (surface) live.current.onSelectSurface?.(surface);
          }
        }
      }
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (isTyping(event.target)) return;
      const code = keyCode(event);
      if (code === 'Escape' && walk.isLocked) walk.unlock();
      if (walk.isLocked && ['KeyW', 'KeyA', 'KeyS', 'KeyD', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'ShiftLeft', 'ShiftRight'].includes(code)) { keys.add(code); event.preventDefault(); }
    };
    const onKeyUp = (event: KeyboardEvent) => { keys.delete(keyCode(event)); };
    const onLock = () => setStatus((s) => ({ ...s, locked: true }));
    const onUnlock = () => { keys.clear(); queueDraft(); setStatus((s) => ({ ...s, locked: false })); };
    const onBlur = () => { keys.clear(); if (walk.isLocked) walk.unlock(); };
    function canStand(position: Vector3) {
      return canStandAt(position, live.current.scene?.room ?? { width: 10, depth: 6.2, height: 3.15 }, collisionBounds);
    }
    function render(now: number) {
      if (disposed) return;
      // Reference browsing retains the WebGL context but submits no hidden frames.
      if (!visible || document.hidden) {
        if (container!.dataset.renderState !== 'suspended') container!.dataset.renderState = 'suspended';
        lastTime = now; statusTime = now; frames = 0; frameDurations.length = 0;
        if (walk.isLocked) walk.unlock();
        raf = requestAnimationFrame(render); return;
      }
      if (container!.dataset.renderState !== 'active') container!.dataset.renderState = 'active';
      frameDurations.push(now - lastTime); if (frameDurations.length > 300) frameDurations.shift();
      const dt = Math.min((now - lastTime) / 1000, .05); lastTime = now;
      if (currentMode === 'orbit') orbit.update();
      if (walk.isLocked && keys.size) {
        const forward = camera.getWorldDirection(new Vector3()); forward.y = 0; forward.normalize();
        const right = new Vector3().crossVectors(forward, new Vector3(0, 1, 0));
        const move = new Vector3();
        if (keys.has('KeyW') || keys.has('ArrowUp')) move.add(forward);
        if (keys.has('KeyS') || keys.has('ArrowDown')) move.sub(forward);
        if (keys.has('KeyD') || keys.has('ArrowRight')) move.add(right);
        if (keys.has('KeyA') || keys.has('ArrowLeft')) move.sub(right);
        move.normalize().multiplyScalar(dt * (keys.has('ShiftLeft') || keys.has('ShiftRight') ? 2.8 : 1.6));
        const next = camera.position.clone(); next.x += move.x; if (canStand(next)) camera.position.x = next.x;
        next.copy(camera.position); next.z += move.z; if (canStand(next)) camera.position.z = next.z;
        camera.position.y = 1.62; queueDraft();
      }
      renderer.setScissorTest(false); renderer.setViewport(0, 0, width, height); renderer.clear();
      if (currentMode === 'camera') {
        const frame = fittedFrame(width, height, lens.aspect_ratio);
        renderer.setScissorTest(true); renderer.setScissor(frame.x, frame.y, frame.width, frame.height); renderer.setViewport(frame.x, frame.y, frame.width, frame.height);
      }
      renderer.render(world, camera);
      submittedFrames++;
      frames++;
      if (now - statusTime > 1000) {
        const missingActors = (live.current.scene?.entities ?? []).filter((e) => e.visible && e.kind === 'actor' && !e.asset_url).length;
        setStatus({ loading: pending.size, missingActors, failed: [...failures.values()], fps: Math.round(frames * 1000 / (now - statusTime)), triangles: renderer.info.render.triangles, locked: walk.isLocked });
        const sorted = [...frameDurations].sort((a, b) => a - b);
        container!.dataset.renderMetrics = JSON.stringify({ profile: 'realtime-agx-v3', toneMapping: 'AgX', exposureStops: Math.log2(renderer.toneMappingExposure), dof: 'final-render-only', frames: frameDurations.length, submittedFrames, p95FrameMs: sorted[Math.floor(sorted.length * .95)], calls: renderer.info.render.calls, triangles: renderer.info.render.triangles, textures: renderer.info.memory.textures, geometries: renderer.info.memory.geometries, pending: pending.size, failed: failures.size, collisionBoxes: collisionBounds.length, mode: currentMode, locked: walk.isLocked, cameraPosition: toCanonicalPoint(camera.position) });
        frames = 0; statusTime = now;
      }
      raf = requestAnimationFrame(render);
    }
    renderer.domElement.addEventListener('pointerdown', onPointerDown);
    renderer.domElement.addEventListener('pointerup', onPointerUp);
    window.addEventListener('keydown', onKeyDown); window.addEventListener('keyup', onKeyUp); window.addEventListener('blur', onBlur);
    window.addEventListener('ghost-director:frame-selection', frameSelection);
    walk.addEventListener('lock', onLock); walk.addEventListener('unlock', onUnlock);
    orbit.addEventListener('end', queueDraft);
    synchronize(); raf = requestAnimationFrame(render);
    return () => {
      disposed = true; bridge.current = null; cancelAnimationFrame(raf); clearTimeout(draftTimeout); resize.disconnect();
      if (walk.isLocked) walk.unlock();
      walk.removeEventListener('lock', onLock); walk.removeEventListener('unlock', onUnlock);
      orbit.removeEventListener('end', queueDraft); orbit.dispose(); walk.dispose();
      renderer.domElement.removeEventListener('pointerdown', onPointerDown); renderer.domElement.removeEventListener('pointerup', onPointerUp);
      window.removeEventListener('keydown', onKeyDown); window.removeEventListener('keyup', onKeyUp); window.removeEventListener('blur', onBlur);
      window.removeEventListener('ghost-director:frame-selection', frameSelection);
      disposeObject(world); selectedBox.geometry.dispose(); (selectedBox.material as Material).dispose();
      lights.dispose(); envTarget.dispose(); renderer.dispose(); renderer.domElement.remove();
    };
  }, []);

  useEffect(() => { bridge.current?.synchronize(); }, [props.scene, props.selectedEntityId, props.activeCameraId, props.cameraOverride, props.mode]);
  const selected = props.scene?.entities.find((entity) => entity.id === props.selectedEntityId);
  return (
    <div className="gd-viewport" data-testid="scene-viewport">
      <div ref={host} className="gd-viewport-canvas" />
      <div className="gd-viewport-topline" aria-live="polite">
        <span>{props.mode === 'camera' ? 'Camera · 16:9 framing' : props.mode === 'walk' ? 'Walk · 1.62 m eye height' : 'Orbit · drag to compose'}</span>
        {props.readOnly && <span>Read-only scene</span>}
      </div>
      {selected && <div className="gd-viewport-selection"><span>{selected.name}</span><code>{selected.id}</code></div>}
      {props.mode === 'walk' && !status.locked && <div className="gd-viewport-hint">Click the set to walk · W A S D · Esc releases the pointer</div>}
      {status.locked && <div className="gd-viewport-crosshair" aria-hidden="true">+</div>}
      <div className="gd-viewport-footer">
        <span>{status.loading ? `Loading ${status.loading} assets` : status.missingActors ? `${status.missingActors} actor assets missing · set preview` : 'Realtime preview · depth of field in render'}</span>
        <span>{status.fps !== null ? `${status.fps} fps · ${(status.triangles / 1000).toFixed(0)}k triangles` : 'Starting renderer'}</span>
      </div>
      {(error || status.failed.length > 0) && <div role="alert" className="gd-viewport-error">{error ?? `Asset load failed: ${status.failed.join(', ')}`}</div>}
      {!props.scene && <div className="gd-viewport-empty">No scene loaded</div>}
    </div>
  );
}
