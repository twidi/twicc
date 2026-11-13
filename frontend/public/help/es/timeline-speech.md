Usa el **botón de altavoz junto a Cronología** para leer automáticamente los nuevos
mensajes del asistente. Se requiere una configuración de texto a voz disponible. El botón
presionado significa que esta cronología escucha los mensajes nuevos.

**Leer solo mensajes de fin de turno** está activado de forma predeterminada. El interruptor aparece
antes del botón de ayuda cuando la función de texto a voz está disponible y esta cronología escucha, incluso durante una pausa. La preferencia se
conserva tras recargar y volver a activar la escucha. TwiCC lee el mensaje final exacto después de un
fin de turno oficial. Los mensajes de progreso visibles pueden permanecer en silencio. El fin del
turno debe ser estrictamente posterior al inicio de la escucha. Desactiva la opción para leer también
mensajes intermedios. La lectura manual sigue disponible en ambos modos.

- La escucha usa el alcance de la cronología: todos los proyectos, un espacio de trabajo, un proyecto
  o un solo worktree. Las sesiones silenciadas se ocultan y se omiten de forma predeterminada. Selecciona
  **Filtros → Mostrar sesiones silenciadas** para mostrarlas y leerlas en esa cronología.
  Los demás filtros de visualización, incluidos la vista seleccionada y el filtro de sesiones, no
  afectan a la escucha.
- Al desactivar **Mostrar sesiones silenciadas** se eliminan sus mensajes en cola. La
  lectura ya seleccionada puede terminar. Al activarlo no se reproducen los mensajes
  omitidos mientras estaba desactivado. La lectura manual sigue disponible en las sesiones silenciadas.
- La escucha continúa cuando navegas a otra página, cambias de pestaña de la aplicación
  o abres una sesión. Solo una cronología escucha por pestaña del navegador.
- El botón de la cronología en el riel muestra un pequeño altavoz mientras la escucha
  está activada. Su etiqueta identifica la cronología que escucha. Haz clic en él para abrir
  esa cronología sin cambiar la escucha.
- Con la opción desactivada, los mensajes deben tener una fecha de mensaje nativa **estrictamente posterior al clic
  de activación**. El historial existente no se reproduce cuando activas la escucha.
- Con la opción desactivada, las llegadas tardías siguen siendo elegibles hasta **cinco segundos** por detrás del último
  mensaje cuya lectura empieza. Exactamente cinco segundos sigue siendo elegible.
  Los mensajes aceptados permanecen en cola hasta su turno.
- La lectura manual tiene prioridad. La lectura automática espera mientras se ejecuta una solicitud
  o una reproducción manual. El altavoz de la cronología en el riel permanece visible durante esta espera
  y entre mensajes.
- Haz clic en el altavoz presionado de la cronología para desactivar la escucha y vaciar su cola.
  Esto detiene su lectura automática y deja en marcha las lecturas manuales no relacionadas.
  Al activar otra cronología se vacía la cola de la cronología anterior.
- El botón **Detener la lectura en voz alta** existente en el riel y el botón **Detener
  lectura** del mensaje detienen la lectura actual. Durante la lectura automática,
  también desactivan la escucha y vacían su cola.
- La escucha y su cola permanecen en memoria. Al recargar la página se borran.

La lectura automática usa la configuración de texto a voz activa. En **Configuración →
Resúmenes**, **Leer resúmenes en la lectura en voz alta de la cronología** controla los resúmenes automáticos.
Está activado de forma predeterminada. Los mensajes elegibles contienen al menos **1000 caracteres
de contenido**. Los mensajes más cortos usan la configuración de adaptación de texto existente.
Los anuncios de proyecto y de sesión conservan su redacción habitual.

TwiCC prepara hasta dos resúmenes de texto en cola mientras se reproduce otra lectura.
Solo genera el audio cuando un mensaje llega a su turno. Una lectura seleccionada
conserva su configuración hasta que termina. Al desactivar el interruptor se libera la preparación
innecesaria y los mensajes pendientes usan sus originales. Un resumen fallido
muestra un error y omite ese mensaje. La escucha continúa con el siguiente mensaje.

## Dónde sigue sonando

Lo que ocurre al salir de la página depende de dónde uses TwiCC.

- **App de Android**: la escucha continúa con la pantalla apagada y al cambiar a otra
  app. Una notificación muestra el estado y ofrece **Omitir** y **Detener todo**. Abre
  TwiCC antes de activar la escucha. Al activarla, Android puede pedirte que permitas las
  notificaciones, que los botones de la notificación necesitan, y mostrar un aviso sobre
  la batería. El uso sin restricciones de la batería es opcional. Hace más fiable la escucha
  en segundo plano. Cambia ambas cosas más tarde en Ajustes de Android → Aplicaciones → TwiCC.
- **Web en teléfono o tableta**: mantén TwiCC en primer plano con la pantalla encendida.
  El navegador puede congelar la página al cambiar de app o bloquear la pantalla. El audio
  se detiene entonces.
- **Web en ordenador**: mantén la pestaña abierta. Puede quedarse en segundo plano, detrás de
  otras pestañas o ventanas. El navegador puede ralentizar una pestaña oculta, pero la escucha
  normalmente continúa.

Para configurar la voz, consulta [Texto a voz](help/text-to-speech).
