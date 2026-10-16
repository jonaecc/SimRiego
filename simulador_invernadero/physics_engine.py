"""
Motor físico y biológico multivariante del invernadero (12 Plantas).
Integra:
  1. Microclima heterogéneo (variación de luz y viento según posición en el espacio 3D).
  2. Variabilidad biológica real entre plantas (fenotipos, vigor, absorción, goteros).
  3. Lógica de control de riego idéntica al firmware NodoControl (Fases P1, P2 y P3).
  4. Configuración completa por planta alineada con los parámetros de la UI oficial
     (config_p1.html, config_p2.html y ajustes.html).
"""

import copy
import math
import random


class NutrientSolution:
    """Solución de riego por bomba; concentraciones NPK expresadas en ppm."""
    def __init__(self, ec=2.0, ph=5.9, n=160.0, p=50.0, k=200.0):
        self.ec, self.ph = ec, ph
        self.n, self.p, self.k = n, p, k

    def as_dict(self):
        return {"ec": self.ec, "ph": self.ph, "n": self.n, "p": self.p, "k": self.k}

class CropStage:
    GERMINATION = "Plántula"
    VEGETATIVE = "Vegetativo"
    STRETCH = "Estiramiento (Preflora)"
    FLOWERING = "Floración Masiva"
    RIPENING = "Maduración / Flush"


MANAGEMENT_STAGES = ("PLANTULA", "CRECIMIENTO", "FLORACION")
STAGE_LABELS = {"PLANTULA": "Plántula", "CRECIMIENTO": "Crecimiento", "FLORACION": "Floración"}

# Supuestos explícitos del gemelo físico. CC/PMP son propiedades del medio
# (no parámetros de riego ni entradas que el usuario deba "medir" dentro de
# una simulación). Se pueden ampliar cuando exista una curva de retención real.
SUBSTRATE_PROFILES = {
    "COCO": {"label": "Coco coir", "vwc_cc": 60.0, "vwc_pmp": 15.0},
    "ROCKWOOL": {"label": "Rockwool", "vwc_cc": 75.0, "vwc_pmp": 10.0},
    "TURBA": {"label": "Turba", "vwc_cc": 55.0, "vwc_pmp": 18.0},
    "SUELO": {"label": "Suelo", "vwc_cc": 45.0, "vwc_pmp": 18.0},
}

class PlantConfig:
    """Configuración individual de riego alineada 100% con los tokens de la UI real."""
    def __init__(self, plant_id=0):
        self.id = plant_id
        # --- FASE P1 (tokens: BU, BV, CB, O, Q, R, S, T, CG) ---
        self.caudal_gotero_lh = 3.6        # BU: L/H por gotero (ej. 3.6 L/H = 60 ml/min)
        self.goteros_planta = 3            # BV: Goteros insertados
        self.capacidad_bandeja_ml = 2500.0 # CB: ml máximos antes de alarma de desborde
        self.p1_vwc_min = 32.0             # O: % VWC mínimo para iniciar P1
        self.p1_ec_target = 2.4            # Q: mS/cm objetivo
        self.p1_runoff_max = 250.0         # R: ml de drenaje que corta la subida P1
        self.p1_vol_cc = 1400.0            # S: Volumen de capacidad de campo calibrado (ml)
        self.p1_shot_pc = 4.0              # T: % de vol_cc por disparo (4% = 56 ml)
        self.p1_tiempo_entre_min = 15      # CG: minutos de reposo entre disparos

        # --- FASE P2 Y P3 (tokens: U, BX, V, W, X, Y, Z, CH, AA) ---
        self.p2_vwc_max = 48.0             # U: % VWC corte superior de mantenimiento
        self.p2_vwc_cc_ref = 46.0          # BX: % VWC de referencia en CC
        self.p2_vwc_min = 36.0             # V: % VWC mínimo para disparar en P2
        self.p2_ec_target = 2.6            # W: mS/cm
        self.p2_runoff_max = 350.0         # X: ml acumulados en el día
        self.p2_vol_cc = 1400.0            # Y: ml
        self.p2_shot_pc = 3.0              # Z: % de vol_cc por disparo (3% = 42 ml)
        self.p2_tiempo_entre_min = 25      # CH: minutos entre disparos P2
        self.p3_vwc_min = 22.0             # AA: % VWC mínimo de emergencia nocturna

    @property
    def flow_ml_min(self):
        """Caudal total entregado en ml/min."""
        return (self.caudal_gotero_lh * 1000.0 / 60.0) * self.goteros_planta

    def calc_shot_duration_seconds(self, shot_pc, vol_cc):
        """Calcula los segundos de apertura de válvula para entregar el shot deseado."""
        shot_ml = vol_cc * (shot_pc / 100.0)
        flow_rate = max(10.0, self.flow_ml_min)
        return (shot_ml / flow_rate) * 60.0

    @property
    def tray_capacity_recommended_ml(self):
        """CB conservadora para una bandeja sin contar con drenaje pasivo.

        Debe admitir el límite de runoff más un disparo que puede terminar de
        drenarse después del corte, con 25 % de margen de instalación.
        """
        p1_shot = self.p1_vol_cc * self.p1_shot_pc / 100.0
        p2_shot = self.p2_vol_cc * self.p2_shot_pc / 100.0
        return 1.25 * (max(self.p1_runoff_max, self.p2_runoff_max) + max(p1_shot, p2_shot))


IRRIGATION_FIELDS = (
    "caudal_gotero_lh", "goteros_planta", "capacidad_bandeja_ml",
    "p1_vwc_min", "p1_ec_target", "p1_runoff_max", "p1_vol_cc", "p1_shot_pc", "p1_tiempo_entre_min",
    "p2_vwc_max", "p2_vwc_cc_ref", "p2_vwc_min", "p2_ec_target", "p2_runoff_max", "p2_vol_cc", "p2_shot_pc", "p2_tiempo_entre_min", "p3_vwc_min",
)


def stage_profile(pot_ml, vwc_cc, vwc_pmp, **irrigation):
    """Perfil editable de una etapa de manejo, independiente de la edad."""
    cfg = PlantConfig()
    for key, value in irrigation.items():
        setattr(cfg, key, value)
    return {
        "pot_volume_ml": pot_ml, "vwc_cc": vwc_cc, "vwc_pmp": vwc_pmp,
        **{field: getattr(cfg, field) for field in IRRIGATION_FIELDS},
    }


DEFAULT_STAGE_PROFILES = {
    # Plántula: maceta de 0.5 L, goteros pequeños, dryback suave para no estresar semillas
    # p1: sube desde el dryback nocturno hasta CC cada mañana con shots pequeños
    # p2: mantiene VWC entre 40-50% (por encima del PMP=15%) con shots pequeños y frecuentes
    "PLANTULA": stage_profile(500.0, 60.0, 15.0, capacidad_bandeja_ml=300.0,
        caudal_gotero_lh=1.8, goteros_planta=1,
        p1_vwc_min=38.0, p1_ec_target=1.4, p1_runoff_max=40.0, p1_vol_cc=300.0, p1_shot_pc=4.0, p1_tiempo_entre_min=15,
        p2_vwc_min=40.0, p2_vwc_max=52.0, p2_vwc_cc_ref=60.0, p2_ec_target=9.0, p2_runoff_max=100.0, p2_vol_cc=300.0, p2_shot_pc=5.0, p2_tiempo_entre_min=20, p3_vwc_min=25.0),
    # Crecimiento: maceta de 5 L, goteros dobles, dryback diurno moderado (Vegetativo)
    "CRECIMIENTO": stage_profile(5000.0, 60.0, 15.0, capacidad_bandeja_ml=1200.0,
        caudal_gotero_lh=3.0, goteros_planta=2,
        p1_vwc_min=46.0, p1_ec_target=2.8, p1_runoff_max=300.0, p1_vol_cc=3000.0, p1_shot_pc=4.5, p1_tiempo_entre_min=15,
        p2_vwc_min=50.0, p2_vwc_max=57.0, p2_vwc_cc_ref=60.0, p2_ec_target=9.0, p2_runoff_max=450.0, p2_vol_cc=3000.0, p2_shot_pc=2.5, p2_tiempo_entre_min=25, p3_vwc_min=30.0),
    # Floración: maceta de 15 L, dryback más agresivo (generativo), fotoperiodo 12/12
    # Por defecto la maceta de floración es la misma que la de crecimiento (5 L):
    # muchos cultivadores no trasplantan al pasar a floración y solo cambian el
    # riego. Si se configura una maceta mayor, el paso incluye trasplante.
    "FLORACION": stage_profile(5000.0, 60.0, 15.0, capacidad_bandeja_ml=1200.0,
        caudal_gotero_lh=3.0, goteros_planta=2,
        p1_vwc_min=36.0, p1_ec_target=4.2, p1_runoff_max=300.0, p1_vol_cc=3000.0, p1_shot_pc=4.0, p1_tiempo_entre_min=15,
        p2_vwc_min=40.0, p2_vwc_max=50.0, p2_vwc_cc_ref=60.0, p2_ec_target=9.0, p2_runoff_max=450.0, p2_vol_cc=3000.0, p2_shot_pc=2.0, p2_tiempo_entre_min=30, p3_vwc_min=24.0),
}


STAGE_SCHEDULES = {
    "PLANTULA": {"light_on_hour": 6.0, "light_off_hour": 24.0, "p1_offset_min": 60.0, "p2_inicio_hour": 11.0, "p2_fin_hour": 20.0},
    "CRECIMIENTO": {"light_on_hour": 6.0, "light_off_hour": 24.0, "p1_offset_min": 60.0, "p2_inicio_hour": 11.0, "p2_fin_hour": 20.0},
    "FLORACION": {"light_on_hour": 6.0, "light_off_hour": 18.0, "p1_offset_min": 60.0, "p2_inicio_hour": 10.5, "p2_fin_hour": 14.5},
}

STAGE_NUTRIENT_SOLUTIONS = {
    "PLANTULA": {
        "p1": {"ec": 1.2, "ph": 5.8, "n": 90.0, "p": 30.0, "k": 110.0},
        "p2": {"ec": 1.3, "ph": 5.8, "n": 90.0, "p": 35.0, "k": 120.0},
    },
    "CRECIMIENTO": {
        "p1": {"ec": 2.2, "ph": 5.9, "n": 160.0, "p": 50.0, "k": 200.0},
        "p2": {"ec": 2.4, "ph": 5.9, "n": 150.0, "p": 55.0, "k": 220.0},
    },
    "FLORACION": {
        "p1": {"ec": 2.8, "ph": 6.0, "n": 110.0, "p": 80.0, "k": 260.0},
        "p2": {"ec": 3.2, "ph": 6.0, "n": 95.0, "p": 95.0, "k": 300.0},
    },
}

