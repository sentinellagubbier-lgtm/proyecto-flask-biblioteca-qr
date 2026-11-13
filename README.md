# Boletos con reserva temporal (Actividad 2)

CRUD de boletos en Flask + MongoDB Atlas al que se le agrego una **reserva
temporal de 5 minutos**.

- **Recurso que se reserva:** el asiento (boleto). Un asiento solo pertenece a una reserva.
- **Modelo elegido:** *unidad unica* (`estado`, `reserva_id`, `vence_en`).
- **Colecciones:** `boletos`, `reservas`, `compras`.

## Que cambio respecto a la version anterior

| Paso de la practica | Donde esta |
|---|---|
| Coleccion `reservas` | `app.py` -> `obtener_coleccion()` (se crea sola con sus indices) |
| Campos de disponibilidad con valores iniciales en datos antiguos | `migrar_boletos_antiguos()` |
| `reserva_id` por visitante | `obtener_reserva_id()` (cookie de sesion de Flask) |
| Reservar | `POST /api/boletos/<id>/reservar` |
| Cancelar | `POST /api/boletos/<id>/cancelar` |
| Vencimiento | `liberar_reservas_vencidas()` (corre al consultar, reservar y confirmar) |
| Confirmar | `POST /api/boletos/<id>/confirmar` (crea `compras`, elimina la reserva) |
| Contador | Solo muestra el `vence_en` guardado en Atlas, usando la hora del servidor |
| Transaccion de Atlas | `en_transaccion()` -> cambia `boletos` y `reservas` juntos o no cambia nada |

**Regla obligatoria:** la condicion de disponibilidad (`estado: "disponible"`) va
dentro de la misma operacion que cambia el boleto. Ademas, un indice unico parcial
en `reservas` impide dos reservas `activas` del mismo boleto.

## 1. Probar en tu computadora

```bash
python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # macOS / Linux
pip install -r requirements.txt
copy .env.example .env         # macOS/Linux: cp .env.example .env
```

Edita `.env` y pon tu `MONGO_URI` real. Para probar el vencimiento sin esperar
5 minutos usa `RESERVA_MINUTOS=1`. Luego:

```bash
python app.py
```

Abre http://127.0.0.1:5000 y pulsa **Cargar 6 boletos de ejemplo**.

> Las transacciones requieren un cluster de Atlas (replica set). Tu cluster lo es;
> un MongoDB local suelto sin replica set las rechazaria.

## 2. MongoDB Atlas

1. En **Network Access** permite tu IP y, para Render, `0.0.0.0/0`.
2. El usuario de base de datos necesita lectura y escritura.
3. No hay que crear nada a mano: al arrancar, la app crea en la base
   `reserva_boletos` las colecciones `boletos`, `reservas` y `compras`, sus indices,
   y actualiza los boletos antiguos.
4. Para las capturas: **Browse Collections > reserva_boletos > reservas**.

## 3. GitHub (rama `reserva-temporal`)

```bash
git checkout -b reserva-temporal
git add .
git commit -m "Agregar reserva temporal"
git push -u origin reserva-temporal
```

Verifica que `.env` no aparezca en el repositorio.

## 4. Render

1. En el servicio Web, en **Settings > Branch**, elige `reserva-temporal`
   (o crea un servicio nuevo con esa rama).
2. Variables de entorno:

```text
MONGO_URI       = cadena completa de Atlas
MONGO_DB        = reserva_boletos
RESERVA_MINUTOS = 5
SECRET_KEY      = un texto largo y aleatorio
```

3. Build: `pip install -r requirements.txt`  Start: `gunicorn app:app`
4. Espera el despliegue, revisa **Logs** y prueba la URL publica en dos ventanas.

## 5. Prueba obligatoria con dos usuarios

1. Usuario A (ventana normal): reserva el asiento A-01. Se ve el contador.
2. Usuario B (incognito): intenta reservar A-01. Debe recibir rechazo.
   En Atlas solo hay **una** reserva `activa`.
3. A cancela. B ahora si puede reservarlo.
4. Repite y deja terminar el tiempo (usa `RESERVA_MINUTOS=1`): al recargar queda
   disponible y la reserva aparece como `vencida`.
5. Repite y confirma: el boleto queda `comprado`, la reserva desaparece y aparece
   un documento en `compras`.

Dos pestanas de la **misma** ventana comparten cookie y por eso cuentan como el
mismo visitante; para simular otra persona usa incognito u otro navegador.

## 6. Entrega en Classroom

- Nombre del proyecto y recurso (asientos / boletos).
- Enlace del repositorio o commit.
- Enlace de la aplicacion en Render.
- Captura de la reserva en Atlas (coleccion `reservas`).
- Captura del contador.
- Capturas de los dos usuarios (se ven folios distintos).
- Captura de cancelacion o vencimiento (`estado: cancelada` / `vencida`).

## Códigos QR por libro

Cada libro tiene una ficha pública en `/ficha/<id>` (muestra el estado actual: disponible, apartado o prestado) y un QR que apunta a esa dirección. En la tabla, el botón **Ver QR** abre el código, la dirección y el botón de copiar.

- La imagen del QR la genera `api.qrserver.com` (igual que en el proyecto de QR), por lo que necesita internet.
- Si el QR sale con una dirección incorrecta en Render, define `APP_BASE_URL` (por ejemplo `https://tu-app.onrender.com`).
- La ficha pública no muestra el nombre de quien tiene el libro.
- No cambia la base de datos ni las rutas `/api/...`: los campos siguen llamándose `evento`, `zona`, `asiento` y `precio`, y en pantalla se muestran como título, sección, código y valor.
