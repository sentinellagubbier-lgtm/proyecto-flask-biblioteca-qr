import hashlib
import os
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from bson import ObjectId
from bson.errors import InvalidId
from dotenv import load_dotenv
from flask import Flask, abort, jsonify, render_template, request, session, url_for
from pymongo import ASCENDING, MongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError
from pymongo.write_concern import WriteConcern
from werkzeug.middleware.proxy_fix import ProxyFix


load_dotenv()

app = Flask(__name__)
# Render entrega la app detras de un proxy HTTPS; sin esto los enlaces del QR
# saldrian con http:// en lugar de https://.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

MONGO_URI = os.getenv("MONGO_URI", "").strip()
MONGO_DB = os.getenv("MONGO_DB", "reserva_boletos").strip()

# La practica pide 5 minutos. Para probar el vencimiento mas rapido se puede
# poner RESERVA_MINUTOS=1 (o 0.5) en el .env o en Render.
DURACION_RESERVA_MINUTOS = max(0.25, min(float(os.getenv("RESERVA_MINUTOS", "5")), 60))

# La sesion (cookie firmada) es lo que da un folio distinto a cada visitante.
# Si no existe SECRET_KEY se deriva una fija de MONGO_URI para que funcione
# igual con varios workers. Lo ideal es definir SECRET_KEY en Render.
app.secret_key = os.getenv("SECRET_KEY") or hashlib.sha256(
    ("reserva-temporal:" + MONGO_URI).encode("utf-8")
).hexdigest()
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = bool(os.getenv("RENDER"))

cliente_mongo = None
boletos = None      # recurso: cada asiento es una unidad unica
reservas = None     # apartados temporales
compras = None      # registro final al confirmar


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------
def ahora_utc():
    return datetime.now(timezone.utc)


def obtener_reserva_id():
    """Folio del visitante: se crea una vez y vive en la cookie de sesion."""
    if not session.get("reserva_id"):
        session["reserva_id"] = uuid.uuid4().hex
    return session["reserva_id"]


def convertir_fecha(valor):
    if not valor:
        return None

    if valor.tzinfo is None:
        valor = valor.replace(tzinfo=timezone.utc)

    return valor.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def limpiar_texto(valor):
    return " ".join(str(valor or "").strip().split())


def obtener_object_id(valor):
    try:
        return ObjectId(valor)
    except (InvalidId, TypeError):
        raise ValueError("El identificador del libro no es valido.")


def enlace_publico(libro_id):
    """Direccion de la ficha publica que guarda el QR."""
    ruta = url_for("ficha_publica", libro_id=libro_id)
    base_configurada = os.getenv("APP_BASE_URL", "").strip().rstrip("/")

    if base_configurada:
        return f"{base_configurada}{ruta}"

    return url_for("ficha_publica", libro_id=libro_id, _external=True)


def enlace_qr(destino):
    """Imagen QR (mismo servicio que usa el proyecto de QR)."""
    destino_codificado = quote(destino, safe="")
    return (
        "https://api.qrserver.com/v1/create-qr-code/"
        f"?size=260x260&margin=12&data={destino_codificado}"
    )


def obtener_nombre(datos, folio):
    nombre = limpiar_texto(datos.get("nombre"))[:40]
    return nombre or f"Visitante {folio[:6]}"


