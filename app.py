import os
from flask import Flask, render_template
from flask_socketio import SocketIO, emit
import time
import threading
import RPi.GPIO as GPIO
import config

# --- CONFIGURACIÓN DE RUTAS ABSOLUTAS ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = os.path.join(BASE_DIR, 'templates')
STATIC_DIR = os.path.join(BASE_DIR, 'static')

app = Flask(__name__, template_folder=TEMPLATE_DIR, static_folder=STATIC_DIR)
app.config['SECRET_KEY'] = 'secreto_industrial_enchapadora_v2'
socketio = SocketIO(app, async_mode='threading')

# --- ESTADO GLOBAL DE LA MAQUINA ---
estado_maquina = {
    "tension_mando": False,
    "emergencia": False,
    "mensaje_error": "",
    "maquina_ocupada": False,  # Nuevo estado

    # Tracking
    "encoder_pos": 0.0,
    "pieza_detectada": False,  # Estado sensor físico
    "longitud_pieza": 0.0,
    "tracking_activo": False,

    # Habilitadores HMI
    "habil_calefaccion": False,
    "habil_cadena": False,
    "habil_fresador": False,
    "habil_alimentador": False,
    "habil_retestador": False,
    "habil_refilador": False,

    # Estado Físico Salidas (Para feedback visual HMI)
    "act_calefaccion": False,  # Indica si PWM > 0
    "temp_actual": 0.0,
    "act_cadena": False,
    "act_fresador": False,
    "act_fresa1": False,
    "act_fresa2": False,
    "act_alimentador": False,
    "act_guillotina": False,
    "act_retestador": False,
    "act_refilador": False,

    # Monitor de Entradas
    "in_sensor_entrada": 1,
    "in_paro_entrada": 1,
    "in_paro_salida": 1,

    # Visuales extra
    "retestador_bajando": False
}

# --- VARIABLES INTERNAS DE CONTROL ---
timers = {
    "alimentador_inicio": 0,
    "guillotina_inicio": 0
}
flags = {
    "alimentador_activo": False,
    "guillotina_activa": False,
    "retestador_delantero_hecho": False,
    "retestador_trasero_hecho": False
}
pid_state = {
    "error_prev": 0,
    "integral": 0,
    "kp": 10.0,  # Ganancia Proporcional
    "ki": 0.1,  # Ganancia Integral
    "kd": 5.0   # Ganancia Derivativa
}

# --- CONFIGURACIÓN GPIO ---
GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)

# Configurar Salidas
salidas = [
    config.PIN_CADENA, config.PIN_MOTOR_FRESADOR, config.PIN_EV_FRESA_1,
    config.PIN_EV_FRESA_2, config.PIN_SSR_ENCOLADOR, config.PIN_EV_ALIMENTADOR,
    config.PIN_EV_GUILLOTINA, config.PIN_MOTOR_RETESTADOR, config.PIN_MOTOR_REFILADOR
]
for pin in salidas:
    GPIO.setup(pin, GPIO.OUT, initial=GPIO.LOW)

# Configurar Entradas
entradas = [config.PIN_SENSOR_ENTRADA,
            config.PIN_PARO_ENTRADA, config.PIN_PARO_SALIDA]
for pin in entradas:
    GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)

# Configurar SPI
GPIO.setup(config.SPI_MOSI, GPIO.OUT)
GPIO.setup(config.SPI_MISO, GPIO.IN)
GPIO.setup(config.SPI_CLK, GPIO.OUT)
GPIO.setup(config.SPI_CS, GPIO.OUT)

# Configurar PWM para Calefacción (GPIO 18)
# 10 Hz para visualización LED
pwm_calefaccion = GPIO.PWM(config.PIN_SSR_ENCOLADOR, 10)
pwm_calefaccion.start(0)

# --- FUNCIONES AUXILIARES ---


def leer_adc_mcp3008(canal):
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


def escribir_salida(pin, estado, clave_diccionario=None):
    GPIO.output(pin, GPIO.HIGH if estado else GPIO.LOW)
    if clave_diccionario:
        estado_maquina[clave_diccionario] = estado


