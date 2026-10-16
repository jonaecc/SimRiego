/**
 * app.js
 * Controlador principal de la UI del Simulador de Invernadero.
 * Maneja WebSocket, selección de las 12 plantas, modo automático (Fases P1/P2/P3),
 * modal de configuración de riego (idéntico a la UI oficial) y el Asesor Dr. Cana.
 */

document.addEventListener('DOMContentLoaded', () => {
    // 1. Instanciar motores
    const greenhouse3D = new Greenhouse3D('canvas3d');
    const charts = new TelemetryCharts();

    // 2. Estado de conexión WebSocket
    let ws = null;
    let latestState = null;
    const wsStatusEl = document.getElementById('wsStatus');

    function connectWS() {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/ws`;
        ws = new WebSocket(wsUrl);

        ws.onopen = () => {
            wsStatusEl.textContent = 'CONECTADO (ONLINE)';
            wsStatusEl.className = 'status-badge online';
        };

        ws.onmessage = (event) => {
            try {
                const state = jsonParseSafe(event.data);
                if (state) {
                    latestState = state;
                    updateUI(state);
                    greenhouse3D.updateFromTelemetry(state);
                    charts.updateHistory(
                        state.history,
                        state.environment,
                        state.plant_focus.vwc_cc
                    );
                }
            } catch (err) {
                console.error("Error procesando telemetría WS:", err);
            }
        };

        ws.onclose = () => {
            wsStatusEl.textContent = 'DESCONECTADO (REINTENTANDO...)';
            wsStatusEl.className = 'status-badge offline';
            setTimeout(connectWS, 2000);
        };

        ws.onerror = (err) => {
            console.error("WS Error:", err);
            ws.close();
        };
    }

    function sendCmd(cmdObj) {
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify(cmdObj));
        }
    }

    function jsonParseSafe(text) {
        try { return JSON.parse(text); } catch { return null; }
    }

    function renderCcStatus(p, cfgOverride) {
        const ccStatus = document.getElementById('ccTestStatus');
        if (!ccStatus) return;
        const cfg = cfgOverride || (p && p.config);
        if (!cfg) return;
        const volCC = cfg.p1_vol_cc || ((cfg.pot_volume_ml || 5000) * 0.6);
        const trayRec = cfg.tray_capacity_recommended_ml || (volCC * 1.25);
        const potVol = cfg.pot_volume_ml || (p && p.pot_volume_ml) || 5000;
        const calculated = `🪴 Maceta: ${potVol.toFixed(0)} ml | 💧 Retención en CC (S/Y): ${volCC.toFixed(0)} ml agua | 🛡️ CB rec: ${trayRec.toFixed(0)} ml.`;
        if (p && p.cc_test) {
            ccStatus.textContent = `Ensayo activo: ${p.cc_test.state}; aplicados ${p.cc_test.total_ml.toFixed(0)} ml. Espera el primer drenaje en bandeja.`;
        } else if (p && p.cc_result) {
            ccStatus.textContent = p.cc_result.completed
                ? `CC detectada: ${p.cc_result.vwc_cc.toFixed(1)}% VWC; ${calculated}`
                : `Ensayo detenido: ${p.cc_result.reason}. Revisa caudal, volumen de maceta y drenaje.`;
        } else {
            ccStatus.textContent = calculated;
        }
    }

    // Tarjeta "Plan de Cultivo": etapa, objetivos y registro de transiciones
    const STAGE_NAMES = { PLANTULA: 'Plántula', CRECIMIENTO: 'Crecimiento', FLORACION: 'Floración' };
    function renderCropPlan(s) {
        const c = s.plant_focus.crop;
        if (!c) return;
        const setTxt = (id, txt) => { const el = document.getElementById(id); if (el) el.textContent = txt; };
        setTxt('planRoomStage', `SALA: ${STAGE_NAMES[c.room_stage] || c.room_stage}`.toUpperCase());
        setTxt('planDay', `${Math.floor(c.day)}`);
        setTxt('planStageDays', `${STAGE_NAMES[c.stage]} · ${c.stage_days.toFixed(1)} d`);
        const bar = document.getElementById('planBioBar');
        if (!c.next_stage) {
            setTxt('planNext', '— (etapa final)');
            setTxt('planBioTxt', `${c.biomass_g} g`);
            setTxt('planDaysTxt', '—');
            setTxt('planStatus', 'Etapa final');
            if (bar) bar.style.width = '100%';
            setTxt('planPot', `${c.stage === 'FLORACION' ? '' : ''}${(s.plant_focus.pot_volume_ml / 1000).toFixed(1)} L`);
        } else {
            const pct = c.biomass_target_g > 0 ? Math.min(100, 100 * c.biomass_g / c.biomass_target_g) : 100;
            if (bar) { bar.style.width = `${pct}%`; bar.style.background = c.biomass_ok ? 'var(--accent-green)' : 'var(--accent-orange)'; }
            setTxt('planNext', STAGE_NAMES[c.next_stage]);
            setTxt('planBioTxt', `${c.biomass_g} / ${c.biomass_target_g} g`);
            setTxt('planDaysTxt', `${c.stage_days.toFixed(1)} d  (mín ${c.min_days} – máx ${c.max_days})`);
            let st;
            if (c.forced) st = '⚠️ forzado por días máximos';
            else if (c.waiting_for_room) st = '✅ lista · esperando a la sala';
            else if (c.ready) st = '✅ lista para pasar';
            else st = `⏳ creciendo (${c.days_ok ? '' : 'faltan días'}${!c.days_ok && !c.biomass_ok ? ' y ' : ''}${c.biomass_ok ? '' : 'falta biomasa'})`;
            setTxt('planStatus', st);
            setTxt('planPot', c.will_transplant
                ? `${(s.plant_focus.pot_volume_ml / 1000).toFixed(1)} L → ${(c.pot_next_ml / 1000).toFixed(1)} L (trasplante)`
                : `${(s.plant_focus.pot_volume_ml / 1000).toFixed(1)} L (se mantiene)`);
        }
        const ev = document.getElementById('planEvents');
        if (ev) {
            const evs = (s.crop_events || []).slice().reverse();
            ev.innerHTML = evs.length ? evs.map(e =>
                `Día ${e.day} · P${e.plant + 1}: ${STAGE_NAMES[e.from]} → ${STAGE_NAMES[e.to]} (${e.biomass_g} g)` +
                `${e.transplant ? ` · trasplante ${e.pot_from_ml / 1000}→${e.pot_to_ml / 1000} L` : ' · misma maceta'}` +
                `${e.forced ? ' · <b style="color:var(--accent-orange)">forzado</b>' : ''}${e.note ? ` · ${e.note}` : ''}`
            ).join('<br>') : 'Sin transiciones todavía.';
        }
    }

    // 3. Actualización de datos numéricos y paneles en pantalla
    function updateUI(s) {
        const c = s.clock;
        const e = s.environment;
        const p = s.plant_focus;
        const a = s.actuators;
        renderCcStatus(p);

        // Header / Reloj
        document.getElementById('simClock').textContent = c.time_str;
        document.getElementById('dayBadge').textContent = c.is_day ? '☀️ DÍA' : '🌙 NOCHE';
        document.getElementById('dayBadge').className = c.is_day ? 'badge-day' : 'badge-night';

        // Botón Pausa
        const btnPause = document.getElementById('btnPause');
        btnPause.textContent = c.is_paused ? '▶ Reanudar' : '⏸ Pausa';
        btnPause.className = c.is_paused ? 'btn-glow' : 'btn-secondary';

        // Botón Modo Automático
        const btnAuto = document.getElementById('btnToggleAuto');
        if (btnAuto) {
            btnAuto.textContent = c.auto_mode ? '🤖 AUTO: ON' : '🖐️ AUTO: OFF';
            btnAuto.className = c.auto_mode ? 'btn-glow' : 'btn-secondary';
        }

        // Selector de planta
        const selPlant = document.getElementById('selectPlant');
        if (selPlant && document.activeElement !== selPlant) {
            selPlant.value = s.selected_plant_id;
        }
        const managementStage = document.getElementById('managementStage');
        if (managementStage && document.activeElement !== managementStage) {
            managementStage.value = p.management_stage;
        }

        renderCropPlan(s);

        // Actuadores
        const pumpP1El = document.getElementById('actPumpP1');
        pumpP1El.textContent = a.pump_p1 ? 'ON (BOMBEANDO P1)' : (a.pump_p2 ? 'ON (BOMBEANDO P2)' : 'OFF');
        pumpP1El.className = (a.pump_p1 || a.pump_p2) ? 'act-on' : 'act-off';

        const valve0El = document.getElementById('actValve0');
        valve0El.textContent = a.valve_0 ? 'ABIERTA' : 'CERRADA';
        valve0El.className = a.valve_0 ? 'act-on' : 'act-off';

        const drippersEl = document.getElementById('kpiDrippers');
        if (drippersEl && p.config) {
            drippersEl.textContent = `${p.config.goteros_planta}x @ ${p.config.caudal_gotero_lh} L/H`;
        }

        // Sensores principales (Top KPIs)
        document.getElementById('kpiVWC').textContent = `${p.vwc}%`;
        document.getElementById('kpiEC').textContent = `${p.ec} mS/cm`;
        document.getElementById('kpiVPD').textContent = `${e.vpd} kPa`;
        document.getElementById('kpiTray').textContent = `${p.tray_weight_g} g`;
        document.getElementById('kpiTemp').textContent = `${e.air_temp} °C`;
        document.getElementById('kpiRH').textContent = `${e.air_rh}%`;
        document.getElementById('kpiPPFD').textContent = `${e.ppfd} µmol`;
        const autoTrayService = document.getElementById('autoTrayService');
        if (autoTrayService && document.activeElement !== autoTrayService) {
            autoTrayService.checked = e.auto_empty_and_tare_end_p2;
        }

        // Datos biológicos del cultivo
        document.getElementById('cropStage').textContent = `${p.management_stage_label} / ${p.stage}`;
        document.getElementById('cropStrain').textContent = p.strain;
        document.getElementById('cropHeight').textContent = `${p.height_cm} cm`;
        document.getElementById('cropBiomass').textContent = `${p.biomass_g} g`;
        const leafEl = document.getElementById('cropLeafArea');
        if (leafEl) leafEl.textContent = `${p.leaf_area_m2} m²`;
        document.getElementById('cropRootHealth').textContent = `${p.root_health_pct}%`;
        document.getElementById('cropPH').textContent = p.ph.toFixed(2);
        document.getElementById('cropNPK').textContent = `${p.npk_ppm.n} / ${p.npk_ppm.p} / ${p.npk_ppm.k} ppm`;
        document.getElementById('cropBuds').textContent = `${p.buds_size_pct}%`;

        // Crop Steering badge
        const steeringEl = document.getElementById('cropSteering');
        if (steeringEl) {
            const isGen = p.steering_type === 'GENERATIVO';
            steeringEl.textContent = isGen ? '⚡ GENERATIVO' : '🌿 VEGETATIVO';
            steeringEl.style.color = isGen ? 'var(--accent-orange)' : 'var(--accent-green)';
        }
        const flowerEl = document.getElementById('cropFlowerInduced');
        if (flowerEl) {
            if (p.flower_induced) {
                flowerEl.textContent = `✅ Día ${p.flower_induction_day.toFixed(0)}`;
                flowerEl.style.color = 'var(--accent-orange)';
            } else {
                flowerEl.textContent = '— Vegetativo';
                flowerEl.style.color = '#888';
            }
        }
        // Maceta actual
        const potEl = document.getElementById('kpiCurrentPot');
        if (potEl) {
            const potL = (p.pot_volume_ml / 1000.0).toFixed(1);
            potEl.textContent = `${p.pot_volume_ml.toFixed(0)} ml (${potL} L)`;
        }

        for (const pump of ['p1', 'p2']) {
            const solution = e[`solution_${pump}`];
            for (const field of ['ec', 'ph', 'n', 'p', 'k']) {
                const input = document.getElementById(`sol_${pump}_${field}`);
                if (input && document.activeElement !== input) input.value = solution[field];
            }
        }

        // Diagnóstico y asesoría agronómica Dr. Cana
        updateAgronomicAdvisor(s);
    }

    function updateAgronomicAdvisor(s) {
        const p = s.plant_focus;
        const e = s.environment;
        const c = s.clock;
        const advEl = document.getElementById('advisorMsg');
        const advBox = document.getElementById('advisorBox');

        let msg = "";
        let alertType = "normal";

        if (p.inundacion_critica) {
            msg = `🚨 <b>RIEGO BLOQUEADO POR INUNDACIÓN (Planta ${p.id + 1}):</b> La celda lee ${p.tray_weight_g} g (CB: ${p.config.capacidad_bandeja_ml} ml). Vacía la bandeja (botón "Vaciar bandeja") y luego ejecuta TA con la bandeja vacía.`;
            alertType = "danger";
        } else if (p.tare_with_water) {
            msg = `⚠️ <b>TARA CON AGUA (Planta ${p.id + 1}):</b> TA dejó la lectura de la celda en cero con ${p.tray_actual_ml} ml físicos. Esto reproduce el firmware actual, pero puede ocultar una inundación: vacía físicamente la bandeja y vuelve a tarar.`;
            alertType = "warning";
        } else if (p.vwc < 20.0) {
            msg = `⚠️ <b>ALERTA DE SEQUÍA (Planta ${p.id + 1}):</b> El sustrato cayó a ${p.vwc}% VWC. Las hojas pierden turgencia. En modo AUTO, el controlador iniciará disparo P1/P2 al cumplirse el reposo.`;
            alertType = "danger";
        } else if (p.vwc > 55.0 && p.tray_weight_g > 300) {
            msg = `💧 <b>SATURACIÓN Y DRENAJE (Planta ${p.id + 1}):</b> Sustrato en Capacidad de Campo. Escorrentía acumulada: ${p.tray_weight_g} ml. El ciclo P1 cortó exitosamente al alcanzar el límite de runoff.`;
            alertType = "warning";
        } else if (p.ec > 4.2) {
            msg = `⚡ <b>TOXICIDAD DE SALES (EC ${p.ec} mS/cm):</b> Acumulación crítica. Posible quemadura de puntas y hojas en garra. Ajusta la EC del tanque a valores menores para lavar sales.`;
            alertType = "danger";
        } else if (e.vpd < 0.6) {
            msg = "🌫️ <b>VPD MUY BAJO (<0.6 kPa):</b> Exceso de humedad ambiental. La transpiración se detiene. Aumenta la ventilación.";
            alertType = "warning";
        } else if (e.vpd > 1.6) {
            msg = "🔥 <b>VPD MUY ALTO (>1.6 kPa):</b> Aire excesivamente seco o cálido. Riesgo de estrés hídrico foliar rápido.";
            alertType = "warning";
        } else if (p.biomass_g > (p.pot_volume_ml / 500.0) * 12.0) {
            msg = `🪴 <b>RESTRICCIÓN RADICULAR (ROOTBOUND en Planta ${p.id + 1}):</b> La biomasa (${p.biomass_g} g) saturó la maceta de ${(p.pot_volume_ml/1000).toFixed(1)} L. Salud de raíz en ${p.root_health_pct}%. Usa el panel "🪴 Trasplante" para pasar a una maceta mayor y permitir que la planta continúe creciendo orgánicamente.`;
            alertType = "warning";
        } else {
            const isGen = p.steering_type === "GENERATIVO";
            if (isGen) {
                msg = `⚡ <b>STEERING GENERATIVO ACTIVO (Planta ${p.id + 1}):</b> Dryback más amplio y EC adecuada. Guiando la planta hacia entrenudos compactos, cálices densos y mayor producción de resina/tricomas.`;
            } else {
                msg = `🌿 <b>STEERING VEGETATIVO ACTIVO (Planta ${p.id + 1}):</b> Humedad radicular estable y baja salinidad. Estimulando rápida expansión foliar, desarrollo de ramas y biomasa verde vigorosa.`;
            }
            alertType = "success";
        }

        advEl.innerHTML = msg;
        advBox.className = `advisor-box ${alertType}`;
    }

    // 4. Configuración de controles y eventos
    // Selector de planta
    document.getElementById('selectPlant').addEventListener('change', (e) => {
        const plantId = parseInt(e.target.value);
        sendCmd({ cmd: 'select_plant', plant_id: plantId });
    });

    document.getElementById('managementStage').addEventListener('change', (e) => {
        const plantId = latestState ? latestState.selected_plant_id : 0;
        const current = latestState ? latestState.plant_focus.management_stage : e.target.value;
        if (!confirm('Salto manual: la planta se reinicia biológicamente a esa etapa (edad, biomasa, altura). ' +
                     'El avance normal es automático según el Plan de Cultivo. ¿Continuar?')) {
            e.target.value = current;
            e.target.blur();
            return;
        }
        sendCmd({ cmd: 'set_management_stage', plant_id: plantId, stage: e.target.value });
        e.target.blur();
    });

    // Toggle modo automático
    document.getElementById('btnToggleAuto').addEventListener('click', () => {
        sendCmd({ cmd: 'toggle_auto_mode' });
    });

    // Velocidades de simulación
    document.querySelectorAll('.btn-speed').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.btn-speed').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            const speed = parseFloat(btn.getAttribute('data-speed'));
            sendCmd({ cmd: 'set_speed', value: speed });
        });
    });

    // Pausa / Reanudar
    document.getElementById('btnPause').addEventListener('click', () => {
        sendCmd({ cmd: 'toggle_pause' });
    });

    // Disparo manual forzado
    document.getElementById('btnShot').addEventListener('click', () => {
        const plantId = latestState ? latestState.selected_plant_id : 0;
        sendCmd({ cmd: 'trigger_shot', plant_id: plantId, duration: 15 });
    });

    // Acción física manual: el firmware no tiene actuador de vaciado.
    document.getElementById('btnEmptyTray').addEventListener('click', () => {
        const plantId = latestState ? latestState.selected_plant_id : 0;
        sendCmd({ cmd: 'empty_tray_manually', plant_id: plantId });
    });

    // TA envía el mismo tipo de orden que el firmware: no vacía la bandeja.
    document.getElementById('btnTareTray').addEventListener('click', () => {
        const plantId = latestState ? latestState.selected_plant_id : 0;
        sendCmd({ cmd: 'tare_tray', plant_id: plantId });
    });

    document.getElementById('autoTrayService').addEventListener('change', (event) => {
        sendCmd({ cmd: 'set_auto_tray_service', enabled: event.target.checked });
    });
    // Avanzar 1 Día
    document.getElementById('btnSkipDay').addEventListener('click', () => {
        sendCmd({ cmd: 'skip_days', days: 1 });
    });

    // Reiniciar cultivo
    document.getElementById('btnResetCrop').addEventListener('click', () => {
        if (confirm("¿Deseas reiniciar la simulación completa a Día 1 desde semilla?")) {
            sendCmd({ cmd: 'reset_crop' });
        }
    });

    // Slider de maceta para trasplante
    const sliderTransplantPot = document.getElementById('sliderTransplantPot');
    const valTransplantPot = document.getElementById('valTransplantPot');
    if (sliderTransplantPot && valTransplantPot) {
        sliderTransplantPot.addEventListener('input', () => {
            const ml = parseFloat(sliderTransplantPot.value);
            valTransplantPot.textContent = `${ml.toFixed(0)} ml (${(ml/1000).toFixed(1)} L)`;
        });
    }

    // Botón de trasplante
    const btnTransplant = document.getElementById('btnTransplant');
    if (btnTransplant) {
        btnTransplant.addEventListener('click', () => {
            const plantId = latestState ? latestState.selected_plant_id : 0;
            const newPotMl = parseFloat(document.getElementById('sliderTransplantPot').value);
            const currentPot = latestState && latestState.plant_focus ? latestState.plant_focus.pot_volume_ml : 0;
            if (newPotMl <= currentPot) {
                alert(`La maceta nueva (${newPotMl} ml) debe ser MAYOR que la actual (${currentPot} ml). El trasplante solo va a maceta más grande.`);
                return;
            }
            if (confirm(`¿Trasplantar Planta ${plantId + 1} de ${currentPot} ml → ${newPotMl} ml? La biología (edad, altura, biomasa) se preserva.`)) {
                sendCmd({ cmd: 'transplant', plant_id: plantId, new_pot_ml: newPotMl });
            }
        });
    }

    // Sliders de entorno
    const sliderTemp = document.getElementById('sliderTemp');
    const valTemp = document.getElementById('valTemp');
    sliderTemp.addEventListener('input', (e) => {
        valTemp.textContent = `${e.target.value} °C`;
        sendCmd({ cmd: 'set_climate', temp: e.target.value });
    });

    const sliderRH = document.getElementById('sliderRH');
    const valRH = document.getElementById('valRH');
    sliderRH.addEventListener('input', (e) => {
        valRH.textContent = `${e.target.value} %`;
        sendCmd({ cmd: 'set_climate', rh: e.target.value });
    });

    for (const pump of ['p1', 'p2']) {
        for (const field of ['ec', 'ph', 'n', 'p', 'k']) {
            document.getElementById(`sol_${pump}_${field}`).addEventListener('change', () => {
                const solution = {};
                for (const key of ['ec', 'ph', 'n', 'p', 'k']) solution[key] = parseFloat(document.getElementById(`sol_${pump}_${key}`).value);
                sendCmd({ cmd: 'update_solution', pump: pump.toUpperCase(), solution });
            });
        }
    }

    const sliderLight = document.getElementById('sliderLight');
    const valLight = document.getElementById('valLight');
    sliderLight.addEventListener('input', (e) => {
        valLight.textContent = `${e.target.value} %`;
        sendCmd({ cmd: 'set_climate', light_dimmer: e.target.value });
    });

    // 5. MODAL DE CONFIGURACIÓN DE RIEGO (P1 / P2 / P3)
    const modalConfig = document.getElementById('modalConfig');
    const btnOpenConfig = document.getElementById('btnOpenConfig');
    const btnCloseModal = document.getElementById('btnCloseModal');
    const btnSaveConfig = document.getElementById('btnSaveConfig');
    const btnApplyAllConfig = document.getElementById('btnApplyAllConfig');

    let activeStageTab = null; // Will track which stage tab is currently displayed

    function populateScheduleFromState() {
        // Horario guardado para la etapa seleccionada en la pestaña (no el activo de la sala).
        const sch = (latestState.stage_schedules && latestState.stage_schedules[activeStageTab]) || {};
        const env = {
            light_on_hour: sch.light_on_hour, light_off_hour: sch.light_off_hour,
            p2_inicio: sch.p2_inicio_hour, p2_fin: sch.p2_fin_hour, p0_minutes: sch.p1_offset_min,
        };
        const lonH = Math.floor(env.light_on_hour ?? 6);
        const lonM = Math.round(((env.light_on_hour ?? 6) % 1) * 60);
        const loffH = Math.floor(env.light_off_hour ?? 24);
        const loffM = Math.round(((env.light_off_hour ?? 24) % 1) * 60);
        const p2iH = Math.floor(env.p2_inicio ?? 11);
        const p2iM = Math.round(((env.p2_inicio ?? 11) % 1) * 60);
        const p2fH = Math.floor(env.p2_fin ?? 20);
        const p2fM = Math.round(((env.p2_fin ?? 20) % 1) * 60);
        document.getElementById('cfg_luces_on_hh').value = lonH;
        document.getElementById('cfg_luces_on_mm').value = lonM;
        document.getElementById('cfg_luces_off_hh').value = loffH;
        document.getElementById('cfg_luces_off_mm').value = loffM;
        document.getElementById('cfg_p0_minutes').value = env.p0_minutes ?? 60;
        document.getElementById('cfg_p2_inicio_hh').value = p2iH;
        document.getElementById('cfg_p2_inicio_mm').value = p2iM;
        document.getElementById('cfg_p2_fin_hh').value = p2fH;
        document.getElementById('cfg_p2_fin_mm').value = p2fM;
    }

    function populateModalWithPlantConfig(cfg, plantId) {
        document.getElementById('modalTitle').textContent = `⚙️ CONFIGURACIÓN DE RIEGO — PLANTA ${plantId + 1}`;
        // Stage tab active sync
        if (!activeStageTab) activeStageTab = latestState.plant_focus.management_stage;
        populateScheduleFromState();
        document.querySelectorAll('.stage-tab').forEach(t => {
            t.classList.toggle('active', t.dataset.stage === activeStageTab);
        });
        // Plan de etapa (la etapa final no tiene salida)
        const plan = (latestState.crop_plan || {})[activeStageTab];
        const isFinal = activeStageTab === 'FLORACION';
        document.getElementById('planFields').style.display = isFinal ? 'none' : '';
        document.getElementById('planFinalNote').style.display = isFinal ? '' : 'none';
        if (plan) {
            document.getElementById('cfg_plan_min_days').value = plan.min_days;
            document.getElementById('cfg_plan_max_days').value = plan.max_days;
            document.getElementById('cfg_plan_biomass').value = plan.biomass_target_g;
        }
        if (cfg.substrate_ph === undefined) cfg = { ...cfg, substrate_ph: latestState.plant_focus.config.substrate_ph };
        // P1
        document.getElementById('cfg_pot_volume_ml').value = cfg.pot_volume_ml;
        document.getElementById('cfg_substrate_type').value = latestState.plant_focus.substrate_type;
        if (latestState) renderCcStatus(latestState.plant_focus, cfg);
        document.getElementById('cfg_p1_vwc_min').value = cfg.p1_vwc_min;
        document.getElementById('cfg_p1_ec_target').value = cfg.p1_ec_target;
        document.getElementById('cfg_p1_shot_pc').value = cfg.p1_shot_pc;
        document.getElementById('cfg_p1_tiempo_entre').value = cfg.p1_tiempo_entre_min;
        document.getElementById('cfg_p1_runoff_max').value = cfg.p1_runoff_max;
        document.getElementById('cfg_caudal_gotero').value = cfg.caudal_gotero_lh;
        document.getElementById('cfg_goteros_planta').value = cfg.goteros_planta;
        document.getElementById('cfg_cap_bandeja').value = cfg.capacidad_bandeja_ml;
        // P2
        document.getElementById('cfg_p2_vwc_max').value = cfg.p2_vwc_max;
        document.getElementById('cfg_p2_ec_target').value = cfg.p2_ec_target;
        document.getElementById('cfg_p2_vwc_min').value = cfg.p2_vwc_min;
        document.getElementById('cfg_p2_shot_pc').value = cfg.p2_shot_pc;
        document.getElementById('cfg_p2_tiempo_entre').value = cfg.p2_tiempo_entre_min;
        document.getElementById('cfg_p2_runoff_max').value = cfg.p2_runoff_max;
        // P3
        document.getElementById('cfg_p3_vwc_min').value = cfg.p3_vwc_min;
        document.getElementById('cfg_substrate_ph').value = cfg.substrate_ph;
    }

    function readModalConfig() {
        return {
            p1_vwc_min: parseFloat(document.getElementById('cfg_p1_vwc_min').value),
            p1_ec_target: parseFloat(document.getElementById('cfg_p1_ec_target').value),
            pot_volume_ml: parseFloat(document.getElementById('cfg_pot_volume_ml').value),
            p1_shot_pc: parseFloat(document.getElementById('cfg_p1_shot_pc').value),
            p1_tiempo_entre_min: parseInt(document.getElementById('cfg_p1_tiempo_entre').value),
            p1_runoff_max: parseFloat(document.getElementById('cfg_p1_runoff_max').value),
            caudal_gotero_lh: parseFloat(document.getElementById('cfg_caudal_gotero').value),
            goteros_planta: parseInt(document.getElementById('cfg_goteros_planta').value),
            capacidad_bandeja_ml: parseFloat(document.getElementById('cfg_cap_bandeja').value),
            p2_vwc_max: parseFloat(document.getElementById('cfg_p2_vwc_max').value),
            p2_ec_target: parseFloat(document.getElementById('cfg_p2_ec_target').value),
            p2_vwc_min: parseFloat(document.getElementById('cfg_p2_vwc_min').value),
            p2_shot_pc: parseFloat(document.getElementById('cfg_p2_shot_pc').value),
            p2_tiempo_entre_min: parseInt(document.getElementById('cfg_p2_tiempo_entre').value),
            p2_runoff_max: parseFloat(document.getElementById('cfg_p2_runoff_max').value),
            p3_vwc_min: parseFloat(document.getElementById('cfg_p3_vwc_min').value),
            substrate_ph: parseFloat(document.getElementById('cfg_substrate_ph').value)
        };
    }

    function readScheduleFromModal() {
        return {
            luces_on: parseInt(document.getElementById('cfg_luces_on_hh').value) + parseInt(document.getElementById('cfg_luces_on_mm').value) / 60.0,
            luces_off: parseInt(document.getElementById('cfg_luces_off_hh').value) + parseInt(document.getElementById('cfg_luces_off_mm').value) / 60.0,
            p0_minutes: parseFloat(document.getElementById('cfg_p0_minutes').value),
            p2_inicio: parseInt(document.getElementById('cfg_p2_inicio_hh').value) + parseInt(document.getElementById('cfg_p2_inicio_mm').value) / 60.0,
            p2_fin: parseInt(document.getElementById('cfg_p2_fin_hh').value) + parseInt(document.getElementById('cfg_p2_fin_mm').value) / 60.0,
        };
    }

    btnOpenConfig.addEventListener('click', () => {
        if (latestState && latestState.plant_focus) {
            const p = latestState.plant_focus;
            activeStageTab = p.management_stage;
            const cfg = (p.stage_profiles && p.stage_profiles[activeStageTab]) ? p.stage_profiles[activeStageTab] : p.config;
            populateModalWithPlantConfig(cfg, latestState.selected_plant_id);
            modalConfig.style.display = 'flex';
        }
    });

    btnCloseModal.addEventListener('click', () => {
        modalConfig.style.display = 'none';
    });

    function readPlanFromModal() {
        return {
            min_days: parseFloat(document.getElementById('cfg_plan_min_days').value),
            max_days: parseFloat(document.getElementById('cfg_plan_max_days').value),
            biomass_target_g: parseFloat(document.getElementById('cfg_plan_biomass').value),
        };
    }

    // Envía plan de la etapa + horario de la etapa (la sala lo aplica al entrar en ella).
    function sendStagePlanAndSchedule() {
        const stage = activeStageTab;
        if (stage !== 'FLORACION') {
            const plan = readPlanFromModal();
            if (plan.max_days < plan.min_days) {
                alert('Los días máximos deben ser ≥ a los días mínimos.');
                return false;
            }
            sendCmd({ cmd: 'update_crop_plan', stage, ...plan });
        }
        sendCmd({ cmd: 'update_stage_schedule', stage, ...readScheduleFromModal() });
        return true;
    }

    btnSaveConfig.addEventListener('click', () => {
        const plantId = latestState ? latestState.selected_plant_id : 0;
        if (!sendStagePlanAndSchedule()) return;
        // Edita el perfil de ESA etapa sin activarla ni tocar la biología.
        sendCmd({ cmd: 'update_stage_profile', plant_id: plantId, stage: activeStageTab, config: readModalConfig() });
        modalConfig.style.display = 'none';
    });

    document.getElementById('btnStartCcTest').addEventListener('click', () => {
        const plantId = latestState ? latestState.selected_plant_id : 0;
        document.getElementById('ccTestStatus').textContent = 'Solicitud enviada: el ensayo reemplaza el disparo automático de esta planta y comienza con un pulso.';
        sendCmd({ cmd: 'start_cc_test', plant_id: plantId,
            pot_volume_ml: parseFloat(document.getElementById('cfg_pot_volume_ml').value),
            pulse_ml: parseFloat(document.getElementById('cc_pulse_ml').value),
            wait_minutes: parseFloat(document.getElementById('cc_wait_minutes').value) });
    });

    document.getElementById('cfg_substrate_type').addEventListener('change', (event) => {
        const plantId = latestState ? latestState.selected_plant_id : 0;
        sendCmd({ cmd: 'set_substrate', plant_id: plantId, substrate_type: event.target.value });
    });

    btnApplyAllConfig.addEventListener('click', () => {
        if (confirm("¿Aplicar esta configuración a las 12 plantas del sistema?")) {
            if (!sendStagePlanAndSchedule()) return;
            const cfg = readModalConfig();
            for (let i = 0; i < 12; i++) {
                sendCmd({ cmd: 'update_stage_profile', plant_id: i, stage: activeStageTab, config: cfg });
            }
            modalConfig.style.display = 'none';
        }
    });

    // Stage tab switching: changes the management stage for the selected plant
    // and refreshes the modal with the new stage's saved profile.
    document.querySelectorAll('.stage-tab').forEach(tab => {
        tab.addEventListener('click', () => {
            const stage = tab.dataset.stage;
            activeStageTab = stage;
            document.querySelectorAll('.stage-tab').forEach(t => t.classList.toggle('active', t.dataset.stage === stage));
            const plantId = latestState ? latestState.selected_plant_id : 0;
            // Solo cambia la pestaña mostrada: NO cambia la etapa real de la planta.
            if (latestState && latestState.plant_focus) {
                const p = latestState.plant_focus;
                const cfg = (p.stage_profiles && p.stage_profiles[stage]) ? p.stage_profiles[stage] : p.config;
                populateModalWithPlantConfig(cfg, plantId);
            }
        });
    });

    // Iniciar conexión WebSocket
    connectWS();
});