# ---------------------------------------------------------------------------
# Conexion, indices y datos antiguos
# ---------------------------------------------------------------------------
def obtener_coleccion():
    global cliente_mongo, boletos, reservas, compras

    if not MONGO_URI:
        raise RuntimeError(
            "Falta MONGO_URI. Copia .env.example como .env y agrega tu conexion de MongoDB Atlas."
        )

    if cliente_mongo is None:
        cliente = MongoClient(
            MONGO_URI,
            serverSelectionTimeoutMS=10000,
            tz_aware=True,
        )
        base_datos = cliente[MONGO_DB]
        coleccion_boletos = base_datos["boletos"]
        coleccion_reservas = base_datos["reservas"]
        coleccion_compras = base_datos["compras"]

        coleccion_boletos.create_index(
            [("evento", ASCENDING), ("asiento", ASCENDING)],
            unique=True,
            name="evento_asiento_unico",
        )
        coleccion_boletos.create_index("estado")
        coleccion_boletos.create_index("vence_en")

        # Segunda barrera: un boleto no puede tener dos reservas activas.
        coleccion_reservas.create_index(
            [("boleto_id", ASCENDING)],
            unique=True,
            partialFilterExpression={"estado": "activa"},
            name="una_reserva_activa_por_boleto",
        )
        coleccion_reservas.create_index(
            [("reserva_id", ASCENDING), ("estado", ASCENDING)],
            name="reserva_id_estado",
        )
        coleccion_reservas.create_index("vence_en")
        coleccion_compras.create_index(
            "boleto_id", unique=True, name="una_compra_por_boleto"
        )

        migrar_boletos_antiguos(coleccion_boletos)

        cliente_mongo = cliente
        boletos = coleccion_boletos
        reservas = coleccion_reservas
        compras = coleccion_compras

    return boletos


def migrar_boletos_antiguos(coleccion):
    """Paso 3 de la practica: los datos de la version anterior reciben valores
    iniciales para los campos nuevos (reserva_id y vence_en). Es idempotente."""
    # Una reserva de la version anterior no tiene documento en `reservas`,
    # asi que se libera.
    coleccion.update_many(
        {"estado": "reservado", "reserva_id": {"$exists": False}},
        {"$set": {"estado": "disponible"}},
    )
    coleccion.update_many(
        {"reserva_id": {"$exists": False}},
        {
            "$set": {"reserva_id": None, "vence_en": None},
            "$unset": {
                "reservado_por": "",
                "reservado_nombre": "",
                "reserva_hasta": "",
            },
        },
    )


def en_transaccion(funcion):
    """Ejecuta `funcion(sesion)` dentro de una transaccion de MongoDB Atlas.
    Si algo falla se deshace todo; los conflictos de escritura se reintentan."""
    obtener_coleccion()
    with cliente_mongo.start_session() as sesion:
        return sesion.with_transaction(
            funcion, write_concern=WriteConcern("majority")
        )


# ---------------------------------------------------------------------------
# Serializacion
# ---------------------------------------------------------------------------
def serializar_boleto(documento, reserva_activa=None, folio=None):
    if not documento:
        return None

    mia = bool(
        folio
        and documento.get("estado") == "reservado"
        and documento.get("reserva_id") == folio
    )

    enlace = enlace_publico(documento["_id"])

    return {
        "_id": str(documento["_id"]),
        "enlace_publico": enlace,
        "enlace_qr": enlace_qr(enlace),
        "evento": documento.get("evento", ""),
        "zona": documento.get("zona", ""),
        "asiento": documento.get("asiento", ""),
        "precio": documento.get("precio", 0),
        "estado": documento.get("estado", "disponible"),
        "mia": mia,
        "reservado_nombre": (reserva_activa or {}).get("nombre"),
        "vence_en": convertir_fecha(documento.get("vence_en")),
        "comprado_nombre": documento.get("comprado_nombre"),
        "comprado_en": convertir_fecha(documento.get("comprado_en")),
        "creado_en": convertir_fecha(documento.get("creado_en")),
        "actualizado_en": convertir_fecha(documento.get("actualizado_en")),
    }


def serializar_reserva(reserva, boleto=None):
    return {
        "_id": str(reserva["_id"]),
        "boleto_id": str(reserva["boleto_id"]),
        "cantidad": reserva.get("cantidad", 1),
        "estado": reserva.get("estado"),
        "nombre": reserva.get("nombre"),
        "creada_en": convertir_fecha(reserva.get("creada_en")),
        "vence_en": convertir_fecha(reserva.get("vence_en")),
        "boleto": serializar_boleto(boleto, reserva, reserva.get("reserva_id"))
        if boleto
        else None,
    }


