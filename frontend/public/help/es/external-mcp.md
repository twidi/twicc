El MCP externo permite que un agente ajeno a TwiCC trabaje con tu instancia. Por ejemplo, puedes
conectar un cliente MCP para inspeccionar proyectos, crear sesiones o enviar mensajes con imágenes.

TwiCC sigue siendo una aplicación de un solo usuario. Tú apruebas cada conexión a tu instancia. No
creas otra cuenta de usuario para el cliente que se conecta.

### MCP interno y externo

Los agentes que se ejecutan dentro de TwiCC ya tienen una conexión MCP interna. No necesitan esta
configuración ni una aprobación OAuth. Si desactivas el MCP externo, su acceso interno sigue
disponible.

Los clientes externos se conectan mediante una dirección pública dedicada. Usan OAuth para obtener
acceso después de tu aprobación.

### Qué puede hacer una conexión

Una conexión aprobada tiene acceso completo a las herramientas del MCP externo. Puede leer y
modificar tus datos de TwiCC, administrar proyectos y sesiones, enviar mensajes y administrar enlaces
compartidos. No hay opciones de permisos por conexión.

La aprobación cubre la conexión, no cada llamada a una herramienta. El cliente puede actuar sin otra
aprobación de TwiCC por cada mensaje o sesión que cree.

Un cliente externo no es una sesión de TwiCC. No puede usar `whoami`, `self` ni `parent` para
identificar una sesión que llama. Debe indicar IDs de sesión explícitos donde la herramienta
necesite una sesión. Las operaciones relativas a una sesión requieren ese contexto; no pueden
deducirlo de la conexión externa.

### Adjuntos

Un cliente externo adjunta un archivo de su propio equipo con un URI de datos base64 en `attach`:
`data:<mime>;name=<percent-encoded file name>;base64,<data>`. `name=` conserva el nombre del
archivo. Los datos en línea están limitados a 50 MiB por comando. Para un archivo más grande, ponlo
en un servicio de almacenamiento de archivos y pasa su URL en el texto del mensaje, o pasa una ruta
absoluta a un archivo que ya esté en el host de TwiCC. Un mensaje de un par envía todos los archivos
en línea, así que ahí solo funciona la URL.

### Antes de conectar

Inicia TwiCC con una contraseña. El MCP externo no se puede activar sin una. Si reinicias TwiCC sin
contraseña, TwiCC desactiva el MCP externo y conserva las autorizaciones existentes. La URL dedicada
sigue guardada. Tras restablecer una contraseña, activa de nuevo el MCP externo. Los clientes
pueden reanudar con sus credenciales existentes, sujetas a su caducidad normal. Las autorizaciones
revocadas anteriormente siguen revocadas.

Para definir una contraseña, ejecuta:

```
<twicc> password set
```

Para `<twicc>`, usa tu forma habitual de iniciar TwiCC. Reinicia TwiCC después de definir la
contraseña.

Abre **Configuración → MCP externo**. Introduce tu **URL de MCP dedicada** y selecciona
**Aplicar**. Introduce el origen HTTPS, como `https://mcp.example.com`, sin `/mcp`. Después activa
la opción **Aceptar clientes MCP externos**.

Usa un nombre de host dedicado, distinto de tus direcciones externa, de compartir y de pares. Este
nombre de host sirve solo el MCP y sus endpoints OAuth. No sirve tu interfaz de TwiCC, tus archivos
ni otras rutas de la aplicación.

La dirección debe ser accesible desde el cliente. Las llamadas MCP son de máquina a máquina. Si tu
túnel habitual pide a los visitantes iniciar sesión por correo electrónico, SSO u otra comprobación
interactiva, no lo uses para MCP. El cliente no puede completar esa comprobación.

Crea una dirección de túnel aparte sin esa barrera y apúntala a tu servidor de TwiCC. Mantén la
protección en tu dirección habitual de TwiCC. TwiCC protege la dirección MCP con OAuth y con tu
aprobación de la conexión.

Consulta [acceder a TwiCC desde cualquier lugar](help/external-url) para la configuración general
del túnel y la contraseña.

### Conectar un cliente

Copia la **URL de MCP** desde Configuración e introdúcela en la configuración de MCP remoto de tu
cliente. Esta dirección incluye `/mcp`, por ejemplo `https://mcp.example.com/mcp`.

Usa un cliente que admita MCP remoto con OAuth. Los controles de configuración exactos dependen del
cliente. TwiCC proporciona la información de autorización mediante los endpoints estándar de
descubrimiento de MCP.