def calcular_pid(temp_actual, temp_objetivo):
    """Calcula el ciclo de trabajo PWM (0-100%)"""
    error = temp_objetivo - temp_actual

    pid_state["integral"] += error * config.TIEMPO_CICLO
    derivada = (error - pid_state["error_prev"]) / config.TIEMPO_CICLO

    # Salida PID
    salida = (pid_state["kp"] * error) + (pid_state["ki"]
                                          * pid_state["integral"]) + (pid_state["kd"] * derivada)

    pid_state["error_prev"] = error

    # Limitar salida entre 0 y 100
    if salida > 100:
        salida = 100
    if salida < 0:
        salida = 0

    return salida


def verificar_emergencia():
    p1 = GPIO.input(config.PIN_PARO_ENTRADA)
    p2 = GPIO.input(config.PIN_PARO_SALIDA)

    estado_maquina["in_paro_entrada"] = p1
    estado_maquina["in_paro_salida"] = p2

    if p1 == 0 or p2 == 0:
        estado_maquina["emergencia"] = True
        estado_maquina["mensaje_error"] = "¡PARO DE EMERGENCIA ACTIVADO!"
        estado_maquina["tension_mando"] = False
        return True
    return False

# --- HILO PRINCIPAL DE CONTROL ---


def control_loop():
    print("--- PLC ENCHAPADORA V2.1.0 INICIADO ---")

    while True:
        start_time = time.time()

        # 1. LECTURA DE TEMPERATURA
        adc_val = leer_adc_mcp3008(0)
        temp_c = (adc_val / 1023.0) * 250.0
        estado_maquina["temp_actual"] = round(temp_c, 1)

        # 2. VERIFICAR SEGURIDAD
        if verificar_emergencia():
            pwm_calefaccion.ChangeDutyCycle(0)  # Apagar PWM
            for pin in salidas:
                if pin != config.PIN_SSR_ENCOLADOR:  # SSR ya manejado por PWM
                    GPIO.output(pin, GPIO.LOW)

            # Reset visuales
            estado_maquina["act_cadena"] = False
            estado_maquina["act_fresador"] = False
            estado_maquina["act_fresa1"] = False
            estado_maquina["act_fresa2"] = False
            estado_maquina["act_alimentador"] = False
            estado_maquina["act_guillotina"] = False
            estado_maquina["act_retestador"] = False
            estado_maquina["act_refilador"] = False
            estado_maquina["act_calefaccion"] = False

            socketio.emit('update_status', estado_maquina)
            time.sleep(0.1)
            continue

        # 3. SIN TENSIÓN DE MANDO
        if not estado_maquina["tension_mando"]:
            pwm_calefaccion.ChangeDutyCycle(0)
            escribir_salida(config.PIN_CADENA, False, "act_cadena")
            escribir_salida(config.PIN_MOTOR_FRESADOR, False, "act_fresador")
            escribir_salida(config.PIN_EV_FRESA_1, False, "act_fresa1")
            escribir_salida(config.PIN_EV_FRESA_2, False, "act_fresa2")
            escribir_salida(config.PIN_MOTOR_RETESTADOR,
                            False, "act_retestador")
            escribir_salida(config.PIN_MOTOR_REFILADOR, False, "act_refilador")
            estado_maquina["act_calefaccion"] = False

        # 4. CON TENSIÓN DE MANDO (RUN)
        else:
            # A. CALEFACCIÓN PID
            if estado_maquina["habil_calefaccion"]:
                duty = calcular_pid(
                    estado_maquina["temp_actual"], config.TEMP_OBJETIVO)
                pwm_calefaccion.ChangeDutyCycle(duty)
                estado_maquina["act_calefaccion"] = (
                    duty > 5)  # Visualmente activo si > 5%
            else:
                pwm_calefaccion.ChangeDutyCycle(0)
                estado_maquina["act_calefaccion"] = False

            # B. CADENA DE AVANCE
            if estado_maquina["habil_cadena"]:
                escribir_salida(config.PIN_CADENA, True, "act_cadena")
            else:
                escribir_salida(config.PIN_CADENA, False, "act_cadena")

            # C. MOTORES PERIFERICOS
            escribir_salida(config.PIN_MOTOR_FRESADOR,
                            estado_maquina["habil_fresador"], "act_fresador")
            escribir_salida(config.PIN_MOTOR_REFILADOR,
                            estado_maquina["habil_refilador"], "act_refilador")
            escribir_salida(config.PIN_MOTOR_RETESTADOR,
                            estado_maquina["habil_retestador"], "act_retestador")

            # D. LOGICA DE SECUENCIA (ENCODER VIRTUAL Y TRACKING)
            sensor_in = GPIO.input(config.PIN_SENSOR_ENTRADA)
            estado_maquina["in_sensor_entrada"] = sensor_in

            # --- DETECCIÓN INICIO ---
            if sensor_in == 0 and not estado_maquina["pieza_detectada"]:
                estado_maquina["pieza_detectada"] = True
                estado_maquina["tracking_activo"] = True
                estado_maquina["maquina_ocupada"] = True
                estado_maquina["encoder_pos"] = 0
                estado_maquina["longitud_pieza"] = 0

                # Reset Flags Ciclo
                flags["alimentador_activo"] = False
                flags["guillotina_activa"] = False
                flags["retestador_delantero_hecho"] = False
                flags["retestador_trasero_hecho"] = False
                print(">> PIEZA INGRESANDO")

            # --- DETECCIÓN FIN PIEZA ---
            if sensor_in == 1 and estado_maquina["pieza_detectada"]:
                estado_maquina["pieza_detectada"] = False
                print(
                    f">> PIEZA COMPLETA. L={estado_maquina['longitud_pieza']:.1f}")

            # --- AVANCE DEL ENCODER ---
            if estado_maquina["act_cadena"] and estado_maquina["tracking_activo"]:
                avance = config.VELOCIDAD_MM_S * config.TIEMPO_CICLO
                estado_maquina["encoder_pos"] += avance

                # Solo sumar longitud si el sensor sigue presionado
                if estado_maquina["pieza_detectada"]:
                    estado_maquina["longitud_pieza"] += avance

                pos = estado_maquina["encoder_pos"]
                longitud = estado_maquina["longitud_pieza"]

                # --- GRUPO FRESADOR (Lógica Corregida) ---
                if estado_maquina["habil_fresador"]:
                    # La referencia es el final de la pieza
                    # Si aun detectamos pieza, el final "se mueve", usamos predicción temporal o esperamos
                    # Usaremos lógica relativa al final teórico calculado dinámicamente

                    pos_final_pieza_en_grupo = config.POS_FRESADOR + longitud

                    # GPIO 16 (Fresa 2): Activa por defecto. Se apaga faltando 30mm para terminar.
                    # Se reactiva cuando termina.
                    # Rango de APAGADO: [Fin - 30mm, Fin]

                    # Solo aplicamos lógica fina cuando sabemos el largo total
                    if not estado_maquina["pieza_detectada"]:
                        inicio_apagado_16 = pos_final_pieza_en_grupo - 30
                        fin_apagado_16 = pos_final_pieza_en_grupo

                        if pos >= inicio_apagado_16 and pos <= fin_apagado_16:
                            escribir_salida(
                                config.PIN_EV_FRESA_2, False, "act_fresa2")
                        else:
                            escribir_salida(
                                config.PIN_EV_FRESA_2, True, "act_fresa2")
                    else:
                        # Siempre ON mientras entra
                        escribir_salida(config.PIN_EV_FRESA_2,
                                        True, "act_fresa2")

                    # GPIO 12 (Fresa 1): Normalmente OFF. Activa faltando 40mm para terminar.
                    # Se desactiva cuando termina.
                    # Rango de ENCENDIDO: [Fin - 40mm, Fin]

                    if not estado_maquina["pieza_detectada"]:
                        inicio_encendido_12 = pos_final_pieza_en_grupo - 40
                        fin_encendido_12 = pos_final_pieza_en_grupo

                        if pos >= inicio_encendido_12 and pos <= fin_encendido_12:
                            escribir_salida(
                                config.PIN_EV_FRESA_1, True, "act_fresa1")
                        else:
                            escribir_salida(
                                config.PIN_EV_FRESA_1, False, "act_fresa1")
                    else:
                        escribir_salida(config.PIN_EV_FRESA_1,
                                        False, "act_fresa1")

                else:
                    escribir_salida(config.PIN_EV_FRESA_1, False, "act_fresa1")
                    escribir_salida(config.PIN_EV_FRESA_2, False, "act_fresa2")

                # --- GRUPO ALIMENTADOR (GPIO 20) ---
                if estado_maquina["habil_alimentador"]:
                    # Se activa al llegar al grupo
                    if pos >= config.POS_ALIMENTADOR and not flags["alimentador_activo"] and pos < (config.POS_ALIMENTADOR + 100):
                        flags["alimentador_activo"] = True
                        timers["alimentador_inicio"] = time.time()
                        escribir_salida(config.PIN_EV_ALIMENTADOR,
                                        True, "act_alimentador")

                    # Temporizador 4 segundos
                    if flags["alimentador_activo"]:
                        if (time.time() - timers["alimentador_inicio"]) > 4.0:
                            escribir_salida(
                                config.PIN_EV_ALIMENTADOR, False, "act_alimentador")
                            # No reseteamos flag aun para que no dispare de nuevo en esta pieza

                # --- GRUPO GUILLOTINA (GPIO 21) ---
                if estado_maquina["habil_alimentador"]:
                    # Se activa cuando la pieza TERMINA de pasar (Posicion == Grupo + Longitud)
                    if not estado_maquina["pieza_detectada"]:
                        pos_corte = config.POS_ALIMENTADOR + longitud  # Misma posición física ref

                        if pos >= pos_corte and not flags["guillotina_activa"]:
                            flags["guillotina_activa"] = True
                            timers["guillotina_inicio"] = time.time()
                            escribir_salida(
                                config.PIN_EV_GUILLOTINA, True, "act_guillotina")

                        # Temporizador 3 segundos
                        if flags["guillotina_activa"]:
                            if (time.time() - timers["guillotina_inicio"]) > 3.0:
                                escribir_salida(
                                    config.PIN_EV_GUILLOTINA, False, "act_guillotina")

                # --- GRUPO RETESTADOR ---
                if estado_maquina["habil_retestador"]:
                    # Lógica simplificada para simulación visual
                    # Delantero
                    if pos >= config.POS_RETESTADOR and not flags["retestador_delantero_hecho"]:
                        estado_maquina["retestador_bajando"] = True
                        flags["retestador_delantero_hecho"] = True

                    if pos > (config.POS_RETESTADOR + 150):
                        estado_maquina["retestador_bajando"] = False

                    # Trasero
                    if not estado_maquina["pieza_detectada"] and not flags["retestador_trasero_hecho"]:
                        pos_trasero = config.POS_RETESTADOR + longitud - 10
                        if pos >= pos_trasero:
                            estado_maquina["retestador_bajando"] = True
                            flags["retestador_trasero_hecho"] = True

                # --- CONTROL DE SALIDA DE MÁQUINA ---
                # Si la posición > longitud maquina + longitud pieza, salió
                limite_salida = config.LONGITUD_MAQUINA + longitud + 200  # +200 margen
                if pos > limite_salida:
                    estado_maquina["tracking_activo"] = False
                    estado_maquina["maquina_ocupada"] = False
                    estado_maquina["encoder_pos"] = 0
                    print("--- PIEZA SALIÓ DE MÁQUINA ---")

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
        camp = "habil_" + tipo
        if camp in estado_maquina:
            estado_maquina[camp] = valor


# --- ARRANQUE ---
if __name__ == '__main__':
    t = threading.Thread(target=control_loop)
    t.daemon = True
    t.start()
    socketio.run(app, host='0.0.0.0', port=5000, debug=False)