def validar_datos_boleto(datos):
    evento = limpiar_texto(datos.get("evento"))
    zona = limpiar_texto(datos.get("zona"))
    asiento = limpiar_texto(datos.get("asiento")).upper()

    try:
        precio = float(datos.get("precio"))
    except (TypeError, ValueError):
        raise ValueError("El valor debe ser numerico.")

    if len(evento) < 3 or len(evento) > 80:
        raise ValueError("El titulo debe tener entre 3 y 80 caracteres.")

    if len(zona) < 2 or len(zona) > 40:
        raise ValueError("La seccion debe tener entre 2 y 40 caracteres.")

    if not asiento or len(asiento) > 20:
        raise ValueError("Escribe un codigo de ejemplar valido de hasta 20 caracteres.")

    if precio < 0:
        raise ValueError("El valor no puede ser negativo.")

    return {
        "evento": evento,
        "zona": zona,
        "asiento": asiento,
        "precio": round(precio, 2),
    }


# ---------------------------------------------------------------------------
# Liberar reservas vencidas (paso 7)
# ---------------------------------------------------------------------------
def liberar_reservas_vencidas():
    """Libera cada reserva vencida exactamente una vez: la reserva pasa de
    `activa` a `vencida` y el boleto vuelve a `disponible` en la misma
    transaccion. Si dos procesos lo intentan a la vez, solo uno la modifica."""
    obtener_coleccion()
    momento = ahora_utc()
    vencidas = list(
        reservas.find({"estado": "activa", "vence_en": {"$lte": momento}})
    )
    liberadas = 0

    for reserva in vencidas:

        def liberar(sesion, reserva=reserva):
            cambio = reservas.update_one(
                {"_id": reserva["_id"], "estado": "activa"},
                {"$set": {"estado": "vencida", "cerrada_en": momento}},
                session=sesion,
            )
            if cambio.modified_count != 1:
                return 0  # otra solicitud ya la libero

            boletos.update_one(
                {
                    "_id": reserva["boleto_id"],
                    "estado": "reservado",
                    "reserva_id": reserva["reserva_id"],
                },
                {
                    "$set": {
                        "estado": "disponible",
                        "reserva_id": None,
                        "vence_en": None,
                        "actualizado_en": momento,
                    }
                },
                session=sesion,
            )
            return 1

        liberadas += en_transaccion(liberar)

    return liberadas


# ---------------------------------------------------------------------------
# Rutas generales
# ---------------------------------------------------------------------------
@app.get("/")
def inicio():
    obtener_reserva_id()
    return render_template(
        "index.html", reserva_minutos=formatear_minutos(DURACION_RESERVA_MINUTOS)
    )


def formatear_minutos(valor):
    return int(valor) if float(valor).is_integer() else valor


@app.get("/ficha/<libro_id>")
def ficha_publica(libro_id):
    """Pagina a la que lleva el QR: muestra el estado actual del libro."""
    if not ObjectId.is_valid(libro_id):
        abort(404)

    obtener_coleccion()
    liberar_reservas_vencidas()
    documento = boletos.find_one({"_id": ObjectId(libro_id)})
    if documento is None:
        abort(404)

    libro = serializar_boleto(documento)
    # La ficha es publica: no se muestra el nombre de quien lo tiene.
    libro["reservado_nombre"] = None
    libro["comprado_nombre"] = None
    return render_template("ficha_publica.html", libro=libro)


@app.get("/api/salud")
def salud():
    coleccion = obtener_coleccion()
    coleccion.database.client.admin.command("ping")

    return jsonify(
        {
            "ok": True,
            "mensaje": "Flask y MongoDB Atlas estan funcionando.",
            "fecha": convertir_fecha(ahora_utc()),
        }
    )


