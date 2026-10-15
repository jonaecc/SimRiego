/**
 * greenhouse3d.js
 * Motor 3D con Three.js para el Invernadero / Carpa de Cultivo Virtual.
 * Incluye iluminación dinámica (fotoperiodo), goteros con partículas de agua,
 * bandeja de drenaje con celda de carga, maceta con sustrato dinámico y
 * planta botánica procedural que crece y reacciona al estrés en tiempo real.
 */

class Greenhouse3D {
    constructor(containerId) {
        this.container = document.getElementById(containerId);
        this.width = this.container.clientWidth;
        this.height = this.container.clientHeight;

        // Estado local de animación
        this.state = null;
        this.waterParticles = [];
        this.fanAngle = 0;
        this.fanOscillation = 0;

        this.initThree();
        this.buildScene();
        this.setupEventListeners();
        this.animate = this.animate.bind(this);
        requestAnimationFrame(this.animate);
    }

    initThree() {
        // 1. Escena
        this.scene = new THREE.Scene();
        this.scene.background = new THREE.Color(0x0e1318);
        this.scene.fog = new THREE.FogExp2(0x0e1318, 0.15);

        // 2. Cámara
        this.camera = new THREE.PerspectiveCamera(45, this.width / this.height, 0.1, 50);
        this.camera.position.set(0, 1.3, 2.2);

        // 3. Renderer
        this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
        this.renderer.setSize(this.width, this.height);
        this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
        this.renderer.shadowMap.enabled = true;
        this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
        this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
        this.renderer.toneMappingExposure = 1.1;
        this.container.appendChild(this.renderer.domElement);

        // 4. Orbit Controls
        this.controls = new THREE.OrbitControls(this.camera, this.renderer.domElement);
        this.controls.enableDamping = true;
        this.controls.dampingFactor = 0.05;
        this.controls.maxPolarAngle = Math.PI / 2 - 0.02; // No pasar del piso
        this.controls.minDistance = 0.5;
        this.controls.maxDistance = 4.5;
        this.controls.target.set(0, 0.5, 0);
    }

    buildScene() {
        // --- ILUMINACIÓN ---
        this.ambientLight = new THREE.AmbientLight(0xffffff, 0.2);
        this.scene.add(this.ambientLight);

        // Luz de cultivo (LED Panel Grow Light)
        this.growLight = new THREE.SpotLight(0xfffaed, 2.8, 4.0, Math.PI / 3, 0.4, 1.2);
        this.growLight.position.set(0, 2.2, 0);
        this.growLight.castShadow = true;
        this.growLight.shadow.mapSize.width = 2048;
        this.growLight.shadow.mapSize.height = 2048;
        this.growLight.shadow.bias = -0.0008;
        this.scene.add(this.growLight);
        this.scene.add(this.growLight.target);
        this.growLight.target.position.set(0, 0.4, 0);

        // Luz secundaria suave (relleno)
        this.fillLight = new THREE.PointLight(0xa5d6a7, 0.4, 3.0);
        this.fillLight.position.set(0.8, 0.8, 0.8);
        this.scene.add(this.fillLight);

        // --- ENTORNO / CARPA ---
        this.buildGrowTent();

        // --- SISTEMA DE MACETA, BANDEJA Y RIEGO ---
        this.buildPotAndTray();

        // --- PLANTA PROCEDURAL 3D ---
        this.buildPlantMesh();

        // --- GOTEROS Y PARTÍCULAS DE AGUA ---
        this.buildDrippers();

        // --- VENTILADOR DE CIRCULACIÓN ---
        this.buildOscillatingFan();
    }

