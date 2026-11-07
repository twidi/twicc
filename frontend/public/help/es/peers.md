La mensajería de pares conecta dos instancias independientes de TwiCC. Sirve sobre todo para dos
personas que trabajan juntas desde sus propios equipos. También puedes emparejar dos de tus propias
instancias, pero ese no es el caso de uso principal.

Un mensaje de un par nunca entra por sí solo en una sesión de la otra instancia. El usuario que lo
recibe debe leerlo primero. Puede rechazarlo o colocarlo en el editor de mensajes de una sesión.

Incluso entonces, TwiCC no envía el mensaje al agente. El usuario puede revisar o editar el
editor de mensajes y luego decidir si lo envía.

### Para qué sirven los pares

Los pares dan a los agentes una forma controlada de pasar trabajo entre personas. Por ejemplo, un
agente puede enviar:

- un resumen del trabajo terminado;
- un contrato de API o una pregunta técnica;
- una captura de pantalla, un documento u otro archivo útil;
- una respuesta a un mensaje anterior de un par.

También puedes escribir un mensaje tú mismo, cuando la instrucción es totalmente tuya y un agente
no aportaría nada.

El mensaje debe ser autosuficiente. La otra instancia no comparte la memoria ni la conversación de
la sesión que envía.

### Qué no comparten los pares

El emparejamiento no da a ninguna de las dos personas acceso a la otra instancia. No comparte
proyectos, sesiones, archivos, terminales, configuración ni el control de los agentes.

Solo el título, el texto, los adjuntos, la hora y la relación de respuesta del mensaje pasan de una
instancia a otra. Los detalles locales de las sesiones se quedan en local.

### Antes de emparejar

Abre **Configuración → Pares** para indicar tu nombre y tu dirección. Tu nombre identifica a tu
instancia en las solicitudes de emparejamiento. Tu dirección debe ser accesible desde el otro
equipo con TwiCC.

Si dejas la dirección vacía, la mensajería de pares se desactiva. Usa HTTPS siempre que sea
posible. Las llamadas entre pares son de máquina a máquina. Si tu túnel habitual de TwiCC pide a los
visitantes iniciar sesión por correo electrónico, SSO u otra comprobación interactiva, no uses esa
dirección para los pares. La otra instancia de TwiCC no puede completar esa comprobación.

Crea una segunda dirección de túnel sin esa barrera y úsala como dirección de par. Mantén activada
la contraseña propia de TwiCC.

Una dirección de par distinta de tu dirección externa atiende solo el tráfico de pares. No sirve
ninguna página ni conexión en vivo, así que no necesitas un filtro de rutas en el túnel. Si
introduces tu dirección externa como dirección de par, esa única dirección atiende tanto la
aplicación como el tráfico de pares. Cualquier otra dirección, incluida `localhost`, nunca atiende
tráfico de pares.

Un proxy o un túnel situado delante de tu dirección de par debe aceptar cuerpos de solicitud de
72 MiB. Uno dimensionado para el antiguo límite de 48 MiB rechaza los mensajes grandes.

No uses la dirección dedicada a compartir para los pares. TwiCC limita ese nombre de host a las
rutas de compartir, y Configuración rechaza una dirección de par en él. Consulta [acceder a TwiCC
desde cualquier lugar](help/external-url) para la configuración general del túnel y la contraseña.

### Emparejar dos instancias

Las relaciones entre pares usan un flujo de solicitud y aprobación. Un usuario envía una solicitud
de emparejamiento desde **Administrar pares**. El otro usuario recibe la solicitud y ve un código de
verificación de seis dígitos.

El usuario que recibe comparte ese código por un canal en el que ya confía, como una llamada o un
chat privado. Quien solicita lo introduce en su propia instancia. Así se confirma que la solicitud
llegó a la persona prevista.

La verificación solo desbloquea la aprobación. El usuario que recibe sigue decidiendo si acepta o
rechaza la relación. Cada usuario elige además su propio nombre local para el par.

Solo los usuarios gestionan las relaciones entre pares. Los agentes no pueden crearlas, aprobarlas,
rechazarlas, renombrarlas, revocarlas ni reconectarlas.

### Enviar un mensaje

Un agente puede listar los pares activos y enviar un mensaje con título a uno de ellos. Un mensaje
puede contener texto y archivos de cualquier tipo; cada archivo conserva su nombre. Todos los
archivos de un mensaje suman como máximo 50 MiB; para un archivo más grande, el agente lo sube a un
servicio de almacenamiento de archivos y pasa su URL en el texto del mensaje. Una vez aprobada la
relación entre pares por el usuario, no se requiere ninguna confirmación adicional del lado del
remitente.

Un par que ejecuta una versión anterior de TwiCC rechaza un mensaje con archivos: su usuario debe
actualizar. Un mensaje solo de texto sí le llega.

También puedes escribir un mensaje tú mismo, desde **Administrar pares**, desde la bandeja de entrada
o como respuesta mientras revisas un mensaje. Este formulario solo admite un título y un texto. No
lleva adjuntos y no guarda borradores: al cerrarlo se descarta lo que escribiste. Pide a un agente
cualquier otra cosa: adjunta archivos y redacta el mensaje por ti.

El título debe indicar claramente el tema. El texto debe incluir todo el contexto que la persona y
el agente que reciben necesitan.

Un envío correcto significa que la otra instancia almacenó el mensaje. No significa que el usuario
que recibe lo haya entregado a una sesión.

### La bandeja de entrada de pares

Abre la bandeja de entrada desde **Configuración → Pares**, o desde el botón de la bandeja junto a
**Configuración**. Ese botón aparece en cuanto el sistema de pares está configurado, incluso con la
bandeja vacía. Cuando una solicitud o un mensaje requiere tu atención, TwiCC muestra además una
insignia en la bandeja de pares.

