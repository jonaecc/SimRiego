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

from substrate import PotColumn, SUBSTRATE_VG


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
        p2_vwc_min=44.0, p2_vwc_max=50.0, p2_vwc_cc_ref=60.0, p2_ec_target=9.0, p2_runoff_max=100.0, p2_vol_cc=300.0, p2_shot_pc=4.0, p2_tiempo_entre_min=20, p3_vwc_min=25.0),
    # Crecimiento: maceta de 5 L, goteros dobles, dryback diurno moderado (Vegetativo)
    "CRECIMIENTO": stage_profile(5000.0, 60.0, 15.0, capacidad_bandeja_ml=1200.0,
        caudal_gotero_lh=3.0, goteros_planta=2,
        p1_vwc_min=40.0, p1_ec_target=2.8, p1_runoff_max=300.0, p1_vol_cc=3000.0, p1_shot_pc=4.5, p1_tiempo_entre_min=15,
        p2_vwc_min=48.0, p2_vwc_max=51.0, p2_vwc_cc_ref=54.0, p2_ec_target=9.0, p2_runoff_max=450.0, p2_vol_cc=3000.0, p2_shot_pc=1.5, p2_tiempo_entre_min=25, p3_vwc_min=30.0),
    # Floración: maceta de 15 L, dryback más agresivo (generativo), fotoperiodo 12/12
    # Con la transpiración realista una planta madura consume ~2.8 L/día: una maceta
    # de 5 L se seca hasta ~15% VWC, así que el trasplante a 15 L ocurre al pasar a
    # floración. Si se configura una maceta igual o menor, se conserva la actual.
    "FLORACION": stage_profile(15000.0, 60.0, 15.0, capacidad_bandeja_ml=2400.0,
        caudal_gotero_lh=3.0, goteros_planta=2,
        p1_vwc_min=38.0, p1_ec_target=5.0, p1_runoff_max=900.0, p1_vol_cc=9000.0, p1_shot_pc=4.0, p1_tiempo_entre_min=15,
        p2_vwc_min=41.0, p2_vwc_max=44.0, p2_vwc_cc_ref=50.0, p2_ec_target=9.0, p2_runoff_max=1350.0, p2_vol_cc=9000.0, p2_shot_pc=1.5, p2_tiempo_entre_min=15, p3_vwc_min=24.0),
}


