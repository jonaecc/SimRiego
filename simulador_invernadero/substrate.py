"""Columna de sustrato en maceta: agua por Richards (van Genuchten-Mualem) y sales por capa.

La maceta es una columna vertical de N capas de igual volumen. El agua se mueve
por la ecuación de Richards (esquema implícito de Picard modificado, conserva
masa); el fondo es una *cara de filtración*: solo drena cuando la capa inferior
alcanza la presión del orificio. De ahí sale sola la capacidad de contenedor y
su gradiente vertical (la capa de agua colgante), que dependen de la altura de la
maceta y de la curva de retención, no de un número fijo.

Las sales (EC·mL por capa) viajan con el agua (advección *upwind*) y se difunden
muy poco; la evaporación las deja, la raíz absorbe una fracción con el agua.

Parámetros de los sustratos: ver SUBSTRATE_VG. Solo el coco tiene datos de
referencia (ver comentario); el resto son ilustrativos y deben calibrarse.
"""
import math

N_LAYERS = 12
TRACERS = ("salt", "n", "p", "k", "ph")
INTENSIVE = ("ph",)         # se quitan junto con el agua (no se concentran por evaporación ni absorción)
CYL_COEF = math.pi * 0.3     # volumen de un cilindro de altura 1,2·d: V = CYL_COEF·d³
SS = 5e-4                    # almacenamiento específico en saturación [1/cm] (evita C=0)
H_MIN = -3.0e5               # tope seco numérico [cm]
HEAD_FLOOR = -15000.0        # punto de marchitez permanente (−1500 kPa) [cm]
SALT_DIFF_CM2_MIN = 5.0e-4   # difusión efectiva de iones en el medio [cm²/min]
ROOT_DECAY = 1.2             # decaimiento exponencial de la densidad radicular con la profundidad (≈60 % arriba)
ROOT_G_PER_L_FULL = 3.0      # g de raíz por litro de maceta con que las raíces alcanzan el fondo [modelo]
MAX_DTHETA = 0.12           # cambio máximo de θ por iteración de Picard
THETA_TOL = 2e-4             # tolerancia de convergencia en θ
FEDDES_H3 = -400.0           # tensión desde la que baja la extracción radicular [cm]

# Parámetros van Genuchten-Mualem. alpha [1/cm], ks [cm/día].
# COCO: 70 % coco + 30 % perlita. Curva ajustada a lo medido en maceta de 15 cm
# (capacidad de contenedor ≈ 59 %, aire ≈ 22 %, porosidad 81 %, agua disponible 10-100 cm ≈ 16 %).
# Una sola curva de van Genuchten no reproduce a la vez la cola seca y el agua
# disponible (coco real es bimodal): se prioriza la capacidad de contenedor y el
# punto de marchitez (≈15 %). Ks y los demás sustratos son ILUSTRATIVOS.
SUBSTRATE_VG = {
    "COCO":     {"theta_s": 0.81, "theta_r": 0.10, "alpha": 0.307, "n": 1.30, "ks": 15000.0},
    "ROCKWOOL": {"theta_s": 0.95, "theta_r": 0.04, "alpha": 0.094, "n": 2.50, "ks": 3000.0},
    "TURBA":    {"theta_s": 0.85, "theta_r": 0.10, "alpha": 0.204, "n": 2.00, "ks": 800.0},
    "SUELO":    {"theta_s": 0.50, "theta_r": 0.08, "alpha": 0.075, "n": 1.60, "ks": 150.0},
}


class VanGenuchten:
    def __init__(self, theta_s, theta_r, alpha, n, ks):
        self.ts, self.tr, self.alpha, self.n, self.ks = theta_s, theta_r, alpha, n, ks
        self.m = 1.0 - 1.0 / n
        self.ks_cm_min = ks / 1440.0

    def theta(self, h):
        if h >= 0.0:
            return self.ts + SS * h
        se = (1.0 + (self.alpha * -h) ** self.n) ** (-self.m)
        return self.tr + (self.ts - self.tr) * se

    def capacity(self, h):
        """dθ/dh [1/cm]."""
        if h >= 0.0:
            return SS
        ah = (self.alpha * -h) ** self.n
        se = (1.0 + ah) ** (-self.m)
        return max(1e-7, (self.ts - self.tr) * self.m * self.n * ah / (-h) * (1.0 + ah) ** (-self.m - 1.0)) if se < 1.0 else SS

    def conductivity(self, h):
        """K(h) de Mualem [cm/min]."""
        if h >= 0.0:
            return self.ks_cm_min
        se = (1.0 + (self.alpha * -h) ** self.n) ** (-self.m)
        return self.ks_cm_min * math.sqrt(se) * (1.0 - (1.0 - se ** (1.0 / self.m)) ** self.m) ** 2

    def head(self, theta):
        """Inversa de θ(h) [cm]."""
        if theta >= self.ts:
            return (theta - self.ts) / SS
        se = (theta - self.tr) / (self.ts - self.tr)
        se = max(1e-6, min(1.0 - 1e-12, se))
        h = -((se ** (-1.0 / self.m) - 1.0) ** (1.0 / self.n)) / self.alpha
        return max(H_MIN, h)