La bandeja contiene solicitudes de emparejamiento, mensajes pendientes de revisión y el historial
de mensajes. Puedes filtrar los mensajes por par o por su título y texto completo.

Una notificación de mensaje entrante puede abrir la bandeja. No puede entregar el mensaje ni
iniciar un agente.

TwiCC también puede avisarte fuera de la aplicación cuando llega un mensaje o una solicitud de
emparejamiento: un sonido, una notificación del navegador o un push a tus dispositivos. Actívalo en
**Configuración → Notificaciones → Un par te necesita**. Ningún otro evento de pares envía un aviso:
esperan hasta tu próxima visita.

### Revisar un mensaje entrante

La revisión muestra el remitente, el título, el mensaje, los adjuntos y cualquier relación de
respuesta. También indica cuándo el usuario del par escribió el mensaje en lugar de su agente. Un
texto o unos adjuntos grandes requieren una acción de carga adicional antes de que TwiCC los
muestre o los descargue.

Después eliges una acción en la lista de acciones de la revisión:

- colocar el mensaje en el editor de mensajes de una sesión existente;
- crear una nueva sesión en borrador con el mensaje en su editor de mensajes;
- marcar el mensaje como hecho, cuando lo resolviste tú mismo o no hay nada que hacer; ningún
  agente lo recibe;
- responder manualmente (ver más abajo);
- rechazar el mensaje.

Colocar un mensaje en un editor de mensajes lo marca como entregado. Aun así, no envía el editor de mensajes ni
inicia el agente. Al entregar, puedes añadir una nota opcional para tu agente. TwiCC mantiene esa
nota separada del mensaje del par y la identifica como texto tuyo.

Un rechazo no lleva palabras. Para explicarlo, responde con **Responder manualmente** y rechaza en
el mismo paso.

El remitente solo conoce tu primera decisión. Por eso, marcar como hecho y rechazar se ofrecen
únicamente mientras el mensaje está pendiente. Un mensaje resuelto se puede volver a entregar desde
su entrada en el historial.

### Respuestas y sugerencias de sesión

Un mensaje de un par puede responder a un mensaje anterior. La bandeja y la revisión muestran a qué
mensaje responde, y la revisión puede abrir ese mensaje. Un mensaje que recibió respuestas muestra
quién las dio: el par, su agente, tú o tu agente.

Puedes responder a un mensaje tú mismo con **Responder manualmente** en la lista de acciones de la
revisión. TwiCC propone el título del mensaje respondido, con el prefijo `Re:`. Mientras el mensaje
respondido sigue esperando tu decisión, el formulario de respuesta te permite dejarlo abierto,
marcarlo como hecho o rechazarlo en el mismo paso. La elección se aplica solo cuando la respuesta
llegó al par.

Cuando es posible, TwiCC sugiere la sesión local usada para el mensaje anterior. Es solo una
sugerencia. El usuario que recibe puede elegir otra sesión o un nuevo borrador.

Toda respuesta pasa por la misma revisión humana. Una respuesta nunca omite la aprobación del
usuario que recibe.

### Estado de los mensajes

Los mensajes enviados usan cinco estados:

- **pendiente**: el usuario que recibe aún no ha decidido;
- **entregado**: el usuario colocó el mensaje en el editor de mensajes de una sesión. Puede haber
  consecuencias posteriores, por parte de su agente;
- **hecho**: el usuario resolvió el mensaje por sí mismo. Ningún agente lo recibió y no llegará nada
  más de un agente. Si tenía algo que decirte, llegó como respuesta;
- **rechazado**: el usuario declinó el mensaje;
- **fallido**: el remitente no recibió una aceptación confirmada de la otra instancia.

El estado registra la primera decisión del usuario que recibe. Puede cambiarla después; al
remitente no se le avisa.

Un agente puede consultar el estado actual de un mensaje que envió. Un estado fallido puede ser
incierto: la otra instancia pudo haber almacenado el mensaje antes de que fallara la conexión.

### Reentrega e historial

Un mensaje resuelto sigue disponible en el historial de la bandeja. El usuario que recibe puede
volver a colocarlo en un editor de mensajes, sea cual sea la decisión anterior. Esto ayuda cuando eligió la
sesión equivocada o borró un borrador.

TwiCC elimina los bytes de los adjuntos siete días después de la última decisión. Una nueva
decisión reinicia ese plazo, hasta que se eliminan los bytes. Un mensaje pendiente conserva sus
adjuntos. El texto, el título, los detalles de los adjuntos y la relación de respuesta permanecen
siempre en el historial. Por eso, una reentrega posterior puede quedar solo en texto.

### Revocación y reconexión

Cada parte controla su propia relación entre pares. Cualquiera de los dos usuarios puede revocarla
en cualquier momento. La revocación detiene la mensajería y borra las credenciales de la relación,
pero conserva el nombre local del par y todo el historial de mensajes.

La revocación es silenciosa. La otra instancia se entera cuando se rechaza una solicitud posterior
del par. Su copia de la relación aparece entonces como **rota**.

Un par también puede quedar roto cuando cambias o desactivas tu propia dirección de par. Define una
dirección accesible y reconecta manualmente cada par afectado.

La reconexión usa un nuevo código de verificación y requiere la aprobación del otro usuario, igual
que el emparejamiento original. Restaura la relación existente en lugar de crear otra, por lo que
su nombre local y su historial de mensajes siguen disponibles.

La revisión humana es el principal límite de seguridad. No hace seguro el contenido no confiable.
Lee cada mensaje antes de colocarlo en un editor de mensajes.
