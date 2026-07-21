"use client";

import { useRef, useMemo, useEffect } from "react";
import { Canvas, useFrame } from "@react-three/fiber";
import { Line, OrbitControls } from "@react-three/drei";
import * as THREE from "three";

/* ---------- helpers ---------- */

function latLngToVec3(lat: number, lng: number, radius: number): THREE.Vector3 {
  const phi = (90 - lat) * (Math.PI / 180);
  const theta = (lng + 180) * (Math.PI / 180);
  return new THREE.Vector3(
    -radius * Math.sin(phi) * Math.cos(theta),
    radius * Math.cos(phi),
    radius * Math.sin(phi) * Math.sin(theta)
  );
}

/* Major market cities */
const CITIES: [number, number][] = [
  [40.7, -74.0],   // New York
  [51.5, -0.1],    // London
  [48.9, 2.3],     // Paris
  [52.5, 13.4],    // Berlin
  [35.7, 139.7],   // Tokyo
  [19.4, -99.1],   // Mexico City
  [-23.5, -46.6],  // Sao Paulo
  [28.6, 77.2],    // Delhi
  [37.6, 127.0],   // Seoul
  [-33.9, 18.5],   // Cape Town
  [31.2, 121.5],   // Shanghai
  [1.3, 103.9],    // Singapore
  [55.8, 37.6],    // Moscow
  [-34.6, -58.4],  // Buenos Aires
  [35.0, -106.6],  // Albuquerque (US interior)
  [25.3, 51.5],    // Doha
  [33.9, -118.2],  // Los Angeles
  [41.0, 29.0],    // Istanbul
  [22.3, 114.2],   // Hong Kong
  [6.5, 3.4],      // Lagos
];

/* connections between select cities */
const CONNECTIONS: [number, number][] = [
  [0, 1], [0, 5], [1, 2], [1, 3], [2, 3],
  [4, 11], [4, 9], [7, 11], [6, 13], [0, 16],
  [8, 18], [10, 18], [3, 12], [5, 6], [15, 7],
  [17, 12], [19, 1], [16, 5],
];

/* ---------- Connection arcs ---------- */

function ConnectionArcs({ radius }: { radius: number }) {
  const arcs = useMemo(() => {
    return CONNECTIONS.map(([ci, cj]) => {
      const start = latLngToVec3(...CITIES[ci], radius);
      const end = latLngToVec3(...CITIES[cj], radius);
      const mid = start.clone().add(end).multiplyScalar(0.5);
      const dist = start.distanceTo(end);
      mid.normalize().multiplyScalar(radius + dist * 0.25);

      const curve = new THREE.QuadraticBezierCurve3(start, mid, end);
      const points = curve.getPoints(48);
      return points.map((p) => [p.x, p.y, p.z] as [number, number, number]);
    });
  }, [radius]);

  return (
    <>
      {arcs.map((pts, i) => (
        <Line
          key={i}
          points={pts}
          color="#06b6d4"
          transparent
          opacity={0.2}
          lineWidth={1}
        />
      ))}
    </>
  );
}

/* ---------- City dots ---------- */

function CityDots({ radius }: { radius: number }) {
  const meshRef = useRef<THREE.InstancedMesh>(null);

  useEffect(() => {
    if (!meshRef.current) return;
    const dummy = new THREE.Object3D();
    CITIES.forEach(([lat, lng], i) => {
      const pos = latLngToVec3(lat, lng, radius);
      dummy.position.copy(pos);
      dummy.lookAt(pos.clone().multiplyScalar(2));
      dummy.updateMatrix();
      meshRef.current!.setMatrixAt(i, dummy.matrix);
    });
    meshRef.current.instanceMatrix.needsUpdate = true;
  }, [radius]);

  return (
    <instancedMesh ref={meshRef} args={[undefined, undefined, CITIES.length]}>
      <sphereGeometry args={[0.025, 12, 12]} />
      <meshBasicMaterial color="#06b6d4" />
    </instancedMesh>
  );
}