STAGE_SUBSTRATE_INIT = {
    "PLANTULA": {"vwc": 45.0, "ec": 1.2, "ph": 5.8, "n": 90.0, "p": 30.0, "k": 110.0},
    "CRECIMIENTO": {"vwc": 52.0, "ec": 2.2, "ph": 5.9, "n": 160.0, "p": 50.0, "k": 200.0},
    "FLORACION": {"vwc": 42.0, "ec": 2.8, "ph": 6.0, "n": 110.0, "p": 75.0, "k": 250.0},
}

# ---------------------------------------------------------------------------
# PLAN DE CULTIVO (configurable desde el día 1)
# ---------------------------------------------------------------------------
# Cada etapa se abandona cuando la planta cumple a la vez:
#   - al menos `min_days` días EN la etapa, y
#   - al menos `biomass_target_g` de biomasa seca.
# Si llega a `max_days` sin cumplir la biomasa, pasa igual y el evento queda
# registrado como "forzado". FLORACION es la etapa final (no tiene salida).
# La maceta de cada etapa vive en su perfil de riego (`pot_volume_ml`): si la
# maceta de la etapa siguiente es mayor se trasplanta; si es igual, la maceta
# se mantiene y solo cambia la configuración de riego.
DEFAULT_CROP_PLAN = {
    "PLANTULA":    {"min_days": 10.0, "max_days": 21.0, "biomass_target_g": 8.0},
    "CRECIMIENTO": {"min_days": 21.0, "max_days": 49.0, "biomass_target_g": 80.0},
    "FLORACION":   {"min_days": 0.0,  "max_days": 0.0,  "biomass_target_g": 0.0},
}
NEXT_STAGE = {"PLANTULA": "CRECIMIENTO", "CRECIMIENTO": "FLORACION", "FLORACION": None}


class PlantBiology:
    """Modelo biológico individual con fenotipo y respuesta fisiológica."""
    def __init__(self, plant_id=0, strain_name="OG Kush", vigor_factor=1.0):
        self.id = plant_id
        self.strain_name = strain_name
        # Un cultivo real no mezcla autoflorecientes con fotoperiódicas. Las 12
        # plantas son FOTOPERIODICAS: solo florecen cuando la sala pasa a 12/12.
        # (Más adelante se podrá diferenciar con "AUTOFLORECIENTE".)
        self.flowering_type = "FOTOPERIODICA"
        self.vigor_factor = vigor_factor   # Variabilidad genética natural (0.85 a 1.20)
        
        self.age_days = 1.0
        self.height_cm = 5.0 * vigor_factor
        self.max_height_cm = 95.0 * vigor_factor
        self.leaf_area_m2 = 0.005 * vigor_factor
        self.biomass_g = 2.0 * vigor_factor
        self.root_health = 100.0
        self.stage = CropStage.GERMINATION
        
        # Inducción floral orgánica y fotoperiodo
        self.flower_induced = False
        self.flower_induction_day = 0.0
        self.steering_type = "VEGETATIVO"
        
        # Síntomas visuales (0.0 a 1.0)
        self.leaf_droop = 0.0
        self.tip_burn = 0.0
        self.leaf_claw = 0.0
        self.chlorosis = 0.0
        self.stem_purple = 0.0
        self.buds_size = 0.0
        self.trichomes_amber = 0.0

    def sync_to_stage(self, stage):
        """Sincroniza la morfología base (solo para resets o inicialización explícita)."""
        if stage == "PLANTULA":
            self.stage = CropStage.GERMINATION
            self.age_days = 4.0
            self.height_cm = 8.0 * self.vigor_factor
            self.biomass_g = 2.5 * self.vigor_factor
            self.leaf_area_m2 = 0.01 * self.vigor_factor
            self.flower_induced = False
            self.buds_size = 0.0
            self.trichomes_amber = 0.0
            self.root_health = 100.0
        elif stage == "CRECIMIENTO":
            self.stage = CropStage.VEGETATIVE
            self.age_days = 22.0
            self.height_cm = 38.0 * self.vigor_factor
            self.biomass_g = 42.0 * self.vigor_factor
            self.leaf_area_m2 = 0.35 * self.vigor_factor
            self.flower_induced = False
            self.buds_size = 0.0
            self.trichomes_amber = 0.0
            self.root_health = 100.0
        elif stage == "FLORACION":
            self.stage = CropStage.FLOWERING
            self.age_days = 48.0
            self.flower_induced = True
            self.flower_induction_day = 30.0
            self.height_cm = 72.0 * self.vigor_factor
            self.biomass_g = 105.0 * self.vigor_factor
            self.leaf_area_m2 = 0.65 * self.vigor_factor
            self.buds_size = 0.45
            self.trichomes_amber = 0.10
            self.root_health = 98.0

    def update_growth(self, dt_hours, vwc, ec, ph, n_ppm, p_ppm, k_ppm, vpd, temp, light_exposure, pot_ml=5000.0, photoperiod_hours=18.0):
        dt_days = dt_hours / 24.0
        self.age_days += dt_days
        
        # 1. Determinación de Fase Fenológica Orgánica (Fotoperiodo & Edad)
        if self.age_days < 10.0:
            self.stage = CropStage.GERMINATION
        else:
            is_auto = (self.flowering_type == "AUTOFLORECIENTE")
            if not self.flower_induced:
                if photoperiod_hours <= 13.5 or (is_auto and self.age_days >= 28.0):
                    self.flower_induced = True
                    self.flower_induction_day = self.age_days

            if self.flower_induced:
                flower_days = max(0.0, self.age_days - self.flower_induction_day)
                if flower_days < 18.0:
                    self.stage = CropStage.STRETCH
                elif flower_days < 52.0:
                    self.stage = CropStage.FLOWERING
                else:
                    self.stage = CropStage.RIPENING
            else:
                self.stage = CropStage.VEGETATIVE

        # 2. Estrés hídrico (diferenciado para plántula y adulto)
        if self.stage == CropStage.GERMINATION:
            water_stress = max(0.0, min(1.0, (38.0 - vwc) / 20.0)) if vwc < 38.0 else 0.0
        else:
            water_stress = max(0.0, min(1.0, (24.0 - vwc) / 14.0)) if vwc < 24.0 else 0.0
            
        hypoxia = max(0.0, min(1.0, (vwc - 66.0) / 20.0)) if vwc > 66.0 else 0.0
        self.root_health = max(10.0, min(100.0, self.root_health + (2.0 - hypoxia * 6.0) * dt_days))

        # 3. Estrés por maceta pequeña sin trasplantar (Restricción radicular / Rootbound)
        pot_biomass_limit = (pot_ml / 500.0) * 12.0
        rootbound_stress = 0.0
        if self.biomass_g > pot_biomass_limit:
            over = (self.biomass_g - pot_biomass_limit) / max(pot_biomass_limit, 5.0)
            rootbound_stress = min(0.65, over * 0.40)
            self.root_health = max(10.0, self.root_health - rootbound_stress * 3.5 * dt_days)
        
        # 4. Estrés por EC y pH
        target_ec_max = 2.2 if self.stage == CropStage.GERMINATION else (3.6 if self.stage == CropStage.VEGETATIVE else 5.2)
        ec_stress = max(0.0, min(1.0, (ec - target_ec_max) / 2.2)) if ec > target_ec_max else 0.0
        ph_stress = max(0.0, min(1.0, (5.5 - ph) / 0.7, (ph - 6.5) / 0.7))

        # Requerimientos NPK
        if self.stage == CropStage.GERMINATION:
            n_req, p_req, k_req = 80.0, 25.0, 90.0
        elif self.stage in (CropStage.VEGETATIVE, CropStage.STRETCH):
            n_req, p_req, k_req = 150.0, 45.0, 180.0
        else:
            n_req, p_req, k_req = 95.0, 65.0, 220.0

        n_ok = min(1.0, n_ppm / n_req)
        p_ok = min(1.0, p_ppm / p_req)
        k_ok = min(1.0, k_ppm / k_req)
        nutrition_factor = max(0.20, min(n_ok, p_ok, k_ok)) * (1.0 - ph_stress * 0.7)
        combined_stress = max(water_stress, hypoxia, ec_stress * 0.8, ph_stress * 0.7, rootbound_stress)
        self.root_health = max(10.0, min(100.0, self.root_health - ph_stress * 5.0 * dt_days))
        growth_factor = max(0.05, 1.0 - combined_stress) * nutrition_factor * (self.root_health / 100.0) * self.vigor_factor * light_exposure

        # 5. Detección de Crop Steering (Vegetativo vs Generativo)
        is_generative_steering = (ec >= 3.2 or (vwc < 48.0 and vpd > 1.25))
        self.steering_type = "GENERATIVO" if is_generative_steering else "VEGETATIVO"
        generative_boost = 1.35 if is_generative_steering else 1.0

        # 6. Crecimiento morfológico guiado
        if self.stage == CropStage.GERMINATION:
            self.height_cm += 1.0 * growth_factor * dt_days
            self.biomass_g += 0.5 * growth_factor * dt_days
        elif self.stage == CropStage.VEGETATIVE:
            veg_foliar_mult = 1.25 if not is_generative_steering else 0.85
            self.height_cm += 2.5 * growth_factor * veg_foliar_mult * dt_days
            self.biomass_g += 3.2 * growth_factor * veg_foliar_mult * dt_days
            self.leaf_area_m2 = min(0.40, self.leaf_area_m2 + 0.018 * growth_factor * dt_days)
        elif self.stage == CropStage.STRETCH:
            stretch_rate = 1.8 if is_generative_steering else 3.8
            self.height_cm += stretch_rate * growth_factor * dt_days
            self.biomass_g += 4.5 * growth_factor * dt_days
            self.leaf_area_m2 = min(0.70, self.leaf_area_m2 + 0.02 * growth_factor * dt_days)
        elif self.stage == CropStage.FLOWERING:
            self.height_cm += 0.2 * growth_factor * dt_days
            self.biomass_g += 4.2 * growth_factor * dt_days
            self.buds_size = min(1.0, self.buds_size + 0.045 * growth_factor * generative_boost * dt_days)
        elif self.stage == CropStage.RIPENING:
            self.buds_size = min(1.0, self.buds_size + 0.015 * growth_factor * generative_boost * dt_days)
            self.trichomes_amber = min(0.85, self.trichomes_amber + (0.050 if is_generative_steering else 0.025) * dt_days)
            
        self.height_cm = min(self.max_height_cm, self.height_cm)
        
        # 7. Síntomas visibles
        self.leaf_droop = max(0.0, min(1.0, water_stress * 1.3 + hypoxia * 0.7 + rootbound_stress * 0.8))
        self.tip_burn = max(self.tip_burn, min(1.0, ec_stress * 1.1))
        self.leaf_claw = max(0.0, min(1.0, ec_stress * 0.9 + hypoxia * 0.5 + rootbound_stress * 0.4))
        self.chlorosis = max(0.0, min(1.0, (100.0 - self.root_health) / 100.0 * 0.85 + (0.45 if self.stage == CropStage.RIPENING else 0.0)))
        self.stem_purple = 1.0 if temp < 17.0 else max(0.0, self.stem_purple - dt_days * 0.2)


