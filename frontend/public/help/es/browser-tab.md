La pestaña Navegador incrusta una página de tu elección —normalmente el servidor de desarrollo
del proyecto en el que trabajas— junto a la conversación. La página
se ejecuta exactamente como en una pestaña normal del navegador: red directa, recarga en vivo,
service workers. TwiCC no la usa como proxy ni la aísla en un sandbox.

**Para que un agente configure tu servidor de desarrollo,** indícale esta página:
<https://github.com/twidi/twicc/blob/main/frontend/public/help/browser-tab.md>.
Contiene todo lo necesario: incrustación, cookies y el script companion.
El agente también necesita la dirección con la que accedes a TwiCC: los mensajes de configuración
de la pestaña ofrecen **Añadir instrucciones al mensaje** y **Copiar instrucciones para un
agente**, un prompt listo para usar con esta página, esa dirección y el snippet
companion.

### Abrir una página

Escribe cualquier URL en la barra de direcciones. El botón **Inicio** salta a la URL
predeterminada guardada; usa el menú de **favoritos** para guardar la URL actual como
predeterminada del proyecto (o de uno de sus espacios de trabajo): la pestaña se abrirá
entonces directamente en ella. La URL en la que estabas también se recuerda por sesión.

### Límites de la incrustación

Algunas páginas no se pueden incrustar:

- Los sitios que se niegan a ser incrustados (la mayoría de los sitios públicos) muestran una advertencia:
  usa **Abrir en una nueva pestaña del navegador** en su lugar.
- Cuando TwiCC se sirve por `https`, el navegador bloquea las páginas `http://` sin cifrar
  (contenido mixto).

Tus servidores de desarrollo locales son el caso ideal; para eso sirve la pestaña.

### Inicios de sesión y almacenamiento

Que la página incrustada mantenga tu sesión iniciada depende de dónde esté tu servidor de desarrollo
respecto a TwiCC.

**Mismo host** — TwiCC y tu aplicación, ambos en `localhost`, con cualquier puerto. No hay nada
que hacer: la página comparte las mismas cookies y el mismo almacenamiento que usa en una pestaña
normal, así que un inicio de sesión hecho allí ya se aplica. Los puertos no importan aquí:
`localhost:5173` y `localhost:3000` cuentan como el mismo sitio.

**Dominios distintos** — por ejemplo, TwiCC en un túnel y tu aplicación en otro. El
navegador aísla la página incrustada, así que empiezas sin sesión iniciada y necesitas alguna configuración
**solo para desarrollo**:

- **Permitir la incrustación** — muchos frameworks la prohíben de forma predeterminada con
  `X-Frame-Options` / `frame-ancestors` (Django, Rails…); relájala en desarrollo.
- **Cookies** — define tu cookie de sesión con `SameSite=None; Secure`. `Secure`
  funciona con `https` y `http://localhost`, pero no con una IP de LAN en http sin cifrar.
- Después, **inicia sesión una vez dentro de la pestaña**. La página incrustada obtiene su propia
  área privada de cookies y almacenamiento, que se conserva entre recargas. `localStorage` e
  `IndexedDB` no requieren configuración: tienen el mismo aislamiento y simplemente funcionan.

Mantén esta configuración detrás de un indicador de desarrollo: no quieres `SameSite=None` ni la
incrustación abierta en producción.

### El script companion

Una página incrustada es una caja negra para TwiCC: los enlaces que sigues dentro de ella son
invisibles, así que Atrás / Adelante solo siguen las URL escritas en la barra de herramientas. TwiCC
ofrece un **script companion** que puedes añadir a tu página de desarrollo para eliminar
ese límite y activar funciones más completas:

- seguimiento real de la navegación: Atrás / Adelante / Recargar controlan el historial propio de la
  página, y la navegación interna (SPA) actualiza la barra de direcciones;
- **selección de elementos** — en lugar de describir "el tercer botón de la
  cabecera" y esperar que el agente lo encuentre, activa el modo con el botón del **puntero** en la barra de herramientas, haz clic en el elemento directamente en la página y presiona el
  botón de **comentario**: se abre un widget donde puedes añadir una nota opcional
  (e incluir una captura de pantalla del elemento) antes de pegar todo en el campo de entrada del
  chat: una descripción precisa y apta para código del
  elemento (etiqueta, ids, clases: cosas que el agente puede encontrar en tu código fuente)
  junto con tu comentario. Perfecto para "haz esto más grande" o "esta etiqueta
  es incorrecta";
- **errores de la página** — las excepciones no capturadas y las llamadas a `console.error` se
  recopilan, se cuentan en la barra de herramientas y están a un clic de tu mensaje.

El snippet es una sola etiqueta `<script>` que apunta a
`/_twicc/browser-companion.js` en tu host de TwiCC:

```html
<script src="https://your-twicc-host/_twicc/browser-companion.js" defer></script>
```

Añádelo al `<html>` **head de tu página, lo antes posible**: lo ideal es que sea
la primera etiqueta de `<head>`, antes de cualquier otro script. Cuanto antes se cargue,
antes se capturan la navegación y los errores (usa `defer`, así que nunca
bloquea tu página). Es invisible para la propia página y no hace nada cuando
la página se ejecuta fuera de TwiCC, por lo que puedes dejarlo sin problema en tu plantilla de desarrollo.

No tienes que escribir la URL a mano: cuando la página incrustada no tiene el
complemento, un banner en la pestaña (y el botón del **enchufe**) ofrece este mismo
snippet, ya completado con el host correcto y listo para copiar. Si accedes a TwiCC desde otro equipo, configura la
[Dirección externa](help/external-url) para que el snippet copiado apunte a una dirección
que tu navegador pueda alcanzar.

El botón del **enchufe** muestra el estado del complemento: del color de la marca cuando
está conectado, rojo cuando falta el script y atenuado mientras se detecta.
Al hacer clic en él se abre esta página de ayuda.

### Modo responsivo

El botón del **teléfono** da a la página un viewport con el tamaño exacto de un dispositivo: elige un
ajuste predefinido (o escribe un ancho × alto), intercambia ambos valores o arrastra los tiradores alrededor
del marco. La página se adapta exactamente como lo haría en una ventana de ese tamaño.
No se necesita el complemento.

### Todo lo demás

- **Pantalla completa** expande toda la pestaña, barra de herramientas incluida.
- **Abrir en una nueva pestaña del navegador** pasa la URL actual a tu navegador real.
- Los errores, las selecciones y los comentarios siempre llegan al mensaje que estás
  redactando: no se envía nada al agente sin que tú lo decidas.
