import os
from flask import Flask, render_template
from flask_socketio import SocketIO, emit
import time
import threading
import RPi.GPIO as GPIO
import config

# --- CONFIGURACIÓN DE RUTAS ABSOLUTAS ---
# Esto asegura que Flask encuentre los templates sin importar desde dónde se ejecute
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = os.path.join(BASE_DIR, 'templates')
STATIC_DIR = os.path.join(BASE_DIR, 'static')

app = Flask(__name__, template_folder=TEMPLATE_DIR, static_folder=STATIC_DIR)
app.config['SECRET_KEY'] = 'secreto_industrial_enchapadora'
socketio = SocketIO(app, async_mode='threading')

# --- ESTADO GLOBAL DE LA MAQUINA (MEMORIA RAM COMPARTIDA) ---
estado_maquina = {
    # Seguridad y Sistema
    "tension_mando": False,
    "emergencia": False,
    "mensaje_error": "",

    # Tracking de Pieza
    "encoder_pos": 0.0,      # Posición actual (mm)
    "pieza_detectada": False,  # Estado físico del sensor entrada
    "longitud_pieza": 0.0,   # Longitud medida
    "tracking_activo": False,  # Si el encoder virtual está contando

    # Habilitadores (Desde HMI)
    "habil_calefaccion": False,
    "habil_cadena": False,
    "habil_fresador": False,
    "habil_alimentador": False,
    "habil_retestador": False,
    "habil_refilador": False,

    # Estados Físicos - SALIDAS (Feedback para LEDs de la HMI)
    "act_calefaccion": False,
    "temp_actual": 0.0,
    "act_cadena": False,
    "act_fresador": False,   # Motor Fresa
    "act_fresa1": False,     # EV Fresa 1
    "act_fresa2": False,     # EV Fresa 2
    "act_alimentador": False,
    "act_guillotina": False,
    "act_retestador": False,
    "act_refilador": False,

    # Estados Físicos - ENTRADAS (Para monitor I/O)
    "in_sensor_entrada": 1,  # 1=Reposo
    "in_paro_entrada": 1,
    "in_paro_salida": 1,

    # Estado Lógico Retestador (Para animación visual)
    "retestador_bajando": False
}

# Variables internas para tiempos
timers = {
    "alimentador_inicio": 0,
    "guillotina_inicio": 0,
    "retestador_trasero_inicio": 0
}
flags = {
    "guillotina_hecha": False,
    "alimentador_activo": False,
    "retestador_delantero_hecho": False,
    "retestador_trasero_hecho": False
}

# --- CONFIGURACIÓN GPIO ---
GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)

# Configurar Salidas [Tabla 4]
salidas = [
    config.PIN_CADENA, config.PIN_MOTOR_FRESADOR, config.PIN_EV_FRESA_1,
    config.PIN_EV_FRESA_2, config.PIN_SSR_ENCOLADOR, config.PIN_EV_ALIMENTADOR,
    config.PIN_EV_GUILLOTINA, config.PIN_MOTOR_RETESTADOR, config.PIN_MOTOR_REFILADOR
]
for pin in salidas:
    GPIO.setup(pin, GPIO.OUT, initial=GPIO.LOW)

# Configurar Entradas Digitales [Tabla 3 - Sin GPIO 17]
entradas = [config.PIN_SENSOR_ENTRADA,
            config.PIN_PARO_ENTRADA, config.PIN_PARO_SALIDA]
for pin in entradas:
    GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)

# Configurar Pines SPI para MCP3008 (Manual)
GPIO.setup(config.SPI_MOSI, GPIO.OUT)
GPIO.setup(config.SPI_MISO, GPIO.IN)
GPIO.setup(config.SPI_CLK, GPIO.OUT)
GPIO.setup(config.SPI_CS, GPIO.OUT)

# --- FUNCIÓN: LEER MCP3008 (Temperatura Simulada) ---


