TwiCC puede **leer en voz alta los mensajes del chat**. Elige una configuración en **Configuración →
Texto a voz**, pruébala, confirma que la escuchaste y actívala: aparecerá entonces un botón
de altavoz en los mensajes.

Hay dos tipos de configuración:

- **OpenRouter**: un modelo de voz de OpenRouter, con sus voces.
- **Script**: un comando de shell propio, que convierte el texto en audio.

## Leer los mensajes nuevos de la cronología

El **botón de altavoz junto a Cronología** lee en voz alta los mensajes nuevos del asistente a medida que llegan. El ámbito, la cola, los resúmenes y lo que ocurre al salir de la página o de la app están en su propia página: [Lectura de la cronología](help/timeline-speech).

## Resumir un mensaje o una selección

Usa **Resumir** en un mensaje elegible del asistente en el chat o en una vista de mensaje
de la cronología. El umbral cuenta el mensaje lógico completo, incluidos sus bloques de código y
de texto. Excluye los datos de artefactos incrustados y el marcado de control.
El resumen apunta a cerca de un tercio del original, con un máximo de 140 palabras.

Los resúmenes de mensajes se guardan. El chat, las vistas de la cronología y la escucha automática comparten
el mismo resultado cuando el origen y la política de modelo coinciden. La preparación automática
también guarda los resúmenes correctos. Un resultado de una política anterior sigue disponible.
La regeneración reemplaza un resultado solo si tiene éxito.

Usa **Mostrar resumen** para cambiar entre el texto original y su resumen. Copiar y **Leer
en voz alta** usan esa vista. La lectura del chat original conserva su alcance de bloques original.
La lectura de la cronología original usa la instantánea ensamblada de la cronología. La lectura manual
interrumpe la lectura automática y funciona de forma independiente del interruptor de resúmenes
automáticos. Los anuncios habituales de proyecto y de sesión nunca identifican un resumen.

Selecciona texto y usa **Resumir** en las acciones de selección para obtener un resumen
temporal. Su cuadro de diálogo admite Copiar, Leer en voz alta, Reintentar, Cancelar y Cerrar. Al cerrar
el cuadro de diálogo se libera su generación y se detiene su lectura correspondiente. Los resúmenes
de selección y la prueba de la configuración nunca se guardan como resúmenes de mensajes.

El texto del resumen aparece a medida que se transmite. La voz empieza con las partes del resumen
disponibles, sin una segunda llamada de adaptación. Si la generación falla después de que empiece la voz,
la reproducción se detiene con un error. TwiCC no reproduce el original ni guarda un
resumen parcial.

### Modelo de resumen

Elige **Auto**, **Claude Haiku 5.5**, **GPT-6 Luna** u **OpenRouter** en
**Configuración → Resúmenes**. Auto selecciona un agente activado en ejecución, primero Claude
y luego Codex. Si el agente seleccionado no está disponible, cede el paso al otro agente.

OpenRouter usa el modelo de texto y la clave de OpenRouter existentes. Sus llamadas se pagan
en tu cuenta de OpenRouter. Sin la clave, o por encima de **60 000 caracteres
de origen**, TwiCC selecciona un agente activado. Conserva el origen completo.
Si OpenRouter falla antes de que se confirme la voz del resumen, un agente activado puede reemplazar
su texto provisional. Sin un modelo disponible, el resumen muestra un error.
El interruptor automático de la cronología no desactiva los resúmenes manuales.

## Probar, confirmar, activar

**Probar** lee en voz alta el texto de prueba con la configuración y luego pregunta **«¿Lo escuchaste
correctamente?»**. Responde **Sí** para validar la configuración. Solo una configuración
validada puede activarse, y la configuración activa es la que usan los mensajes.

Cambiar lo que produce el sonido (el modelo, la voz, el formato, el comando…)
elimina la validación: vuelve a probar la configuración.

## Configuraciones de OpenRouter

Necesitan una clave de OpenRouter: añade `TWICC_OPENROUTER_API_KEY` al archivo `.env`
del directorio de datos de TwiCC y luego reinicia TwiCC. La clave nunca sale
del servidor.

Los precios mostrados son los precios de lista de OpenRouter. Después de una prueba, la configuración muestra
cuánto costó realmente la voz de la prueba.

