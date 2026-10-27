# Instrucciones de Despliegue Automático para Antigravity (Ubuntu Server)

> **INSTRUCCIÓN PARA EL AGENTE ANTIGRAVITY EN EL SERVIDOR UBUNTU:**
> El usuario acaba de clonar este repositorio (`cazada`) en su Ubuntu Server. Tu misión es dejar tanto el servidor web (`server.py`) como el rastreo diario autónomo (`scripts/run_daily_scan.py`) funcionando de forma permanente mediante servicios nativos del sistema operativo (`systemd` + `cron`), sin depender de que Antigravity quede abierto.
> **IMPORTANTE:** En cuanto termines todos los pasos y verifiques que el servicio web y el cron están activos, **debes borrar este fichero (`DESPLIEGUE_UBUNTU_ANTIGRAVITY.md`)** del disco.

---

## Pasos que debes ejecutar en orden

### 1. Detectar rutas y usuario actual
Obtén el directorio absoluto donde está clonado el repositorio y el usuario de Ubuntu actual:
```bash
REPO_DIR="$(pwd)"
CURRENT_USER="$(whoami)"
```

### 2. Crear entorno virtual Python e instalar dependencias
Asegúrate de que `python3-venv` está instalado, crea `.venv` dentro del repositorio e instala las dos únicas dependencias necesarias (`requests` y `pillow`):
```bash
sudo apt-get update && sudo apt-get install -y python3 python3-venv python3-pip
python3 -m venv "$REPO_DIR/.venv"
"$REPO_DIR/.venv/bin/pip" install --upgrade pip requests pillow
```

### 3. Verificar acceso en red local (`0.0.0.0:8765`)
Comprueba que en `server.py` la función `run_server` escucha en `host: str = "0.0.0.0"` (puerto `8765` por defecto) para que el usuario pueda acceder a la web desde cualquier dispositivo de su red local (`http://<IP-LOCAL-SERVIDOR>:8765`).

### 4. Configurar y activar el servicio `systemd` para la Web (`cazada.service`)
Crea el archivo `/etc/systemd/system/cazada.service` (sustituyendo `<REPO_DIR>` y `<CURRENT_USER>` por los valores reales detectados en el paso 1):

```ini
[Unit]
Description=Cazada - Plataforma Web de Viviendas Zaragoza
After=network.target

[Service]
Type=simple
User=<CURRENT_USER>
WorkingDirectory=<REPO_DIR>
ExecStart=<REPO_DIR>/.venv/bin/python <REPO_DIR>/server.py 8765
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

Recarga `systemd`, habilita el arranque automático con el servidor e inicia el servicio:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now cazada.service
sudo systemctl status cazada.service --no-pager
```

Verifica que la API responde correctamente:
```bash
curl -s http://127.0.0.1:8765/api/state | head -c 300
```

### 5. Configurar el rastreo diario autónomo con `cron` (sin intermediarios)
Añade una tarea en el `crontab` del usuario actual para que todos los días a las **09:00 AM** ejecute el rastreador incremental silencioso (0 tokens, 100% autónomo en Python) y registre el resultado en `data/cron_scan.log`. Evita duplicar la línea si ya existiera:

```bash
CRON_CMD="0 9 * * * cd $REPO_DIR && $REPO_DIR/.venv/bin/python $REPO_DIR/scripts/run_daily_scan.py --incremental --quiet >> $REPO_DIR/data/cron_scan.log 2>&1"
(crontab -l 2>/dev/null | grep -v "run_daily_scan.py" ; echo "$CRON_CMD") | crontab -
crontab -l
```

### 6. Eliminar archivos `.bat` de Windows (opcional) y BORRAR este fichero de instrucciones
Una vez hayas verificado que:
1. `systemctl is-active cazada.service` devuelve `active`,
2. `http://127.0.0.1:8765` responde correctamente, y
3. `crontab -l` muestra la tarea diaria de `run_daily_scan.py`,

**elimina este fichero de instrucciones**:
```bash
rm -f "$REPO_DIR/DESPLIEGUE_UBUNTU_ANTIGRAVITY.md"
```

Por último, muestra al usuario la IP local del Ubuntu Server (`hostname -I`) y la URL exacta (`http://<IP>:8765`) para que pueda abrir **Cazada** desde su navegador.