Los clientes de escritorio pueden recibir la devolución de llamada del navegador en
`http://127.0.0.1` o `http://[::1]` con un puerto disponible distinto en cada conexión. La
dirección registrada, la ruta y la consulta deben seguir coincidiendo. Esta excepción de puerto no
se aplica a las devoluciones de llamada HTTPS remotas ni al nombre de host `localhost`.

Inicia la conexión desde el cliente. Abre una página de autorización con un código de verificación.
Mantén esa página abierta hasta que termine la conexión.

TwiCC muestra una notificación persistente de solicitud de conexión en tus dispositivos con la
sesión iniciada. Selecciona **Revisar** e introduce el código del navegador que se conecta. El
código vincula tu aprobación con la solicitud que iniciaste.

Puedes aprobar desde otro dispositivo. El navegador que inició la conexión sigue completando el
flujo OAuth. La notificación desaparece de tus otros dispositivos cuando se resuelve la solicitud.

Revisa los detalles del cliente antes de seleccionar **Autorizar**. Selecciona **Rechazar** si no
iniciaste la solicitud. Las solicitudes caducan a los diez minutos; si caduca, empieza de nuevo
desde el cliente.

### Nombres y encabezados de mensaje

Puedes dar a cada conexión un nombre opcional, como **ChatGPT** o **Asistente de escritorio**.
Puedes cambiarlo más tarde en los detalles de la conexión.

La revisión rellena el campo del nombre con el nombre que el cliente declara de sí mismo.
Confírmalo, cámbialo o bórralo antes de autorizar. TwiCC usa solo el nombre que aprobaste: un
cliente no puede ponerse nombre a sí mismo en tus conversaciones.

Los mensajes enviados mediante esa conexión llevan un encabezado como **Mensaje vía ChatGPT**. Sin
nombre, el encabezado dice **Mensaje vía MCP externo**. El encabezado identifica la conexión, no
otra sesión de TwiCC.

El **ID del cliente** de los detalles es un identificador técnico. No es un token de acceso ni el
código de verificación. No necesitas copiarlo para aprobar una solicitud.

### Administrar conexiones

Abre **Configuración → MCP externo → Administrar conexiones**. La lista muestra tres secciones cuando
contienen entradas:

- **Solicitudes pendientes**: solicitudes en espera de tu decisión;
- **Activas**: conexiones aprobadas, incluidas las que aún completan OAuth;
- **Revocadas**: conexiones que ya no tienen acceso.

Selecciona **Revisar** para inspeccionar una solicitud, o **Detalles** para inspeccionar una
conexión existente. Al cerrar los detalles, vuelves a la lista si los abriste desde ella. Abrir una
solicitud desde su notificación va directamente a sus detalles; al cerrar ese cuadro de diálogo no
se abre la lista.

Varios clientes pueden conectarse de forma independiente. Revocar una conexión no revoca las demás.

### Revocar el acceso

Selecciona **Revocar** para impedir que una conexión haga más llamadas MCP. Sus credenciales
existentes dejan de dar acceso. La revocación no deshace el trabajo ya hecho ni detiene las sesiones
que el cliente inició antes.

La conexión revocada permanece en la lista. Para conectarte de nuevo, inicia una nueva conexión
desde el cliente y aprueba su nueva solicitud.

Cambiar la URL de MCP dedicada o desactivar el acceso externo revoca las conexiones existentes.
Configura la nueva dirección en tus clientes y vuelve a conectarte.

### Protección contra el abuso de OAuth

TwiCC limita las nuevas solicitudes OAuth por cliente y por origen de red. Un cliente puede tener
tres solicitudes pendientes; un origen puede tener diez. Un origen puede mantener veinte clientes
registrados que nunca han recibido una autorización.

Las negativas de admisión repetidas o un tráfico de admisión inusualmente alto activan una pausa de
diez minutos en los nuevos registros y en las solicitudes de autorización. El propietario recibe
una notificación **Posible abuso de OAuth**. Configuración y el gestor de conexiones muestran el
motivo, los contadores recientes y el tiempo de pausa restante.

La pausa mantiene disponibles las conexiones MCP existentes, las renovaciones de token, la
revocación y las solicitudes de autorización ya en curso. Caduca automáticamente. La alerta permanece
hasta que la descartes. Un tráfico alto es una sospecha de abuso, no una prueba de ataque. Varios
clientes pueden compartir un origen de red.

Selecciona **Suspender todo el acceso externo** para detener todas las nuevas solicitudes de MCP
externo y OAuth sin revocar las autorizaciones existentes. Las solicitudes que ya se están
ejecutando pueden terminar. Para reanudar, activa de nuevo el acceso externo en Configuración. Esta
suspensión manual se mantiene tras los reinicios.

