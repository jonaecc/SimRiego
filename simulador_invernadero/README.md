# 🌱 AGRO-SIM 3D — Simulador Interactivo de Invernadero y Gemelo Digital

Simulador interactivo 3D acelerado por GPU (Three.js/WebGL) conectado a un motor físico-biológico en tiempo real (FastAPI/WebSockets) para emular cultivos de alta precisión y probar estrategias de riego (*Crop Steering*).

---

## 🚀 Inicio Rápido

Ejecuta en tu terminal:
```bash
python iniciar_simulador.py
```
O simplemente haz doble clic en `iniciar_simulador.bat`. Se abrirá automáticamente tu navegador en `http://localhost:8000`.

---

## 🎮 Controles y Navegación

### 1. Visor 3D del Invernadero
- **Rotación 360°**: Clic izquierdo y arrastrar el ratón.
- **Zoom**: Rueda del ratón (scroll).
- **Desplazamiento (Pan)**: Clic derecho y arrastrar.

### 2. Control del Tiempo (Time-Warp)
- **1x**: Tiempo real (1 segundo real = 1 segundo de simulación).
- **10x**: 1 segundo real = 10 segundos de simulación.
- **60x (Predeterminado)**: 1 segundo real = 1 minuto de cultivo.
- **300x**: 1 segundo real = 5 minutos de cultivo.
- **1000x**: 1 segundo real = ~17 minutos (un día entero de cultivo transcurre en ~80 segundos).
- **Botón `⏭ +1 Día`**: Adelanta 24 horas instantáneamente en bloques de simulación.
- **Botón `⏸ Pausa`**: Congela la física en cualquier momento para inspeccionar la planta.
- **Botón `↺ Reiniciar`**: Reinicia el ciclo desde el Día 1 en etapa de plántula.

### 3. Hidráulica & Riego Manual
- **Botón `⚡ Disparo (15s)`**: Abre la electroválvula de la maceta y activa la bomba P1 durante un pulso de riego.
  - Verás las partículas de agua desprendiéndose de los 3 goteros en 3D.
  - El VWC del sustrato se eleva rápidamente.
  - El color del sustrato pasa de seco (café claro) a húmedo (café oscuro).
  - Al superar la Capacidad de Campo (CC = 46%), el exceso drena a la bandeja con escorrentía visible y pesaje en la celda de carga.
- **Botón `🗑 Vaciar Bandeja`**: Restablece el peso de drenaje de la bandeja a 0 g.

### 4. Ajustes Microclimáticos en Vivo
- **Temperatura Diurna (°C)**: Desliza para alterar la temperatura ambiental.
- **Humedad Relativa (%)**: Modifica la HR del invernadero.
- **EC Tanque de Riego (mS/cm)**: Regula la concentración de sales y nutrientes de la solución de riego.
- **Dimmer LED (%)**: Modifica la potencia luminosa del panel superior (PPFD en µmol/m²/s).

---

## 🔬 Modelado Físico y Biológico Implementado

1. **Dinámica del Sustrato (`physics_engine.py`)**:
   - $W$: Volumen de agua en sustrato [ml] ($VWC = 100 \cdot W / V_{maceta}$).
   - $S$: Concentración de sales minerales ($EC = S / W$).
   - Capacidad de Campo ($CC = 46\%$): El agua que supera este umbral drena hacia la bandeja arrastrando sales a la EC de saturación.
   - Evapotranspiración ($ET$): Transpiración diurna y nocturna dinámica basada en área foliar, biomasa y VPD.

2. **Crecimiento Vegetal Procedural 3D**:
   - **Plántula (Días 1-10)**: Estructura pequeña, raíces incipientes.
   - **Vegetativo (Días 11-28)**: Gran elongación foliar y ramificación lateral.
   - **Preflora / Estiramiento (Días 29-42)**: Crecimiento vertical acelerado.
   - **Floración Masiva (Días 43-62)**: Cese del estiramiento y engorde de cogollos con tricomas.
   - **Maduración / Flush (Días 63+)**: Senescencia natural y maduración de resina.

3. **Respuesta Fisiológica y Síntomas Visuales 3D**:
   - **Pérdida de turgencia (Marchitez)**: Si $VWC < 20\%$, las ramas y hojas se inclinan hacia abajo (`leaf_droop`).
   - **Toxicidad de sales**: Si la $EC > 4.2\text{ mS/cm}$, las hojas adoptan forma de garra (`leaf_claw`) y puntas quemadas (`tip_burn`).
   - **Clorosis**: Amarilleo de hojas si hay asfixia radicular ($VWC > 65\%$ prolongado) o senescencia.
