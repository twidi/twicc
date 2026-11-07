Los artefactos son contenido que un agente crea para ti, bajo petición. Se guardan
fuera de los archivos del propio proyecto, así que tu proyecto no se toca.

El agente crea cada uno como un archivo y lo guarda en la carpeta de artefactos de la
sesión: imágenes, páginas web (zonas de prueba interactivas, miniaplicaciones, etc.), PDF,
Markdown, diagramas, audio, video. Pídele al agente desde el principio que lo haga como un
artefacto y es más probable que termine siéndolo; ser explícito también le ayuda
a entender lo que buscas.

Los encontrarás en la pestaña **Artefactos** de esa sesión, donde TwiCC los muestra
renderizados. Esa pestaña solo aparece cuando la sesión tiene al menos un
artefacto.

Desde ahí puedes añadir a **favoritos** cualquier artefacto, dándole un nombre y un ámbito:
el proyecto al que pertenece la sesión, el espacio de trabajo de ese proyecto, o todas partes.

Las páginas interactivas también pueden **guardar datos**: lo que haces en la página (elecciones,
ajustes, notas, estado de un juego…) se almacena en una carpeta `data/` junto al
artefacto y se conserva al cerrarlo y volver a abrirlo. El agente puede volver a leer esos
archivos, así que una página puede funcionar como un formulario enriquecido: el agente te entrega un
artefacto, haces tus elecciones y guardas, le dices al agente que terminaste,
y él las lee desde ahí. Los archivos aparecen en el árbol de artefactos como cualquier otro,
así que siempre puedes inspeccionar o eliminar lo que una página ha almacenado.

En las páginas web renderizadas, el botón redondo de **herramientas** de la esquina despliega
acciones adicionales: un **modo responsivo** para previsualizar la página con el tamaño exacto de un dispositivo,
y un modo de **selección de elementos** para hacer clic en cualquier elemento de la página
y entregarle al agente una descripción precisa de él, con una nota y una captura de pantalla opcionales,
en lugar de describirlo con palabras. Funcionan exactamente igual que en
la [pestaña Navegador](help/browser-tab).

La **vista de Artefactos** (ábrela con el botón **Artefactos** de la barra de
iconos) reúne los artefactos que has añadido a favoritos. Cada artefacto aparece según
el ámbito que elegiste para él. Al crear favoritos, dales nombres claros y descriptivos para que
sean fáciles de encontrar ahí.