class GreenhousePlantPhysics:
    """Física detallada de una maceta individual de las 12 del sistema."""
    def __init__(self, plant_id=0, grid_x=0.0, grid_z=0.0):
        self.id = plant_id
        self.grid_x = grid_x               # Posición física en la mesa de cultivo (m)
        self.grid_z = grid_z
        
        # Variabilidad biológica estocástica
        random.seed(plant_id * 42 + 7)
        vigor = random.uniform(0.88, 1.14)
        strains = ["OG Kush", "Gorilla Glue", "Amnesia Haze", "Northern Lights"]
        strain = strains[plant_id % len(strains)]
        self.biology = PlantBiology(plant_id, strain_name=strain, vigor_factor=vigor)
        
        # Configuración de riego de esta planta
        self.config = PlantConfig(plant_id)
        
        # Variabilidad en goteros (+/- 4%) y capacidad de campo por compactación (+/- 2%)
        self.dripper_variance = random.uniform(0.96, 1.04)
        self.pot_ml = 3500.0
        self.vwc_cc = 46.0 * random.uniform(0.97, 1.03)
        self.vwc_pmp = 15.0
        self.W = 0.38 * self.pot_ml
        self.S = 2.4 * self.W
        self.ph = 5.9
        self.n_mg, self.p_mg, self.k_mg = 160.0 * self.W / 1000.0, 50.0 * self.W / 1000.0, 200.0 * self.W / 1000.0
        
        # Bandeja y celda de carga
        self.tray_ml = 0.0
        self.tray_tare_ml = 0.0
        self.tare_with_water = False
        self.tray_runoff_day = 0.0
        self.drain_rate_max = 950.0
        
        # Electroválvula y actuadores
        self.valve_open = False
        self.valve_cmd = False
        self.valve_feedback = False
        self.valve_transition_ms = 0
        
        # Variables de control automático de riego
        self.ultimo_disparo_sim_time = -99999.0
        self.disparo_activo = False
        self.disparo_timer_segundos = 0.0
        self.disparo_duracion_total = 0.0
        self.disparo_fase = None
        self.fase_actual_planta = "P1_ESPERA" # P1_ESPERA, P1_SUBIENDO, P2_MANTENIENDO, P3_NOCHE

        # Firmware: p2EsperandoPiso — hysteresis flag.
        # Set True when VWC hits p2_vwc_max; cleared when VWC drops below p2_vwc_min.
        # P2 will not fire again until this flag is cleared.
        self.p2_esperando_piso = False
        self.fase_p1_iniciada = False

        # Contadores de runoff por fase — réplica exacta de runoffFaseP1/P2/P3 del firmware.
        # LogicaRiego.cpp:146-154: cada contador se resetea al entrar en su fase.
        # P1 usa runoff_fase_p1, P2 usa runoff_fase_p2, P3 usa runoff_fase_p3.
        # NO usar tray_runoff_day para las comprobaciones de P2/P3 (BUG original).
        self.runoff_fase_p1 = 0.0
        self.runoff_fase_p2 = 0.0
        self.runoff_fase_p3 = 0.0
        self._prev_fase_global = None   # Para detectar transición de fase en execute_irrigation

        # Equivalente simplificado de la proteccion de inundacion del NodoControl.
        # Una bandeja que alcanza su limite bloquea nuevos riegos hasta que el
        # operador la vacia; no se debe ocultar el desborde recortando el valor.
        self.inundacion_critica = False
        # Ensayo virtual CC: mismo principio que CalibracionCC.cpp (pulsos,
        # espera y primer drenaje observable en la bandeja).
        self.cc_test = None
        self.cc_result = None
        
        # Contabilidad
        self.total_irrigation_ml = 0.0
        self.total_drain_ml = 0.0

        # La etapa de manejo es una decisión del cultivador (y puede cambiar
        # con un trasplante); no se confunde con la fase fenológica calculada
        # por edad en PlantBiology.
        self.management_stage = "PLANTULA"
        # Edad de cultivo (días) en que la planta entró en la etapa de manejo actual.
        self.stage_start_day = self.biology.age_days
        self.substrate_type = "COCO"
        self.stage_profiles = {key: dict(value) for key, value in DEFAULT_STAGE_PROFILES.items()}
        self._load_stage_profile(self.management_stage, preserve_vwc=False)
        self.set_substrate(self.substrate_type, preserve_vwc=False)

    @property
    def vwc(self): return 100.0 * self.W / self.pot_ml
    @property
    def ec(self): return self.S / max(self.W, 1.0)
    @property
    def n_ppm(self): return 1000.0 * self.n_mg / max(self.W, 1.0)
    @property
    def p_ppm(self): return 1000.0 * self.p_mg / max(self.W, 1.0)
    @property
    def k_ppm(self): return 1000.0 * self.k_mg / max(self.W, 1.0)
    @property
    def W_cc(self): return (self.vwc_cc / 100.0) * self.pot_ml
    @property
    def tray_weight_ml(self):
        """Lectura de la celda, ya afectada por la última tara (pesoFiltrado)."""
        return self.tray_ml - self.tray_tare_ml

    def drain_tray_passively(self, dt_seconds, tau_seconds):
        """Desagüe físico pasivo usado por el modelo SIL; no es un comando del firmware."""
        if tau_seconds > 0.0 and self.tray_ml > 0.0:
            self.tray_ml *= math.exp(-dt_seconds / tau_seconds)

    def _save_stage_profile(self):
        profile = self.stage_profiles[self.management_stage]
        profile.update({field: getattr(self.config, field) for field in IRRIGATION_FIELDS})
        profile.update({"pot_volume_ml": self.pot_ml, "vwc_cc": self.vwc_cc, "vwc_pmp": self.vwc_pmp})

    def _load_stage_profile(self, stage, preserve_vwc=True, allow_downsize=True):
        profile = self.stage_profiles[stage]
        old_vwc, old_ec = self.vwc, self.ec
        old_n, old_p, old_k = self.n_ppm, self.p_ppm, self.k_ppm
        old_pot = self.pot_ml
        self.pot_ml = float(profile["pot_volume_ml"])
        if not allow_downsize:
            # Una planta viva nunca pasa a una maceta menor: el volumen se mantiene.
            self.pot_ml = max(self.pot_ml, old_pot)
        self.vwc_cc = float(profile["vwc_cc"])
        self.vwc_pmp = float(profile["vwc_pmp"])
        for field in IRRIGATION_FIELDS:
            setattr(self.config, field, profile[field])
        if preserve_vwc:
            self.W = self.pot_ml * old_vwc / 100.0
            self.S = self.W * old_ec
            self.n_mg, self.p_mg, self.k_mg = old_n * self.W / 1000.0, old_p * self.W / 1000.0, old_k * self.W / 1000.0
        else:
            init_vals = STAGE_SUBSTRATE_INIT.get(stage, STAGE_SUBSTRATE_INIT["PLANTULA"])
            self.W = self.pot_ml * (init_vals["vwc"] / 100.0)
            self.S = self.W * init_vals["ec"]
            self.ph = init_vals["ph"]
            self.n_mg = init_vals["n"] * self.W / 1000.0
            self.p_mg = init_vals["p"] * self.W / 1000.0
            self.k_mg = init_vals["k"] * self.W / 1000.0

    def set_management_stage(self, stage, sync_bio=True):
        if stage not in MANAGEMENT_STAGES:
            return False
        self._save_stage_profile()
        self.management_stage = stage
        self._load_stage_profile(stage, preserve_vwc=True)
        # La maceta cambia con el trasplante, pero la curva de retención la
        # gobierna el sustrato seleccionado, no la etapa de riego.
        self.set_substrate(self.substrate_type, preserve_vwc=True)
        if sync_bio:
            self.biology.sync_to_stage(stage)
        self.stage_start_day = self.biology.age_days
        return True

    @property
    def stage_days(self):
        """Días transcurridos dentro de la etapa de manejo actual."""
        return max(0.0, self.biology.age_days - self.stage_start_day)

    def advance_stage(self, new_stage):
        """Pasa a la etapa siguiente SIN tocar la biología (la planta sigue viva).

        Cambia el perfil de riego; la maceta solo crece (trasplante) si el perfil
        nuevo la define mayor, y el agua/sales del sustrato se conservan.
        """
        if new_stage not in MANAGEMENT_STAGES or new_stage == self.management_stage:
            return None
        old_stage, old_pot = self.management_stage, self.pot_ml
        self._save_stage_profile()
        self.management_stage = new_stage
        self._load_stage_profile(new_stage, preserve_vwc=True, allow_downsize=False)
        self.set_substrate(self.substrate_type, preserve_vwc=True)
        transplanted = self.pot_ml > old_pot
        if transplanted:
            self.biology.root_health = min(100.0, self.biology.root_health + 20.0)
        self.stage_start_day = self.biology.age_days
        # Se guarda el perfil con la maceta real en uso.
        self._save_stage_profile()
        return {"from": old_stage, "to": new_stage, "transplant": transplanted,
                "pot_from_ml": old_pot, "pot_to_ml": self.pot_ml}

    def set_substrate(self, substrate_type, preserve_vwc=True):
        if substrate_type not in SUBSTRATE_PROFILES:
            return False
        old_vwc, old_ec = self.vwc, self.ec
        profile = SUBSTRATE_PROFILES[substrate_type]
        self.substrate_type = substrate_type
        self.vwc_cc, self.vwc_pmp = profile["vwc_cc"], profile["vwc_pmp"]
        if preserve_vwc:
            self.W, self.S = self.pot_ml * old_vwc / 100.0, self.pot_ml * old_vwc / 100.0 * old_ec
        else:
            init_vals = STAGE_SUBSTRATE_INIT.get(self.management_stage, STAGE_SUBSTRATE_INIT["PLANTULA"])
            self.W = self.pot_ml * (init_vals["vwc"] / 100.0)
            self.S = self.W * init_vals["ec"]
            self.ph = init_vals["ph"]
            self.n_mg = init_vals["n"] * self.W / 1000.0
            self.p_mg = init_vals["p"] * self.W / 1000.0
            self.k_mg = init_vals["k"] * self.W / 1000.0
        retained_ml = self.W_cc
        self.config.p1_vol_cc = retained_ml
        self.config.p2_vol_cc = retained_ml
        self.config.p2_vwc_cc_ref = self.vwc_cc
        return True

    def step(self, dt_seconds, is_day, vpd, temp, pump_active, solution, light_exposure,
             fase_global=None, photoperiod_hours=18.0):
        dt_min = dt_seconds / 60.0
        dt_hours = dt_seconds / 3600.0
        
        # 1. Transición mecánica de la electroválvula (400 ms)
        if self.valve_cmd != self.valve_open:
            self.valve_transition_ms += dt_seconds * 1000.0
            if self.valve_transition_ms >= 400.0:
                self.valve_open = self.valve_cmd
                self.valve_feedback = self.valve_open
                self.valve_transition_ms = 0
        else:
            self.valve_transition_ms = 0
            
        # 2. Riego entrante
        inflow_ml = 0.0
        if self.valve_open and pump_active:
            inflow_rate = self.config.flow_ml_min * self.dripper_variance
            inflow_ml = inflow_rate * dt_min
            water_before = self.W
            self.W += inflow_ml
            self.S += inflow_ml * solution.ec
            self.ph = (self.ph * water_before + solution.ph * inflow_ml) / max(self.W, 1.0)
            self.n_mg += inflow_ml * solution.n / 1000.0
            self.p_mg += inflow_ml * solution.p / 1000.0
            self.k_mg += inflow_ml * solution.k / 1000.0
            self.total_irrigation_ml += inflow_ml
            
        # 3. Drenaje / Escorrentía
        runoff_ml = 0.0
        if self.W > self.W_cc:
            excess = self.W - self.W_cc
            drain = min(excess, (self.drain_rate_max * dt_min) * (excess / (self.pot_ml * 0.15)))
            drain = min(drain, excess)
            runoff_ml = drain
            self.W -= runoff_ml
            sales_drained = runoff_ml * self.ec
            self.S = max(0.0, self.S - sales_drained)
            fraction = runoff_ml / max(self.W + runoff_ml, 1.0)
            self.n_mg *= max(0.0, 1.0 - fraction)
            self.p_mg *= max(0.0, 1.0 - fraction)
            self.k_mg *= max(0.0, 1.0 - fraction)
            self.tray_ml += runoff_ml
            # El firmware compara pesoFiltrado (la lectura de la celda) contra
            # CB. Se conserva el agua física por separado para visualizar una
            # tara hecha incorrectamente con agua en la bandeja.
            if self.tray_weight_ml > self.config.capacidad_bandeja_ml:
                self.inundacion_critica = True
            self.tray_runoff_day += runoff_ml
            self.total_drain_ml += runoff_ml
            # ── Acumular en el contador de fase correspondiente ────────────────────
            # Réplica de LogicaRiego.cpp:187-190: el firmware acumula el runoff
            # de la bandeja en el contador de la fase activa en ese momento.
            if fase_global == "P1":
                self.runoff_fase_p1 += runoff_ml
            elif fase_global == "P2":
                self.runoff_fase_p2 += runoff_ml
            elif fase_global == "P3":
                self.runoff_fase_p3 += runoff_ml
            
        # 4. Transpiración dinámica
        et_base = (3.2 if is_day else 0.50) * self.biology.vigor_factor * light_exposure
        stage_mult = 0.18 if self.biology.stage == CropStage.GERMINATION else (0.70 if self.biology.stage == CropStage.VEGETATIVE else 1.15)
        vpd_factor = max(0.2, min(1.6, vpd / 1.1))
        et_rate = et_base * stage_mult * vpd_factor
        if self.vwc < 25.0:
            et_rate *= max(0.05, (self.vwc - self.vwc_pmp) / 10.0)
            
        et_loss = min(self.W * 0.8, max(0.0, et_rate * dt_min))
        self.W -= et_loss
        
        # 5. Asimilación fisiológica de nutrientes
        # Las raíces absorben iones del flujo de agua transpirativa (flujo de masa + transporte activo).
        # Típicamente entre 55% y 75% de las sales disueltas en el agua transpirada son asimiladas para
        # síntesis de biomasa celular. El resto se concentra gradualmente en el dryback.
        root_efficiency = max(0.35, min(0.85, 0.65 * (self.biology.root_health / 100.0)))
        uptake_s = et_loss * self.ec * root_efficiency
        self.S = max(0.0, self.S - uptake_s)
        
        uptake_fraction = min(0.5, (et_loss / max(self.W, 1.0)) * root_efficiency)
        self.n_mg = max(0.0, self.n_mg * (1.0 - uptake_fraction))
        self.p_mg = max(0.0, self.p_mg * (1.0 - uptake_fraction * 0.9))
        self.k_mg = max(0.0, self.k_mg * (1.0 - uptake_fraction * 1.1))
        
        # 6. Biología
        self.biology.update_growth(
            dt_hours, self.vwc, self.ec, self.ph,
            self.n_ppm, self.p_ppm, self.k_ppm,
            vpd, temp, light_exposure,
            pot_ml=self.pot_ml,
            photoperiod_hours=photoperiod_hours
        )
        
        return {
            "inflow_ml": inflow_ml,
            "runoff_ml": runoff_ml,
            "vwc": self.vwc,
            "ec": self.ec,
            "tray_ml": self.tray_ml
        }



