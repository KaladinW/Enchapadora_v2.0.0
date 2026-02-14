# config.py
import os

# --- PARAMETROS FISICOS DE LA MAQUINA ---
VELOCIDAD_MAQUINA_M_MIN = 10
VELOCIDAD_MM_S = (VELOCIDAD_MAQUINA_M_MIN * 1000) / 60.0

# Posiciones de los grupos (mm desde el sensor de entrada)
POS_FRESADOR = 150
POS_ALIMENTADOR = 300
POS_GUILLOTINA = 450
POS_RETESTADOR = 600
POS_REFILADOR = 900

# --- MAPA DE PINES GPIO (BCM) ---

# 1. ENTRADAS DIGITALES (Sensores y Seguridad)
# Configuración Pull-Up: 1 = Reposo/Abierto, 0 = Activo/Cerrado
PIN_SENSOR_ENTRADA = 4       # Pulsador 1 [Tabla 3]
# PIN_SENSOR_RETESTADOR = 17 # ELIMINADO POR SOLICITUD DE SIMPLIFICACION
PIN_PARO_ENTRADA = 27        # Dip switch (NC) [Tabla 3]
PIN_PARO_SALIDA = 22         # Dip switch (NC) [Tabla 3]

# 2. CONEXIÓN SPI (MCP3008 - Temperatura) [Tabla 3]
SPI_CLK = 11
SPI_MISO = 9
SPI_MOSI = 10
SPI_CS = 8

# 3. SALIDAS (Actuadores y Motores) [Tabla 4]
PIN_CADENA = 5               # Contactor Cadena Avance
PIN_MOTOR_FRESADOR = 6       # Contactor Motores Fresado
PIN_EV_FRESA_1 = 12          # EV Fresa 1
PIN_EV_FRESA_2 = 16          # EV Fresa 2
PIN_SSR_ENCOLADOR = 18       # SSR Calefacción
PIN_EV_ALIMENTADOR = 20      # EV Alimentador Canto
PIN_EV_GUILLOTINA = 21       # EV Guillotina
PIN_MOTOR_RETESTADOR = 23    # Contactor Motores Retestador
PIN_MOTOR_REFILADOR = 26     # Contactor Motores Refilador

# --- PARAMETROS DE CONTROL ---
TIEMPO_CICLO = 0.05  # El cerebro piensa cada 50ms
TEMP_OBJETIVO = 190.0  # Grados Celsius
