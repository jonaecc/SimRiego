"""
Servidor WebSocket y HTTP para el Simulador de Invernadero Interactivo 3D.
"""

import asyncio
import json
import os
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from physics_engine import GreenhouseEnvironment

app = FastAPI(title="Simulador de Invernadero Interactivo 3D")

# Instancia global del entorno
env = GreenhouseEnvironment()

# Clientes WebSocket conectados
connected_clients = set()

# Configuración de archivos estáticos
static_dir = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=static_dir), name="static")

@app.get("/")
async def get_index():
    return FileResponse(os.path.join(static_dir, "index.html"))

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    global env
    await websocket.accept()
    connected_clients.add(websocket)
    try:
        # Enviar estado inicial inmediato
        await websocket.send_text(json.dumps(env.dump_state()))
        
        while True:
            data_text = await websocket.receive_text()
            try:
                msg = json.loads(data_text)
                cmd = msg.get("cmd")
                
                if cmd == "set_speed":
                    env.set_time_speed(msg.get("value", 60.0))
                elif cmd == "toggle_pause":
                    env.toggle_pause()
                elif cmd == "trigger_shot":
                    plant_id = msg.get("plant_id", 0)
                    duration = msg.get("duration", 15)
                    env.trigger_shot(plant_id, duration)
                elif cmd == "stop_irrigation":
                    env.close_all_valves()
                elif cmd == "empty_tray_manually":
                    plant_id = msg.get("plant_id", 0)
                    env.empty_tray_manually(plant_id)
                elif cmd == "tare_tray":
                    plant_id = msg.get("plant_id", 0)
                    env.tare_tray(plant_id)
                elif cmd == "set_auto_tray_service":
                    env.set_auto_tray_service(msg.get("enabled", False))
                elif cmd == "start_cc_test":
                    env.start_field_capacity_test(
                        msg.get("plant_id", env.selected_plant_id),
                        msg.get("pot_volume_ml", 0), msg.get("pulse_ml", 0),
                        msg.get("wait_minutes", 0),
                    )
                elif cmd == "skip_days":
                    days = msg.get("days", 1)
                    # Simular paso acelerado en bloques
                    target_seconds = days * 86400.0
                    step_size = 300.0  # pasos de 5 min
                    steps = int(target_seconds / step_size)
                    for _ in range(steps):
                        env.step(step_size / env.time_speed)
                elif cmd == "set_climate":
                    if "temp" in msg: env.target_temp_day = float(msg["temp"])
                    if "rh" in msg: env.target_rh_day = float(msg["rh"])
                    if "light_dimmer" in msg: env.light_dimmer_pct = float(msg["light_dimmer"])
                    if "ppfd_max" in msg: env.set_ppfd_manual(msg["ppfd_max"])
                    if "footprint_m2" in msg: env.plant_footprint_m2 = max(0.05, min(4.0, float(msg["footprint_m2"])))
                    if "aero_cond" in msg: env.aero_conductance_m_s = max(0.002, min(0.5, float(msg["aero_cond"])))
                elif cmd == "set_lamp":
                    env.set_lamp(msg.get("params", {}))
                elif cmd == "set_carbon":
                    env.set_carbon_params(msg.get("params", {}))
                elif cmd == "set_nutrient":
                    env.set_nutrient_params(msg.get("params", {}))
                elif cmd == "set_stress":
                    env.set_stress_params(msg.get("params", {}))
                elif cmd == "update_solution":
                    env.update_solution(msg.get("pump", "P1"), msg.get("solution", {}))
                elif cmd == "select_plant":
                    env.set_selected_plant(msg.get("plant_id", 0))
                elif cmd == "set_management_stage":
                    env.set_management_stage(msg.get("plant_id", env.selected_plant_id), msg.get("stage", "PLANTULA"))
                elif cmd == "set_substrate":
                    env.set_substrate(msg.get("plant_id", env.selected_plant_id), msg.get("substrate_type", "COCO"))
                elif cmd == "toggle_auto_mode":
                    env.set_auto_irrigation_mode(not env.auto_irrigation_mode)
                elif cmd == "update_plant_config":
                    plant_id = msg.get("plant_id", env.selected_plant_id)
                    cfg_data = msg.get("config", {})
                    env.update_plant_config(plant_id, cfg_data)
                elif cmd == "update_schedule":
                    env.update_schedule(msg)
                elif cmd == "update_stage_schedule":
                    env.update_stage_schedule(msg.get("stage", env.room_stage), msg)
                elif cmd == "update_stage_profile":
                    env.update_stage_profile(msg.get("plant_id", env.selected_plant_id),
                                             msg.get("stage", "PLANTULA"), msg.get("config", {}))
                elif cmd == "update_crop_plan":
                    env.update_crop_plan(msg.get("stage", "PLANTULA"), msg.get("min_days"),
                                         msg.get("max_days"), msg.get("biomass_target_g"))
                elif cmd == "reset_crop":
                    env = GreenhouseEnvironment()
                elif cmd == "transplant":
                    plant_id = msg.get("plant_id", env.selected_plant_id)
                    new_pot_ml = float(msg.get("new_pot_ml", 5000.0))
                    env.transplant_plant(plant_id, new_pot_ml)

            except Exception as e:
                print(f"Error procesando comando WS: {e}")
                
    except WebSocketDisconnect:
        connected_clients.remove(websocket)
    except Exception as e:
        if websocket in connected_clients:
            connected_clients.remove(websocket)


async def simulation_loop():
    """Bucle asíncrono que corre la física a 20 FPS reales y transmite a los clientes."""
    fps = 20.0
    dt_real = 1.0 / fps
    broadcast_interval = 0.1  # Broadcast cada 100 ms (10 Hz)
    time_since_broadcast = 0.0
    
    while True:
        await asyncio.sleep(dt_real)
        
        # Avanzar física
        env.step(dt_real)
        
        # Transmisión WebSocket
        time_since_broadcast += dt_real
        if time_since_broadcast >= broadcast_interval and connected_clients:
            time_since_broadcast = 0.0
            state = env.dump_state()
            state_json = json.dumps(state)
            
            # Broadcast seguro a todos los clientes
            disconnected = []
            for ws in connected_clients:
                try:
                    await ws.send_text(state_json)
                except Exception:
                    disconnected.append(ws)
            for ws in disconnected:
                if ws in connected_clients:
                    connected_clients.remove(ws)


@app.on_event("startup")
async def startup_event():
    asyncio.create_task(simulation_loop())


if __name__ == "__main__":
    import uvicorn
    print("\n=======================================================")
    print(" Invernadero Virtual 3D - Servidor Interactivo Iniciado")
    print(" Abrir en navegador: http://localhost:8000")
    print("=======================================================\n")
    uvicorn.run("server:app", host="127.0.0.1", port=8000, reload=False)