class GreenhouseEnvironment:
    """Entorno global con 12 plantas y Controlador Automático de Riego (NodoControl SIL)."""
    def __init__(self):
        self.sim_time_seconds = 8 * 3600.0
        self.time_speed = 60.0
        self.is_paused = False
        self.auto_irrigation_mode = True   # Control automático por fases P1/P2/P3 activo
        self.selected_plant_id = 0         # Planta enfocada en la UI (0 a 11)
        
        # Configuración Global de Ventanas Horarias (ajustes.html)
        self.light_on_hour = 6.0           # 06:00
        self.light_off_hour = 24.0         # 24:00 (Fotoperiodo 18/6)
        self.p1_offset_min = 60.0          # P0: dryback posterior al encendido
        self.p2_inicio_hour = 11.0         # 11:00 AM (Inicio ventana P2)
        self.p2_fin_hour = 20.0            # 20:00 PM (Fin ventana P2)
        
        # Clima y tanque
        self.target_temp_day = 25.5
        self.target_temp_night = 19.0
        self.target_rh_day = 62.0
        self.target_rh_night = 55.0
        self.light_dimmer_pct = 100.0
        self.ppfd_max = 950
        
        self.air_temp = 25.0
        self.air_rh = 60.0
        self.co2_ppm = 850
        
        # El firmware distingue BOMBAP1 y BOMBAP2. El gemelo conserva dos
        # soluciones independientes para ensayar recetas de subida y mantenimiento.
        self.solution_p1 = NutrientSolution(ec=2.0, ph=5.9, n=160.0, p=50.0, k=200.0)
        self.solution_p2 = NutrientSolution(ec=2.4, ph=5.9, n=130.0, p=55.0, k=230.0)
        
        self.pump_p1 = False
        self.pump_p2 = False

        # En el SIL físico la bandeja evacúa a desagüe con tau = 1800 s.
        # No corresponde a una bomba/relé ni a una orden automática TA.
        self.tray_passive_drain_enabled = True
        self.tray_drain_tau_seconds = 1800.0
        # Opción del simulador para representar un drenaje motorizado externo.
        # El firmware actual no expone este actuador: se modela por separado.
        self.auto_empty_and_tare_end_p2 = False
        self.last_tray_service_day = -1
        
        # Crear las 12 plantas organizadas en una mesa de cultivo (rejilla 3 x 4)
        self.plants = []
        for i in range(12):
            row = i // 4
            col = i % 4
            x = (col - 1.5) * 0.42
            z = (row - 1.0) * 0.45
            self.plants.append(GreenhousePlantPhysics(i, grid_x=x, grid_z=z))
            
        self.history = []
        self.history_max = 600
        self.history_timer = 0.0

        # Plan de cultivo orgánico: configurable desde el día 1.
        self.crop_plan = copy.deepcopy(DEFAULT_CROP_PLAN)
        self.stage_schedules = copy.deepcopy(STAGE_SCHEDULES)
        self.stage_solutions = copy.deepcopy(STAGE_NUTRIENT_SOLUTIONS)
        self.room_stage = "PLANTULA"
        self.crop_events = []
        self.apply_stage_environment("PLANTULA")

    @property
    def hour_of_day(self):
        return (self.sim_time_seconds / 3600.0) % 24.0

    @property
    def is_day(self):
        h = self.hour_of_day
        if self.light_on_hour < self.light_off_hour:
            return self.light_on_hour <= h < self.light_off_hour
        else:
            return h >= self.light_on_hour or h < self.light_off_hour

    @property
    def current_ppfd(self):
        if not self.is_day: return 0
        return int(self.ppfd_max * (self.light_dimmer_pct / 100.0))

    @property
    def vpd(self):
        svp = 0.61078 * math.exp((17.27 * self.air_temp) / (self.air_temp + 237.3))
        avp = svp * (self.air_rh / 100.0)
        return max(0.05, svp - avp)

    def set_time_speed(self, speed): self.time_speed = float(speed)
    def toggle_pause(self): self.is_paused = not self.is_paused; return self.is_paused
    def set_selected_plant(self, plant_id):
        if 0 <= plant_id < len(self.plants):
            self.selected_plant_id = int(plant_id)

    def apply_stage_environment(self, stage):
        """Aplica la receta de nutrientes y horario recomendado para la etapa de manejo."""
        if stage not in MANAGEMENT_STAGES:
            return
        self.room_stage = stage
        sched = self.stage_schedules[stage]
        self.light_on_hour = sched["light_on_hour"]
        self.light_off_hour = sched["light_off_hour"]
        self.p1_offset_min = sched["p1_offset_min"]
        self.p2_inicio_hour = sched["p2_inicio_hour"]
        self.p2_fin_hour = sched["p2_fin_hour"]
        sols = self.stage_solutions[stage]
        self.update_solution("P1", sols["p1"])
        self.update_solution("P2", sols["p2"])

    def set_management_stage(self, plant_id, stage, update_env=False):
        """Salto MANUAL de etapa (reinicia la biología). El avance orgánico usa review_crop_plan."""
        if not 0 <= plant_id < len(self.plants):
            return False
        plant = self.plants[plant_id]
        if plant.cc_test:
            return False
        self._cerrar_disparo(plant)
        res = plant.set_management_stage(stage)
        if update_env and res:
            self.apply_stage_environment(stage)
        return res

    # ------------------------------------------------------------------
    # Plan de cultivo: transiciones automáticas por días y biomasa
    # ------------------------------------------------------------------
    def plant_stage_status(self, p):
        """Estado de la planta frente al plan de su etapa actual (None si es la final)."""
        nxt = NEXT_STAGE.get(p.management_stage)
        if nxt is None:
            return None
        plan = self.crop_plan[p.management_stage]
        days, biomass = p.stage_days, p.biology.biomass_g
        days_ok = days >= plan["min_days"]
        biomass_ok = biomass >= plan["biomass_target_g"]
        ready = days_ok and biomass_ok
        forced = (not ready) and plan["max_days"] > 0 and days >= plan["max_days"]
        return {"next": nxt, "days": days, "biomass": biomass, "days_ok": days_ok,
                "biomass_ok": biomass_ok, "ready": ready, "forced": forced, **plan}

    def _log_crop_event(self, p, info, forced, note=""):
        ev = {"day": int(self.sim_time_seconds / 86400.0) + 1, "plant": p.id,
              "age_days": round(p.biology.age_days, 1), "biomass_g": round(p.biology.biomass_g, 1),
              "forced": bool(forced), "note": note, **info}
        self.crop_events.append(ev)
        del self.crop_events[:-60]
        return ev

    def _advance_plant(self, p, new_stage, forced=False, note=""):
        if p.cc_test:
            return None
        self._cerrar_disparo(p)
        info = p.advance_stage(new_stage)
        return self._log_crop_event(p, info, forced, note) if info else None

    def review_crop_plan(self):
        """Revisión diaria. Plántula→Crecimiento es por planta; Crecimiento→Floración
        es de sala (fotoperiodo único): cuando TODAS están listas, o cuando alguna
        llega a sus días máximos (forzado)."""
        events = []
        for p in self.plants:
            if p.management_stage == "PLANTULA":
                st = self.plant_stage_status(p)
                if st and (st["ready"] or st["forced"]):
                    ev = self._advance_plant(p, "CRECIMIENTO", forced=st["forced"])
                    if ev:
                        events.append(ev)
        if self.room_stage == "PLANTULA" and not any(p.management_stage == "PLANTULA" for p in self.plants):
            self.apply_stage_environment("CRECIMIENTO")
        if self.room_stage == "CRECIMIENTO":
            grow = [p for p in self.plants if p.management_stage == "CRECIMIENTO"]
            stats = [self.plant_stage_status(p) for p in grow]
            if grow and (all(s["ready"] for s in stats) or any(s["forced"] for s in stats)):
                self.apply_stage_environment("FLORACION")   # 12/12 + receta de floración
                for p, s in zip(grow, stats):
                    ev = self._advance_plant(p, "FLORACION", forced=not s["ready"],
                                             note="cambio de sala a 12/12")
                    if ev:
                        events.append(ev)
        return events

    def set_substrate(self, plant_id, substrate_type):
        if not 0 <= plant_id < len(self.plants):
            return False
        plant = self.plants[plant_id]
        if plant.cc_test:
            return False
        self._cerrar_disparo(plant)
        return plant.set_substrate(substrate_type)

    def fase_riego_actual(self):
        """Replica las ventanas del firmware: P0, P1, P2, espera y P3 nocturna."""
        h = self.hour_of_day
        if not self.is_day:
            return "P3"
        p1_inicio = (self.light_on_hour + self.p1_offset_min / 60.0) % 24.0
        if h < p1_inicio:
            return "P0"
        if h < self.p2_inicio_hour:
            return "P1"
        if h < self.p2_fin_hour:
            return "P2"
        # El ultimo riego termina antes de apagar las luces: no es P3.
        return "ESPERA"

    @staticmethod
    def bomba_para_fase(fase):
        # Igual que el firmware: P2 usa Bomba P2; P1, salvataje P3 y
        # riego manual usan Bomba P1.
        return "P2" if fase == "P2" else "P1"

    def _cerrar_disparo(self, p, registrar_intervalo=True):
        p.disparo_activo = False
        p.disparo_fase = None
        p.valve_cmd = False
        p.disparo_timer_segundos = 0.0
        if registrar_intervalo:
            p.ultimo_disparo_sim_time = self.sim_time_seconds

    def set_auto_irrigation_mode(self, enabled):
        """Al desactivar AUTO se lleva el sistema a estado hidráulico seguro."""
        self.auto_irrigation_mode = bool(enabled)
        if not self.auto_irrigation_mode:
            self.close_all_valves()

    def empty_tray_manually(self, plant_id):
        """Acción física del operador. No altera el offset de la celda."""
        if 0 <= plant_id < len(self.plants):
            p = self.plants[plant_id]
            p.tray_ml = 0.0
            return True
        return False

    def tare_tray(self, plant_id):
        """Replica TA/CMD_TARAR_CELDA: fija el offset incluso si hay agua."""
        if not 0 <= plant_id < len(self.plants):
            return False
        p = self.plants[plant_id]
        p.tray_tare_ml = p.tray_ml
        p.tare_with_water = p.tray_ml > 15.0
        return True

    def start_field_capacity_test(self, plant_id, pot_volume_ml, pulse_ml, wait_minutes):
        """Ensayo CC virtual; al primer drenaje registra VWC_CC y agua retenida."""
        if not 0 <= plant_id < len(self.plants) or self.hay_inundacion_critica():
            return False
        p = self.plants[plant_id]
        if p.cc_test:
            return False
        pot_volume_ml, pulse_ml, wait_minutes = float(pot_volume_ml), float(pulse_ml), float(wait_minutes)
        if pot_volume_ml <= 0 or pulse_ml <= 0 or wait_minutes <= 0:
            return False
        # El firmware excluye la planta del automático mientras CC está
        # activa. Se corta primero un disparo normal de esta planta para que
        # el ensayo pueda arrancar sin requerir una ventana horaria especial.
        self._cerrar_disparo(p)
        old_pot_ml = p.pot_ml
        p.pot_ml = pot_volume_ml
        p.W *= pot_volume_ml / old_pot_ml
        p.cc_test = {
            "state": "PULSO", "pulse_ml": pulse_ml, "wait_seconds": wait_minutes * 60.0,
            "pulse_seconds": (pulse_ml / max(p.config.flow_ml_min * p.dripper_variance, 1.0)) * 60.0,
            "elapsed": 0.0, "total_ml": 0.0, "initial_tray_g": p.tray_weight_ml,
            "hard_limit_ml": pot_volume_ml * 0.70,
        }
        p.cc_result = None
        return True

    def _finish_cc_test(self, p, detected_drainage, reason):
        test = p.cc_test
        p.valve_cmd = False
        if detected_drainage:
            # El firmware conserva BW (VWC en CC). El gemelo además deriva el
            # volumen retenido para que S/Y y los porcentajes de disparo usen
            # la maceta realmente configurada.
            p.vwc_cc = p.vwc
            retained_ml = p.W_cc
            p.config.p1_vol_cc = retained_ml
            p.config.p2_vol_cc = retained_ml
            p.config.p2_vwc_cc_ref = p.vwc_cc
        p.cc_result = {
            "completed": bool(detected_drainage), "reason": reason,
            "vwc_cc": p.vwc_cc, "retained_ml": p.W_cc,
            "water_applied_ml": test["total_ml"], "pot_volume_ml": p.pot_ml,
        }
        p.cc_test = None

    def _process_cc_test(self, p, dt_seconds):
        test = p.cc_test
        if not test:
            return False
        if test["state"] == "PULSO":
            p.valve_cmd = True
            test["elapsed"] += dt_seconds
            if test["elapsed"] >= test["pulse_seconds"]:
                test["total_ml"] += test["pulse_ml"]
                p.valve_cmd = False
                test["state"] = "ESPERA"
                test["elapsed"] = 0.0
        else:
            p.valve_cmd = False
            if p.tray_weight_ml > test["initial_tray_g"] + 5.0:
                self._finish_cc_test(p, True, "drenaje_detectado")
                return False
            test["elapsed"] += dt_seconds
            if test["total_ml"] >= test["hard_limit_ml"]:
                self._finish_cc_test(p, False, "limite_duro_70pct")
                return False
            if test["elapsed"] >= test["wait_seconds"]:
                test["state"] = "PULSO"
                test["elapsed"] = 0.0
        return True

    def set_auto_tray_service(self, enabled):
        """Habilita un equipo externo que vacía y tara una vez al fin de P2."""
        self.auto_empty_and_tare_end_p2 = bool(enabled)

    def run_end_p2_tray_service(self):
        """Servicio diario seguro: vaciado físico primero, TA solamente en cero."""
        if not self.auto_empty_and_tare_end_p2 or self.fase_riego_actual() != "ESPERA":
            return False
        day = int(self.sim_time_seconds / 86400.0)
        if self.last_tray_service_day == day:
            return False
        # Se ejecuta luego de que la lógica cerró los disparos P2 y la física
        # actualizó el feedback de válvula; así no se vacía mientras se riega.
        if any(p.valve_open or p.valve_cmd for p in self.plants):
            return False
        for plant_id in range(len(self.plants)):
            self.empty_tray_manually(plant_id)
            self.tare_tray(plant_id)
        self.last_tray_service_day = day
        self.hay_inundacion_critica()
        return True

    def hay_inundacion_critica(self):
        """Replica la recuperación automática de LogicaRiego::auditarValvulas.

        Con válvula cerrada, el firmware limpia ERR_INUNDACION_CRITICA cuando
        la lectura tarada vuelve a quedar dentro de CB. Una TA con agua puede
        por tanto ocultar la carga: el simulador lo conserva y lo señala.
        """
        for p in self.plants:
            if p.tray_weight_ml > p.config.capacidad_bandeja_ml:
                p.inundacion_critica = True
            elif p.inundacion_critica and not p.valve_cmd and not p.valve_open:
                p.inundacion_critica = False
            if p.inundacion_critica:
                return True
        return False

    def execute_automatic_irrigation_logic(self, dt_sim_seconds):
        """
        Lógica exacta del firmware NodoControl (EjecucionRiegoP1.cpp y EjecucionRiegoP2.cpp).
        Evalúa ventanas horarias, intervalos de reposo y condiciones de corte para las 12 plantas.
        """
        if not self.auto_irrigation_mode:
            return

        # bloqueoEmergenciaGlobal(): ante bandeja excedida el firmware corta
        # bombas/válvulas y no admite nuevos disparos hasta vaciarla.
        if self.hay_inundacion_critica():
            self.close_all_valves()
            return

        h = self.hour_of_day
        fase_global = self.fase_riego_actual()

        any_valve_active_p1 = False
        any_valve_active_p2 = False

        for p in self.plants:
            cfg = p.config
            if p.cc_test:
                continue
            
            # ── Detección de transición de fase (réplica LogicaRiego.cpp:144-154) ──────
            # Al entrar en cada nueva fase, el firmware resetea el contador de runoff
            # de esa fase y limpia flags de hysteresis asociados.
            fase_planta = fase_global  # La fase global aplica a todas las plantas
            if p._prev_fase_global != fase_planta:
                if fase_planta == "P1":
                    p.runoff_fase_p1 = 0.0
                    p.p2_esperando_piso = False   # Limpia hysteresis para el nuevo ciclo P2 del día siguiente
                elif fase_planta == "P2":
                    p.runoff_fase_p2 = 0.0
                    p.p2_esperando_piso = True    # Firmware: p2EsperandoPiso = true al entrar en P2
                    p.p2_ec_objetivo_alcanzado = False  # Firmware: p2EcObjetivoAlcanzado = false al entrar P2
                elif fase_planta == "P3":
                    p.runoff_fase_p3 = 0.0
                p._prev_fase_global = fase_planta

            # 1. Si está en medio de un disparo, actualizar temporizador
            if p.disparo_activo:
                # El firmware aborta un disparo al cambiar de fase o ante una
                # bandeja llena, en vez de continuarlo con otra bomba.
                if (p.disparo_fase != "MANUAL" and p.disparo_fase != fase_global) or p.inundacion_critica:
                    self._cerrar_disparo(p)
                    continue
                p.disparo_timer_segundos += dt_sim_seconds
                if p.disparo_timer_segundos >= p.disparo_duracion_total:
                    self._cerrar_disparo(p)
                else:
                    p.valve_cmd = True
                    if p.disparo_fase == "MANUAL" or self.bomba_para_fase(p.disparo_fase) == "P1": any_valve_active_p1 = True
                    else: any_valve_active_p2 = True
                continue

            # 2. Evaluación de inicio de nuevo disparo según la fase
            tiempo_desde_ultimo_min = (self.sim_time_seconds - p.ultimo_disparo_sim_time) / 60.0

            if p.inundacion_critica:
                continue

            if fase_global == "P1":
                # FASE P1: Subida matutina hacia CC
                # La subida matutina se activa si VWC cayó bajo el umbral de dryback (p1_vwc_min)
                # o si la EC se concentró durante la noche (ec > p1_ec_target).
                # Una vez activada, continúa apilando shots cada intervalo hasta
                # alcanzar Capacidad de Campo y generar el runoff objetivo (cfg.p1_runoff_max).
                intervalo_ok = tiempo_desde_ultimo_min >= cfg.p1_tiempo_entre_min
                runoff_ok = p.runoff_fase_p1 < cfg.p1_runoff_max  # ← firmware: runoffFaseP1 < p1RunoffMax

                if p.vwc <= cfg.p1_vwc_min or p.ec > cfg.p1_ec_target:
                    p.fase_p1_iniciada = True
                if p.runoff_fase_p1 >= cfg.p1_runoff_max or p.vwc >= p.vwc_cc + 1.0:
                    p.fase_p1_iniciada = False

                necesita_subir = getattr(p, 'fase_p1_iniciada', False) and runoff_ok and (p.vwc < p.vwc_cc + 1.0)

                if intervalo_ok and necesita_subir:
                    # Iniciar disparo P1 — volumen escalado por factor VPD (EjecucionRiegoP1.cpp)
                    # volDisparo = volBase * obtenerFactorVPD(i); rango 0.75-1.25
                    vpd_factor = max(0.75, min(1.25, self.vpd / 1.1))
                    duracion_s = cfg.calc_shot_duration_seconds(cfg.p1_shot_pc, cfg.p1_vol_cc) * vpd_factor
                    p.disparo_activo = True
                    p.disparo_timer_segundos = 0.0
                    p.disparo_duracion_total = duracion_s
                    p.disparo_fase = "P1"
                    p.valve_cmd = True
                    any_valve_active_p1 = True
                    p.fase_actual_planta = "P1_DISPARANDO"

            elif fase_global == "P2":
                # FASE P2: Mantenimiento diurno
                # Histéresis firmware: p2EsperandoPiso se activa cuando VWC >= p2VwcMax
                # y se limpia cuando VWC <= p2VwcMin. No dispara mientras está activa.
                if p.vwc >= cfg.p2_vwc_max:
                    p.p2_esperando_piso = True
                elif p.vwc <= cfg.p2_vwc_min:
                    p.p2_esperando_piso = False

                intervalo_ok = tiempo_desde_ultimo_min >= cfg.p2_tiempo_entre_min
                # Histéresis real (EjecucionRiegoP2.cpp:102): una vez que el VWC toca el
                # piso (p2_vwc_min) el flag se limpia y se siguen apilando disparos, uno
                # por intervalo, hasta alcanzar p2_vwc_max. NO se exige VWC < piso en cada
                # disparo: eso cortaba la subida tras el primero.
                # ── CORRECCIÓN CRÍTICA ──────────────────────────────────────────────────
                # Usar runoff_fase_p2 (INDEPENDIENTE del runoff de P1).
                # El firmware resetea runoffFaseP2=0 al entrar en FASE_P2_MANTENIMIENTO
                # (LogicaRiego.cpp:149) y NO comparte el acumulado de P1.
                runoff_ok = p.runoff_fase_p2 < cfg.p2_runoff_max
                # El campo p2_ec_target (token W) en el firmware es p2EcObj, cuyo valor
                # por defecto es 9.0 mS/cm (diccionario.cpp:64) — un límite catastrófico.
                # Nunca debe bloquear el riego cuando el sustrato se concentra normalmente
                # (0.5 mS/cm de concentración natural). Removido el check ec_ok del disparo
                # principal; EC sólo se usa para activar End-Flush o p2EcObjetivoAlcanzado.
                ec_objetivo_ok = not getattr(p, 'p2_ec_objetivo_alcanzado', False)

                if (not p.p2_esperando_piso and intervalo_ok
                        and runoff_ok and ec_objetivo_ok):
                    # vwcPrevisto (EjecucionRiegoP2.cpp): proyección antes del disparo
                    # Si el shot dispararía el sustrato por encima de p2VwcMax, se bloquea.
                    shot_ml = cfg.p2_vol_cc * (cfg.p2_shot_pc / 100.0)
                    vwc_previsto = p.vwc + (shot_ml / max(p.pot_ml, 1.0)) * 100.0
                    if vwc_previsto > cfg.p2_vwc_max:
                        continue  # Bloquear: el shot subiría demasiado el VWC
                    # VPD factor aplica también a P2
                    vpd_factor = max(0.75, min(1.25, self.vpd / 1.1))
                    duracion_s = cfg.calc_shot_duration_seconds(cfg.p2_shot_pc, cfg.p2_vol_cc) * vpd_factor
                    p.disparo_activo = True
                    p.disparo_timer_segundos = 0.0
                    p.disparo_duracion_total = duracion_s
                    p.disparo_fase = "P2"
                    p.valve_cmd = True
                    any_valve_active_p2 = True
                    p.fase_actual_planta = "P2_DISPARANDO"

            elif fase_global == "P3":
                # FASE P3: Dryback nocturno
                # Solo salvataje nocturno: bloqueado al inicio y al final de P3
                # para conservar el dryback y evitar sustrato mojado al amanecer.
                # Firmware: usa runoffFaseP3 < p1RunoffMax (EjecucionRiegoP2.cpp:121)
                intervalo_ok = tiempo_desde_ultimo_min >= 60.0  # Mínimo 1 hora entre emergencias
                horas_desde_fin_p2 = (h - self.p2_fin_hour) % 24.0
                horas_hasta_luces = (self.light_on_hour - h) % 24.0
                salvataje_permitido = horas_desde_fin_p2 >= 2.0 and horas_hasta_luces > 2.0
                runoff_p3_ok = p.runoff_fase_p3 < cfg.p1_runoff_max  # firmware usa p1RunoffMax para P3
                if p.vwc < cfg.p3_vwc_min and intervalo_ok and salvataje_permitido and runoff_p3_ok:
                    duracion_s = cfg.calc_shot_duration_seconds(cfg.p2_shot_pc * 0.7, cfg.p2_vol_cc)
                    p.disparo_activo = True
                    p.disparo_timer_segundos = 0.0
                    p.disparo_duracion_total = duracion_s
                    p.disparo_fase = "P3"
                    p.valve_cmd = True
                    any_valve_active_p1 = True
                    p.fase_actual_planta = "P3_EMERGENCIA"


        # Activar bomba si hay alguna válvula abierta
        self.pump_p1 = any_valve_active_p1 or any(p.cc_test for p in self.plants)
        self.pump_p2 = any_valve_active_p2

    def step(self, dt_real_seconds):
        if self.is_paused: return
        dt_sim_seconds = dt_real_seconds * self.time_speed
        prev_day = int(self.sim_time_seconds / 86400.0)
        self.sim_time_seconds += dt_sim_seconds
        new_day = int(self.sim_time_seconds / 86400.0)
        
        # Reiniciar runoff diario al amanecer (igual que el firmware en cada nuevo día)
        if new_day != prev_day:
            for p in self.plants:
                p.tray_runoff_day = 0.0
                p.fase_p1_iniciada = False
                # Los contadores de fase también arrancan en cero con cada nuevo día
                p.runoff_fase_p1 = 0.0
                p.runoff_fase_p2 = 0.0
                p.runoff_fase_p3 = 0.0
                p._prev_fase_global = None
            self.review_crop_plan()

        # 1. Ejecutar el controlador de riego automático
        self.execute_automatic_irrigation_logic(dt_sim_seconds)
        
        # 2. Clima
        target_t = self.target_temp_day if self.is_day else self.target_temp_night
        target_rh = self.target_rh_day if self.is_day else self.target_rh_night
        inertia = min(1.0, dt_sim_seconds / 1800.0)
        self.air_temp += (target_t - self.air_temp) * inertia
        self.air_rh += (target_rh - self.air_rh) * inertia
        
        # 3. Física de las 12 plantas
        fase_global = self.fase_riego_actual()
        photoperiod_hours = (self.light_off_hour - self.light_on_hour) % 24.0
        for p in self.plants:
            # Exposición a la luz según posición en la mesa:
            # Centro (distancia pequeña del origen) = 1.0; esquinas = 0.85
            dist_center = math.sqrt(p.grid_x**2 + p.grid_z**2)
            light_exposure = max(0.82, 1.0 - (dist_center * 0.18))
            
            self._process_cc_test(p, dt_sim_seconds)
            pump_name = "P1" if p.cc_test else (self.bomba_para_fase(p.disparo_fase) if p.disparo_fase else None)
            pump_active = ((pump_name == "P1" and self.pump_p1)
                           or (pump_name == "P2" and self.pump_p2))
            solution = self.solution_p1 if pump_name == "P1" else self.solution_p2
            p.step(
                dt_seconds=dt_sim_seconds,
                is_day=self.is_day,
                vpd=self.vpd,
                temp=self.air_temp,
                pump_active=pump_active,
                solution=solution,
                light_exposure=light_exposure,
                fase_global=fase_global,
                photoperiod_hours=photoperiod_hours
            )
            if self.tray_passive_drain_enabled:
                p.drain_tray_passively(dt_sim_seconds, self.tray_drain_tau_seconds)

        # Automatización opcional del invernadero, distinta del firmware de
        # riego: al final de P2 deja cada bandeja físicamente vacía y la tara.
        self.run_end_p2_tray_service()
            
        # 4. Telemetría de historial (se guarda la planta seleccionada)
        self.history_timer += dt_sim_seconds
        if self.history_timer >= 600.0 or len(self.history) == 0:
            self.history_timer = 0.0
            sel_p = self.plants[self.selected_plant_id]
            self.history.append({
                "sim_time": self.sim_time_seconds,
                "hour": round(self.hour_of_day, 2),
                "day": int(self.sim_time_seconds / 86400.0) + 1,
                "is_day": self.is_day,
                "temp": round(self.air_temp, 1),
                "rh": round(self.air_rh, 1),
                "vpd": round(self.vpd, 2),
                "ppfd": self.current_ppfd,
                "p0_vwc": round(sel_p.vwc, 1),
                "p0_ec": round(sel_p.ec, 2),
                "p0_ph": round(sel_p.ph, 2),
                "p1_input_ec": self.solution_p1.ec,
                "p2_input_ec": self.solution_p2.ec,
                "p0_tray_g": round(sel_p.tray_ml, 1),
                "pump_p1": self.pump_p1,
                "pump_p2": self.pump_p2,
                "valve_0": sel_p.valve_open
            })
            if len(self.history) > self.history_max:
                self.history.pop(0)

    def trigger_shot(self, plant_id=0, duration_seconds=15):
        """Disparo manual forzado."""
        if 0 <= plant_id < len(self.plants):
            p = self.plants[plant_id]
            if self.hay_inundacion_critica():
                return False
            p.disparo_activo = True
            p.disparo_timer_segundos = 0.0
            p.disparo_duracion_total = duration_seconds
            p.disparo_fase = "MANUAL"
            p.valve_cmd = True
            self.pump_p1 = True
            return True
        return False

    def close_all_valves(self):
        for p in self.plants:
            self._cerrar_disparo(p)
        self.pump_p1 = False
        self.pump_p2 = False

    def transplant_plant(self, plant_id, new_pot_ml):
        """Trasplanta la planta a una maceta más grande sin resetear su biología.
        
        Réplica de la acción física del cultivador: la planta (misma edad, altura,
        biomasa y estado biológico) se mueve a una maceta mayor. El volumen de agua
        y sales se preserva. Los vol_cc de P1/P2 se recalculan para la nueva maceta.
        """
        if not 0 <= plant_id < len(self.plants):
            return False
        p = self.plants[plant_id]
        new_pot_ml = max(200.0, float(new_pot_ml))
        if new_pot_ml <= p.pot_ml:
            return False  # No trasplantar a maceta más pequeña
        # Escalar el agua proporcionalmente a la nueva maceta para mantener el VWC actual
        old_vwc = p.vwc
        p.pot_ml = new_pot_ml
        p.W = new_pot_ml * old_vwc / 100.0  # Preservar el VWC
        # Actualizar vol_cc basado en el sustrato y la nueva maceta
        retained_ml = p.W_cc
        p.config.p1_vol_cc = retained_ml
        p.config.p2_vol_cc = retained_ml
        # Actualizar el perfil de la etapa activa
        p._save_stage_profile()
        # Resetear estrés por rootbound — la raíz ya tiene espacio
        p.biology.root_health = min(100.0, p.biology.root_health + 20.0)
        return True

    def update_plant_config(self, plant_id, cfg_data):
        """Actualiza la configuración de riego de una planta desde la UI."""
        if 0 <= plant_id < len(self.plants):
            p = self.plants[plant_id]
            cfg = p.config
            # P1
            if "p1_vwc_min" in cfg_data: cfg.p1_vwc_min = float(cfg_data["p1_vwc_min"])
            if "p1_ec_target" in cfg_data: cfg.p1_ec_target = float(cfg_data["p1_ec_target"])
            if "p1_runoff_max" in cfg_data: cfg.p1_runoff_max = float(cfg_data["p1_runoff_max"])
            if "p1_vol_cc" in cfg_data: cfg.p1_vol_cc = float(cfg_data["p1_vol_cc"])
            if "p1_shot_pc" in cfg_data: cfg.p1_shot_pc = float(cfg_data["p1_shot_pc"])
            if "p1_tiempo_entre_min" in cfg_data: cfg.p1_tiempo_entre_min = int(cfg_data["p1_tiempo_entre_min"])
            if "caudal_gotero_lh" in cfg_data: cfg.caudal_gotero_lh = float(cfg_data["caudal_gotero_lh"])
            if "goteros_planta" in cfg_data: cfg.goteros_planta = int(cfg_data["goteros_planta"])
            if "pot_volume_ml" in cfg_data:
                new_pot_ml = max(100.0, float(cfg_data["pot_volume_ml"]))
                p.W *= new_pot_ml / p.pot_ml
                p.pot_ml = new_pot_ml
                retained_ml = p.W_cc
                cfg.p1_vol_cc = retained_ml
                cfg.p2_vol_cc = retained_ml
            if "vwc_cc" in cfg_data:
                p.vwc_cc = max(1.0, min(95.0, float(cfg_data["vwc_cc"])))
                retained_ml = p.W_cc
                cfg.p1_vol_cc = retained_ml
                cfg.p2_vol_cc = retained_ml
                cfg.p2_vwc_cc_ref = p.vwc_cc
            if "capacidad_bandeja_ml" in cfg_data:
                cfg.capacidad_bandeja_ml = float(cfg_data["capacidad_bandeja_ml"])
                if self.plants[plant_id].tray_weight_ml > cfg.capacidad_bandeja_ml:
                    self.plants[plant_id].inundacion_critica = True
            # P2 & P3
            if "p2_vwc_max" in cfg_data: cfg.p2_vwc_max = float(cfg_data["p2_vwc_max"])
            if "p2_ec_target" in cfg_data: cfg.p2_ec_target = float(cfg_data["p2_ec_target"])
            if "p2_vwc_min" in cfg_data: cfg.p2_vwc_min = float(cfg_data["p2_vwc_min"])
            if "p2_runoff_max" in cfg_data: cfg.p2_runoff_max = float(cfg_data["p2_runoff_max"])
            if "p2_shot_pc" in cfg_data: cfg.p2_shot_pc = float(cfg_data["p2_shot_pc"])
            if "p2_tiempo_entre_min" in cfg_data: cfg.p2_tiempo_entre_min = int(cfg_data["p2_tiempo_entre_min"])
            if "p3_vwc_min" in cfg_data: cfg.p3_vwc_min = float(cfg_data["p3_vwc_min"])
            if "substrate_ph" in cfg_data: p.ph = float(cfg_data["substrate_ph"])
            p._save_stage_profile()

    def update_solution(self, pump, data):
        solution = self.solution_p1 if pump == "P1" else self.solution_p2
        for field, low, high in (("ec", 0.0, 6.0), ("ph", 4.5, 7.5), ("n", 0.0, 400.0), ("p", 0.0, 200.0), ("k", 0.0, 500.0)):
            if field in data:
                setattr(solution, field, max(low, min(high, float(data[field]))))
        # Lo editado sobre la receta activa también queda como receta de la etapa de sala.
        self.stage_solutions[self.room_stage]["p1" if pump == "P1" else "p2"] = solution.as_dict()

    def update_schedule(self, data):
        """Edita el horario de la etapa activa de la sala y lo guarda en su receta de etapa."""
        if "p0_minutes" in data: self.p1_offset_min = max(0.0, float(data["p0_minutes"]))
        if "p2_inicio" in data: self.p2_inicio_hour = float(data["p2_inicio"])
        if "p2_fin" in data: self.p2_fin_hour = float(data["p2_fin"])
        if "luces_on" in data: self.light_on_hour = float(data["luces_on"])
        if "luces_off" in data: self.light_off_hour = float(data["luces_off"])
        self.stage_schedules[self.room_stage] = {
            "light_on_hour": self.light_on_hour, "light_off_hour": self.light_off_hour,
            "p1_offset_min": self.p1_offset_min, "p2_inicio_hour": self.p2_inicio_hour,
            "p2_fin_hour": self.p2_fin_hour}

    def update_stage_schedule(self, stage, data):
        """Edita el horario de CUALQUIER etapa (se aplica al entrar la sala en ella)."""
        if stage not in MANAGEMENT_STAGES:
            return False
        if stage == self.room_stage:
            self.update_schedule(data)
            return True
        sched = self.stage_schedules[stage]
        for key, src, low in (("light_on_hour", "luces_on", 0.0), ("light_off_hour", "luces_off", 0.0),
                              ("p1_offset_min", "p0_minutes", 0.0), ("p2_inicio_hour", "p2_inicio", 0.0),
                              ("p2_fin_hour", "p2_fin", 0.0)):
            if src in data:
                sched[key] = max(low, float(data[src]))
        return True

    def update_crop_plan(self, stage, min_days=None, max_days=None, biomass_target_g=None):
        """Configura cuándo se abandona una etapa. Valida max >= min >= 0 y biomasa >= 0."""
        if stage not in MANAGEMENT_STAGES or NEXT_STAGE[stage] is None:
            return False
        plan = self.crop_plan[stage]
        new_min = plan["min_days"] if min_days is None else float(min_days)
        new_max = plan["max_days"] if max_days is None else float(max_days)
        new_bio = plan["biomass_target_g"] if biomass_target_g is None else float(biomass_target_g)
        if new_min < 0 or new_bio < 0 or new_max < new_min:
            return False
        plan.update({"min_days": new_min, "max_days": new_max, "biomass_target_g": new_bio})
        return True

    def update_stage_profile(self, plant_id, stage, cfg_data):
        """Edita el perfil de riego guardado de una etapa sin activarla."""
        if not 0 <= plant_id < len(self.plants) or stage not in MANAGEMENT_STAGES:
            return False
        p = self.plants[plant_id]
        if stage == p.management_stage:
            self.update_plant_config(plant_id, cfg_data)
            return True
        prof = p.stage_profiles[stage]
        for key, val in cfg_data.items():
            if key in prof:
                prof[key] = type(prof[key])(float(val)) if isinstance(prof[key], (int, float)) else val
        return True

    def _crop_focus(self, p):
        """Bloque 'plan de cultivo' de la planta enfocada para la UI."""
        st = self.plant_stage_status(p)
        out = {"stage": p.management_stage, "day": round(p.biology.age_days, 1),
               "stage_days": round(p.stage_days, 1), "flowering_type": p.biology.flowering_type,
               "biomass_g": round(p.biology.biomass_g, 1), "room_stage": self.room_stage,
               "next_stage": None}
        if st:
            nxt_pot = float(p.stage_profiles[st["next"]]["pot_volume_ml"])
            will_transplant = nxt_pot > p.pot_ml
            waiting_for_room = (p.management_stage == "CRECIMIENTO" and st["ready"])
            out.update({"next_stage": st["next"], "min_days": st["min_days"], "max_days": st["max_days"],
                        "biomass_target_g": st["biomass_target_g"], "days_ok": st["days_ok"],
                        "biomass_ok": st["biomass_ok"], "ready": st["ready"], "forced": st["forced"],
                        "will_transplant": will_transplant,
                        "pot_next_ml": max(nxt_pot, p.pot_ml),
                        "waiting_for_room": waiting_for_room})
        return out

    def dump_state(self):
        """Exporta el estado completo de las 12 plantas y el foco actual."""
        sel_p = self.plants[self.selected_plant_id]
        bio = sel_p.biology
        cfg = sel_p.config
        
        days_sim = int(self.sim_time_seconds / 86400.0) + 1
        hours_sim = int((self.sim_time_seconds % 86400.0) / 3600.0)
        mins_sim = int((self.sim_time_seconds % 3600.0) / 60.0)
        
        # Resumen de las 12 plantas para el selector y la mesa 3D
        plants_summary = []
        for p in self.plants:
            st = self.plant_stage_status(p)
            plants_summary.append({
                "stage_days": round(p.stage_days, 1),
                "biomass_g": round(p.biology.biomass_g, 1),
                "crop_ready": bool(st and st["ready"]),
                "crop_forced": bool(st and st["forced"]),
                "pot_volume_ml": p.pot_ml,
                "id": p.id,
                "strain": p.biology.strain_name,
                "stage": p.biology.stage,
                "vwc": round(p.vwc, 1),
                "ec": round(p.ec, 2),
                "tray_weight_g": round(p.tray_weight_ml, 1),
                "tray_actual_ml": round(p.tray_ml, 1),
                "cc_testing": bool(p.cc_test),
                "management_stage": p.management_stage,
                "substrate_type": p.substrate_type,
                "valve_open": p.valve_open,
                "inundacion_critica": p.inundacion_critica,
                "irrigation_phase": p.fase_actual_planta,
                "grid_x": p.grid_x,
                "grid_z": p.grid_z,
                "height_cm": round(p.biology.height_cm, 1),
                "root_health": round(p.biology.root_health, 0)
            })

        return {
            "clock": {
                "time_str": f"Día {days_sim:02d} - {hours_sim:02d}:{mins_sim:02d}",
                "sim_time_seconds": self.sim_time_seconds,
                "day_number": days_sim,
                "hour": self.hour_of_day,
                "is_day": self.is_day,
                "time_speed": self.time_speed,
                "is_paused": self.is_paused,
                "auto_mode": self.auto_irrigation_mode
            },
            "environment": {
                "air_temp": round(self.air_temp, 1),
                "air_rh": round(self.air_rh, 1),
                "vpd": round(self.vpd, 2),
                "ppfd": self.current_ppfd,
                "light_dimmer": self.light_dimmer_pct,
                "co2_ppm": self.co2_ppm,
                "solution_p1": self.solution_p1.as_dict(),
                "solution_p2": self.solution_p2.as_dict(),
                "tray_passive_drain_enabled": self.tray_passive_drain_enabled,
                "tray_drain_tau_seconds": self.tray_drain_tau_seconds,
                "auto_empty_and_tare_end_p2": self.auto_empty_and_tare_end_p2,
                "p2_inicio": self.p2_inicio_hour,
                "p2_fin": self.p2_fin_hour,
                "p0_minutes": self.p1_offset_min,
                "light_on_hour": self.light_on_hour,
                "light_off_hour": self.light_off_hour,
                "irrigation_phase": self.fase_riego_actual()
            },
            "actuators": {
                "pump_p1": self.pump_p1,
                "pump_p2": self.pump_p2,
                "light_on": self.is_day,
                "valve_0": sel_p.valve_open,
                "valve_feedback_0": sel_p.valve_feedback
            },
            "selected_plant_id": self.selected_plant_id,
            "crop_plan": copy.deepcopy(self.crop_plan),
            "room_stage": self.room_stage,
            "stage_schedules": copy.deepcopy(self.stage_schedules),
            "crop_events": list(self.crop_events[-20:]),
            "plants_all": plants_summary,
            "plant_focus": {
                "id": sel_p.id,
                "management_stage": sel_p.management_stage,
                "management_stage_label": STAGE_LABELS[sel_p.management_stage],
                "substrate_type": sel_p.substrate_type,
                "substrate_label": SUBSTRATE_PROFILES[sel_p.substrate_type]["label"],
                "vwc": round(sel_p.vwc, 1),
                "ec": round(sel_p.ec, 2),
                "ph": round(sel_p.ph, 2),
                "npk_ppm": {"n": round(sel_p.n_ppm, 0), "p": round(sel_p.p_ppm, 0), "k": round(sel_p.k_ppm, 0)},
                "tray_weight_g": round(sel_p.tray_weight_ml, 1),
                "tray_actual_ml": round(sel_p.tray_ml, 1),
                "tray_tare_ml": round(sel_p.tray_tare_ml, 1),
                "tare_with_water": sel_p.tare_with_water,
                "water_ml": round(sel_p.W, 1),
                "pot_volume_ml": sel_p.pot_ml,
                "vwc_cc": round(sel_p.vwc_cc, 1),
                "total_irrigated_ml": round(sel_p.total_irrigation_ml, 1),
                "total_drain_ml": round(sel_p.total_drain_ml, 1),
                "cc_test": sel_p.cc_test,
                "cc_result": sel_p.cc_result,
                # Runoff acumulado por fase (equivalente firmware runoffFaseP1/P2/P3)
                "runoff_fase_p1": round(sel_p.runoff_fase_p1, 1),
                "runoff_fase_p2": round(sel_p.runoff_fase_p2, 1),
                "runoff_fase_p3": round(sel_p.runoff_fase_p3, 1),
                "p2_ec_objetivo_alcanzado": getattr(sel_p, 'p2_ec_objetivo_alcanzado', False),
                "tray_runoff_day": round(sel_p.tray_runoff_day, 1),
                "inundacion_critica": sel_p.inundacion_critica,
                "p2_esperando_piso": sel_p.p2_esperando_piso,
                "irrigation_phase": sel_p.fase_actual_planta,
                # Biología
                "stage": bio.stage,
                "strain": bio.strain_name,
                "age_days": round(bio.age_days, 1),
                "height_cm": round(bio.height_cm, 1),
                "biomass_g": round(bio.biomass_g, 1),
                "root_health_pct": round(bio.root_health, 0),
                "buds_size_pct": round(bio.buds_size * 100.0, 1),
                "trichomes_amber_pct": round(bio.trichomes_amber * 100.0, 1),
                # Crop Steering
                "steering_type": bio.steering_type,
                "flower_induced": bio.flower_induced,
                "flower_induction_day": round(bio.flower_induction_day, 1),
                "visuals": {
                    "leaf_droop": round(bio.leaf_droop, 2),
                    "tip_burn": round(bio.tip_burn, 2),
                    "leaf_claw": round(bio.leaf_claw, 2),
                    "chlorosis": round(bio.chlorosis, 2),
                    "buds_size": round(bio.buds_size, 2),
                    "stem_purple": round(bio.stem_purple, 2)
                },
                "stage_profiles": {k: dict(v) for k, v in sel_p.stage_profiles.items()},
                "crop": self._crop_focus(sel_p),
                # Configuración activa de esta planta
                "config": {
                    "caudal_gotero_lh": cfg.caudal_gotero_lh,
                    "goteros_planta": cfg.goteros_planta,
                    "capacidad_bandeja_ml": cfg.capacidad_bandeja_ml,
                    "tray_capacity_recommended_ml": round(cfg.tray_capacity_recommended_ml, 1),
                    "pot_volume_ml": sel_p.pot_ml,
                    "vwc_cc": sel_p.vwc_cc,
                    "p1_vwc_min": cfg.p1_vwc_min,
                    "p1_ec_target": cfg.p1_ec_target,
                    "p1_runoff_max": cfg.p1_runoff_max,
                    "p1_vol_cc": cfg.p1_vol_cc,
                    "p1_shot_pc": cfg.p1_shot_pc,
                    "p1_tiempo_entre_min": cfg.p1_tiempo_entre_min,
                    "p2_vwc_max": cfg.p2_vwc_max,
                    "p2_vwc_cc_ref": cfg.p2_vwc_cc_ref,
                    "p2_vwc_min": cfg.p2_vwc_min,
                    "p2_ec_target": cfg.p2_ec_target,
                    "p2_runoff_max": cfg.p2_runoff_max,
                    "p2_vol_cc": cfg.p2_vol_cc,
                    "p2_shot_pc": cfg.p2_shot_pc,
                    "p2_tiempo_entre_min": cfg.p2_tiempo_entre_min,
                    "p3_vwc_min": cfg.p3_vwc_min,
                    "substrate_ph": round(sel_p.ph, 2)
                }
            },
            "history": self.history[-60:]
        }