STAGE_SCHEDULES = {
    "PLANTULA": {"light_on_hour": 6.0, "light_off_hour": 24.0, "p1_offset_min": 60.0, "p2_inicio_hour": 11.0, "p2_fin_hour": 20.0},
    "CRECIMIENTO": {"light_on_hour": 6.0, "light_off_hour": 24.0, "p1_offset_min": 60.0, "p2_inicio_hour": 11.0, "p2_fin_hour": 20.0},
    "FLORACION": {"light_on_hour": 6.0, "light_off_hour": 18.0, "p1_offset_min": 60.0, "p2_inicio_hour": 10.5, "p2_fin_hour": 17.5},
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

# VWC (%) con el que arranca el día 1 de la simulación.
INITIAL_VWC_PCT = 20.0

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

# ---------------------------------------------------------------------------
# EVAPOTRANSPIRACIÓN (calibrada por superficie)
# ---------------------------------------------------------------------------
# Transpiración con luces ON a VPD 1,1 kPa y exposición 1,0, por m² de hoja.
# 3,0 mL/min/m² ≈ 3,2 L/m²/día a 18 h de luz: una planta vegetativa de 0,37 m²
# gasta ~1,2 L/día y una en floración de ~1,5 m², ~3,5 L/día (12 h de luz).
K_TRANSP_DAY = 3.0             # (ya no se usa: la transpiración sale de Penman-Monteith)
K_TRANSP_NIGHT = 0.45

# Transpiración por Penman-Monteith a escala de dosel (ver el documento de investigación).
#   λE = [Δ·Rn + ρ·cp·VPD·ga] / [Δ + γ·(1 + ga/gc)],   gc = gs · LAI_iluminado
# Todo se calcula por m² de suelo y se multiplica por el área de suelo de la planta.
PM_LIGHT_ABSORPTION = 0.85      # fracción de la luz LED que absorbe un dosel cerrado
PM_NET_LONGWAVE_W_M2 = 15.0     # pérdida neta de onda larga de un dosel cerrado [W/m²]
PM_K_EXT = 0.6                  # coeficiente de extinción de luz del dosel
PM_GS_MOL_BASE = 0.3715         # gs de hoja [mol/m²/s] = BASE + SLOPE·PPFD (cannabis, medido)
PM_GS_MOL_SLOPE = 1.16e-4
PM_GS_MOL_NIGHT = 0.05          # estomas casi cerrados de noche (a calibrar)
PM_MOL_TO_M_S = 0.0245          # 1 mol/m²/s de gas ≈ 0,0245 m/s a 25 °C
PM_VPD_KNEE_KPA = 1.5           # sobre este VPD los estomas cierran 20 %/kPa (a calibrar)
PM_CO2_GS_SLOPE = 0.42 / 400.0  # gs −42 % al subir de 350 a 750 ppm (Chandra et al. 2008)


def transpiration_ml_min(leaf_area_m2, footprint_m2, ppfd, vpd_kpa, temp_c, co2_ppm,
                         aero_cond_m_s, is_day, water_stress=1.0):
    """Transpiración de UNA planta [mL/min] por balance de energía (Penman-Monteith)."""
    footprint_m2 = max(0.01, footprint_m2)
    lai = max(0.0, leaf_area_m2) / footprint_m2
    if lai <= 0.0:
        return 0.0
    f_int = 1.0 - math.exp(-PM_K_EXT * lai)                 # luz interceptada
    lai_lit = f_int / PM_K_EXT                              # LAI iluminado
    rn = (ppfd / 4.57 * PM_LIGHT_ABSORPTION - PM_NET_LONGWAVE_W_M2) * f_int if is_day else 0.0
    rn = max(0.0, rn)
    gs_mol = (PM_GS_MOL_BASE + PM_GS_MOL_SLOPE * ppfd) if is_day else PM_GS_MOL_NIGHT
    gs_mol *= max(0.5, min(1.1, 1.0 - PM_CO2_GS_SLOPE * (co2_ppm - 350.0)))
    gs_mol *= 1.0 / (1.0 + 0.2 * max(0.0, vpd_kpa - PM_VPD_KNEE_KPA))
    gs_mol *= max(0.02, water_stress)
    gc = max(1e-5, gs_mol * PM_MOL_TO_M_S * lai_lit)        # m/s
    es = 0.6108 * math.exp(17.27 * temp_c / (temp_c + 237.3))
    delta = 4098.0 * es / (temp_c + 237.3) ** 2             # kPa/°C
    gamma = 0.0666                                          # kPa/°C a nivel del mar
    rho_cp = 101.3 / (0.287 * (temp_c + 273.15)) * 1.013    # kJ/m³/K
    adv = rho_cp * max(0.0, vpd_kpa) * aero_cond_m_s * 1000.0 * f_int   # W/m²
    lam_e = (delta * rn + adv) / (delta + gamma * (1.0 + aero_cond_m_s / gc))
    liters_m2_s = max(0.0, lam_e) / 2.45e6
    return liters_m2_s * footprint_m2 * 1000.0 * 60.0       # mL/min por planta


# Evaporación directa del sustrato descubierto (≈2 L/m²/día con luces ON).
COL_MIN_SOLVE_S = 15.0     # intervalo mínimo entre resoluciones de Richards sin riego [s]
K_EVAP_DAY = 1.85
K_EVAP_NIGHT = 0.55


# ── Maceta: columna de sustrato (ver substrate.py) ───────────────────────────
# El agua se mueve por Richards (van Genuchten-Mualem) en N capas con drenaje por
# cara de filtración; las sales viajan con el agua. La capacidad de contenedor y su
# gradiente vertical salen de la curva de retención y la altura de la maceta.

def pot_surface_area_m2(pot_ml):
    """Superficie libre de una maceta cilíndrica de altura ≈1,2 × diámetro."""
    d_cm = (max(pot_ml, 50.0) / (math.pi * 0.3)) ** (1.0 / 3.0)
    return math.pi * (d_cm / 2.0) ** 2 / 10000.0


# ── Balance de carbono (fase 2) ─────────────────────────────────────────────
# Todos los parámetros son MANUALES: los valores por defecto salen de la
# literatura (ver documento de investigación) y no cambian solos según el
# régimen de riego ni la etapa. Los marcados "[modelo]" son valores típicos de
# modelos de cultivo, NO medidos en cannabis, y deben calibrarse con datos reales.
CARBON_DEFAULTS = {
    "amax": 11.0,            # Amax EFECTIVA del dosel a 25-30 °C, 350 ppm [µmol CO2/m²/s]: calibrada para RUE aérea ≈0,26-0,30 g/mol (hoja joven expuesta medida ≈24; el promedio de dosel es menor por edad, sombra y N)
    "alpha": 0.060,          # rendimiento cuántico [mol CO2/mol fotones] (0,058-0,068 medido, baja con la edad)
    "k_ext": 0.6,            # coeficiente de extinción de luz (el mismo de Penman-Monteith)
    "t_opt": 27.0,           # temperatura óptima de fotosíntesis [°C]
    "t_width_lo": 14.0,      # ancho de la respuesta bajo el óptimo [°C]
    "t_width_hi": 9.0,       # ancho de la respuesta sobre el óptimo [°C]
    "lai_soft": 3.5,         # LAI desde el que las hojas bajas, sombreadas, empiezan a caer [modelo; LAI medido 2,1-4,4 a 21 d]
    "lai_max": 6.0,          # LAI al que la caída foliar alcanza su máximo [modelo]
    "leaf_turnover_max": 0.06,  # tasa máxima de caída foliar por autosombreo [1/día] [modelo]
    "yg": 0.70,              # eficiencia de crecimiento (g materia seca / g CH2O) [modelo]
    "rm_leaf": 0.030,        # mantenimiento foliar a 25 °C [g CH2O/g/día] [modelo]
    "rm_stem": 0.015,        # mantenimiento de tallo [modelo]
    "rm_root": 0.015,        # mantenimiento de raíz [modelo]
    "rm_flower": 0.010,      # mantenimiento de flor [modelo]
    "q10": 2.0,              # sensibilidad térmica de la respiración [modelo]
    "slw": 45.0,             # peso específico foliar [g seco/m²] (35-58 medido)
    "tau_use_days": 0.25,    # constante de tiempo con que el crecimiento consume la reserva de carbono [d] [modelo]
    "root_frac": 0.22,       # fracción del crecimiento que va a raíz en vegetativo [modelo]
    "leaf_frac_shoot": 0.62, # hoja / (hoja+tallo) del crecimiento aéreo vegetativo (60-63 % medido)
    "flower_sink_max": 0.85, # máxima fracción del crecimiento aéreo destinada a flor [modelo; HI cosecha 0,56-0,73 medido]
    "flower_start_day": 4.0, # días desde el cambio 12/12 hasta el inicio de la formación de flores
    "flower_ramp_days": 14.0,# días en que el sumidero floral sube de 0 a su máximo
    "root_drop_days": 14.0,  # días tras 12/12 en que cae la fracción radicular
    "root_drop": 0.65,       # caída relativa de la fracción radicular al florecer (cae la relación raíz:parte aérea)
    "stretch_days": 18.0,    # duración del estirón tras 12/12 [d]
    "ripening_day": 52.0,    # día tras 12/12 en que empieza la maduración (51-81 medido según genética)
    "senescence_start": 35.0,# día tras 12/12 en que empieza la senescencia foliar
    "senescence_span": 70.0, # días para llegar a senescencia total
    "ssl_cm_per_g": 3.0,     # elongación por masa de tallo [cm/g] [modelo]
    "bud_ref_g": 70.0,       # masa de flor seca que corresponde a buds_size = 1 [g]
}


# ── Nutrición por ion (fase 4) ──────────────────────────────────────────────
# Tejido objetivo [% materia seca]: N y P de hoja y flor medidos en cannabis (hoja N 3,0-3,1 %,
# flor 3,7-3,9 %; P hoja 0,66-0,89 %, flor 1,02-1,13 %); tallo y raíz solo con el orden relativo
# medido (N: hoja > raíz > tallo; P: raíz > hoja > tallo). K NO tiene datos medidos aquí: son valores
# típicos de cultivos [modelo]. Todo es manual y se debe calibrar con análisis foliares propios.
ION_MASS_NO3_PER_N = 62.005 / 14.007      # masa de nitrato por masa de N
ION_MASS_H2PO4_PER_P = 96.987 / 30.974    # masa de dihidrógenofosfato por masa de P
TDS_PER_EC = 640.0                        # mg/L de sales por mS/cm (conversión habitual, aproximada)

NUTRIENT_DEFAULTS = {
    "x_n_leaf": 3.1, "x_n_stem": 1.2, "x_n_root": 2.5, "x_n_flower": 3.8,
    "x_p_leaf": 0.8, "x_p_stem": 0.4, "x_p_root": 0.9, "x_p_flower": 1.1,
    "x_k_leaf": 2.5, "x_k_stem": 2.8, "x_k_root": 2.0, "x_k_flower": 2.3,
    "vmax_n": 40.0,       # absorción activa máxima [mg ion / g raíz seca / día] [modelo]
    "vmax_p": 8.0,
    "vmax_k": 40.0,
    "km_n": 15.0,         # constante de Michaelis [mg/L en el agua de poro] [modelo]
    "km_p": 3.0,
    "km_k": 20.0,
    "catchup_days": 2.0,  # días en que la planta intenta cubrir un déficit de tejido [modelo]
    "luxury": 1.25,       # relación tejido/objetivo sobre la que deja de absorber [modelo]
    "deficit_crit": 0.45, # relación tejido/objetivo a la que la fotosíntesis cae al mínimo [modelo]
    "nh4_fraction": 0.06, # fracción del N absorbido como amonio (7,2 de 120 mg/L N en ensayo de vegetativo)
    "buffer_mmol_l_ph": 25.0, # capacidad tampón del sustrato [mmol H+/L por unidad de pH] [modelo]
}
ION_ORGANS = ("leaf", "stem", "root", "flower")


def canopy_photosynthesis_umol(ppfd, lai, temp_c, co2_ppm, params, stress=1.0):
    """Asimilación bruta del dosel [µmol CO2/m² de suelo/s].

    Hipérbola rectangular por hoja, A = α·I·Amax/(α·I+Amax), con luz que decae
    exponencialmente en el dosel (Beer, I(l)=k·I0·e^(-k·l)). La integral sobre el
    LAI tiene forma cerrada: A_c = (Amax/k)·ln[(α·k·I0+Amax)/(α·k·I0·e^(-k·L)+Amax)].
    Amax responde a temperatura (campana asimétrica) y a CO2 (×1,5 a 750 ppm, ×0,5 a 250 ppm).
    """
    if ppfd <= 0.0 or lai <= 0.0 or stress <= 0.0:
        return 0.0
    p = params
    width = p["t_width_lo"] if temp_c < p["t_opt"] else p["t_width_hi"]
    f_t = math.exp(-((temp_c - p["t_opt"]) / width) ** 2)
    if co2_ppm >= 350.0:
        f_c = 1.0 + 0.5 * (co2_ppm - 350.0) / 400.0
    else:
        f_c = max(0.1, 1.0 - 0.49 * (350.0 - co2_ppm) / 100.0)
    amax = p["amax"] * f_t * f_c * stress
    k = p["k_ext"]
    x0 = p["alpha"] * k * ppfd
    x_bottom = x0 * math.exp(-k * lai)
    return amax / k * math.log((x0 + amax) / (x_bottom + amax))


def _smoothstep(x):
    x = max(0.0, min(1.0, x))
    return x * x * (3.0 - 2.0 * x)


class PlantBiology:
    """Modelo biológico individual: balance de carbono, reparto por órgano y fenología."""
    def __init__(self, plant_id=0, strain_name="OG Kush", vigor_factor=1.0):
        self.id = plant_id
        self.strain_name = strain_name
        # Un cultivo real no mezcla autoflorecientes con fotoperiódicas. Las 12
        # plantas son FOTOPERIODICAS: solo florecen cuando la sala pasa a 12/12.
        self.flowering_type = "FOTOPERIODICA"
        self.vigor_factor = vigor_factor   # Variabilidad genética natural (0.85 a 1.20)
        self.params = dict(CARBON_DEFAULTS)
        self.nutri = dict(NUTRIENT_DEFAULTS)

        self.age_days = 1.0
        self.height_cm = 5.0 * vigor_factor
        self.max_height_cm = 95.0 * vigor_factor
        self._set_organs(2.0 * vigor_factor, root=0.25, leaf=0.55, stem=0.20, flower=0.0)
        self.root_health = 100.0
        self.stage = CropStage.GERMINATION

        # Inducción floral orgánica y fotoperiodo
        self.flower_induced = False
        self.flower_induction_day = 0.0
        self.steering_type = "VEGETATIVO"
        self.leaf_senescence = 0.0

        # Contenido de iones en la planta [mg] y tasa de crecimiento por órgano [g/día] (media móvil)
        self.growth_rate = {o: 0.0 for o in ION_ORGANS}
        self.content = {ion: self.target_content(ion) for ion in "npk"}
        self.nutrient_status = {ion: 1.0 for ion in "npk"}   # contenido / objetivo
        self.nutrient_uptake_total = {ion: 0.0 for ion in "npk"}

        # Reserva de carbono (g CH2O) y contadores de diagnóstico acumulados
        self.reserve_g = 0.0
        self.litter_g = 0.0           # hojarasca acumulada
        self.gpp_total_g = 0.0        # fotosíntesis bruta
        self.resp_total_g = 0.0       # respiración de mantenimiento
        self.par_total_mol = 0.0      # PAR incidente por planta (mol)
        self.a_canopy = 0.0           # última asimilación del dosel [µmol/m²/s]

        # Síntomas visuales (0.0 a 1.0)
        self.leaf_droop = 0.0
        self.tip_burn = 0.0
        self.leaf_claw = 0.0
        self.chlorosis = 0.0
        self.stem_purple = 0.0
        self.buds_size = 0.0
        self.trichomes_amber = 0.0

    # ── Órganos ─────────────────────────────────────────────────────────────
    def _set_organs(self, total, root, leaf, stem, flower):
        s = root + leaf + stem + flower
        self.root_g = total * root / s
        self.leaf_g = total * leaf / s
        self.stem_g = total * stem / s
        self.flower_g = total * flower / s

    @property
    def biomass_g(self):
        """Materia seca total de la planta [g] (hoja + tallo + raíz + flor)."""
        return self.leaf_g + self.stem_g + self.root_g + self.flower_g

    @biomass_g.setter
    def biomass_g(self, value):
        value = max(0.0, float(value))
        total = self.biomass_g
        if total <= 1e-9:
            self._set_organs(value, 0.25, 0.55, 0.20, 0.0)
            return
        f = value / total
        self.root_g *= f
        self.leaf_g *= f
        self.stem_g *= f
        self.flower_g *= f
        for ion in self.content:
            self.content[ion] *= f

    @property
    def leaf_area_m2(self):
        """Área foliar [m²] = masa foliar verde / peso específico foliar (SLW)."""
        return max(0.0, self.leaf_g * (1.0 - self.leaf_senescence) / self.params["slw"])

    @property
    def shoot_g(self):
        return self.leaf_g + self.stem_g + self.flower_g

    @property
    def harvest_index(self):
        return self.flower_g / max(self.shoot_g, 1e-9)

    @property
    def flower_days(self):
        return max(0.0, self.age_days - self.flower_induction_day) if self.flower_induced else 0.0

    # ── Nutrición ───────────────────────────────────────────────────────────
    def organ_mass(self, organ):
        return getattr(self, organ + "_g")

    def tissue_target(self, ion, organ):
        """Concentración objetivo [mg/g materia seca] del ion en el órgano."""
        return 10.0 * self.nutri["x_%s_%s" % (ion, organ)]

    def target_content(self, ion):
        """Contenido del ion [mg] que tendría la planta con todos sus órganos en el objetivo."""
        return sum(self.organ_mass(o) * self.tissue_target(ion, o) for o in ION_ORGANS)

    def tissue_concentration(self, ion):
        """Concentración media del ion en la planta [% materia seca]."""
        return 0.1 * self.content[ion] / max(self.biomass_g, 1e-9)

    def nutrient_demand(self, dt_days):
        """Demanda de cada ion [mg] en dt_days: lo que exige el crecimiento reciente más la recuperación de un déficit."""
        n = self.nutri
        out = {}
        for ion in "npk":
            growth = sum(self.growth_rate[o] * self.tissue_target(ion, o) for o in ION_ORGANS) * dt_days
            target = self.target_content(ion)
            gap = target - self.content[ion]
            catchup = max(0.0, gap) / n["catchup_days"] * dt_days
            demand = growth + catchup
            if self.content[ion] > n["luxury"] * target:
                demand = 0.0
            out[ion] = demand
        return out

    def sync_to_stage(self, stage):
        """Sincroniza la morfología base (solo para resets o inicialización explícita)."""
        p = self.params
        if stage == "PLANTULA":
            self.stage = CropStage.GERMINATION
            self.age_days = 4.0
            self.height_cm = 8.0 * self.vigor_factor
            self._set_organs(2.5 * self.vigor_factor, 0.25, 0.55, 0.20, 0.0)
            self.flower_induced = False
            self.leaf_senescence = 0.0
            self.buds_size = 0.0
            self.trichomes_amber = 0.0
            self.root_health = 100.0
        elif stage == "CRECIMIENTO":
            self.stage = CropStage.VEGETATIVE
            self.age_days = 22.0
            self.height_cm = 38.0 * self.vigor_factor
            shoot = 1.0 - p["root_frac"]
            self._set_organs(42.0 * self.vigor_factor, p["root_frac"],
                             shoot * p["leaf_frac_shoot"], shoot * (1.0 - p["leaf_frac_shoot"]), 0.0)
            self.flower_induced = False
            self.leaf_senescence = 0.0
            self.buds_size = 0.0
            self.trichomes_amber = 0.0
            self.root_health = 100.0
        elif stage == "FLORACION":
            self.stage = CropStage.FLOWERING
            self.age_days = 48.0
            self.flower_induced = True
            self.flower_induction_day = 30.0
            self.height_cm = 72.0 * self.vigor_factor
            total = 105.0 * self.vigor_factor
            self.buds_size = 0.45
            flower = self.buds_size * p["bud_ref_g"]
            root = 0.12 * total
            rest = max(0.0, total - flower - root)
            self.root_g, self.flower_g = root, flower
            self.leaf_g, self.stem_g = rest * 0.55, rest * 0.45
            self.leaf_senescence = 0.0
            self.trichomes_amber = 0.10
            self.root_health = 98.0
        self.content = {ion: self.target_content(ion) for ion in "npk"}   # tejidos en su objetivo tras el reinicio

    def _update_phenology(self, photoperiod_hours):
        """Fase biológica por fotoperiodo y edad (independiente de la etapa de manejo de riego)."""
        p = self.params
        if self.age_days < 10.0:
            self.stage = CropStage.GERMINATION
            return
        is_auto = (self.flowering_type == "AUTOFLORECIENTE")
        if not self.flower_induced and (photoperiod_hours <= 13.5 or (is_auto and self.age_days >= 28.0)):
            self.flower_induced = True
            self.flower_induction_day = self.age_days
        if self.flower_induced:
            d = self.flower_days
            if d < p["stretch_days"]:
                self.stage = CropStage.STRETCH
            elif d < p["ripening_day"]:
                self.stage = CropStage.FLOWERING
            else:
                self.stage = CropStage.RIPENING
        else:
            self.stage = CropStage.VEGETATIVE

    def _partition(self, water_stress, generative):
        """Fracciones (raíz, hoja, tallo, flor) del carbono nuevo."""
        p = self.params
        d = self.flower_days
        root = p["root_frac"]
        flower_of_shoot = 0.0
        leaf_share = p["leaf_frac_shoot"]
        if self.flower_induced:
            root *= 1.0 - p["root_drop"] * _smoothstep(d / p["root_drop_days"])
            flower_of_shoot = p["flower_sink_max"] * _smoothstep((d - p["flower_start_day"]) / p["flower_ramp_days"])
            if generative:
                flower_of_shoot = min(0.9, flower_of_shoot * 1.1)
            if d < p["stretch_days"]:
                leaf_share = 0.55          # el estirón invierte más en tallo
        root = min(0.55, root * (1.0 + 0.8 * water_stress))   # equilibrio funcional: sequía → más raíz
        shoot = 1.0 - root
        veg_shoot = shoot * (1.0 - flower_of_shoot)
        return root, veg_shoot * leaf_share, veg_shoot * (1.0 - leaf_share), shoot * flower_of_shoot

    def update_growth(self, dt_hours, vwc, ec, ph, vpd, temp, light_exposure,
                      pot_ml=5000.0, photoperiod_hours=18.0, ppfd=0.0, co2_ppm=400.0, footprint_m2=0.25,
                      uptake_mg=None):
        """uptake_mg: {"n","p","k"} mg absorbidos en este paso. Si es None (uso aislado) la planta
        se alimenta idealmente: absorbe justo lo que demanda."""
        p = self.params
        dt_days = dt_hours / 24.0
        self.age_days += dt_days
        self._update_phenology(photoperiod_hours)

        # Senescencia foliar al final de la floración
        if self.flower_induced:
            self.leaf_senescence = max(self.leaf_senescence, min(
                0.6, (self.flower_days - p["senescence_start"]) / p["senescence_span"]))

        # 1. Estrés hídrico (diferenciado para plántula y adulto), hipoxia y raíz
        if self.stage == CropStage.GERMINATION:
            water_stress = max(0.0, min(1.0, (38.0 - vwc) / 20.0)) if vwc < 38.0 else 0.0
        else:
            water_stress = max(0.0, min(1.0, (24.0 - vwc) / 14.0)) if vwc < 24.0 else 0.0
        hypoxia = max(0.0, min(1.0, (vwc - 66.0) / 20.0)) if vwc > 66.0 else 0.0
        self.root_health = max(10.0, min(100.0, self.root_health + (2.0 - hypoxia * 6.0) * dt_days))

        # 2. Maceta pequeña sin trasplantar (restricción radicular)
        pot_biomass_limit = (pot_ml / 500.0) * 12.0
        rootbound_stress = 0.0
        if self.biomass_g > pot_biomass_limit:
            over = (self.biomass_g - pot_biomass_limit) / max(pot_biomass_limit, 5.0)
            rootbound_stress = min(0.65, over * 0.40)
            self.root_health = max(10.0, self.root_health - rootbound_stress * 3.5 * dt_days)

        # 3. Estrés por EC y pH
        target_ec_max = 2.2 if self.stage == CropStage.GERMINATION else (3.6 if self.stage == CropStage.VEGETATIVE else 5.2)
        ec_stress = max(0.0, min(1.0, (ec - target_ec_max) / 2.2)) if ec > target_ec_max else 0.0
        ph_stress = max(0.0, min(1.0, (5.5 - ph) / 0.7, (ph - 6.5) / 0.7))

        # Nutrición: el tejido recibe lo absorbido; el estado (contenido/objetivo) limita la fotosíntesis.
        if uptake_mg is None:
            uptake_mg = self.nutrient_demand(dt_days)
        for ion in "npk":
            self.content[ion] += uptake_mg.get(ion, 0.0)
            self.nutrient_uptake_total[ion] += uptake_mg.get(ion, 0.0)
            self.nutrient_status[ion] = self.content[ion] / max(self.target_content(ion), 1e-9)
        crit = self.nutri["deficit_crit"]
        worst = min(self.nutrient_status.values())
        nutrition_factor = max(0.20, min(1.0, (worst - crit) / (1.0 - crit))) * (1.0 - ph_stress * 0.7)
        combined_stress = max(water_stress, hypoxia, ec_stress * 0.8, ph_stress * 0.7, rootbound_stress)
        self.root_health = max(10.0, min(100.0, self.root_health - ph_stress * 5.0 * dt_days))

        # 4. Detección de Crop Steering (vegetativo vs generativo)
        generative = (ec >= 3.2 or (vwc < 48.0 and vpd > 1.25))
        self.steering_type = "GENERATIVO" if generative else "VEGETATIVO"

        # 5. Fotosíntesis del dosel (solo con luz) y respiración
        footprint_m2 = max(0.01, footprint_m2)
        ppfd_canopy = max(0.0, ppfd * light_exposure)
        lai = self.leaf_area_m2 / footprint_m2
        stress = ((1.0 - 0.85 * combined_stress) * (0.35 + 0.65 * nutrition_factor)
                  * (0.4 + 0.6 * self.root_health / 100.0) * self.vigor_factor)
        a_c = canopy_photosynthesis_umol(ppfd_canopy, lai, temp, co2_ppm, p, stress)
        self.a_canopy = a_c
        dt_s = dt_hours * 3600.0
        gpp = a_c * 30.0e-6 * footprint_m2 * dt_s                       # g CH2O
        q = p["q10"] ** ((temp - 25.0) / 10.0)
        maint = q * dt_days * (p["rm_leaf"] * self.leaf_g + p["rm_stem"] * self.stem_g
                               + p["rm_root"] * self.root_g + p["rm_flower"] * self.flower_g)
        self.gpp_total_g += gpp
        self.resp_total_g += maint
        self.par_total_mol += ppfd_canopy * footprint_m2 * dt_s * 1e-6

        self.reserve_g += gpp - maint
        if self.reserve_g < 0.0:
            # Déficit: la planta consume tejido (hoja y raíz) para pagar el mantenimiento.
            loss = min(-self.reserve_g, 0.5 * (self.leaf_g + self.root_g))
            tot = max(self.leaf_g + self.root_g, 1e-9)
            self.leaf_g -= loss * self.leaf_g / tot
            self.root_g -= loss * self.root_g / tot
            self.reserve_g = 0.0

        # 6. Crecimiento: la reserva se convierte en materia seca con eficiencia Yg
        use = self.reserve_g * (1.0 - math.exp(-dt_days / p["tau_use_days"]))
        self.reserve_g -= use
        new_mass = p["yg"] * use
        f_root, f_leaf, f_stem, f_flower = self._partition(water_stress, generative)
        self.root_g += new_mass * f_root
        self.leaf_g += new_mass * f_leaf
        self.stem_g += new_mass * f_stem
        self.flower_g += new_mass * f_flower
        alpha = 1.0 - math.exp(-dt_days / 0.5)
        for organ, frac in (("root", f_root), ("leaf", f_leaf), ("stem", f_stem), ("flower", f_flower)):
            rate = new_mass * frac / max(dt_days, 1e-9)
            self.growth_rate[organ] += alpha * (rate - self.growth_rate[organ])

        # Caída de hojas bajas por autosombreo (hojarasca: ese carbono se pierde)
        shade = max(0.0, min(1.0, (lai - p["lai_soft"]) / max(1e-6, p["lai_max"] - p["lai_soft"])))
        shed = self.leaf_g * p["leaf_turnover_max"] * shade * dt_days
        self.leaf_g -= shed
        self.litter_g += shed
        for ion in "npk":      # la hoja que cae se lleva su contenido de nutrientes
            self.content[ion] = max(0.0, self.content[ion] - shed * self.tissue_target(ion, "leaf") * min(1.0, self.nutrient_status[ion]))

        # Altura: elongación por masa de tallo (más intensa en el estirón; menos con steering generativo)
        if self.stage == CropStage.STRETCH:
            elong = 1.2 if generative else 1.8
        elif self.stage in (CropStage.FLOWERING, CropStage.RIPENING):
            elong = 0.3
        else:
            elong = 1.0
        room = max(0.0, 1.0 - self.height_cm / self.max_height_cm)
        self.height_cm = min(self.max_height_cm, self.height_cm + p["ssl_cm_per_g"] * elong * new_mass * f_stem * room)

        # Flor y tricomas
        self.buds_size = min(1.0, self.flower_g / p["bud_ref_g"])
        if self.stage == CropStage.RIPENING:
            self.trichomes_amber = min(0.85, self.trichomes_amber + (0.050 if generative else 0.025) * dt_days)

        # 7. Síntomas visibles
        self.leaf_droop = max(0.0, min(1.0, water_stress * 1.3 + hypoxia * 0.7 + rootbound_stress * 0.8))
        self.tip_burn = max(self.tip_burn, min(1.0, ec_stress * 1.1))
        self.leaf_claw = max(0.0, min(1.0, ec_stress * 0.9 + hypoxia * 0.5 + rootbound_stress * 0.4))
        self.chlorosis = max(0.0, min(1.0, (100.0 - self.root_health) / 100.0 * 0.85
                                      + max(0.45 if self.stage == CropStage.RIPENING else 0.0, self.leaf_senescence)
                                      + max(0.0, 1.0 - self.nutrient_status["n"]) * 1.2))
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
        compaction = random.uniform(0.97, 1.03)     # compactación del sustrato (±3 % en α)
        self._pot_ml = 3500.0
        self.col = PotColumn(self._pot_ml, "COCO", alpha_scale=compaction)
        self._col_acc = self._new_acc()
        self.cc_source = "model"            # "model" (derivada de la curva), "test" (ensayo CC) o "manual"
        self.vwc_cc = self.col.capacity_sensor_vwc()
        self.vwc_pmp = 15.0
        self.fill_uniform(38.0, 2.4, n=160.0, p=50.0, k=200.0, ph=5.9)
        
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
        self.total_et_ml = 0.0   # transpiración acumulada (diagnóstico)

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
    def pot_ml(self): return self._pot_ml
    @pot_ml.setter
    def pot_ml(self, value):
        """Cambiar de maceta conserva el perfil de VWC y la EC por capa; la CC derivada se actualiza."""
        value = max(50.0, float(value))
        if abs(value - self._pot_ml) < 1e-9:
            return
        self._pot_ml = value
        self.col.resize(value)
        if self.cc_source == "model":
            self.vwc_cc = self.col.capacity_sensor_vwc()
    def _new_acc(self):
        return {"dt": 0.0, "sink": [0.0] * self.col.n, "evap": [0.0] * self.col.n,
                "mass": {}, "proton_mmol": [0.0] * self.col.n}

    def fill_uniform(self, vwc_pct, ec, n=None, p=None, k=None, ph=None):
        """Llena la maceta con VWC, EC, NPK [ppm] y pH uniformes (estado inicial o de reinicio).

        Lo que no se indica conserva su promedio actual."""
        keep = {"n": self.n_ppm, "p": self.p_ppm, "k": self.k_ppm, "ph": self.ph} if self.col.water_ml > 0 else {"n": 0.0, "p": 0.0, "k": 0.0, "ph": 5.9}
        self.col.theta = [vwc_pct / 100.0] * self.col.n
        self.col.set_salt(self.col.water_ml * ec)
        self.col.set_uniform("n", keep["n"] if n is None else n)
        self.col.set_uniform("p", keep["p"] if p is None else p)
        self.col.set_uniform("k", keep["k"] if k is None else k)
        self.col.set_uniform("ph", keep["ph"] if ph is None else ph)
    @property
    def W(self): return self.col.water_ml
    @W.setter
    def W(self, value):
        """Asignar el agua total escala el perfil actual (o lo hace uniforme si está vacío)."""
        self.col.set_water(value)
    @property
    def vol_top_ml(self): return self.pot_ml * 0.5     # mitad superior (proyección vwcPrevisto del firmware)
    @property
    def vol_bottom_ml(self): return self.pot_ml * 0.5
    @property
    def vwc(self):
        """Lectura del sensor: promedio de las capas que abarca (sensor_z0..sensor_z1 de la profundidad)."""
        return self.col.sensor_vwc()
    @property
    def vwc_bottom(self): return self.col.zone_vwc(0.5, 1.0)
    @property
    def vwc_mean(self): return 100.0 * self.W / self.pot_ml
    @property
    def vwc_root(self):
        """VWC que percibe la raíz (promedio ponderado por la distribución radicular)."""
        w = self.col.root_weights(self.biology.root_g)
        return 100.0 * sum(wi * t for wi, t in zip(w, self.col.theta))
    @property
    def S(self): return self.col.salt_total
    @S.setter
    def S(self, value):
        """Asignar la sal total la reparte según el agua de cada capa (EC uniforme)."""
        self.col.set_salt(max(0.0, value))
    @property
    def ec(self):
        """EC que lee el sensor: agua de poro de las capas que abarca."""
        return self.col.sensor_ec()
    @property
    def ec_bottom(self): return self.col.zone_ec(0.5, 1.0)
    @property
    def ec_mean(self): return self.S / max(self.W, 1.0)
    @property
    def ec_root(self):
        """EC que percibe la raíz (promedio ponderado por la distribución radicular)."""
        w = self.col.root_weights(self.biology.root_g)
        return sum(wi * e for wi, e in zip(w, self.col.ec_layers()))
    @property
    def n_mg(self): return self.col.tracer_total("n")
    @property
    def p_mg(self): return self.col.tracer_total("p")
    @property
    def k_mg(self): return self.col.tracer_total("k")
    @property
    def n_ppm(self): return self.col.mean_conc("n")
    @property
    def p_ppm(self): return self.col.mean_conc("p")
    @property
    def k_ppm(self): return self.col.mean_conc("k")
    @property
    def ph(self):
        """pH medio del agua de poro."""
        return self.col.mean_conc("ph")
    @ph.setter
    def ph(self, value):
        self.col.set_uniform("ph", float(value))
    @property
    def ph_sensor(self): return self.col.zone_conc("ph", self.col.sensor_z0, self.col.sensor_z1)
    @property
    def W_cc(self):
        """Agua retenida en capacidad de contenedor [mL] (física si la CC es la derivada del modelo)."""
        if self.cc_source == "model":
            return self.col.capacity_ml()
        return (self.vwc_cc / 100.0) * self.pot_ml
    @property
    def tray_weight_ml(self):
        """Lectura de la celda, ya afectada por la última tara (pesoFiltrado)."""
        return self.tray_ml - self.tray_tare_ml

    def _save_stage_profile(self):
        profile = self.stage_profiles[self.management_stage]
        profile.update({field: getattr(self.config, field) for field in IRRIGATION_FIELDS})
        profile.update({"pot_volume_ml": self.pot_ml, "vwc_cc": self.vwc_cc, "vwc_pmp": self.vwc_pmp})

    def _load_stage_profile(self, stage, preserve_vwc=True, allow_downsize=True):
        profile = self.stage_profiles[stage]
        old_pot = self.pot_ml
        self.pot_ml = float(profile["pot_volume_ml"])
        if not allow_downsize:
            # Una planta viva nunca pasa a una maceta menor: el volumen se mantiene.
            self.pot_ml = max(self.pot_ml, old_pot)
        self.vwc_pmp = float(profile["vwc_pmp"])
        for field in IRRIGATION_FIELDS:
            setattr(self.config, field, profile[field])
        if not preserve_vwc:
            # Con preserve_vwc el perfil de agua, sales, iones y pH por capa se conserva (el trasplante solo cambia la geometría).
            init_vals = STAGE_SUBSTRATE_INIT.get(stage, STAGE_SUBSTRATE_INIT["PLANTULA"])
            self.fill_uniform(init_vals["vwc"], init_vals["ec"], n=init_vals["n"], p=init_vals["p"],
                              k=init_vals["k"], ph=init_vals["ph"])

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
        profile = SUBSTRATE_PROFILES[substrate_type]
        self.substrate_type = substrate_type
        self.col.set_substrate(substrate_type)
        self.cc_source = "model"
        self.vwc_cc = self.col.capacity_sensor_vwc()
        self.vwc_pmp = self.col.wilting_vwc()
        if not preserve_vwc:
            init_vals = STAGE_SUBSTRATE_INIT.get(self.management_stage, STAGE_SUBSTRATE_INIT["PLANTULA"])
            self.fill_uniform(init_vals["vwc"], init_vals["ec"], n=init_vals["n"], p=init_vals["p"],
                              k=init_vals["k"], ph=init_vals["ph"])
        retained_ml = self.W_cc
        self.config.p1_vol_cc = retained_ml
        self.config.p2_vol_cc = retained_ml
        self.config.p2_vwc_cc_ref = self.vwc_cc
        return True

    def _ion_uptake(self, demand, water_uptake_layers, dt_min, dt_days, rw):
        """Absorción de N, P y K por capa: la que cubra la demanda de la planta, con lo que dé
        el flujo de masa (agua tomada × concentración) o la absorción activa saturable
        (Vmax·raíz·C/(Km+C)), la mayor de ambas, sin vaciar la capa. Devuelve (mg por ion, mg por capa)."""
        col, nu = self.col, self.biology.nutri
        n = col.n
        health = max(0.3, self.biology.root_health / 100.0)
        root_g = self.biology.root_g
        w = [max(t * col.layer_ml, 1e-6) for t in col.theta]
        uptake, per_layer = {}, {}
        for ion in "npk":
            mass = col.tracers[ion]
            sup = [0.0] * n
            for j in range(n):
                m = max(0.0, mass[j])
                c_mg_ml = m / w[j]
                passive = water_uptake_layers[j] * dt_min * c_mg_ml          # mL/min × min
                c_ppm = 1000.0 * c_mg_ml
                active = nu["vmax_" + ion] * root_g * rw[j] * health * c_ppm / (nu["km_" + ion] + c_ppm) * dt_days
                sup[j] = min(0.9 * m, max(passive, active))
            tot = sum(sup)
            f = min(1.0, demand[ion] / tot) if tot > 1e-12 else 0.0
            per_layer[ion] = [x * f for x in sup]
            uptake[ion] = sum(per_layer[ion])
        return uptake, per_layer

    def step(self, dt_seconds, is_day, vpd, temp, pump_active, solution, light_exposure,
             fase_global=None, photoperiod_hours=18.0, ppfd=0.0, co2_ppm=400.0,
             footprint_m2=0.25, aero_cond_m_s=0.02):
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
            
        # 2. Demanda evapotranspirativa con el estado actual de la maceta
        # La transpiración es proporcional al ÁREA FOLIAR (no a la etapa) y se suma
        # la evaporación directa del sustrato, que domina en plántulas y desaparece
        # cuando el dosel cubre la maceta.
        vpd_factor = max(0.2, min(1.6, vpd / 1.1))
        leaf_m2 = self.biology.leaf_area_m2
        heads = self.col.heads()
        rw = self.col.root_weights(self.biology.root_g)
        vwc_root_now = 100.0 * sum(wi * t for wi, t in zip(rw, self.col.theta))
        water_stress = 1.0 if vwc_root_now >= 25.0 else max(0.05, (vwc_root_now - self.vwc_pmp) / 10.0)
        et_trans = transpiration_ml_min(leaf_m2, footprint_m2, ppfd * light_exposure, vpd, temp,
                                        co2_ppm, aero_cond_m_s, is_day, water_stress)
        pot_area_m2 = pot_surface_area_m2(self.pot_ml)
        canopy_cover = 1.0 - math.exp(-PM_K_EXT * leaf_m2 / max(0.01, footprint_m2))
        et_evap = ((K_EVAP_DAY if is_day else K_EVAP_NIGHT) * pot_area_m2 * (1.0 - canopy_cover)
                   * vpd_factor * self.col.evaporation_factor(heads[0]))
        et_evap *= water_stress

        # La raíz toma el agua de las capas según su distribución y la tensión de cada una
        # (compensación entre capas); la evaporación sale de las capas superiores y deja las sales.
        uptake_layers, trans_rate = self.col.allocate_uptake(et_trans, dt_min, self.biology.root_g, heads=heads)
        evap_layers, evap_rate = self.col.evaporation(et_evap, dt_min)
        self.total_et_ml += (trans_rate + evap_rate) * dt_min

        # 3. Riego entrante
        inflow_rate = 0.0
        inflow_ml = 0.0
        if self.valve_open and pump_active:
            inflow_rate = self.config.flow_ml_min * self.dripper_variance
            inflow_ml = inflow_rate * dt_min
            self.total_irrigation_ml += inflow_ml

        # 4. Columna, nutrientes y biología. Los pasos cortos sin riego se acumulan
        # (≥ COL_MIN_SOLVE_S) antes de resolver Richards, absorber iones y crecer:
        # los sumideros se aplican por su masa acumulada, así el balance no cambia.
        acc = self._col_acc
        acc["dt"] += dt_min
        for i in range(self.col.n):
            acc["sink"][i] += uptake_layers[i] * dt_min
            acc["evap"][i] += evap_layers[i] * dt_min
        runoff_ml = 0.0
        if inflow_rate > 0.0 or acc["dt"] * 60.0 >= COL_MIN_SOLVE_S - 1e-9:
            dt_solve = acc["dt"]
            sinks = [x / dt_solve for x in acc["sink"]]
            evaps = [x / dt_solve for x in acc["evap"]]
            # Absorción de iones por capa según la demanda de la planta; sale de cada capa y arrastra
            # su fracción de sales (las no medidas se retiran en proporción a N+P+K) y protones.
            dt_days_s = dt_solve / 1440.0
            demand = self.biology.nutrient_demand(dt_days_s)
            uptake_mg, per_layer = self._ion_uptake(demand, sinks, dt_solve, dt_days_s, rw)
            nu_b = self.biology.nutri
            nh4 = nu_b["nh4_fraction"]
            tr = self.col.tracers
            acc["mass"] = {k: [0.0] * self.col.n for k in ("salt", "n", "p", "k")}
            for j in range(self.col.n):
                # Sales retiradas con N, P y K: masa iónica absorbida (NO3⁻ 4.43 g/g N, H2PO4⁻ 3.13 g/g P,
                # K⁺ 1 g/g K) convertida a CE con ~640 mg/L por mS/cm. El resto de sales (Ca, Mg, SO4,
                # Na…) no lo toma la planta aquí y se queda: la CE de la maceta no cae a cero.
                ion_mg = (ION_MASS_NO3_PER_N * per_layer["n"][j] + ION_MASS_H2PO4_PER_P * per_layer["p"][j]
                          + per_layer["k"][j])
                acc["mass"]["salt"][j] = min(0.9 * max(0.0, tr["salt"][j]), ion_mg * 1000.0 / TDS_PER_EC)
                for ion in "npk":
                    acc["mass"][ion][j] = per_layer[ion][j]
                # Carga neta absorbida: cationes (K+, NH4+) liberan H+, aniones (NO3-, H2PO4-) lo consumen.
                acc["proton_mmol"][j] = (per_layer["k"][j] / 39.098 + (2.0 * nh4 - 1.0) * per_layer["n"][j] / 14.007
                                         - per_layer["p"][j] / 30.974)
            c_in = {"salt": solution.ec, "n": solution.n / 1000.0, "p": solution.p / 1000.0,
                    "k": solution.k / 1000.0, "ph": solution.ph}
            runoff_ml = self.col.step(dt_solve, inflow_rate, c_in, sinks, uptake_mass=acc["mass"],
                                      evap_ml_min=evaps, heads=heads if dt_solve == dt_min else None)
            # Protones de la absorción: cambian el pH de cada capa según el tampón del sustrato.
            buf = nu_b["buffer_mmol_l_ph"]
            ph_m = self.col.tracers["ph"]
            for j in range(self.col.n):
                w_ml = max(self.col.theta[j] * self.col.layer_ml, 1e-6)
                ph_j = ph_m[j] / w_ml - acc["proton_mmol"][j] / (buf * w_ml / 1000.0)
                ph_m[j] = max(3.5, min(9.0, ph_j)) * w_ml
            self._col_acc = self._new_acc()

            # 5. Biología (con lo absorbido y el estado de la zona radicular)
            self.biology.update_growth(
                dt_solve / 60.0, vwc_root_now, sum(wi * ec_l for wi, ec_l in zip(rw, self.col.ec_layers())),
                sum(wi * ph_l for wi, ph_l in zip(rw, self.col.conc_layers("ph"))),
                vpd, temp, light_exposure,
                pot_ml=self.pot_ml,
                photoperiod_hours=photoperiod_hours,
                ppfd=ppfd, co2_ppm=co2_ppm, footprint_m2=footprint_m2, uptake_mg=uptake_mg
            )
        if runoff_ml > 0.0:
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
        self.sim_time_seconds = 6 * 3600.0   # Día 1 arranca al encender la luz
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
        self.plant_footprint_m2 = 0.25      # área de suelo por planta (50 cm × 50 cm)
        self.aero_conductance_m_s = 0.02    # conductancia aerodinámica del dosel (ventilación)
        
        self.air_temp = 25.0
        self.air_rh = 60.0
        self.co2_ppm = 850
        
        # El firmware distingue BOMBAP1 y BOMBAP2. El gemelo conserva dos
        # soluciones independientes para ensayar recetas de subida y mantenimiento.
        self.solution_p1 = NutrientSolution(ec=2.0, ph=5.9, n=160.0, p=50.0, k=200.0)
        self.solution_p2 = NutrientSolution(ec=2.4, ph=5.9, n=130.0, p=55.0, k=230.0)
        
        self.pump_p1 = False
        self.pump_p2 = False

        # En crop steering el drenaje debe medirse, no desaparecer: la bandeja NO
        # tiene desagüe pasivo. El agua se acumula en la celda de carga hasta que
        # se vacía a mano (empty_tray_manually) o con el servicio opcional de fin
        # de P2 (auto_empty_and_tare_end_p2).
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
        # Día 1: el sustrato arranca con VWC inicial bajo (el P1 lo recarga) (INITIAL_VWC_PCT), no a capacidad de campo.
        for p in self.plants:
            p.fill_uniform(INITIAL_VWC_PCT, p.ec)   # conserva NPK y pH actuales

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
        p.pot_ml = pot_volume_ml
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
            p.cc_source = "test"
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
                # Firmware (realizarDisparoP2): el mantenimiento se corta en pleno
                # riego si el VWC llega al máximo, el runoff de P2 al límite o la
                # EC al objetivo (esta última queda marcada hasta el próximo P2).
                corte_p2 = False
                if p.disparo_fase == "P2":
                    if p.ec >= cfg.p2_ec_target:
                        p.p2_ec_objetivo_alcanzado = True
                    corte_p2 = (p.vwc >= cfg.p2_vwc_max or p.runoff_fase_p2 >= cfg.p2_runoff_max
                                or p.p2_ec_objetivo_alcanzado)
                if p.disparo_timer_segundos >= p.disparo_duracion_total or corte_p2:
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
                    # VolDisparo incluye el factor VPD antes de proyectar (firmware, l.136-148).
                    vpd_factor = max(0.75, min(1.25, self.vpd / 1.1))
                    shot_ml = cfg.p2_vol_cc * (cfg.p2_shot_pc / 100.0) * vpd_factor
                    vwc_previsto = p.vwc + (shot_ml / max(p.vol_top_ml, 1.0)) * 100.0
                    if vwc_previsto > cfg.p2_vwc_max:
                        continue  # Bloquear: el shot subiría demasiado el VWC
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

    # Un paso grande (velocidades altas, "+1 Día") se subdivide: así una válvula
    # que abre no entrega minutos de caudal de más y el control (VWC/umbrales,
    # cierre de disparos) se evalúa con la misma resolución a cualquier velocidad.
    MAX_SUBSTEP_SECONDS = 5.0
    MAX_SUBSTEPS = 240

    def step(self, dt_real_seconds):
        if self.is_paused: return
        dt_total = dt_real_seconds * self.time_speed
        n = max(1, min(self.MAX_SUBSTEPS, math.ceil(dt_total / self.MAX_SUBSTEP_SECONDS)))
        for _ in range(n):
            self._step_sim(dt_total / n)

    def _step_sim(self, dt_sim_seconds):
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
                photoperiod_hours=photoperiod_hours,
                ppfd=self.current_ppfd,
                co2_ppm=self.co2_ppm,
                footprint_m2=self.plant_footprint_m2,
                aero_cond_m_s=self.aero_conductance_m_s
            )

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
        p.pot_ml = new_pot_ml               # conserva el perfil de VWC y de EC por capa
        # Actualizar vol_cc basado en el sustrato y la nueva maceta
        retained_ml = p.W_cc
        p.config.p1_vol_cc = retained_ml
        p.config.p2_vol_cc = retained_ml
        # Actualizar el perfil de la etapa activa
        p._save_stage_profile()
        # Resetear estrés por rootbound — la raíz ya tiene espacio
        p.biology.root_health = min(100.0, p.biology.root_health + 20.0)
        return True

    def set_carbon_params(self, values):
        """Cambia a MANO parámetros del balance de carbono en todas las plantas.

        Solo acepta claves de CARBON_DEFAULTS con valores numéricos finitos y positivos
        (los parámetros nunca cambian solos). Devuelve las claves aplicadas.
        """
        applied = {}
        for key, val in (values or {}).items():
            if key not in CARBON_DEFAULTS:
                continue
            try:
                v = float(val)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(v) or v <= 0.0:
                continue
            applied[key] = v
        for p in self.plants:
            p.biology.params.update(applied)
        return sorted(applied)

    def set_nutrient_params(self, values):
        """Cambia a MANO parámetros de la nutrición por ion (contenido objetivo en tejido, Vmax, Km,
        tampón del sustrato, fracción amoniacal…). Solo acepta claves de NUTRIENT_DEFAULTS con valores
        finitos y positivos (nh4_fraction puede ser 0). Los parámetros nunca cambian solos."""
        applied = {}
        for key, val in (values or {}).items():
            if key not in NUTRIENT_DEFAULTS:
                continue
            try:
                v = float(val)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(v) or v < 0.0 or (v == 0.0 and key != "nh4_fraction"):
                continue
            if key == "nh4_fraction" and v > 1.0:
                continue
            applied[key] = v
        for p in self.plants:
            p.biology.nutri.update(applied)
        return sorted(applied)

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
                p.pot_ml = new_pot_ml
                retained_ml = p.W_cc
                cfg.p1_vol_cc = retained_ml
                cfg.p2_vol_cc = retained_ml
            if cfg_data.get("sensor_z0_pct") is not None or cfg_data.get("sensor_z1_pct") is not None:
                z0 = float(cfg_data.get("sensor_z0_pct") if cfg_data.get("sensor_z0_pct") is not None else p.col.sensor_z0 * 100.0) / 100.0
                z1 = float(cfg_data.get("sensor_z1_pct") if cfg_data.get("sensor_z1_pct") is not None else p.col.sensor_z1 * 100.0) / 100.0
                z0 = max(0.0, min(0.92, z0))
                z1 = max(z0 + 1.0 / p.col.n, min(1.0, z1))
                p.col.sensor_z0, p.col.sensor_z1 = z0, z1
                if p.cc_source == "model":      # la CC derivada es la lectura del sensor en ese punto
                    p.vwc_cc = p.col.capacity_sensor_vwc()
                    p.config.p2_vwc_cc_ref = p.vwc_cc
            if "vwc_cc" in cfg_data:
                p.vwc_cc = max(1.0, min(95.0, float(cfg_data["vwc_cc"])))
                p.cc_source = "manual"
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
        for field, low, high in (("ec", 0.0, 15.0), ("ph", 4.5, 7.5), ("n", 0.0, 400.0), ("p", 0.0, 200.0), ("k", 0.0, 500.0)):
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
                "ppfd_max": self.ppfd_max,
                "plant_footprint_m2": self.plant_footprint_m2,
                "aero_conductance_m_s": self.aero_conductance_m_s,
                "co2_ppm": self.co2_ppm,
                "solution_p1": self.solution_p1.as_dict(),
                "solution_p2": self.solution_p2.as_dict(),
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
                "vwc_bottom": round(sel_p.vwc_bottom, 1),
                "profile_vwc": [round(100.0 * t, 1) for t in sel_p.col.theta],
                "profile_ec": [round(e, 2) for e in sel_p.col.ec_layers()],
                "profile_head_cm": [round(h, 1) for h in sel_p.col.heads()],
                "profile_ph": [round(x, 2) for x in sel_p.col.conc_layers("ph")],
                "profile_npk_ppm": {i: [round(1000.0 * x, 1) for x in sel_p.col.conc_layers(i)] for i in "npk"},
                "sensor_zone_pct": [round(100.0 * sel_p.col.sensor_z0), round(100.0 * sel_p.col.sensor_z1)],
                "cc_source": sel_p.cc_source,
                "pot_height_cm": round(sel_p.col.height, 1),
                "vwc_mean": round(sel_p.vwc_mean, 1),
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
                "leaf_area_m2": round(bio.leaf_area_m2, 3),
                "lai": round(bio.leaf_area_m2 / max(0.01, self.plant_footprint_m2), 2),
                "leaf_mass_g": round(bio.leaf_g, 1),
                "stem_mass_g": round(bio.stem_g, 1),
                "root_mass_g": round(bio.root_g, 1),
                "flower_mass_g": round(bio.flower_g, 1),
                "harvest_index": round(bio.harvest_index, 2),
                "a_canopy_umol": round(bio.a_canopy, 1),
                "carbon_reserve_g": round(bio.reserve_g, 2),
                "gpp_total_g": round(bio.gpp_total_g, 1),
                "resp_total_g": round(bio.resp_total_g, 1),
                "litter_g": round(bio.litter_g, 1),
                "carbon_params": dict(bio.params),
                "tissue_pct": {i: round(bio.tissue_concentration(i), 2) for i in "npk"},
                "nutrient_status": {i: round(bio.nutrient_status[i], 2) for i in "npk"},
                "nutrient_uptake_mg": {i: round(bio.nutrient_uptake_total[i], 1) for i in "npk"},
                "nutrient_params": dict(bio.nutri),
                "total_et_ml": round(sel_p.total_et_ml, 0),
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
                    "sensor_z0_pct": round(100.0 * sel_p.col.sensor_z0),
                    "sensor_z1_pct": round(100.0 * sel_p.col.sensor_z1),
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