@app.get("/api/boletos")
def listar_boletos():
    liberar_reservas_vencidas()
    folio = obtener_reserva_id()
    documentos = list(
        boletos.find().sort([("evento", ASCENDING), ("asiento", ASCENDING)])
    )
    activas = {
        reserva["boleto_id"]: reserva
        for reserva in reservas.find({"estado": "activa"})
    }

    return jsonify(
        {
            # La hora del servidor sirve para que el contador no dependa del
            # reloj del navegador.
            "ahora": convertir_fecha(ahora_utc()),
            "reserva_id": folio,
            "duracion_minutos": formatear_minutos(DURACION_RESERVA_MINUTOS),
            "boletos": [
                serializar_boleto(doc, activas.get(doc["_id"]), folio)
                for doc in documentos
            ],
        }
    )


@app.get("/api/reservas/mias")
def consultar_mis_reservas():
    """Funcion `Consultar`: lo apartado por este folio y el tiempo que queda."""
    liberar_reservas_vencidas()
    folio = obtener_reserva_id()
    activas = list(
        reservas.find({"reserva_id": folio, "estado": "activa"}).sort(
            "creada_en", ASCENDING
        )
    )
    ids = [reserva["boleto_id"] for reserva in activas]
    por_id = {doc["_id"]: doc for doc in boletos.find({"_id": {"$in": ids}})}

    return jsonify(
        {
            "ahora": convertir_fecha(ahora_utc()),
            "reserva_id": folio,
            "reservas": [
                serializar_reserva(reserva, por_id.get(reserva["boleto_id"]))
                for reserva in activas
            ],
        }
    )


@app.post("/api/boletos/semilla")
def crear_semilla():
    coleccion = obtener_coleccion()
    momento = ahora_utc()
    ejemplos = [
        ("Cien años de soledad", "Literatura", "LIT-001", 250),
        ("El principito", "Literatura", "LIT-002", 120),
        ("Don Quijote de la Mancha", "Literatura", "LIT-003", 300),
        ("Breve historia del tiempo", "Ciencias", "CIE-001", 280),
        ("Cosmos", "Ciencias", "CIE-002", 260),
        ("Python para todos", "Computación", "COM-001", 350),
    ]

    for evento, zona, asiento, precio in ejemplos:
        coleccion.update_one(
            {"evento": evento, "asiento": asiento},
            {
                "$setOnInsert": {
                    "evento": evento,
                    "zona": zona,
                    "asiento": asiento,
                    "precio": precio,
                    "estado": "disponible",
                    "reserva_id": None,
                    "vence_en": None,
                    "comprado_nombre": None,
                    "comprado_en": None,
                    "creado_en": momento,
                    "actualizado_en": momento,
                }
            },
            upsert=True,
        )

    return jsonify({"mensaje": "Se prepararon los seis libros de ejemplo."}), 201


@app.post("/api/boletos/liberar-vencidas")
def liberar_vencidas():
    liberadas = liberar_reservas_vencidas()
    return jsonify(
        {"mensaje": "Revision de reservas terminada.", "liberadas": liberadas}
    )


# ---------------------------------------------------------------------------
# CRUD de boletos
# ---------------------------------------------------------------------------
@app.post("/api/boletos")
def crear_boleto():
    coleccion = obtener_coleccion()
    datos = validar_datos_boleto(request.get_json(silent=True) or {})
    momento = ahora_utc()
    documento = {
        **datos,
        "estado": "disponible",
        "reserva_id": None,
        "vence_en": None,
        "comprado_nombre": None,
        "comprado_en": None,
        "creado_en": momento,
        "actualizado_en": momento,
    }
    resultado = coleccion.insert_one(documento)
    documento["_id"] = resultado.inserted_id

    return jsonify(
        {
            "mensaje": "Libro registrado correctamente.",
            "boleto": serializar_boleto(documento),
        }
    ), 201