def leer_adc_mcp3008(canal):
    """Lee un canal (0-7) del MCP3008 usando bit-banging."""
    if canal > 7 or canal < 0:
        return -1

    GPIO.output(config.SPI_CS, True)
    GPIO.output(config.SPI_CLK, False)
    GPIO.output(config.SPI_CS, False)

    command = canal
    command |= 0x18
    command <<= 3

    for i in range(5):
        if command & 0x80:
            GPIO.output(config.SPI_MOSI, True)
        else:
            GPIO.output(config.SPI_MOSI, False)
        command <<= 1
        GPIO.output(config.SPI_CLK, True)
        GPIO.output(config.SPI_CLK, False)

    lectura = 0
    for i in range(12):
        GPIO.output(config.SPI_CLK, True)
        GPIO.output(config.SPI_CLK, False)
        lectura <<= 1
        if GPIO.input(config.SPI_MISO):
            lectura |= 0x1

    GPIO.output(config.SPI_CS, True)
    lectura >>= 1
    return lectura & 0x3FF

# --- FUNCIÓN AUXILIAR PARA ESCRIBIR SALIDAS ---


def escribir_salida(pin, estado, clave_diccionario=None):
    """Activa/Desactiva pin físico y actualiza el diccionario para la Web."""
    GPIO.output(pin, GPIO.HIGH if estado else GPIO.LOW)
    if clave_diccionario:
        estado_maquina[clave_diccionario] = estado

# --- FUNCIÓN DE SEGURIDAD ---


def verificar_emergencia():
    p1 = GPIO.input(config.PIN_PARO_ENTRADA)
    p2 = GPIO.input(config.PIN_PARO_SALIDA)

    # Actualizamos el estado para el monitor I/O
    estado_maquina["in_paro_entrada"] = p1
    estado_maquina["in_paro_salida"] = p2

    if p1 == 0 or p2 == 0:
        estado_maquina["emergencia"] = True
        estado_maquina["mensaje_error"] = "¡PARO DE EMERGENCIA ACTIVADO! REVISE SETAS."
        estado_maquina["tension_mando"] = False
        return True
    return False

# --- HILO PRINCIPAL DE CONTROL (LOOP DE LA MAQUINA) ---


