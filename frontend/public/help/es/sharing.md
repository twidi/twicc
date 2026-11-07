Un **enlace compartido** es una URL pública de solo lectura a una de tus sesiones o
artefactos. Cualquiera que tenga el enlace puede abrirlo en un navegador, sin cuenta de TwiCC
ni inicio de sesión. Solo puede **mirar**: un enlace nunca puede enviar un
mensaje, ejecutar una herramienta ni cambiar nada de tu lado.

## Qué puedes compartir

- **Un artefacto** — un archivo en favoritos de la pestaña Artefactos de una sesión,
  mostrado por sí solo (una página HTML, un gráfico, un documento…).
- **Una sesión** — la conversación, mostrada en solo lectura. Las llamadas a herramientas y los
  diffs se muestran tal como los ves tú.

> **Una sesión se comparte tal cual**: nada de lo que contiene se oculta ni se limpia.
> Antes de compartir una, asegúrate de que no contiene secretos ni datos privados, y
> da el enlace solo a personas de confianza. Un artefacto es un único archivo que
> elegiste, así que hay mucho menos que revisar.

## Antes de poder compartir

Para compartir se necesita un **host de uso compartido dedicado**: un nombre de host distinto del que
usas para TwiCC, que apunte a la misma instancia. Configúralo en
**Configuración → Compartir**. Hasta que esté configurado, los botones de compartir permanecen
desactivados.

Ten en cuenta que **cada visita la atiende el equipo que ejecuta TwiCC**:
tu propio equipo, a través de tu túnel. No existe una copia alojada, así que evita
repartir enlaces a gran escala: todo ese tráfico llega directamente a tu equipo.

## Tus enlaces

Al crear un enlace puedes ponerle una **etiqueta**, protegerlo con una
**contraseña** y definir una **caducidad**. Puedes crear **varios enlaces** a la
misma sesión o artefacto, cada uno con su propia contraseña y caducidad: entrega
enlaces distintos a personas distintas y consulta por separado los registros de visitas de cada uno.
Un enlace por persona es práctico: puedes revocar el acceso de una persona
sin tocar los de las demás.

Gestiónalos desde el botón de compartir (o **Configuración → Compartir → Enlaces
compartidos**), donde cada enlace también muestra cuántas veces se abrió:

- **Revocar** desactiva un enlace temporalmente —quien lo visite recibe un 404— (pero conserva
  sus registros de visitas), de modo que puedas reactivarlo más tarde.
- **Eliminar** lo quita de forma permanente, incluidos sus registros de visitas.

## Agentes y enlaces compartidos

De forma predeterminada, solo tú puedes crear o administrar enlaces compartidos; los agentes no.
Dos interruptores en **Configuración → Compartir** cambian esto, uno por tipo (enlaces de
sesión, enlaces de artefacto). Ambos están **desactivados** hasta que los actives.

Al activar **Enlaces de sesiones**, los agentes pueden crear enlaces de sesión para su propia
sesión o para cualquier sesión de su subárbol de creación. Al activar **Enlaces de artefactos**,
los agentes pueden crear enlaces de artefacto para los favoritos que pertenecen a su propia sesión
o a cualquier sesión de su subárbol de creación.

Para el tipo activado, los agentes también pueden:

- **administrar** (actualizar, eliminar, volver a publicar) los enlaces creados por ellos mismos o por
  agentes de su subárbol de creación;
- **revocar** cualquier enlace existente de ese tipo, incluidos los que creaste
  tú (dejar de publicar siempre se considera seguro);
- **leer la URL** de todos los enlaces existentes de ese tipo, incluidos los tuyos.

Los agentes nunca pueden quitar la contraseña de un enlace, nunca pueden compartir con el modo de
visualización `debug`, y sus nuevos enlaces de sesión son instantáneas congeladas, salvo que
pidan explícitamente un enlace en vivo.

## Artefactos y acceso a la red

Un artefacto compartido se ejecuta para quien lo ve **sin solicitudes de consentimiento**. Si
hace llamadas de red, solo se puede acceder a los hosts que **has permitido**;
cualquier otro host se bloquea y quien lo ve recibe una breve nota "bloqueado por el
propietario". Permites o deniegas hosts desde el **favorito** del artefacto, y
los hosts a los que quienes lo ven intentaron acceder aparecen listados allí para que puedas aprobarlos
a posteriori.

Si cambias los archivos de un artefacto después de compartirlo, usa **Enviar actualización** en el
enlace para actualizar lo que ven los visitantes.
