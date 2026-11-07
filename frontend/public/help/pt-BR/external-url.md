O **Endereço externo** é onde você acessa *esta* instância do TwiCC nos seus
dispositivos. O TwiCC também o usa para criar links para suas sessões. Assim, uma
notificação externa abre a conversa correta em vez de um endereço `localhost`
inacessível.

### Para que serve

- Abra uma sessão pelo **telefone ou por outro computador** enquanto o TwiCC continua
  em execução na sua máquina de casa.
- O endereço também entra nas **notificações externas** (Apprise). Assim, um alerta
  pode levar diretamente à sessão correspondente.

Deixe o campo vazio para o TwiCC omitir esses links. Os outros recursos continuam funcionando.

### Por que você precisa de um túnel

O TwiCC funciona como um processo local. Por isso, `http://localhost:…` só faz sentido
na máquina em que ele está em execução. Para acessá-lo de outro lugar, use um
**túnel**. Esse serviço fornece um endereço público estável para sua instância local e
encaminha o tráfego até ela. Insira esse endereço público no campo
Endereço externo. Algumas opções populares:

- **Cloudflare Tunnel** (`cloudflared`) — um túnel nomeado com hostname estável.
- **Tailscale** — uma VPN mesh privada entre seus dispositivos, com exposição
  pública opcional (Funnel).
- **ngrok** — URLs públicas rápidas para uma porta local.

### Proteja seu túnel

Um túnel pode tornar sua instância acessível pela internet pública. Proteja esse acesso.
Todos os provedores oferecem formas de restringi-lo. Use esses recursos:

- **Cloudflare Tunnel** → coloque o **Cloudflare Access** na frente: PIN de uso
  único por email ou SSO do Google/GitHub. Adicione regras de IP ou WAF.
- **Tailscale** → mantenha o acesso na sua **tailnet privada**, limitado aos seus dispositivos.
  Evite o Funnel público, a menos que precise dele. Use ACLs de dispositivos para restringir mais o acesso.
- **ngrok** → ative **Basic Auth**, **OAuth** (Google, GitHub…)
  ou listas de IPs permitidos.

### A senha do próprio TwiCC vem primeiro

O ponto principal: **o TwiCC recusa toda conexão não local até você definir uma
senha.** Assim, o Endereço externo só funciona depois de executar
`twicc password set`. Essa senha é sua proteção principal.

(A proteção do provedor do túnel funciona *sobre* essa proteção.
Ela é uma segunda camada, além da senha, e continua importante.
Ela impede que tráfego indesejado chegue ao TwiCC, antes mesmo da tela de login.)
