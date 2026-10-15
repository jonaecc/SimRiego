"""
Lanzador del Simulador de Invernadero 3D Interactivo.
Inicia el servidor local y abre automáticamente la interfaz en el navegador web.
"""

import os
import sys
import time
import webbrowser
import subprocess

def main():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    sim_dir = os.path.join(base_dir, "simulador_invernadero")
    
    # Evitar problemas de codificación en consolas Windows (cp1252)
    if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
        try:
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass

    print("\n" + "="*65)
    print(" [AGRO-SIM 3D] // INVERNADERO VIRTUAL & GEMELO DIGITAL")
    print("="*65)
    print(" Iniciando servidor de simulacion en http://localhost:8000 ...")
    print(" Abriendo interfaz grafica 3D en tu navegador predeterminado...")
    print(" Presiona Ctrl+C en esta consola para detener el simulador.")
    print("="*65 + "\n")
    
    # Abrir navegador tras 1.2 segundos
    def open_browser():
        time.sleep(1.2)
        webbrowser.open("http://localhost:8000")
        
    import threading
    threading.Thread(target=open_browser, daemon=True).start()
    
    # Asegurar que el directorio del simulador esté en sys.path
    if sim_dir not in sys.path:
        sys.path.insert(0, sim_dir)
    os.chdir(sim_dir)
    
    from server import app
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")

if __name__ == "__main__":
    main()
