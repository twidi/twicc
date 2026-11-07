A aba Navegador incorpora uma página de sua escolha ao lado da conversa.
Geralmente, essa página é o servidor de desenvolvimento do projeto em que você trabalha.
A página funciona como em uma aba normal do navegador: rede direta,
recarregamento automático e service workers. O TwiCC não usa proxy nem sandbox para ela.

**Para um agente configurar seu servidor de desenvolvimento,** indique esta página:
<https://github.com/twidi/twicc/blob/main/frontend/public/help/browser-tab.md>.
Ela contém tudo que é necessário: incorporação em frames, cookies e o companion script.
O agente também precisa do endereço usado para acessar o TwiCC. As mensagens de configuração
na aba oferecem **Adicionar instruções à mensagem** e **Copiar instruções para um
agente**. Elas fornecem um prompt pronto com esta página, esse endereço e o trecho de código do companion.

### Abrir uma página

Digite qualquer URL na barra de endereços. O botão **Início** abre a URL
padrão salva. Use o menu de **favoritos** para salvar a URL atual como
padrão do projeto ou de um de seus espaços de trabalho. A aba passa a abrir
nessa URL imediatamente. A última URL acessada também é salva por sessão.

### Limites da incorporação

Algumas páginas não podem ser incorporadas:

- Sites que recusam a exibição em frames mostram um aviso. A maioria dos sites públicos faz isso.
  Use **Abrir em uma nova aba do navegador**.
- Quando o próprio TwiCC usa `https`, o navegador bloqueia páginas
  `http://` comuns por conteúdo misto.

Seus servidores de desenvolvimento locais são o uso ideal desta aba.

### Login e armazenamento

A permanência do login na página incorporada depende da localização do
servidor de desenvolvimento em relação ao TwiCC.

**Mesmo host** — TwiCC e seu aplicativo usam `localhost`, em qualquer porta.
Nenhuma configuração é necessária. A página compartilha os mesmos cookies e armazenamento de uma
aba normal. Assim, um login feito nessa aba já se aplica. As portas não importam:
`localhost:5173` e `localhost:3000` contam como o mesmo site.

**Domínios diferentes** — por exemplo, TwiCC em um túnel e seu aplicativo em outro.
O navegador isola a página incorporada. Você começa sem login e precisa de algumas
configurações **apenas para desenvolvimento**:

- **Permitir frames** — muitos frameworks bloqueiam a incorporação por padrão com
  `X-Frame-Options` / `frame-ancestors`, como Django e Rails. Relaxe essa restrição no desenvolvimento.
- **Cookies** — defina o cookie de sessão como `SameSite=None; Secure`. `Secure`
  funciona com `https` e `http://localhost`, mas não com um IP da LAN usando HTTP simples.
- Depois, **faça login uma vez dentro da aba**. A página incorporada recebe sua própria
  área privada de cookies e armazenamento, preservada entre recarregamentos. `localStorage` e
  `IndexedDB` não exigem configuração. Eles usam o mesmo isolamento e funcionam diretamente.

Mantenha essas configurações sob uma flag de desenvolvimento. Não use `SameSite=None` nem
incorporação sem restrições em produção.

### O companion script

O TwiCC não consegue observar o conteúdo de uma página incorporada. Links abertos dentro dela
ficam invisíveis. Assim, Voltar / Avançar só acompanham URLs digitadas na barra de ferramentas.
O TwiCC fornece um **companion script** para adicionar à página de desenvolvimento.
Ele remove esse limite e libera mais recursos:

- acompanhamento real da navegação — Voltar / Avançar / Atualizar controlam o histórico
  da própria página. A navegação interna da página (SPA) atualiza a barra de endereços;
- **seleção de elementos** — você não precisa descrever "o terceiro botão do
  cabeçalho" e esperar que o agente o encontre. Ative o modo pelo botão de **cursor
  do mouse** na barra de ferramentas. Clique no elemento da página e depois
  no botão de **comentário**. Um widget permite adicionar uma
  nota opcional e uma captura do elemento. Depois, você cola tudo
  no campo de mensagem. Ele inclui uma descrição precisa do
  elemento, adequada para trabalhar com código: tag, ids e classes que o agente encontra no código-fonte.
  Seu comentário acompanha a descrição. Ideal para pedidos como "aumente isto" ou "este texto
  está errado";
- **erros da página** — exceções não tratadas e chamadas de `console.error` são
  coletadas e contadas na barra de ferramentas. Você as adiciona à mensagem com um clique.

O trecho de código é uma única tag `<script>` que aponta para
`/_twicc/browser-companion.js` no host do TwiCC:

```html
<script src="https://your-twicc-host/_twicc/browser-companion.js" defer></script>
```

Adicione a tag ao **head do `<html>`, o mais cedo possível**.
O ideal é colocá-la primeiro em `<head>`, antes de qualquer outro script.
Quanto antes ela carregar, antes captura a navegação e os erros.
Ela usa `defer`, portanto nunca bloqueia a página. A página não percebe sua presença.
Ela não faz nada quando a página funciona fora do TwiCC. Você pode mantê-la no template de desenvolvimento.

Você não precisa digitar a URL manualmente. Quando a página incorporada não tem
companion, um aviso na aba e o botão de **plugue** oferecem esse mesmo
trecho de código, já com o host correto, pronto para copiar. Se você
acessa o TwiCC de outra máquina, configure o
[Endereço externo](help/external-url). Assim, o trecho de código copiado aponta para um endereço
acessível pelo seu navegador.

O botão de **plugue** mostra o status do companion: usa a cor da marca quando
conectado, vermelho quando o script está ausente e fica esmaecido durante a detecção.
Clique nele para abrir esta página de ajuda.

### Modo responsivo

O botão de **telefone** define a área visível da página com o tamanho exato de um dispositivo.
Escolha uma predefinição ou digite largura × altura. Você pode trocar as duas dimensões
ou arrastar as alças ao redor do frame. A página se reorganiza como em uma janela desse tamanho.
O companion não é necessário.

### Outros recursos

- **Tela cheia** expande a aba inteira, incluindo a barra de ferramentas.
- **Abrir em uma nova aba do navegador** abre a URL atual no seu navegador.
- Erros, seleção e comentários sempre entram na mensagem que você está
  escrevendo. Nada é enviado ao agente sem sua ação.