    buildGrowTent() {
        // Piso de carpa reflectante mylar
        const floorGeo = new THREE.PlaneGeometry(3.0, 3.0);
        const floorMat = new THREE.MeshStandardMaterial({
            color: 0x1b232b,
            roughness: 0.35,
            metalness: 0.6
        });
        const floor = new THREE.Mesh(floorGeo, floorMat);
        floor.rotation.x = -Math.PI / 2;
        floor.receiveShadow = true;
        this.scene.add(floor);

        // Grid industrial sutil
        const grid = new THREE.GridHelper(3.0, 20, 0x00e5ff, 0x1f2e3d);
        grid.position.y = 0.002;
        this.scene.add(grid);

        // Postes metálicos de la carpa (4 esquinas)
        const poleGeo = new THREE.CylinderGeometry(0.015, 0.015, 2.4);
        const poleMat = new THREE.MeshStandardMaterial({ color: 0x546e7a, metalness: 0.8, roughness: 0.2 });
        const corners = [
            [-0.9, -0.9], [0.9, -0.9], [-0.9, 0.9], [0.9, 0.9]
        ];
        corners.forEach(([x, z]) => {
            const pole = new THREE.Mesh(poleGeo, poleMat);
            pole.position.set(x, 1.2, z);
            this.scene.add(pole);
        });

        // Panel LED overhead fixture
        const fixtureGeo = new THREE.BoxGeometry(0.65, 0.04, 0.45);
        const fixtureMat = new THREE.MeshStandardMaterial({ color: 0x263238, metalness: 0.7, roughness: 0.3 });
        this.fixtureMesh = new THREE.Mesh(fixtureGeo, fixtureMat);
        this.fixtureMesh.position.set(0, 2.22, 0);
        this.scene.add(this.fixtureMesh);

        // Diodos emisores del panel LED
        const ledsGeo = new THREE.PlaneGeometry(0.58, 0.38);
        this.ledsMat = new THREE.MeshBasicMaterial({ color: 0xfff9e6 });
        const ledsMesh = new THREE.Mesh(ledsGeo, this.ledsMat);
        ledsMesh.rotation.x = Math.PI / 2;
        ledsMesh.position.set(0, 2.199, 0);
        this.scene.add(ledsMesh);
    }

    buildPotAndTray() {
        // 1. Celda de carga (HX711 aluminio bajo la bandeja)
        const cellGeo = new THREE.BoxGeometry(0.14, 0.02, 0.04);
        const cellMat = new THREE.MeshStandardMaterial({ color: 0xb0bec5, metalness: 0.85, roughness: 0.2 });
        const loadCell = new THREE.Mesh(cellGeo, cellMat);
        loadCell.position.set(0, 0.01, 0);
        this.scene.add(loadCell);

        // 2. Bandeja de drenaje (Runoff Tray)
        const trayGeo = new THREE.CylinderGeometry(0.24, 0.21, 0.045, 32);
        const trayMat = new THREE.MeshStandardMaterial({
            color: 0x11161d,
            roughness: 0.3,
            metalness: 0.2
        });
        this.trayMesh = new THREE.Mesh(trayGeo, trayMat);
        this.trayMesh.position.set(0, 0.04, 0);
        this.trayMesh.receiveShadow = true;
        this.scene.add(this.trayMesh);

        // Disco de agua acumulada en la bandeja (escorrentía visible)
        const waterGeo = new THREE.CylinderGeometry(0.23, 0.22, 0.038, 32);
        this.waterMat = new THREE.MeshStandardMaterial({
            color: 0x29b6f6,
            roughness: 0.1,
            metalness: 0.1,
            transparent: true,
            opacity: 0.0
        });
        this.waterMesh = new THREE.Mesh(waterGeo, this.waterMat);
        this.waterMesh.position.set(0, 0.042, 0);
        this.scene.add(this.waterMesh);

        // 3. Elevador / Rejilla sobre la bandeja
        const riserGeo = new THREE.CylinderGeometry(0.18, 0.18, 0.02, 16);
        const riserMat = new THREE.MeshStandardMaterial({ color: 0x37474f, wireframe: true });
        const riser = new THREE.Mesh(riserGeo, riserMat);
        riser.position.set(0, 0.07, 0);
        this.scene.add(riser);

        // 4. Maceta geotextil / contenedora (Smart Pot 3.5L)
        const potGeo = new THREE.CylinderGeometry(0.15, 0.13, 0.22, 32, 1, true);
        const potMat = new THREE.MeshStandardMaterial({
            color: 0x212121,
            roughness: 0.9,
            metalness: 0.05,
            side: THREE.DoubleSide
        });
        this.potMesh = new THREE.Mesh(potGeo, potMat);
        this.potMesh.position.set(0, 0.18, 0);
        this.potMesh.castShadow = true;
        this.potMesh.receiveShadow = true;
        this.scene.add(this.potMesh);

        // 5. Sustrato superior (Coco / Perlita - cambia de color según humedad)
        const soilGeo = new THREE.CylinderGeometry(0.147, 0.145, 0.02, 32);
        this.soilMat = new THREE.MeshStandardMaterial({
            color: 0x3e2723, // Café húmedo
            roughness: 0.95,
            metalness: 0.0
        });
        this.soilMesh = new THREE.Mesh(soilGeo, this.soilMat);
        this.soilMesh.position.set(0, 0.28, 0);
        this.soilMesh.receiveShadow = true;
        this.scene.add(this.soilMesh);

        // Malla conector de drenaje
        const drainPipeGeo = new THREE.CylinderGeometry(0.008, 0.008, 0.06);
        const drainPipeMat = new THREE.MeshStandardMaterial({ color: 0x455a64 });
        const drainPipe = new THREE.Mesh(drainPipeGeo, drainPipeMat);
        drainPipe.position.set(0.20, 0.03, 0);
        drainPipe.rotation.z = Math.PI / 3;
        this.scene.add(drainPipe);
    }