@app.put("/api/boletos/<boleto_id>")
def editar_boleto(boleto_id):
    coleccion = obtener_coleccion()
    datos = validar_datos_boleto(request.get_json(silent=True) or {})
    datos["actualizado_en"] = ahora_utc()

    documento = coleccion.find_one_and_update(
        {"_id": obtener_object_id(boleto_id), "estado": "disponible"},
        {"$set": datos},
        return_document=ReturnDocument.AFTER,
    )

    if not documento:
        return jsonify(
            {"mensaje": "Solo se pueden editar libros disponibles."}
        ), 409

    return jsonify(
        {
            "mensaje": "Libro actualizado correctamente.",
            "boleto": serializar_boleto(documento),
        }
    )


@app.delete("/api/boletos/<boleto_id>")
def eliminar_boleto(boleto_id):
    coleccion = obtener_coleccion()
    documento = coleccion.find_one_and_delete(
        {"_id": obtener_object_id(boleto_id), "estado": "disponible"}
    )

    if not documento:
        return jsonify(
            {"mensaje": "Solo se pueden eliminar libros disponibles."}
        ), 409

    return jsonify({"mensaje": "Libro eliminado correctamente."})


# ---------------------------------------------------------------------------
# Reservar / cancelar / confirmar (pasos 5, 6 y 8)
# ---------------------------------------------------------------------------
@app.post("/api/boletos/<boleto_id>/reservar")
def reservar_boleto(boleto_id):
    obtener_coleccion()
    datos = request.get_json(silent=True) or {}
    oid = obtener_object_id(boleto_id)
    folio = obtener_reserva_id()
    nombre = obtener_nombre(datos, folio)

    # Si el apartado anterior de este asiento ya vencio, primero se libera.
    liberar_reservas_vencidas()

    momento = ahora_utc()
    vence_en = momento + timedelta(minutes=DURACION_RESERVA_MINUTOS)

    def reservar(sesion):
        # REGLA OBLIGATORIA: la condicion de disponibilidad va dentro de la
        # misma operacion que cambia el boleto. Si Atlas no modifica ningun
        # documento, otra persona lo reservo primero.
        boleto = boletos.find_one_and_update(
            {"_id": oid, "estado": "disponible"},
            {
                "$set": {
                    "estado": "reservado",
                    "reserva_id": folio,
                    "vence_en": vence_en,
                    "actualizado_en": momento,
                }
            },
            return_document=ReturnDocument.AFTER,
            session=sesion,
        )
        if boleto is None:
            return None

        reserva = {
            "reserva_id": folio,
            "boleto_id": oid,
            "cantidad": 1,
            "estado": "activa",
            "nombre": nombre,
            "creada_en": momento,
            "vence_en": vence_en,
        }
        reservas.insert_one(reserva, session=sesion)
        return boleto, reserva

    try:
        resultado = en_transaccion(reservar)
    except DuplicateKeyError:
        resultado = None

    if resultado is None:
        return jsonify(
            {"mensaje": "Otra persona lo aparto primero o ya no esta disponible."}
        ), 409

    boleto, reserva = resultado
    return jsonify(
        {
            "mensaje": (
                f"Libro apartado durante {formatear_minutos(DURACION_RESERVA_MINUTOS)} "
                "minuto(s)."
            ),
            "reserva": serializar_reserva(reserva, boleto),
        }
    )


@app.post("/api/boletos/<boleto_id>/cancelar")
def cancelar_reserva(boleto_id):
    obtener_coleccion()
    oid = obtener_object_id(boleto_id)
    folio = obtener_reserva_id()
    momento = ahora_utc()

    def cancelar(sesion):
        reserva = reservas.find_one_and_update(
            {"boleto_id": oid, "reserva_id": folio, "estado": "activa"},
            {"$set": {"estado": "cancelada", "cerrada_en": momento}},
            return_document=ReturnDocument.AFTER,
            session=sesion,
        )
        if reserva is None:
            return None

        # Libera exactamente lo apartado por esta reserva.
        liberado = boletos.find_one_and_update(
            {"_id": oid, "estado": "reservado", "reserva_id": folio},
            {
                "$set": {
                    "estado": "disponible",
                    "reserva_id": None,
                    "vence_en": None,
                    "actualizado_en": momento,
                }
            },
            return_document=ReturnDocument.AFTER,
            session=sesion,
        )
        return liberado

    boleto = en_transaccion(cancelar)

    if boleto is None:
        return jsonify(
            {"mensaje": "El apartado ya vencio, ya se cancelo o pertenece a otro lector."}
        ), 409

    return jsonify(
        {
            "mensaje": "Apartado cancelado. El libro vuelve a estar disponible.",
            "boleto": serializar_boleto(boleto),
        }
    )