/* ---------- Glow dots (outer) ---------- */

function GlowDots({ radius }: { radius: number }) {
  const meshRef = useRef<THREE.InstancedMesh>(null);

  useEffect(() => {
    if (!meshRef.current) return;
    const dummy = new THREE.Object3D();
    CITIES.forEach(([lat, lng], i) => {
      const pos = latLngToVec3(lat, lng, radius);
      dummy.position.copy(pos);
      dummy.updateMatrix();
      meshRef.current!.setMatrixAt(i, dummy.matrix);
    });
    meshRef.current.instanceMatrix.needsUpdate = true;
  }, [radius]);

  return (
    <instancedMesh ref={meshRef} args={[undefined, undefined, CITIES.length]}>
      <sphereGeometry args={[0.05, 8, 8]} />
      <meshBasicMaterial color="#06b6d4" transparent opacity={0.25} />
    </instancedMesh>
  );
}

/* ---------- Wireframe globe ---------- */

function WireframeGlobe({ radius }: { radius: number }) {
  return (
    <mesh>
      <sphereGeometry args={[radius, 48, 48]} />
      <meshBasicMaterial
        color="#1e3a5f"
        wireframe
        transparent
        opacity={0.12}
      />
    </mesh>
  );
}

/* ---------- Graticule lines ---------- */

function Graticule({ radius }: { radius: number }) {
  const lines = useMemo(() => {
    const all: [number, number, number][][] = [];

    // latitude lines
    for (let lat = -60; lat <= 60; lat += 30) {
      const pts: [number, number, number][] = [];
      for (let lng = -180; lng <= 180; lng += 5) {
        const v = latLngToVec3(lat, lng, radius + 0.002);
        pts.push([v.x, v.y, v.z]);
      }
      all.push(pts);
    }

    // longitude lines
    for (let lng = -180; lng < 180; lng += 30) {
      const pts: [number, number, number][] = [];
      for (let lat = -90; lat <= 90; lat += 5) {
        const v = latLngToVec3(lat, lng, radius + 0.002);
        pts.push([v.x, v.y, v.z]);
      }
      all.push(pts);
    }

    return all;
  }, [radius]);

  return (
    <>
      {lines.map((pts, i) => (
        <Line
          key={i}
          points={pts}
          color="#3b82f6"
          transparent
          opacity={0.06}
          lineWidth={1}
        />
      ))}
    </>
  );
}

/* ---------- Atmosphere glow ---------- */

function Atmosphere({ radius }: { radius: number }) {
  return (
    <mesh>
      <sphereGeometry args={[radius * 1.15, 48, 48]} />
      <meshBasicMaterial
        color="#3b82f6"
        transparent
        opacity={0.04}
        side={THREE.BackSide}
      />
    </mesh>
  );
}

/* ---------- Rotating group ---------- */

function RotatingGlobe() {
  const groupRef = useRef<THREE.Group>(null);
  const RADIUS = 1.8;

  useFrame((_, delta) => {
    if (groupRef.current) {
      groupRef.current.rotation.y += delta * 0.12;
    }
  });

  return (
    <group ref={groupRef} rotation={[0.3, 0, 0.1]}>
      <WireframeGlobe radius={RADIUS} />
      <Graticule radius={RADIUS} />
      <ConnectionArcs radius={RADIUS} />
      <CityDots radius={RADIUS} />
      <GlowDots radius={RADIUS} />
      <Atmosphere radius={RADIUS} />
    </group>
  );
}

/* ---------- Main export ---------- */

export default function Globe() {
  return (
    <div className="globe-container w-full h-full">
      <Canvas
        camera={{ position: [0, 0, 4.5], fov: 45 }}
        gl={{ antialias: true, alpha: true }}
        style={{ background: "transparent" }}
      >
        <ambientLight intensity={0.5} />
        <RotatingGlobe />
        <OrbitControls
          enableZoom={false}
          enablePan={false}
          enableRotate={false}
          autoRotate={false}
        />
      </Canvas>
    </div>
  );
}
