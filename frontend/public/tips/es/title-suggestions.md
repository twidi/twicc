TwiCC pone nombre a tus sesiones automáticamente. Para controlar el estilo, abre
**Configuración** y luego **Sugerencia de título**. Elige si cada sesión usa su
proveedor, Claude Haiku o GPT-6 Luna.

Edita el prompt para pedir un idioma, un límite de longitud, un prefijo u otro estilo.

Usa **{text}** en el prompt como marcador de posición del contenido de la sesión.

**Títulos automáticos** controla tanto el primer título como las actualizaciones posteriores.
La generación de títulos también debe estar activada. Las sesiones reales obtienen sus títulos del
backend, aunque no haya ninguna pestaña de sesión abierta. Los cambios aparecen sin avisos en la
barra lateral y el encabezado.

La primera comprobación necesita un mensaje de usuario relevante. Las comprobaciones posteriores necesitan al menos
**6 mensajes nuevos y 15 minutos** desde el último intento. Una detención manual o un
archivado activa una comprobación de cierre con **3 mensajes nuevos** desde la última
comprobación correcta, sin importar el tiempo. Una salida normal de la CLI no la activa.
Los comandos de barra sin argumentos no cuentan; los comandos con argumentos pueden contar.

TwiCC mantiene el título actual, salvo que los temas principales de la conversación necesiten
una mejor cobertura. Una comprobación fallida conserva los mensajes para un intento posterior.
Las comprobaciones siguen la actividad de la sesión; el tiempo transcurrido por sí solo no activa ninguna.

Una terminal híbrida activa puede recibir su primer título. Las actualizaciones automáticas esperan
hasta que la terminal se detiene o la sesión continúa fuera de la terminal.
Una sesión híbrida detenida puede recibir actualizaciones con normalidad.

Las sesiones efímeras conservan las sugerencias del frontend porque todavía no tienen una
sesión guardada. Los títulos existentes de antes de esta función permanecen congelados.
Las sesiones ocultas, obsoletas y de subagentes no reciben títulos automáticos.
Las sesiones elegibles creadas por agentes pueden recibirlos cuando no se proporcionó ningún título.