    buildDrippers() {
        // Tubería de alimentación de riego principal (PE 16mm)
        const pipeGeo = new THREE.TorusGeometry(0.165, 0.006, 8, 32);
        const pipeMat = new THREE.MeshStandardMaterial({ color: 0x050505, roughness: 0.4 });
        const mainPipe = new THREE.Mesh(pipeGeo, pipeMat);
        mainPipe.position.set(0, 0.29, 0);
        mainPipe.rotation.x = Math.PI / 2;
        this.scene.add(mainPipe);

        // 3 goteros clavados en el sustrato (Drippers)
        this.dripperPositions = [];
        const dripperGeo = new THREE.CylinderGeometry(0.005, 0.007, 0.04, 8);
        const dripperMat = new THREE.MeshStandardMaterial({ color: 0x00e676, roughness: 0.3 });

        for (let i = 0; i < 3; i++) {
            const angle = (i * (2 * Math.PI / 3)) + 0.3;
            const r = 0.09;
            const x = Math.cos(angle) * r;
            const z = Math.sin(angle) * r;
            const dripper = new THREE.Mesh(dripperGeo, dripperMat);
            dripper.position.set(x, 0.29, z);
            this.scene.add(dripper);
            this.dripperPositions.push(new THREE.Vector3(x, 0.28, z));
        }

        // Sistema de partículas para gotas de agua animadas
        const dropGeo = new THREE.SphereGeometry(0.003, 6, 6);
        const dropMat = new THREE.MeshBasicMaterial({ color: 0x40c4ff, transparent: true, opacity: 0.85 });
        this.particlePool = [];
        for (let i = 0; i < 35; i++) {
            const drop = new THREE.Mesh(dropGeo, dropMat);
            drop.visible = false;
            this.scene.add(drop);
            this.particlePool.push({
                mesh: drop,
                active: false,
                pos: new THREE.Vector3(),
                vy: 0,
                dripperIdx: 0
            });
        }
    }