def control_loop():
    print("--- PLC INICIADO: SISTEMA ENCHAPADORA 4.0 ---")

    while True:
        start_time = time.time()

        # 1. LECTURA DE TEMPERATURA
        adc_val = leer_adc_mcp3008(0)
        temp_c = (adc_val / 1023.0) * 250.0
        estado_maquina["temp_actual"] = round(temp_c, 1)

        # 2. VERIFICAR SEGURIDAD
        if verificar_emergencia():
            for pin in salidas:
                GPIO.output(pin, GPIO.LOW)
            estado_maquina["act_cadena"] = False
            estado_maquina["act_fresador"] = False
            estado_maquina["act_retestador"] = False
            estado_maquina["act_refilador"] = False
            estado_maquina["act_calefaccion"] = False

            socketio.emit('update_status', estado_maquina)
            time.sleep(0.1)
            continue

        # 3. LOGICA SIN TENSIÓN DE MANDO
        if not estado_maquina["tension_mando"]:
            escribir_salida(config.PIN_CADENA, False, "act_cadena")
            escribir_salida(config.PIN_MOTOR_FRESADOR, False, "act_fresador")
            escribir_salida(config.PIN_MOTOR_RETESTADOR,
                            False, "act_retestador")
            escribir_salida(config.PIN_MOTOR_REFILADOR, False, "act_refilador")
            escribir_salida(config.PIN_SSR_ENCOLADOR, False, "act_calefaccion")

        # 4. LOGICA CON TENSIÓN DE MANDO (RUN)
        else:
            # A. CALEFACCIÓN
            if estado_maquina["habil_calefaccion"]:
                if estado_maquina["temp_actual"] < config.TEMP_OBJETIVO:
                    escribir_salida(config.PIN_SSR_ENCOLADOR,
                                    True, "act_calefaccion")
                else:
                    escribir_salida(config.PIN_SSR_ENCOLADOR,
                                    False, "act_calefaccion")
            else:
                escribir_salida(config.PIN_SSR_ENCOLADOR,
                                False, "act_calefaccion")

            # B. CADENA DE AVANCE
            if estado_maquina["habil_cadena"]:
                escribir_salida(config.PIN_CADENA, True, "act_cadena")
            else:
                escribir_salida(config.PIN_CADENA, False, "act_cadena")

            # C. MOTORES PERIFERICOS
            # Aseguramos que el motor fresador responda al habilitador
            escribir_salida(config.PIN_MOTOR_FRESADOR,
                            estado_maquina["habil_fresador"], "act_fresador")
            escribir_salida(config.PIN_MOTOR_REFILADOR,
                            estado_maquina["habil_refilador"], "act_refilador")
            escribir_salida(config.PIN_MOTOR_RETESTADOR,
                            estado_maquina["habil_retestador"], "act_retestador")

            # D. LOGICA DE SECUENCIA (ENCODER VIRTUAL)
            sensor_in = GPIO.input(config.PIN_SENSOR_ENTRADA)
            # Actualizar monitor I/O
            estado_maquina["in_sensor_entrada"] = sensor_in

            # --- DETECCIÓN DE PIEZA ---
            if sensor_in == 0 and not estado_maquina["pieza_detectada"]:
                estado_maquina["pieza_detectada"] = True
                estado_maquina["tracking_activo"] = True
                estado_maquina["encoder_pos"] = 0
                estado_maquina["longitud_pieza"] = 0
                flags["retestador_delantero_hecho"] = False
                flags["retestador_trasero_hecho"] = False
                flags["guillotina_hecha"] = False
                flags["alimentador_activo"] = False
                print(">> PIEZA INGRESANDO")

            if sensor_in == 1 and estado_maquina["pieza_detectada"]:
                estado_maquina["pieza_detectada"] = False
                print(
                    f">> PIEZA DENTRO. Longitud: {estado_maquina['longitud_pieza']:.1f} mm")

            # --- AVANCE DEL ENCODER ---
            if estado_maquina["act_cadena"] and estado_maquina["tracking_activo"]:
                avance = config.VELOCIDAD_MM_S * config.TIEMPO_CICLO
                estado_maquina["encoder_pos"] += avance

                if estado_maquina["pieza_detectada"]:
                    estado_maquina["longitud_pieza"] += avance

                pos = estado_maquina["encoder_pos"]
                long = estado_maquina["longitud_pieza"]

                # --- GRUPO FRESADOR (EVs) ---
                if estado_maquina["habil_fresador"]:
                    # EV1: Inicio hasta faltar 30mm
                    limite_ev1 = config.POS_FRESADOR + long - \
                        30 if not estado_maquina["pieza_detectada"] else 99999
                    if pos >= config.POS_FRESADOR and pos < limite_ev1:
                        escribir_salida(config.PIN_EV_FRESA_1,
                                        True, "act_fresa1")
                    else:
                        escribir_salida(config.PIN_EV_FRESA_1,
                                        False, "act_fresa1")

                    # EV2: Faltando 40mm
                    if not estado_maquina["pieza_detectada"]:
                        fin_pieza = config.POS_FRESADOR + long
                        if pos >= (fin_pieza - 40) and pos <= (fin_pieza + 10):
                            escribir_salida(
                                config.PIN_EV_FRESA_2, True, "act_fresa2")
                        else:
                            escribir_salida(
                                config.PIN_EV_FRESA_2, False, "act_fresa2")
                else:
                    escribir_salida(config.PIN_EV_FRESA_1, False, "act_fresa1")
                    escribir_salida(config.PIN_EV_FRESA_2, False, "act_fresa2")

                # --- GRUPO ALIMENTADOR ---
                if estado_maquina["habil_alimentador"]:
                    if pos >= config.POS_ALIMENTADOR and not flags["alimentador_activo"] and (pos < config.POS_ALIMENTADOR + 50):
                        flags["alimentador_activo"] = True
                        timers["alimentador_inicio"] = time.time()
                        escribir_salida(config.PIN_EV_ALIMENTADOR,
                                        True, "act_alimentador")

                    if flags["alimentador_activo"]:
                        if (time.time() - timers["alimentador_inicio"]) > 4.0:
                            escribir_salida(
                                config.PIN_EV_ALIMENTADOR, False, "act_alimentador")
                            flags["alimentador_activo"] = False

                # --- GRUPO GUILLOTINA ---
                if estado_maquina["habil_alimentador"]:
                    if not estado_maquina["pieza_detectada"]:
                        pos_corte = config.POS_GUILLOTINA + long
                        if pos >= pos_corte and not flags["guillotina_hecha"]:
                            escribir_salida(
                                config.PIN_EV_GUILLOTINA, True, "act_guillotina")
                            timers["guillotina_inicio"] = time.time()
                            flags["guillotina_hecha"] = True

                    if flags["guillotina_hecha"]:
                        if (time.time() - timers["guillotina_inicio"]) > 1.0:
                            escribir_salida(
                                config.PIN_EV_GUILLOTINA, False, "act_guillotina")

                # --- GRUPO RETESTADOR (Simplificado sin GPIO 17) ---
                if estado_maquina["habil_retestador"]:
                    # CORTE DELANTERO
                    if pos >= config.POS_RETESTADOR and not flags["retestador_delantero_hecho"]:
                        estado_maquina["retestador_bajando"] = True
                        flags["retestador_delantero_hecho"] = True

                    if pos > (config.POS_RETESTADOR + 100):
                        estado_maquina["retestador_bajando"] = False

                    # CORTE TRASERO
                    if not estado_maquina["pieza_detectada"] and not flags["retestador_trasero_hecho"]:
                        pos_corte_trasero = config.POS_RETESTADOR + long - 10
                        if pos >= pos_corte_trasero:
                            # Ya no verificamos sensor home (GPIO 17 eliminado)
                            estado_maquina["retestador_bajando"] = True
                            flags["retestador_trasero_hecho"] = True

                # --- FINALIZAR CICLO ---
                if pos > 2500:
                    estado_maquina["tracking_activo"] = False
                    estado_maquina["encoder_pos"] = 0
                    print("--- CICLO FINALIZADO ---")

        # 5. ENVIAR DATOS A LA WEB
        socketio.emit('update_status', estado_maquina)

        # 6. CONTROL DE TIEMPO
        elapsed = time.time() - start_time
        if elapsed < config.TIEMPO_CICLO:
            time.sleep(config.TIEMPO_CICLO - elapsed)

