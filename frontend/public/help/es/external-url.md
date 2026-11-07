La **dirección externa** es la dirección con la que accedes a *esta* instancia de TwiCC desde tus
dispositivos. TwiCC también la usa para construir enlaces de vuelta a tus sesiones. Una
notificación externa puede así abrir la conversación correcta en lugar de una dirección
`localhost` inútil.

### Para qué sirve

- Abrir una sesión desde tu **teléfono u otro equipo** mientras TwiCC sigue
  ejecutándose en tu máquina de casa.
- También se integra en las **notificaciones externas** (Apprise): una alerta enviada
  puede enlazar directamente a la sesión a la que se refiere.

Si la dejas vacía, TwiCC simplemente omite esos enlaces; nada más deja de funcionar.

### Por qué necesitas un túnel

TwiCC se ejecuta como un proceso local, así que `http://localhost:…` solo tiene sentido en
la máquina donde se ejecuta. Para acceder desde otro lugar, lo expones mediante un
**túnel**: un servicio que da a tu instancia local una dirección pública estable y
le reenvía el tráfico. Esa dirección pública es exactamente la que pones en la
dirección externa. Algunas opciones populares:

- **Cloudflare Tunnel** (`cloudflared`) — un túnel con nombre y un nombre de host estable.
- **Tailscale** — una VPN en malla privada entre tus propios dispositivos, con exposición
  pública opcional (Funnel).
- **ngrok** — URL públicas rápidas para un puerto local.

### Protege tu túnel

Un túnel puede hacer que tu instancia sea accesible desde internet abierto, así que tómalo
en serio. Cada proveedor ofrece formas de protegerlo: úsalas.

- **Cloudflare Tunnel** → pon **Cloudflare Access** delante (PIN de un solo uso por correo,
  SSO de Google/GitHub), añade reglas de IP o WAF.
- **Tailscale** → mantenlo en tu **tailnet privada** para que solo tus propios dispositivos
  accedan a él (evita Funnel público salvo que lo necesites de verdad); refuérzalo con ACL de
  dispositivos.
- **ngrok** → activa su **Basic Auth** integrada, **OAuth** (Google, GitHub…)
  o listas de IP permitidas.

### La contraseña de TwiCC va primero

Lo más importante: **TwiCC rechaza toda conexión no local salvo que hayas definido una
contraseña.** Por eso una dirección externa solo funciona de verdad una vez que has ejecutado
`twicc password set`: esa contraseña es tu protección principal.

(La protección del proveedor del túnel va *encima* de esa: una segunda capa, además de la
contraseña, pero sigue siendo importante: mantiene el tráfico no deseado completamente
lejos de TwiCC, antes de que llegue siquiera a la pantalla de inicio de sesión.)