    buildOscillatingFan() {
        this.fanGroup = new THREE.Group();
        this.fanGroup.position.set(0.75, 1.1, -0.6);

        // Soporte de poste
        const mountGeo = new THREE.BoxGeometry(0.04, 0.06, 0.08);
        const mountMat = new THREE.MeshStandardMaterial({ color: 0x263238 });
        const mount = new THREE.Mesh(mountGeo, mountMat);
        this.fanGroup.add(mount);

        // Cabeza oscilante
        this.fanHead = new THREE.Group();
        this.fanHead.position.set(-0.06, 0, 0);

        // Rejilla circular
        const cageGeo = new THREE.TorusGeometry(0.09, 0.008, 8, 24);
        const cageMat = new THREE.MeshStandardMaterial({ color: 0x90a4ae });
        const cage = new THREE.Mesh(cageGeo, cageMat);
        this.fanHead.add(cage);

        // Aspas giratorias
        this.fanBlades = new THREE.Group();
        const bladeGeo = new THREE.BoxGeometry(0.075, 0.02, 0.002);
        const bladeMat = new THREE.MeshStandardMaterial({ color: 0x37474f });
        for (let b = 0; b < 3; b++) {
            const blade = new THREE.Mesh(bladeGeo, bladeMat);
            blade.rotation.z = b * (Math.PI * 2 / 3);
            blade.position.x = Math.cos(blade.rotation.z) * 0.035;
            blade.position.y = Math.sin(blade.rotation.z) * 0.035;
            this.fanBlades.add(blade);
        }
        this.fanHead.add(this.fanBlades);
        this.fanGroup.add(this.fanHead);
        this.scene.add(this.fanGroup);
    }

    buildPlantMesh() {
        this.plantGroup = new THREE.Group();
        this.plantGroup.position.set(0, 0.28, 0); // Emerge del sustrato
        this.scene.add(this.plantGroup);

        // Materiales
        this.stemMat = new THREE.MeshStandardMaterial({
            color: 0x43a047,
            roughness: 0.6,
            metalness: 0.1
        });

        this.leafMat = new THREE.MeshStandardMaterial({
            color: 0x2e7d32,
            roughness: 0.45,
            metalness: 0.05,
            side: THREE.DoubleSide
        });

        this.budMat = new THREE.MeshStandardMaterial({
            color: 0x33691e,
            roughness: 0.85,
            metalness: 0.0
        });

        this.rebuildPlantStructure(15.0, 0.0, 0.0, 0.0, 0.0);
    }

