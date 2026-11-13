const elementos = {
  estadoApi: document.getElementById("estadoApi"),
  clienteNombre: document.getElementById("clienteNombre"),
  clienteIdCorto: document.getElementById("clienteIdCorto"),
  mensaje: document.getElementById("mensaje"),
  tabla: document.getElementById("tablaBoletos"),
  listaCarrito: document.getElementById("listaCarrito"),
  filtroEstado: document.getElementById("filtroEstado"),
  formulario: document.getElementById("formBoleto"),
  boletoId: document.getElementById("boletoId"),
  evento: document.getElementById("evento"),
  zona: document.getElementById("zona"),
  asiento: document.getElementById("asiento"),
  precio: document.getElementById("precio"),
  tituloFormulario: document.getElementById("tituloFormulario"),
  textoGuardar: document.getElementById("textoGuardar"),
  btnCancelarEdicion: document.getElementById("btnCancelarEdicion"),
  btnSemilla: document.getElementById("btnSemilla"),
  btnActualizar: document.getElementById("btnActualizar"),
  totalDisponibles: document.getElementById("totalDisponibles"),
  totalReservados: document.getElementById("totalReservados"),
  totalComprados: document.getElementById("totalComprados"),
  totalBoletos: document.getElementById("totalBoletos"),
  modalQr: document.getElementById("modalQr"),
  qrTitulo: document.getElementById("qrTitulo"),
  qrImagen: document.getElementById("qrImagen"),
  qrEnlace: document.getElementById("qrEnlace"),
  qrAbrir: document.getElementById("qrAbrir"),
  qrCopiar: document.getElementById("qrCopiar"),
  btnCerrarQr: document.getElementById("btnCerrarQr")
};

let boletos = [];
let misReservas = [];
let folio = "";
let desfaseServidor = 0; // servidor - navegador, en milisegundos
let temporizadorMensaje = null;
let actualizando = false;
let vencimientoEnProceso = false;

const nombreGuardado = localStorage.getItem("flaskClienteNombre");
if (nombreGuardado) {
  elementos.clienteNombre.value = nombreGuardado;
}

// El folio (reserva_id) lo crea el servidor y vive en la cookie de sesion.
// Solo se envia el nombre, que es una etiqueta para mostrar.
function obtenerCliente() {
  return { nombre: elementos.clienteNombre.value.trim() };
}

function ahoraServidor() {
  return Date.now() + desfaseServidor;
}