Los contadores de tráfico, las alertas y las pausas automáticas se reinician cuando TwiCC se
reinicia. Las cuotas de registro y de solicitudes pendientes permanecen almacenadas. La caducidad
normal de tokens y solicitudes se aplica durante toda la suspensión.

Los límites de frecuencia usan la dirección de cliente que proporciona el servidor ASGI. Un proxy
debe transmitir la dirección real del cliente y contar con la confianza del servidor; TwiCC nunca
confía por sí mismo en encabezados de reenvío arbitrarios. Las direcciones IPv6 del mismo /64
comparten una cuota de origen. Solo se almacena un hash con clave del origen, no la dirección IP.

### Si una conexión no termina

Comprueba que el MCP externo esté activado y que el cliente use la URL copiada, incluido `/mcp`.
Comprueba que el nombre de host dedicado llegue a esta instancia de TwiCC y que no muestre una
página de inicio de sesión del proveedor del túnel.

Mantén abierto el navegador que se conecta mientras apruebas. Si la lista dice **conectando**, la
aprobación se realizó pero el cliente aún no ha completado OAuth. Si la solicitud caduca, inicia una
nueva conexión desde el cliente.

### Comandos por lotes

Usa `batch_read` para reunir lecturas independientes en una sola llamada MCP. De forma
predeterminada ejecuta hasta cuatro comandos a la vez. Establece `mode` en `sequential` para
ejecutarlos en orden. Usa `batch` para comandos ordenados, incluidos los que hacen cambios.
Siempre se ejecuta de forma secuencial. Las mismas herramientas están disponibles para los agentes
que se ejecutan dentro de TwiCC.

Descubre primero el esquema de cada comando. Indica su nombre MCP normal y sus argumentos:

```json
{
  "calls": [
    {"id": "recent", "name": "sessions", "arguments": {"limit": 5}},
    {"id": "projects", "name": "projects", "arguments": {}}
  ]
}
```

Cada llamada necesita un `id` único, un `name` de comando y un objeto `arguments`. TwiCC valida el
lote completo antes de iniciar ningún comando. Una sola llamada no válida rechaza el lote sin
ejecutar sus otros comandos. Las comprobaciones de permisos normales siguen aplicándose a cada
comando. Las llamadas externas siguen requiriendo IDs de sesión explícitos.

Los resultados llegan juntos, en el orden de entrada. Cada registro incluye su ID, el estado de
ejecución y la respuesta normal `{exit_code, result, error}` del comando. Comprueba el campo `ok`
agregado y cada registro. Un lote completado puede contener comandos fallidos u omitidos. Un
comando también puede tener éxito mientras se omite su respuesta grande (`response_omitted: true`);
en ese caso, el `ok` agregado es falso.

De forma predeterminada, `batch` se detiene tras un error de ejecución. Establece `on_error` en
`continue` para intentar los comandos restantes. `batch_read` usa `continue` de forma
predeterminada. La ejecución en paralelo requiere `continue`; usa el modo secuencial para `stop`.
La omisión de salida no detiene los comandos siguientes. Los cambios completados permanecen
aplicados: no hay reversión, reintento automático ni transferencia de resultados a argumentos
posteriores. No se admiten lotes anidados.

Un lote acepta hasta 20 comandos. Ambas conexiones MCP comparten un límite de cuatro lotes activos
y ocho comandos de lote activos en toda la instancia. Los lotes adicionales reciben `server_busy`.
Las herramientas individuales quedan fuera de este límite de admisión de lotes. La solicitud
completa está limitada a 72 MiB, incluidos los adjuntos; cada comando acepta como máximo 50 MiB de
datos adjuntos en línea (consulta Adjuntos más arriba para un archivo más grande). Las respuestas de
comandos de más de 384 KiB se omiten de forma explícita. El resultado combinado de la herramienta,
incluidos el texto y los datos estructurados, se mantiene por debajo de 16 MiB. Los clientes pueden
tener límites más pequeños.

Mantén los lotes cortos y usa llamadas separadas para las esperas largas. Los tiempos de espera
existentes de los comandos siguen aplicándose; no hay un tiempo de espera adicional para el lote.
Las esperas largas pueden ocupar toda la capacidad de lotes, incluso después de que el cliente
cancele su solicitud. Una cancelación o un tiempo de espera agotado no prueba que las escrituras se
hayan detenido. Inspecciona los recursos afectados antes de reintentar una escritura dudosa. Los IDs
de lote identifican las llamadas para el diagnóstico; no evitan el trabajo duplicado.

TwiCC comprueba la autorización externa antes de iniciar cada comando. La caducidad, la revocación
o la desactivación del acceso externo detienen los comandos restantes. Si TwiCC no puede verificar
la autorización, también los detiene. Los comandos ya iniciados pueden terminar, y sus resultados
recopilados se conservan.