    rebuildPlantStructure(heightCm, droop, tipBurn, claw, budsSize) {
        // Limpiar mallas anteriores
        while (this.plantGroup.children.length > 0) {
            const obj = this.plantGroup.children[0];
            this.plantGroup.remove(obj);
        }

        const scaleH = (heightCm / 100.0) * 0.95; // Convertir cm a unidades Three.js
        const stemRadius = Math.max(0.006, 0.003 + scaleH * 0.02);

        // Tallo principal con curvatura natural
        const curvePoints = [
            new THREE.Vector3(0, 0, 0),
            new THREE.Vector3(0.005, scaleH * 0.3, 0.002),
            new THREE.Vector3(-0.003, scaleH * 0.7, -0.004),
            new THREE.Vector3(0, scaleH, 0)
        ];
        const curve = new THREE.CatmullRomCurve3(curvePoints);
        const stemGeo = new THREE.TubeGeometry(curve, 16, stemRadius, 8, false);
        const stemMesh = new THREE.Mesh(stemGeo, this.stemMat);
        stemMesh.castShadow = true;
        this.plantGroup.add(stemMesh);

        // Nudos y ramas
        const nodes = Math.min(10, Math.max(2, Math.floor(scaleH * 16)));
        for (let n = 1; n <= nodes; n++) {
            const frac = n / (nodes + 1);
            const nodeY = scaleH * frac;
            const branchLen = scaleH * (1.1 - frac * 0.6) * 0.55;
            const numLeaves = (n % 2 === 0) ? 4 : 2;

            for (let l = 0; l < numLeaves; l++) {
                const angle = (l * (Math.PI * 2 / numLeaves)) + (n * 0.85);
                const leafGroup = new THREE.Group();
                leafGroup.position.set(0, nodeY, 0);
                leafGroup.rotation.y = angle;

                // Rotación de turgencia (droop: hojas marchitas caídas hacia el suelo)
                // Hojas normales: ángulo hacia arriba (~15-25°)
                // Con marchitez (droop=1.0): caen hacia abajo (-65°)
                const basePitch = (20 * Math.PI / 180) - (droop * (85 * Math.PI / 180));
                leafGroup.rotation.z = -basePitch;

                // Hoja individual en abanico (Cannabis fan leaf blade)
                const bladeGeo = new THREE.ConeGeometry(branchLen * 0.42, branchLen, 5);
                bladeGeo.scale(0.35, 1.0, 0.06);
                bladeGeo.translate(0, branchLen * 0.5, 0);

                const blade = new THREE.Mesh(bladeGeo, this.leafMat);
                blade.rotation.z = Math.PI / 2;
                blade.castShadow = true;
                leafGroup.add(blade);

                // Dedos laterales de la hoja compuesta
                if (branchLen > 0.07) {
                    [-0.32, 0.32].forEach(offAngle => {
                        const sideBlade = new THREE.Mesh(bladeGeo, this.leafMat);
                        sideBlade.scale.set(0.75, 0.75, 0.75);
                        sideBlade.rotation.z = (Math.PI / 2) + offAngle;
                        sideBlade.castShadow = true;
                        leafGroup.add(sideBlade);
                    });
                }

                // Cogollo / Flor en axila del nudo (si está en flora)
                if (budsSize > 0.05) {
                    const budRadius = 0.015 + (budsSize * 0.038) * (frac * 1.2);
                    const budGeo = new THREE.DodecahedronGeometry(budRadius, 1);
                    const bud = new THREE.Mesh(budGeo, this.budMat);
                    bud.position.set(Math.cos(angle) * 0.012, nodeY, Math.sin(angle) * 0.012);
                    bud.castShadow = true;
                    this.plantGroup.add(bud);
                }

                this.plantGroup.add(leafGroup);
            }
        }

        // Cogollo apical superior (Main Cola)
        if (budsSize > 0.05) {
            const topBudRadius = 0.025 + (budsSize * 0.055);
            const topBudGeo = new THREE.ConeGeometry(topBudRadius, topBudRadius * 2.4, 8);
            const topBud = new THREE.Mesh(topBudGeo, this.budMat);
            topBud.position.set(0, scaleH + topBudRadius * 0.8, 0);
            topBud.castShadow = true;
            this.plantGroup.add(topBud);
        }
    }