# --- RUTAS WEB ---


@app.route('/')
def index():
    return render_template('index.html')

# --- EVENTOS SOCKET.IO ---


@socketio.on('comando_control')
def handle_command(data):
    tipo = data.get('tipo')
    valor = data.get('valor')

    if estado_maquina["emergencia"] and tipo != "reset_emergencia":
        return

    if tipo == "tension_mando":
        if valor == True:
            if not verificar_emergencia():
                estado_maquina["tension_mando"] = True
                estado_maquina["mensaje_error"] = ""
        else:
            estado_maquina["tension_mando"] = False

    elif tipo == "reset_emergencia":
        if not verificar_emergencia():
            estado_maquina["emergencia"] = False
            estado_maquina["mensaje_error"] = ""

    elif estado_maquina["tension_mando"]:
        if tipo == "calefaccion":
            estado_maquina["habil_calefaccion"] = valor
        elif tipo == "cadena":
            estado_maquina["habil_cadena"] = valor
        elif tipo == "fresador":
            estado_maquina["habil_fresador"] = valor
        elif tipo == "alimentador":
            estado_maquina["habil_alimentador"] = valor
        elif tipo == "retestador":
            estado_maquina["habil_retestador"] = valor
        elif tipo == "refilador":
            estado_maquina["habil_refilador"] = valor


# --- ARRANQUE ---
if __name__ == '__main__':
    t = threading.Thread(target=control_loop)
    t.daemon = True
    t.start()
    socketio.run(app, host='0.0.0.0', port=5000, debug=False)
