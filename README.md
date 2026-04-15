# Enchapadora de Canto Automática V2.0.0🚀

Sistema de control industrial basado en **Raspberry Pi 4** con interfaz HMI moderna para el proceso de enchapado de madera. Este proyecto reemplaza lógicas obsoletas (PLC) utilizando tecnologías de código abierto e IoT.

## 🛠️ Stack Tecnológico
* **Backend:** Python 3.11 + Flask.
* **Tiempo Real:** Flask-SocketIO (WebSockets).
* **Frontend:** HTML5, CSS3 (Dark Mode), Bootstrap 5.
* **Hardware de Simulación:** MCP3008 (ADC), Potenciómetro (Simulación PT100), Optoacopladores PC817, Entradas/Salidas Digitales (LEDs y Pulsadores).

## ⚙️ Características
- Control de temperatura PID en tiempo real.
- Seguimiento de pieza mediante **Encoder Virtual** (Ciclo de 50 ms).
- Gestión automática de 6 grupos de trabajo (Fresado, Encolado, Retestado, etc.).
- Sistema de seguridad por Paros de Emergencia (NC) con bloqueo instantáneo.
- Arquitectura cliente-servidor con latencia mínima en red LAN.

## 🚀 Instalación y Ejecución
1. Clonar el repositorio.
2. Crear el entorno virtual: `python3 -m venv venv`
3. Activar el entorno: `source venv/bin/activate`
4. Instalar las dependencias: `pip install -r requirements.txt`
5. Ejecutar el servidor: `python3 app.py`

*Nota: La HMI es accesible desde cualquier navegador web en la misma red local a través del puerto 5000.*