    updateFromTelemetry(state) {
        if (!state) return;
        this.state = state;

        const env = state.environment;
        const clock = state.clock;
        const plant = state.plant_focus;
        const acts = state.actuators;
        const visuals = plant.visuals;

        // 1. Iluminación y ciclo día/noche
        if (clock.is_day) {
            const dimmer = env.light_dimmer / 100.0;
            this.growLight.intensity = 2.8 * dimmer;
            this.ambientLight.intensity = 0.25 * dimmer + 0.08;
            this.ledsMat.color.setHex(0xfffaed);
            this.scene.background.setHex(0x0e1318);
        } else {
            // Noche: luz tenue azulada ambiental
            this.growLight.intensity = 0.0;
            this.ambientLight.intensity = 0.06;
            this.ledsMat.color.setHex(0x1a2128);
            this.scene.background.setHex(0x05070a);
        }

        // 2. Color del sustrato según VWC (%)
        // VWC bajo (<25%): café claro reseco #795548
        // VWC óptimo (>40%): café oscuro húmedo #2d1810
        const vwc = plant.vwc;
        const dryColor = new THREE.Color(0x8d6e63);
        const wetColor = new THREE.Color(0x27160c);
        const t = Math.max(0, Math.min(1, (vwc - 15.0) / 35.0));
        this.soilMat.color.lerpColors(dryColor, wetColor, t);

        // 3. Nivel de agua acumulada en la bandeja (Runoff)
        const trayMl = plant.tray_weight_g;
        const trayRatio = Math.min(1.0, trayMl / 1800.0);
        this.waterMat.opacity = trayRatio > 0.01 ? (0.35 + trayRatio * 0.5) : 0.0;
        this.waterMesh.scale.set(1.0, Math.max(0.1, trayRatio), 1.0);

        // 4. Color de hojas según clorosis y toxicidad
        const healthyLeaf = new THREE.Color(0x2e7d32);
        const yellowLeaf = new THREE.Color(0xcddc39);
        const darkClawLeaf = new THREE.Color(0x1b5e20);
        if (visuals.chlorosis > 0.1) {
            this.leafMat.color.lerpColors(healthyLeaf, yellowLeaf, visuals.chlorosis);
        } else if (visuals.leaf_claw > 0.2) {
            this.leafMat.color.lerpColors(healthyLeaf, darkClawLeaf, visuals.leaf_claw);
        } else {
            this.leafMat.color.copy(healthyLeaf);
        }

        // Tallo morado si hay frío
        if (visuals.stem_purple > 0.2) {
            this.stemMat.color.setHex(0x6a1b9a);
        } else {
            this.stemMat.color.setHex(0x43a047);
        }

        // 5. Reconstruir planta si cambió la planta seleccionada, su altura o síntomas
        if (!this.lastRebuild || 
            this.lastRebuild.plantId !== state.selected_plant_id ||
            Math.abs(plant.height_cm - this.lastRebuild.height) > 1.2 ||
            Math.abs(visuals.leaf_droop - this.lastRebuild.droop) > 0.08 ||
            Math.abs(visuals.buds_size - this.lastRebuild.buds) > 0.05) {

            this.rebuildPlantStructure(
                plant.height_cm,
                visuals.leaf_droop,
                visuals.tip_burn,
                visuals.leaf_claw,
                visuals.buds_size
            );
            this.lastRebuild = {
                plantId: state.selected_plant_id,
                height: plant.height_cm,
                droop: visuals.leaf_droop,
                buds: visuals.buds_size
            };
        }
    }

    animate() {
        requestAnimationFrame(this.animate);

        // 1. Controles de órbita
        this.controls.update();

        // 2. Ventilador oscilante girando
        if (this.fanBlades) {
            this.fanBlades.rotation.z += 0.35; // Aspas a alta velocidad
            this.fanOscillation += 0.015;
            this.fanHead.rotation.y = Math.sin(this.fanOscillation) * (Math.PI / 4) - 0.2;
        }

        // 3. Goteros activos desprendiendo agua animada
        const isWatering = this.state && this.state.actuators.valve_0 && (this.state.actuators.pump_p1 || this.state.actuators.pump_p2);
        if (isWatering && this.dripperPositions.length === 3) {
            // Generar nuevas gotas
            const p = this.particlePool.find(drop => !drop.active);
            if (p) {
                p.active = true;
                p.mesh.visible = true;
                p.dripperIdx = Math.floor(Math.random() * 3);
                const pos = this.dripperPositions[p.dripperIdx];
                p.pos.set(pos.x + (Math.random() - 0.5) * 0.006, pos.y, pos.z + (Math.random() - 0.5) * 0.006);
                p.mesh.position.copy(p.pos);
                p.vy = -0.004;
            }
        }

        // Actualizar física de gotas que caen
        this.particlePool.forEach(p => {
            if (p.active) {
                p.vy -= 0.0008; // Gravedad
                p.pos.y += p.vy;
                p.mesh.position.copy(p.pos);
                if (p.pos.y <= 0.28) { // Choca con el sustrato
                    p.active = false;
                    p.mesh.visible = false;
                }
            }
        });

        // 4. Render
        this.renderer.render(this.scene, this.camera);
    }

    setupEventListeners() {
        window.addEventListener('resize', () => {
            this.width = this.container.clientWidth;
            this.height = this.container.clientHeight;
            this.camera.aspect = this.width / this.height;
            this.camera.updateProjectionMatrix();
            this.renderer.setSize(this.width, this.height);
        });
    }
}
