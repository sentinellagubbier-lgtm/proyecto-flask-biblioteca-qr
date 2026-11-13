# Rutas y pruebas de la API

El visitante se identifica con un **folio** (`reserva_id`) que Flask guarda en una
cookie de sesion firmada. Ya no se envia ningun identificador desde el navegador:
cada ventana de incognito obtiene un folio distinto.

## Modelo de datos (modelo "unidad unica")

Coleccion `boletos` (el recurso: cada asiento es una unidad con identidad propia):

| Campo | Significado |
|---|---|
| `estado` | `disponible`, `reservado` o `comprado` |
| `reserva_id` | folio de quien lo tiene apartado (`null` si no) |
| `vence_en` | fecha UTC en que termina el apartado (`null` si no) |

Coleccion `reservas`:

```json
{
  "reserva_id": "folio-del-visitante",
  "boleto_id": "ObjectId del asiento",
  "cantidad": 1,
  "estado": "activa",
  "nombre": "Ana",
  "creada_en": "fecha UTC",
  "vence_en": "fecha UTC + 5 minutos"
}
```

`estado` puede ser `activa`, `cancelada` o `vencida` (queda como evidencia).
Al **confirmar**, la reserva se elimina y se crea un documento en `compras`.

## Rutas

| Metodo | Ruta | Funcion |
|---|---|---|
| GET | `/api/salud` | Comprueba Flask y Atlas |
| GET | `/api/boletos` | Lista boletos, libera vencidos, devuelve hora del servidor y folio |
| POST | `/api/boletos` | Crea un boleto |
| PUT | `/api/boletos/<id>` | Edita un boleto disponible |
| DELETE | `/api/boletos/<id>` | Elimina un boleto disponible |
| POST | `/api/boletos/semilla` | Carga seis ejemplos |
| POST | `/api/boletos/<id>/reservar` | **Reservar** (transaccion en Atlas) |
| GET | `/api/reservas/mias` | **Consultar** lo apartado por este folio y su `vence_en` |
| POST | `/api/boletos/<id>/cancelar` | **Cancelar** la reserva propia |
| POST | `/api/boletos/liberar-vencidas` | **Liberar vencidas** (una sola vez cada una) |
| POST | `/api/boletos/<id>/confirmar` | **Confirmar**: crea la compra y elimina la reserva |

Reservar acepta opcionalmente `{"nombre": "Ana"}` (solo una etiqueta para mostrar).

## Casos de prueba

| Caso | Resultado esperado |
|---|---|
| Reservar disponible | 200, boleto `reservado`, documento `activa` en `reservas` |
| Reservar lo ya reservado (otro folio) | 409, sigue habiendo una sola reserva activa |
| Cancelar reserva propia | 200, boleto `disponible`, reserva `cancelada` |
| Cancelar reserva ajena | 409 |
| Dejar pasar `vence_en` | Al recargar: boleto `disponible`, reserva `vencida` |
| Confirmar antes de vencer | 200, boleto `comprado`, reserva eliminada, documento en `compras` |
| Confirmar despues de vencer | 409 y se libera |
| Editar o eliminar reservado/comprado | 409 |

Estas pruebas se pueden hacer con Postman usando una sola sesion de cookies, pero
la prueba obligatoria de la practica es con **dos navegadores** (normal + incognito).