function escapar(valor) {
  return String(valor ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function mostrarMensaje(texto, tipo = "correcto") {
  clearTimeout(temporizadorMensaje);
  elementos.mensaje.textContent = texto;
  elementos.mensaje.classList.remove("oculto", "error");

  if (tipo === "error") {
    elementos.mensaje.classList.add("error");
  }

  temporizadorMensaje = setTimeout(() => {
    elementos.mensaje.classList.add("oculto");
  }, 5000);
}

async function solicitud(ruta, opciones = {}) {
  const configuracion = {
    ...opciones,
    headers: {
      "Content-Type": "application/json",
      ...(opciones.headers || {})
    }
  };

  const respuesta = await fetch(ruta, configuracion);
  const datos = await respuesta.json().catch(() => ({}));

  if (!respuesta.ok) {
    throw new Error(datos.mensaje || "No fue posible completar la operacion.");
  }

  return datos;
}

function precioMoneda(valor) {
  return new Intl.NumberFormat("es-MX", {
    style: "currency",
    currency: "MXN"
  }).format(valor);
}

function segundosRestantes(fecha) {
  if (!fecha) return 0;
  return Math.max(0, Math.ceil((new Date(fecha).getTime() - ahoraServidor()) / 1000));
}

function formatoTiempo(segundos) {
  const minutos = Math.floor(segundos / 60);
  const resto = segundos % 60;
  return `${String(minutos).padStart(2, "0")}:${String(resto).padStart(2, "0")}`;
}

function esMio(boleto) {
  return boleto.estado === "reservado" && boleto.mia === true;
}

function actualizarResumen() {
  elementos.totalDisponibles.textContent = boletos.filter(
    (boleto) => boleto.estado === "disponible"
  ).length;
  elementos.totalReservados.textContent = boletos.filter(
    (boleto) => boleto.estado === "reservado"
  ).length;
  elementos.totalComprados.textContent = boletos.filter(
    (boleto) => boleto.estado === "comprado"
  ).length;
  elementos.totalBoletos.textContent = boletos.length;
}

function textoPropietario(boleto) {
  if (boleto.estado === "reservado") {
    const propietario = esMio(boleto)
      ? "Apartado por ti"
      : `Apartado por ${escapar(boleto.reservado_nombre || "otro lector")}`;

    return `
      <span>${propietario}</span><br>
      <small class="contador-tabla" data-hasta="${escapar(boleto.vence_en)}">
        ${formatoTiempo(segundosRestantes(boleto.vence_en))}
      </small>
    `;
  }

  if (boleto.estado === "comprado") {
    return `Prestado a ${escapar(boleto.comprado_nombre || "un lector")}`;
  }

  return "Listo para apartar";
}

function botonesBoleto(boleto) {
  if (boleto.estado === "disponible") {
    return `
      <button class="boton pequeno primario" data-accion="reservar" data-id="${boleto._id}">
        Apartar
      </button>
      <button class="boton pequeno secundario" data-accion="editar" data-id="${boleto._id}">
        Editar
      </button>
      <button class="boton pequeno peligro" data-accion="eliminar" data-id="${boleto._id}">
        Eliminar
      </button>
    `;
  }

  if (esMio(boleto)) {
    return `
      <button class="boton pequeno exito" data-accion="confirmar" data-id="${boleto._id}">
        Confirmar préstamo
      </button>
      <button class="boton pequeno peligro" data-accion="cancelar" data-id="${boleto._id}">
        Cancelar
      </button>
    `;
  }

  return `<button class="boton pequeno secundario" disabled>No disponible</button>`;
}

const ETIQUETAS_ESTADO = {
  disponible: "Disponible",
  reservado: "Apartado",
  comprado: "Prestado"
};

function etiquetaEstado(estado) {
  return escapar(ETIQUETAS_ESTADO[estado] || estado);
}

function dibujarTabla() {
  const filtro = elementos.filtroEstado.value;
  const visibles = boletos.filter(
    (boleto) => filtro === "todos" || boleto.estado === filtro
  );

  if (!visibles.length) {
    elementos.tabla.innerHTML = `
      <tr>
        <td colspan="8">
          <div class="tabla-vacia">No hay libros para mostrar.</div>
        </td>
      </tr>
    `;
    return;
  }

  elementos.tabla.innerHTML = visibles
    .map(
      (boleto) => `
        <tr>
          <td>${escapar(boleto.evento)}</td>
          <td>${escapar(boleto.zona)}</td>
          <td><strong>${escapar(boleto.asiento)}</strong></td>
          <td>${precioMoneda(boleto.precio)}</td>
          <td>
            <span class="insignia ${boleto.estado}">${etiquetaEstado(boleto.estado)}</span>
          </td>
          <td>${textoPropietario(boleto)}</td>
          <td>
            <button class="boton pequeno secundario" data-accion="qr" data-id="${boleto._id}">
              Ver QR
            </button>
          </td>
          <td><div class="acciones-tabla">${botonesBoleto(boleto)}</div></td>
        </tr>
      `
    )
    .join("");
}

function dibujarCarrito() {
  if (!misReservas.length) {
    elementos.listaCarrito.innerHTML = `
      <div class="carrito-vacio">
        Todavía no has apartado libros en esta ventana.
      </div>
    `;
    return;
  }

  elementos.listaCarrito.innerHTML = misReservas
    .map((reserva) => {
      const boleto = reserva.boleto || {};
      return `
        <article class="item-carrito">
          <div>
            <h3>${escapar(boleto.evento)} - ${escapar(boleto.asiento)}</h3>
            <p>${escapar(boleto.zona)} · ${precioMoneda(boleto.precio)}</p>
          </div>
          <div>
            <div class="contador" data-hasta="${escapar(reserva.vence_en)}">
              ${formatoTiempo(segundosRestantes(reserva.vence_en))}
            </div>
            <small>tiempo autorizado por Atlas</small>
          </div>
          <div class="acciones-carrito">
            <button class="boton pequeno exito" data-accion="confirmar" data-id="${escapar(reserva.boleto_id)}">
              Confirmar préstamo
            </button>
            <button class="boton pequeno peligro" data-accion="cancelar" data-id="${escapar(reserva.boleto_id)}">
              Cancelar apartado
            </button>
          </div>
        </article>
      `;
    })
    .join("");
}

function dibujar() {
  actualizarResumen();
  dibujarTabla();
  dibujarCarrito();
}

async function comprobarApi() {
  try {
    await solicitud("/api/salud");
    elementos.estadoApi.textContent = "Flask y Atlas activos";
    elementos.estadoApi.classList.add("correcto");
  } catch (error) {
    elementos.estadoApi.textContent = "API sin conexion";
    elementos.estadoApi.classList.add("error");
    mostrarMensaje(error.message, "error");
  }
}

async function cargarBoletos(mostrarError = true) {
  if (actualizando) return;
  actualizando = true;

  try {
    const [listado, mias] = await Promise.all([
      solicitud("/api/boletos"),
      solicitud("/api/reservas/mias")
    ]);

    desfaseServidor = new Date(listado.ahora).getTime() - Date.now();
    folio = listado.reserva_id;
    boletos = listado.boletos;
    misReservas = mias.reservas;
    elementos.clienteIdCorto.textContent = folio.slice(0, 8);
    elementos.clienteIdCorto.title = folio;
    dibujar();
  } catch (error) {
    if (mostrarError) mostrarMensaje(error.message, "error");
  } finally {
    actualizando = false;
    vencimientoEnProceso = false;
  }
}

async function ejecutarAccion(accion, id) {
  const boleto = boletos.find((item) => item._id === id);
  if (!boleto) return;

  try {
    if (accion === "reservar") {
      const datos = await solicitud(`/api/boletos/${id}/reservar`, {
        method: "POST",
        body: JSON.stringify(obtenerCliente())
      });
      mostrarMensaje(datos.mensaje);
    }

    if (accion === "cancelar") {
      const datos = await solicitud(`/api/boletos/${id}/cancelar`, {
        method: "POST",
        body: JSON.stringify(obtenerCliente())
      });
      mostrarMensaje(datos.mensaje);
    }

    if (accion === "confirmar") {
      const datos = await solicitud(`/api/boletos/${id}/confirmar`, {
        method: "POST",
        body: JSON.stringify(obtenerCliente())
      });
      mostrarMensaje(datos.mensaje);
    }

    if (accion === "eliminar") {
      const confirmar = window.confirm(
        `¿Eliminar el libro ${boleto.evento} (${boleto.asiento})? Esta acción no se puede deshacer.`
      );
      if (!confirmar) return;

      const datos = await solicitud(`/api/boletos/${id}`, {
        method: "DELETE"
      });
      mostrarMensaje(datos.mensaje);
    }

    await cargarBoletos();
  } catch (error) {
    mostrarMensaje(error.message, "error");
    await cargarBoletos(false);
  }
}

function iniciarEdicion(id) {
  const boleto = boletos.find((item) => item._id === id);
  if (!boleto || boleto.estado !== "disponible") return;

  elementos.boletoId.value = boleto._id;
  elementos.evento.value = boleto.evento;
  elementos.zona.value = boleto.zona;
  elementos.asiento.value = boleto.asiento;
  elementos.precio.value = boleto.precio;
  elementos.tituloFormulario.textContent = "Editar libro";
  elementos.textoGuardar.textContent = "Guardar cambios";
  elementos.btnCancelarEdicion.classList.remove("oculto");
  elementos.formulario.scrollIntoView({ behavior: "smooth", block: "center" });
}

function limpiarFormulario() {
  elementos.formulario.reset();
  elementos.boletoId.value = "";
  elementos.tituloFormulario.textContent = "Registrar libro";
  elementos.textoGuardar.textContent = "Guardar libro";
  elementos.btnCancelarEdicion.classList.add("oculto");
}

elementos.formulario.addEventListener("submit", async (eventoFormulario) => {
  eventoFormulario.preventDefault();

  const id = elementos.boletoId.value;
  const cuerpo = {
    evento: elementos.evento.value,
    zona: elementos.zona.value,
    asiento: elementos.asiento.value,
    precio: Number(elementos.precio.value)
  };

  try {
    const datos = await solicitud(id ? `/api/boletos/${id}` : "/api/boletos", {
      method: id ? "PUT" : "POST",
      body: JSON.stringify(cuerpo)
    });

    mostrarMensaje(datos.mensaje);
    limpiarFormulario();
    await cargarBoletos();
  } catch (error) {
    mostrarMensaje(error.message, "error");
  }
});

document.addEventListener("click", (eventoClic) => {
  const boton = eventoClic.target.closest("[data-accion]");
  if (!boton) return;

  const { accion, id } = boton.dataset;

  if (accion === "editar") {
    iniciarEdicion(id);
  } else if (accion === "qr") {
    abrirQr(id);
  } else {
    ejecutarAccion(accion, id);
  }
});

elementos.btnSemilla.addEventListener("click", async () => {
  try {
    const datos = await solicitud("/api/boletos/semilla", { method: "POST" });
    mostrarMensaje(datos.mensaje);
    await cargarBoletos();
  } catch (error) {
    mostrarMensaje(error.message, "error");
  }
});

elementos.btnActualizar.addEventListener("click", () => cargarBoletos());
elementos.btnCancelarEdicion.addEventListener("click", limpiarFormulario);
elementos.filtroEstado.addEventListener("change", dibujarTabla);

elementos.clienteNombre.addEventListener("input", () => {
  localStorage.setItem(
    "flaskClienteNombre",
    elementos.clienteNombre.value.trim()
  );
});

function actualizarContadores() {
  document.querySelectorAll("[data-hasta]").forEach((elemento) => {
    const restantes = segundosRestantes(elemento.dataset.hasta);
    elemento.textContent = formatoTiempo(restantes);

    if (restantes === 0 && !vencimientoEnProceso) {
      vencimientoEnProceso = true;
      setTimeout(() => cargarBoletos(false), 250);
    }
  });
}

comprobarApi();
cargarBoletos();

setInterval(actualizarContadores, 1000);
setInterval(() => cargarBoletos(false), 5000);

// ---------------------------------------------------------------------------
// Codigo QR de cada libro (la direccion y la imagen las genera el servidor)
// ---------------------------------------------------------------------------
let enlaceQrActual = "";

function abrirQr(id) {
  const libro = boletos.find((item) => item._id === id);
  if (!libro) return;

  enlaceQrActual = libro.enlace_publico;
  elementos.qrTitulo.textContent = `${libro.evento} (${libro.asiento})`;
  elementos.qrImagen.src = libro.enlace_qr;
  elementos.qrEnlace.textContent = libro.enlace_publico;
  elementos.qrAbrir.href = libro.enlace_publico;
  elementos.qrCopiar.textContent = "Copiar dirección";
  elementos.modalQr.classList.remove("oculto");
}

function cerrarQr() {
  elementos.modalQr.classList.add("oculto");
  elementos.qrImagen.removeAttribute("src");
}

elementos.btnCerrarQr.addEventListener("click", cerrarQr);
elementos.modalQr.addEventListener("click", (eventoClic) => {
  if (eventoClic.target === elementos.modalQr) cerrarQr();
});
document.addEventListener("keydown", (eventoTecla) => {
  if (eventoTecla.key === "Escape") cerrarQr();
});

elementos.qrCopiar.addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(enlaceQrActual);
    elementos.qrCopiar.textContent = "Dirección copiada";
  } catch (_error) {
    elementos.qrCopiar.textContent = "No se pudo copiar";
  }
  setTimeout(() => {
    elementos.qrCopiar.textContent = "Copiar dirección";
  }, 1800);
});