@app.post("/api/boletos/<boleto_id>/confirmar")
def confirmar_compra(boleto_id):
    obtener_coleccion()
    datos = request.get_json(silent=True) or {}
    oid = obtener_object_id(boleto_id)
    folio = obtener_reserva_id()
    nombre = obtener_nombre(datos, folio)
    momento = ahora_utc()

    def confirmar(sesion):
        # El servidor vuelve a revisar la hora: vence_en debe ser futuro.
        reserva = reservas.find_one_and_delete(
            {
                "boleto_id": oid,
                "reserva_id": folio,
                "estado": "activa",
                "vence_en": {"$gt": momento},
            },
            session=sesion,
        )
        if reserva is None:
            return None

        boleto = boletos.find_one_and_update(
            {"_id": oid, "estado": "reservado", "reserva_id": folio},
            {
                "$set": {
                    "estado": "comprado",
                    "reserva_id": None,
                    "vence_en": None,
                    "comprado_nombre": nombre,
                    "comprado_en": momento,
                    "actualizado_en": momento,
                }
            },
            return_document=ReturnDocument.AFTER,
            session=sesion,
        )
        if boleto is None:
            raise RuntimeError("La reserva no coincide con el boleto; se deshizo la operacion.")

        compras.insert_one(
            {
                "boleto_id": oid,
                "evento": boleto.get("evento"),
                "zona": boleto.get("zona"),
                "asiento": boleto.get("asiento"),
                "precio": boleto.get("precio"),
                "cantidad": reserva.get("cantidad", 1),
                "reserva_id": folio,
                "comprador": nombre,
                "reservada_en": reserva.get("creada_en"),
                "comprado_en": momento,
            },
            session=sesion,
        )
        return boleto

    boleto = en_transaccion(confirmar)

    if boleto is None:
        liberar_reservas_vencidas()
        return jsonify(
            {
                "mensaje": (
                    "No fue posible confirmar: el apartado vencio o pertenece a otro lector."
                )
            }
        ), 409

    return jsonify(
        {
            "mensaje": "Prestamo confirmado correctamente.",
            "boleto": serializar_boleto(boleto),
        }
    )


# ---------------------------------------------------------------------------
# Errores
# ---------------------------------------------------------------------------
@app.errorhandler(ValueError)
def manejar_valor_invalido(error):
    return jsonify({"mensaje": str(error)}), 400


@app.errorhandler(DuplicateKeyError)
def manejar_duplicado(error):
    return jsonify(
        {"mensaje": "Ya existe un libro con el mismo titulo y codigo."}
    ), 409


@app.errorhandler(RuntimeError)
def manejar_configuracion(error):
    return jsonify({"mensaje": str(error)}), 500


@app.errorhandler(PyMongoError)
def manejar_mongo(error):
    app.logger.exception(error)
    return jsonify(
        {"mensaje": "No fue posible comunicarse con MongoDB Atlas."}
    ), 500


@app.errorhandler(404)
def no_encontrado(error):
    if request.path.startswith("/api/"):
        return jsonify({"mensaje": "Ruta no encontrada."}), 404
    return render_template("404.html"), 404


if __name__ == "__main__":
    puerto = int(os.getenv("PORT", "5000"))
    modo_debug = os.getenv("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=puerto, debug=modo_debug)