def feddes_factor(h):
    """Fracción de extracción radicular posible con la tensión h (1 hasta −400 cm, 0 en el marchitez)."""
    if h >= FEDDES_H3:
        return 1.0
    if h <= HEAD_FLOOR:
        return 0.0
    return (math.log10(-HEAD_FLOOR) - math.log10(-h)) / (math.log10(-HEAD_FLOOR) - math.log10(-FEDDES_H3))


class PotColumn:
    def __init__(self, pot_ml, substrate="COCO", n_layers=N_LAYERS, alpha_scale=1.0):
        self.n = n_layers
        self.alpha_scale = alpha_scale      # compactación de este sustrato (variabilidad entre macetas)
        self.set_substrate(substrate)
        self.set_geometry(pot_ml)
        self.theta = [0.0] * self.n
        # Trazadores por capa: salt = EC·mL; n, p, k = mg; ph = pH·mL (variable intensiva, mezcla por volumen)
        self.tracers = {name: [0.0] * self.n for name in TRACERS}
        self.last_fluxes = [0.0] * (self.n + 1)   # mL/min, + hacia abajo (0 = entrada, n = drenaje)
        self.sensor_z0, self.sensor_z1 = 0.15, 0.50   # el sensor promedia entre estas fracciones de profundidad
        self._rw_cache = {}

    # ── Geometría y sustrato ────────────────────────────────────────────────
    def set_substrate(self, substrate):
        self.substrate = substrate
        params = dict(SUBSTRATE_VG[substrate])
        params["alpha"] *= self.alpha_scale
        self.vg = VanGenuchten(**params)

    def set_geometry(self, pot_ml):
        self.pot_ml = max(50.0, float(pot_ml))
        d = (self.pot_ml / CYL_COEF) ** (1.0 / 3.0)         # cilindro de altura 1,2·diámetro
        self.height = 1.2 * d
        self.area = math.pi * d * d / 4.0
        self.dz = self.height / self.n
        self.layer_ml = self.area * self.dz

    def resize(self, pot_ml):
        """Cambia de maceta conservando el perfil de VWC y la EC por capa (trasplante)."""
        ratio = max(50.0, float(pot_ml)) / self.pot_ml
        self.set_geometry(pot_ml)
        for name in TRACERS:
            self.tracers[name] = [m * ratio for m in self.tracers[name]]

    # ── Estado ──────────────────────────────────────────────────────────────
    def water_layers(self):
        return [t * self.layer_ml for t in self.theta]

    @property
    def water_ml(self):
        return sum(self.theta) * self.layer_ml

    def set_water(self, total_ml):
        """Fija el agua total: escala el perfil actual (o lo hace uniforme si está vacío)."""
        total_ml = max(0.0, total_ml)
        cur = self.water_ml
        cap = (self.vg.ts + SS * 50.0) * self.pot_ml
        total_ml = min(total_ml, cap)
        if cur > 1e-9:
            k = total_ml / cur
            self.theta = [min(self.vg.ts, t * k) if k < 1.0 else min(self.vg.ts + SS * 50.0, t * k) for t in self.theta]
            # si el recorte por saturación dejó agua sin colocar, se reparte en las capas con espacio
            missing = total_ml - self.water_ml
            for _ in range(3):
                if missing <= 1e-9:
                    break
                room = [max(0.0, self.vg.ts - t) for t in self.theta]
                tr = sum(room)
                if tr <= 1e-12:
                    break
                add = min(1.0, missing / (tr * self.layer_ml))
                self.theta = [t + r * add for t, r in zip(self.theta, room)]
                missing = total_ml - self.water_ml
        else:
            self.theta = [total_ml / self.pot_ml] * self.n

    @property
    def salt(self):
        return self.tracers["salt"]

    @salt.setter
    def salt(self, values):
        self.tracers["salt"] = list(values)

    def set_tracer_total(self, name, total):
        """Reparte la masa total de un trazador según el agua de cada capa (concentración uniforme)."""
        w = self.water_ml
        if w <= 1e-9:
            self.tracers[name] = [total / self.n] * self.n
        else:
            self.tracers[name] = [total * t * self.layer_ml / w for t in self.theta]

    def set_salt(self, total):
        """Reparte la sal total según el agua de cada capa (EC uniforme)."""
        self.set_tracer_total("salt", total)

    def set_uniform(self, name, conc):
        """Concentración uniforme: ppm para n/p/k, pH para ph, mS/cm para salt."""
        per_ml = conc / 1000.0 if name in ("n", "p", "k") else conc
        self.tracers[name] = [per_ml * t * self.layer_ml for t in self.theta]

    def tracer_total(self, name):
        return sum(self.tracers[name])

    def conc_layers(self, name):
        """Concentración por capa: ppm (mg/L) para n/p/k, pH para ph, mS/cm para salt."""
        k = 1000.0 if name in ("n", "p", "k") else 1.0
        return [k * m / max(t * self.layer_ml, 1e-6) for m, t in zip(self.tracers[name], self.theta)]

    def zone_conc(self, name, z0, z1):
        idx = self._zone(z0, z1)
        w = sum(self.theta[i] for i in idx) * self.layer_ml
        k = 1000.0 if name in ("n", "p", "k") else 1.0
        return k * sum(self.tracers[name][i] for i in idx) / max(w, 1.0)

    def mean_conc(self, name):
        k = 1000.0 if name in ("n", "p", "k") else 1.0
        return k * self.tracer_total(name) / max(self.water_ml, 1.0)

    @property
    def salt_total(self):
        return sum(self.salt)

    def heads(self):
        return [self.vg.head(t) for t in self.theta]

    def ec_layers(self):
        return [s / max(t * self.layer_ml, 1e-6) for s, t in zip(self.salt, self.theta)]

    def _zone(self, z0, z1):
        a = max(0, min(self.n - 1, int(z0 * self.n)))
        b = max(a + 1, min(self.n, int(math.ceil(z1 * self.n))))
        return range(a, b)

    def zone_vwc(self, z0, z1):
        idx = self._zone(z0, z1)
        return 100.0 * sum(self.theta[i] for i in idx) / len(idx)

    def zone_ec(self, z0, z1):
        idx = self._zone(z0, z1)
        w = sum(self.theta[i] for i in idx) * self.layer_ml
        return sum(self.salt[i] for i in idx) / max(w, 1.0)

    def sensor_vwc(self):
        return self.zone_vwc(self.sensor_z0, self.sensor_z1)

    def sensor_ec(self):
        return self.zone_ec(self.sensor_z0, self.sensor_z1)

    def equilibrium_theta(self):
        """Perfil tras saturar y drenar libremente: h = −(altura sobre el fondo)."""
        return [self.vg.theta(-(self.height - (i + 0.5) * self.dz)) for i in range(self.n)]

    def capacity_sensor_vwc(self):
        """Lectura del sensor en capacidad de contenedor (lo que mediría el ensayo de CC)."""
        eq = self.equilibrium_theta()
        idx = self._zone(self.sensor_z0, self.sensor_z1)
        return 100.0 * sum(eq[i] for i in idx) / len(idx)

    def capacity_ml(self):
        return sum(self.equilibrium_theta()) * self.layer_ml

    def wilting_vwc(self):
        return 100.0 * self.vg.theta(HEAD_FLOOR)

    # ── Raíces ──────────────────────────────────────────────────────────────
    def root_weights(self, root_g):
        """Peso radicular por capa: decae con la profundidad y llega hasta donde alcanzan las raíces."""
        depth_frac = min(1.0, 0.2 + root_g / max(ROOT_G_PER_L_FULL * self.pot_ml / 1000.0, 1e-6))
        key = (round(depth_frac, 3), self.n)
        cached = self._rw_cache.get("k")
        if cached is not None and cached[0] == key:
            return cached[1]
        w = []
        for i in range(self.n):
            zc = (i + 0.5) / self.n
            w.append(math.exp(-ROOT_DECAY * zc) if zc <= depth_frac + 0.5 / self.n else 0.0)
        tot = sum(w) or 1.0
        w = [x / tot for x in w]
        self._rw_cache["k"] = (key, w)
        return w

    def allocate_uptake(self, demand_ml_min, dt_min, root_g, floor_head=HEAD_FLOOR, heads=None):
        """Reparte la demanda transpirativa entre capas (compensación: lo que no da una capa lo da otra).

        Devuelve (mL por minuto por capa, total efectivo).
        """
        w = self.root_weights(root_g)
        h = heads if heads is not None else self.heads()
        om = [wi * feddes_factor(hi) for wi, hi in zip(w, h)]
        floor = [self.vg.theta(floor_head)] * self.n
        avail = [max(0.0, (t - f) * self.layer_ml) / max(dt_min, 1e-9) for t, f in zip(self.theta, floor)]
        out = [0.0] * self.n
        remaining = max(0.0, demand_ml_min)
        for _ in range(3):
            tot = sum(o for o, a, u in zip(om, avail, out) if a - u > 1e-12)
            if remaining <= 1e-12 or tot <= 1e-12:
                break
            given = 0.0
            for i in range(self.n):
                room = avail[i] - out[i]
                if room <= 1e-12 or om[i] <= 0.0:
                    continue
                take = min(room, remaining * om[i] / tot)
                out[i] += take
                given += take
            remaining -= given
        return out, sum(out)

    def evaporation(self, demand_ml_min, dt_min):
        """Evaporación desde las dos capas superiores hasta un piso de 3000 cm de tensión."""
        floor = self.vg.theta(-3000.0)
        out = [0.0] * self.n
        remaining = max(0.0, demand_ml_min)
        for i in (0, 1):
            room = max(0.0, (self.theta[i] - floor) * self.layer_ml) / max(dt_min, 1e-9)
            take = min(room, remaining * (0.7 if i == 0 else 1.0))
            out[i] = take
            remaining -= take
        return out, sum(out)

    def evaporation_factor(self, h0=None):
        """Fracción de la evaporación potencial que permite la humedad de la capa superior."""
        if h0 is None:
            h0 = self.vg.head(self.theta[0])
        if h0 >= -50.0:
            return 1.0
        return max(0.0, 1.0 - (math.log10(-h0) - math.log10(50.0)) / (math.log10(1000.0) - math.log10(50.0)))

    # ── Paso ────────────────────────────────────────────────────────────────
    def step(self, dt_min, inflow_ml_min, c_in, sinks_ml_min, uptake_mass=None, evap_ml_min=None, heads=None):
        """Avanza dt_min. Devuelve el drenaje [mL].

        c_in: concentración de entrada por trazador (mass/mL: EC, mg/mL, pH).
        uptake_mass: {trazador: masa quitada por capa en este paso} (absorción radicular de sal/iones).
        evap_ml_min: extracción por evaporación por capa, sin sal.
        """
        n, dz, vg, A = self.n, self.dz, self.vg, self.area
        evap = evap_ml_min or [0.0] * n
        sink_cm = [(s + e) / A for s, e in zip(sinks_ml_min, evap)]      # cm/min
        q_top = inflow_ml_min / A                                          # cm/min
        theta_n = list(self.theta)
        w_before = self.water_ml
        hm = list(heads) if heads is not None else [vg.head(t) for t in theta_n]

        for it in range(40):
            thm = [vg.theta(x) for x in hm]
            cap = [vg.capacity(x) for x in hm]
            kn = [vg.conductivity(x) for x in hm]
            # conductividad en la interfaz: nodo de aguas arriba según la dirección del flujo
            # (con dz de 1-2 cm el promedio geométrico frena artificialmente los frentes de mojado)
            ki = [kn[i] if (hm[i] - hm[i + 1] + dz) >= 0.0 else kn[i + 1] for i in range(n - 1)]
            a = [0.0] * n
            b = [0.0] * n
            c = [0.0] * n
            r = [0.0] * n
            for i in range(n):
                diag = cap[i] * dz / dt_min
                rhs = -dz * (thm[i] - theta_n[i]) / dt_min - sink_cm[i]
                if i > 0:
                    kup = ki[i - 1]
                    diag += kup / dz
                    a[i] = -kup / dz
                    rhs += kup * ((hm[i - 1] - hm[i]) / dz + 1.0)
                else:
                    rhs += q_top
                if i < n - 1:
                    kdn = ki[i]
                    diag += kdn / dz
                    c[i] = -kdn / dz
                    rhs -= kdn * ((hm[i] - hm[i + 1]) / dz + 1.0)
                else:
                    if hm[i] > -dz / 2.0:            # cara de filtración activa
                        kb = kn[i]
                        diag += 2.0 * kb / dz
                        rhs -= kb * (1.0 + 2.0 * hm[i] / dz)
                b[i] = diag
                r[i] = rhs
            # Thomas
            cp = [0.0] * n
            dp = [0.0] * n
            cp[0] = c[0] / b[0]
            dp[0] = r[0] / b[0]
            for i in range(1, n):
                den = b[i] - a[i] * cp[i - 1]
                cp[i] = c[i] / den
                dp[i] = (r[i] - a[i] * dp[i - 1]) / den
            delta = [0.0] * n
            delta[-1] = dp[-1]
            for i in range(n - 2, -1, -1):
                delta[i] = dp[i] - cp[i] * delta[i + 1]
            # amortiguación: ninguna capa cambia más de MAX_DTHETA de θ por iteración
            lam = 1.0
            for _try in range(8):
                worst = max(abs(vg.theta(max(H_MIN, min(80.0, hm[i] + lam * delta[i]))) - thm[i]) for i in range(n))
                if worst <= MAX_DTHETA:
                    break
                lam *= 0.5
            err = 0.0
            for i in range(n):
                step_h = lam * delta[i]
                hm[i] = max(H_MIN, min(80.0, hm[i] + step_h))
                err = max(err, abs(vg.theta(hm[i]) - thm[i]))
            if err < THETA_TOL and lam == 1.0:
                break

        new_theta = [vg.theta(x) for x in hm]
        # Balance de masa: lo que no se almacenó ni extrajo y salió por el fondo es el drenaje.
        w_after = sum(new_theta) * self.layer_ml
        sink_ml = sum(sinks_ml_min) * dt_min + sum(evap) * dt_min
        drain = inflow_ml_min * dt_min - sink_ml - (w_after - w_before)
        if drain < 0.0:
            # error numérico (agua "creada"): se quita de la capa inferior para conservar masa
            fix = -drain / self.layer_ml
            new_theta[-1] = max(vg.tr, new_theta[-1] - fix)
            drain = 0.0
        self.theta = new_theta

        # Flujos entre capas consistentes con el balance (mL/min, + hacia abajo)
        f = [0.0] * (n + 1)
        f[0] = inflow_ml_min
        for i in range(n):
            dW = (new_theta[i] - theta_n[i]) * self.layer_ml / dt_min
            f[i + 1] = f[i] - sinks_ml_min[i] - evap[i] - dW
        f[n] = drain / dt_min
        self.last_fluxes = f
        self._transport(dt_min, f, c_in, sinks_ml_min, evap, uptake_mass or {}, theta_n)
        return drain

    def _transport(self, dt_min, f, c_in, sinks, evap, uptake_mass, theta_old):
        """Advección *upwind* implícita hacia abajo (explícita hacia arriba) + difusión, por trazador."""
        n = self.n
        w_new = [max(t * self.layer_ml, 1e-6) for t in self.theta]
        w_old = [max(t * self.layer_ml, 1e-6) for t in theta_old]
        self.last_drained = {}
        for name in TRACERS:
            s = list(self.tracers[name])
            c_old = [s[i] / w_old[i] for i in range(n)]
            up = [0.0] * (n + 1)       # masa que sube por la interfaz i (de la capa i a la i-1)
            for i in range(1, n):
                if f[i] < 0.0:
                    up[i] = min(-f[i] * dt_min * c_old[i], 0.5 * s[i])
            c_prev = c_in.get(name, 0.0)
            um = uptake_mass.get(name)
            drained = 0.0
            for i in range(n):
                f_in = f[i] if f[i] > 0.0 else 0.0
                f_out = f[i + 1] if f[i + 1] > 0.0 else 0.0
                mass_in = f_in * dt_min * c_prev + (up[i + 1] if i < n - 1 else 0.0)
                mass_out_up = up[i] if i > 0 else 0.0
                if name in INTENSIVE:
                    sink = (sinks[i] + evap[i]) * dt_min * c_old[i]
                else:
                    sink = um[i] if um is not None else 0.0
                base = max(0.0, s[i] + mass_in - mass_out_up - sink)
                s_new = base / (1.0 + f_out * dt_min / w_new[i])
                c_prev = s_new / w_new[i]
                drained = f_out * dt_min * c_prev
                s[i] = s_new
            conc = [s[i] / w_new[i] for i in range(n)]
            for i in range(n - 1):
                th_avg = 0.5 * (self.theta[i] + self.theta[i + 1])
                m = SALT_DIFF_CM2_MIN * th_avg * self.area / self.dz * (conc[i] - conc[i + 1]) * dt_min
                m = max(-0.5 * s[i + 1], min(0.5 * s[i], m))
                s[i] -= m
                s[i + 1] += m
            self.tracers[name] = s
            self.last_drained[name] = drained
        self.last_salt_drained = self.last_drained["salt"]
