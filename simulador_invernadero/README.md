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

1. **Dinámica del Sustrato (`substrate.py`)**:
   - La maceta es una columna de 12 capas. El agua se mueve por la ecuación de Richards con retención van Genuchten-Mualem (esquema implícito, conserva masa). El goteo entra por arriba y el fondo es una cara de filtración: solo drena cuando la capa inferior alcanza la presión del orificio.
   - La **capacidad de contenedor** y su gradiente vertical (agua colgante) salen de la curva y de la altura de la maceta; ya no es un número fijo. El ensayo CC del firmware virtual la mide como lo haría el real.
   - Las sales (EC·mL por capa) viajan con el agua; la evaporación las deja y la raíz absorbe una fracción. El sensor lee el promedio de una zona de profundidad configurable.
   - **Nutrición por ion (N, P, K) y pH**: cada capa lleva su masa de N, P, K, sales y protones. La planta pide lo que exige su crecimiento reciente (contenido objetivo del tejido × tasa de crecimiento) más la recuperación de un déficit; absorbe de cada capa lo que aporten el flujo de masa (agua tomada × concentración) o la absorción activa saturable (Vmax·raíz·C/(Km+C)), sin vaciar la capa. El estado nutricional (contenido/objetivo) limita la fotosíntesis. Al absorber N como nitrato y K como catión cambia el pH según el tampón del sustrato. Las sales no medidas (Ca, Mg, SO₄…) no las absorbe la planta y se quedan en la maceta.
   - Los contenidos objetivo, Vmax, Km, el tampón y la fracción amoniacal son **valores del modelo, no medidos**: se editan a mano (panel de clima/cultivo, comando `set_nutrient`) y nunca cambian solos. Los volúmenes y la EC del riego los define el usuario; el simulador no los ajusta.
   - **Estrés y fenología**: la sequía sale de la tensión del sustrato por capa (más la tensión osmótica de la sal que supere lo tolerado), con compensación entre capas; esa tensión cierra los estomas en Penman-Monteith y limita el crecimiento. La hipoxia sale de la porosidad con aire de la zona radicular, y la salinidad usa un umbral de CE por etapa con pendiente (Maas–Hoffman). La salud de raíz tiene memoria: el daño por sal o por falta de aire se recupera a ~2 %/día. La floración se induce tras acumular días cortos (≤13,5 h) y las etapas avanzan con los días desde la inducción, acelerados o frenados por la temperatura (tiempo térmico).
   - Contrastes con datos publicados (hay pruebas): N a 30 vs 160 mg/L baja la fotosíntesis ~25 % (Saloner y Bernstein 2020; absorción de N ajustada a ese dato), óptimo térmico 25–30 °C, luz de hoja saturada hacia 1400 µmol/m²/s y +50 % de fotosíntesis de hoja a 750 ppm de CO₂ (Chandra et al. 2008). Sequía, salinidad e hipoxia no tienen umbrales medidos en cannabis: son valores del modelo, editables a mano y a calibrar con sus datos (fase 6).
   - Solo el coco tiene una curva de referencia (aproximada); rockwool, turba y suelo son ilustrativos y deben calibrarse con mediciones.
   - Evapotranspiración: Penman-Monteith del dosel (PPFD, VPD, ventilación, área foliar); la raíz extrae por capa según su distribución y la tensión.

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
