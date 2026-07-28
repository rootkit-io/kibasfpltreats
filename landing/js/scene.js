/* ═══════════════════════════════════════════════
   KFT — three.js hero scene
   particle football + orbit rings + data field
   ═══════════════════════════════════════════════ */
import * as THREE from "https://unpkg.com/three@0.160.0/build/three.module.js";

export function initScene(container) {
  const W = () => container.clientWidth;
  const H = () => container.clientHeight;

  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.setSize(W(), H());
  container.appendChild(renderer.domElement);

  const scene = new THREE.Scene();
  scene.fog = new THREE.FogExp2(0x05070b, 0.035);

  const camera = new THREE.PerspectiveCamera(50, W() / H(), 0.1, 100);
  camera.position.set(0, 0, 9);

  const ORANGE = new THREE.Color("#FF5F1F");
  const GREEN  = new THREE.Color("#00FF87");
  const BLUE   = new THREE.Color("#38BDF8");

  const group = new THREE.Group();
  scene.add(group);
  /* push the ball right of centre on wide screens */
  const layout = () => { group.position.x = W() > 900 ? 2.6 : 0; group.position.y = W() > 900 ? 0.1 : 1.4; };
  layout();

  /* ── wireframe football ── */
  const ballGeo = new THREE.IcosahedronGeometry(2.15, 1);
  const wire = new THREE.LineSegments(
    new THREE.EdgesGeometry(ballGeo),
    new THREE.LineBasicMaterial({ color: ORANGE, transparent: true, opacity: 0.55 })
  );
  group.add(wire);

  /* glowing vertices */
  const vertGeo = new THREE.BufferGeometry().setFromPoints(
    Array.from({ length: ballGeo.attributes.position.count }, (_, i) =>
      new THREE.Vector3().fromBufferAttribute(ballGeo.attributes.position, i)
    )
  );
  const verts = new THREE.Points(
    vertGeo,
    new THREE.PointsMaterial({ color: GREEN, size: 0.09, transparent: true, opacity: 0.95, blending: THREE.AdditiveBlending, depthWrite: false })
  );
  group.add(verts);

  /* inner core */
  const core = new THREE.Mesh(
    new THREE.IcosahedronGeometry(0.55, 2),
    new THREE.MeshBasicMaterial({ color: ORANGE, wireframe: true, transparent: true, opacity: 0.35 })
  );
  group.add(core);

  /* ── orbit rings ── */
  const rings = [];
  [[3.1, ORANGE, 0.5, 0.9], [3.7, GREEN, 0.28, -0.6], [4.4, BLUE, 0.16, 0.35]].forEach(([r, col, op, tilt]) => {
    const ring = new THREE.Mesh(
      new THREE.TorusGeometry(r, 0.008, 8, 140),
      new THREE.MeshBasicMaterial({ color: col, transparent: true, opacity: op })
    );
    ring.rotation.x = Math.PI / 2 + tilt;
    ring.rotation.y = tilt * 0.6;
    group.add(ring);
    rings.push(ring);

    /* satellite dot per ring */
    const sat = new THREE.Mesh(new THREE.SphereGeometry(0.05, 12, 12), new THREE.MeshBasicMaterial({ color: col }));
    sat.userData = { r, speed: 0.35 / r * 3, phase: Math.random() * Math.PI * 2, ring };
    group.add(sat);
    rings.push(sat);
  });

  /* ── ambient data field ── */
  const N = 900;
  const pos = new Float32Array(N * 3);
  const col = new Float32Array(N * 3);
  const spd = new Float32Array(N);
  for (let i = 0; i < N; i++) {
    pos[i * 3]     = (Math.random() - 0.5) * 30;
    pos[i * 3 + 1] = (Math.random() - 0.5) * 18;
    pos[i * 3 + 2] = (Math.random() - 0.5) * 16 - 3;
    const c = Math.random() < 0.12 ? ORANGE : Math.random() < 0.2 ? GREEN : new THREE.Color(0.35, 0.42, 0.52);
    col[i * 3] = c.r; col[i * 3 + 1] = c.g; col[i * 3 + 2] = c.b;
    spd[i] = 0.12 + Math.random() * 0.5;
  }
  const fieldGeo = new THREE.BufferGeometry();
  fieldGeo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
  fieldGeo.setAttribute("color", new THREE.BufferAttribute(col, 3));
  const field = new THREE.Points(
    fieldGeo,
    new THREE.PointsMaterial({ size: 0.045, vertexColors: true, transparent: true, opacity: 0.8, blending: THREE.AdditiveBlending, depthWrite: false })
  );
  scene.add(field);

  /* ── interaction state ── */
  let mx = 0, my = 0, tmx = 0, tmy = 0, scroll = 0;
  window.addEventListener("pointermove", (e) => {
    tmx = (e.clientX / window.innerWidth) * 2 - 1;
    tmy = (e.clientY / window.innerHeight) * 2 - 1;
  }, { passive: true });

  const clock = new THREE.Clock();
  let raf, visible = true;

  function tick() {
    raf = requestAnimationFrame(tick);
    if (!visible) return;
    const t = clock.getElapsedTime();
    mx += (tmx - mx) * 0.045;
    my += (tmy - my) * 0.045;

    group.rotation.y = t * 0.12 + mx * 0.45;
    group.rotation.x = my * 0.3 + Math.sin(t * 0.3) * 0.05;
    core.rotation.y = -t * 0.4;
    core.rotation.z = t * 0.25;
    const pump = 1 + Math.sin(t * 1.6) * 0.04;
    core.scale.setScalar(pump);

    rings.forEach((obj) => {
      if (obj.userData && obj.userData.r) {
        const { r, speed, phase, ring } = obj.userData;
        const a = t * speed + phase;
        const p = new THREE.Vector3(Math.cos(a) * r, 0, Math.sin(a) * r);
        p.applyEuler(ring.rotation);
        obj.position.copy(p);
      } else if (obj.geometry && obj.geometry.type === "TorusGeometry") {
        obj.rotation.z += 0.0009;
      }
    });

    /* drifting field */
    const fp = field.geometry.attributes.position;
    for (let i = 0; i < N; i++) {
      let y = fp.getY(i) + spd[i] * 0.008;
      if (y > 9) y = -9;
      fp.setY(i, y);
    }
    fp.needsUpdate = true;
    field.rotation.y = mx * 0.05;

    /* scroll: recede + fade */
    camera.position.z = 9 + scroll * 5;
    camera.position.y = -scroll * 2.4;
    renderer.domElement.style.opacity = String(1 - scroll * 0.9);

    renderer.render(scene, camera);
  }
  tick();

  /* pause when off-screen */
  new IntersectionObserver(([e]) => { visible = e.isIntersecting; }, { threshold: 0 }).observe(container);

  window.addEventListener("resize", () => {
    camera.aspect = W() / H();
    camera.updateProjectionMatrix();
    renderer.setSize(W(), H());
    layout();
  });

  return {
    setScroll(p) { scroll = p; },
    destroy() { cancelAnimationFrame(raf); renderer.dispose(); },
  };
}