## Mensajes largos

Un mensaje largo se lee **por partes**, una tras otra, en un solo audio. Cada
parte se genera cuando la reproducción la necesita, con un minuto de antelación aproximadamente: el sonido
empieza rápido, y una detención no genera (ni paga) el resto. Las uniones
entre partes pueden oírse.

## Leer una selección

Selecciona texto en el chat o en el visor de archivos: el widget flotante muestra el
**botón Leer** (el icono de altavoz), junto a los botones Comentar y Copiar. No
aparece en la terminal, en el panel del navegador ni en las tarjetas de elementos.
Solo aparece cuando el servicio de voz está disponible, igual que el botón de altavoz de
un mensaje.

- El botón lee el texto seleccionado con la configuración activa. Su título es
  «Leer en voz alta».
- Mientras lee, el mismo botón es un **icono de detener** (título «Detener la lectura»),
  y permanece visible. Haz clic en él para cancelar la solicitud o detener el sonido.
- El sonido continúa cuando el widget se cierra. Deténlo desde el indicador de voz
  del riel, igual que con un mensaje.
- Un texto sin nada que leer en voz alta una vez quitado su formato
  se rechaza, igual que con un mensaje.

## Adaptar el texto

Un modelo pequeño puede **reescribir un texto para el oído** antes de leerlo, cuando el
texto no es simple: una tabla se convierte en frases, una lista en una enumeración hablada, un
bloque de código en una breve descripción; los hashes, las rutas largas y la sintaxis Markdown
desaparecen. El contenido y el idioma siguen siendo los mismos. Un **texto simple se lee
de inmediato**, sin sus marcas Markdown y sin modelo: el sonido empieza
antes y no se paga nada por la reescritura. Un enlace Markdown se lee por
su texto.

La prueba de una configuración nunca usa la lectura directa: pasa por el
modelo siempre que el modelo pueda ejecutarse, así que muestra la adaptación, y su última
ejecución de prueba muestra el texto adaptado. Con **Desactivado**, o sin modelo disponible, se
lee tal como está escrito.

Por ejemplo, estos textos **no son simples** y pasan por el modelo:

- tablas, código y texto parecido a código (cualquier cosa con símbolos de código como `$`,
  `=`, `|` o llaves, nombres en `snake_case`, `#123`) y HTML;
- direcciones, rutas, hashes y números largos;
- listas largas: más de cinco elementos, elementos largos o sublistas;
- una selección con un salto de línea o un tabulador en el chat, y cualquier selección en el visor
  de archivos.

Cuando las reglas no reconocen un texto con certeza, decide el modelo.

Elige el modelo en **Adaptar el texto para voz**, en la parte superior de **Configuración →
Texto a voz**: **Claude Haiku 5.5**, **GPT-6 Luna**, **OpenRouter** o
**Desactivado**, que lee todos los textos tal como están escritos. Sin elección, TwiCC toma
Claude y, si no, Codex. Un modelo de Claude o Codex cuyo proveedor está desactivado se
reemplaza por el otro agente, y tu elección vuelve cuando activas de nuevo su
proveedor.

Claude Haiku 5.5 y GPT-6 Luna se ejecutan con tu suscripción de Claude o Codex.
**OpenRouter** llama a un modelo rápido en OpenRouter: actualmente, Qwen3.8 27B servido por
Cerebras. Necesita la clave de OpenRouter, igual que las configuraciones de OpenRouter. Envía
el texto a OpenRouter y se paga en tu cuenta de OpenRouter. Con la
clave definida, adapta el texto incluso cuando no se ejecuta ningún proveedor.
Sin la clave, lo reemplaza el valor predeterminado, y tu elección vuelve una vez que la
clave está definida y TwiCC se ha reiniciado.

Si OpenRouter falla antes de que empiece la lectura, el primer agente que se ejecuta
(Claude y, si no, Codex) adapta el texto. Si ningún modelo puede ejecutarse, si el texto es
demasiado largo o si el modelo falla antes de que empiece la lectura (para OpenRouter: si
ese agente también falla, o si no se ejecuta ningún proveedor), el texto se lee **tal como
está escrito**. Si el modelo falla más tarde, la lectura se detiene con un error.

La última ejecución de prueba de una configuración muestra el modelo que adaptó el texto, o cada
modelo que falló y por qué: una clave incorrecta o una cuenta de OpenRouter vacía se ve
ahí. El costo de una prueba es solo el costo de la voz: la adaptación de
OpenRouter no está incluida.

## Configuraciones de script

TwiCC ejecuta el **Comando** en tu shell, como un shell de inicio de sesión no interactivo en
tu directorio personal, y escribe el texto en su **entrada estándar**. El
comando escribe el audio en su **salida estándar** y termina con estado 0.
El texto nunca va en la línea de comandos.

| Salida | El comando escribe | El sonido empieza |
|---|---|---|
| **Stream de audio** | el audio, mientras se genera | con sus primeros bytes |
| **Audio completo** | el audio, al final | cuando termina la ejecución |
| **Ruta de archivo** | una ruta absoluta, a un archivo nuevo en cada ejecución | cuando termina la ejecución |

| Formato | Lo que da el comando |
|---|---|
| **MP3** | MP3 |
| **WAV** | WAV PCM de 16 bits (un WAV escrito sobre la marcha, con tamaños desconocidos, sirve) |
| **PCM** | PCM sin procesar de 16 bits little-endian: indica su frecuencia de muestreo y canales |

**Máx. de caracteres por ejecución**: vacío, todo el texto va a una sola ejecución. En otro caso,
el texto se divide en partes de este tamaño como máximo, una ejecución cada una. Con **Audio
completo** o **Ruta de archivo**, defínelo: el sonido empieza entonces después de la primera parte,
no después de todo el texto.

**Eliminar el archivo después de leerlo** (solo Ruta de archivo, activado de forma predeterminada): TwiCC
elimina el archivo una vez que lo ha leído y la ejecución ha tenido éxito. Solo comprueba
que la ruta sea un archivo normal, no dónde está: asegúrate de que el comando
devuelva su propio archivo nuevo. Una ejecución fallida conserva su archivo.

### Cuando falla

- Un comando que termina con un estado distinto de cero muestra la **última línea de su
  salida de error**.
- En otro caso, TwiCC dice qué está mal: una salida que no es del formato elegido,
  falta de audio, una ruta que no es una única ruta absoluta…
- **Nada más puede imprimirse en la salida estándar**: un mensaje del perfil de tu shell
  o una línea de progreso rompe el audio.
- Lo que define `~/.bashrc` o `~/.zshrc` normalmente falta (alias, adiciones a
  `PATH`): usa rutas completas o un script que defina lo que necesita.

### Conviene saber

- Una detención termina todo el comando, incluidos los pipelines.
- Varias lecturas pueden ejecutar el comando al mismo tiempo (una prueba y un mensaje,
  dos dispositivos).
- Un stream de audio que no envía nada durante un minuto después de su primer audio se
  detiene. El audio de una lectura está limitado a 512 MB.
- TwiCC solo ve la línea de comandos: después de editar un script que ejecuta el comando,
  vuelve a probar la configuración (de lo contrario, un mensaje puede reproducir un audio en caché
  hasta 10 minutos).

### Ejemplos

Un tono de prueba, que ignora el texto (**Stream de audio**, **WAV**):

```
ffmpeg -nostdin -loglevel error -f lavfi -i sine=frequency=440:duration=3 -ac 1 -ar 22050 -f wav -
```

[eSpeak NG](https://github.com/espeak-ng/espeak-ng) (**Stream de audio**,
**WAV**):

```
espeak-ng --stdin --stdout -v en
```

Lo mismo, mediante un archivo (**Ruta de archivo**, **WAV**):

```
f=$(mktemp) && espeak-ng --stdin -v en -w "$f" && echo "$f"
```

[Piper](https://github.com/rhasspy/piper) (**Stream de audio**, **PCM**, la
frecuencia de muestreo de la voz, a menudo 22050 Hz, 1 canal):

```
piper --model ~/voices/en_US-lessac-medium.onnx --output-raw
```

## Límites conocidos

- En un iPhone o un iPad (y en Safari para MP3), el audio se descarga
  completo antes de reproducirse: el sonido empieza más tarde.
- En Android, una lectura larga en PCM (o WAV) puede detenerse poco después de que la pantalla
  se bloquee: el navegador congela la página.
