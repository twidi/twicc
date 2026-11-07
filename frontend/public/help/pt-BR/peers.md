As mensagens entre instâncias parceiras conectam duas instâncias independentes do TwiCC. Elas servem principalmente para duas
pessoas que trabalham juntas nas próprias máquinas. Você também pode parear duas
instâncias suas, mas esse não é o uso principal.

Uma mensagem de uma instância parceira nunca entra sozinha em uma sessão da outra instância. O
usuário que a recebe precisa lê-la primeiro. Ele pode recusá-la ou colocá-la no
campo de mensagem de uma sessão.

Mesmo assim, o TwiCC não envia a mensagem ao agente. O usuário pode revisar ou
editar o campo de mensagem e decidir se envia.

### Para que servem as instâncias parceiras

As instâncias parceiras dão aos agentes uma forma controlada de passar trabalho entre pessoas. Por exemplo,
um agente pode enviar:

- um resumo do trabalho concluído;
- um contrato de API ou uma pergunta técnica;
- uma captura de tela, um documento ou outro arquivo útil;
- uma resposta a uma mensagem anterior de uma instância parceira.

Você também pode escrever uma mensagem por conta própria quando a instrução é inteiramente sua
e um agente não acrescenta nada.

A mensagem deve conter todo o contexto necessário. A outra instância não compartilha a
memória nem a conversa da sessão remetente.

### O que as instâncias parceiras não compartilham

O pareamento não dá a nenhuma das pessoas acesso à outra instância. Ele não
compartilha projetos, sessões, arquivos, terminais, configurações nem controle de agentes.

Apenas título, texto, anexos, horário e relação de resposta da mensagem passam
entre as instâncias. Os detalhes das sessões locais permanecem locais.

### Antes de parear

Abra **Configurações → Instâncias parceiras** para definir seu nome e endereço. Seu nome identifica
sua instância nas solicitações de pareamento. Seu endereço deve ser acessível pela
outra máquina com TwiCC.

Deixar o endereço vazio desativa as mensagens entre instâncias parceiras. Use HTTPS quando possível.
As chamadas entre instâncias parceiras ocorrem entre máquinas. Se seu túnel habitual do TwiCC exige
login por email, SSO ou outra verificação interativa, não use esse endereço
para instâncias parceiras. A outra instância do TwiCC não consegue concluir essa verificação.

Crie um segundo endereço de túnel sem essa barreira e use-o como endereço para
instâncias parceiras. Mantenha a senha do próprio TwiCC ativada.

Um endereço para instâncias parceiras diferente do Endereço externo atende apenas ao tráfego
entre instâncias parceiras. Ele não fornece páginas nem conexões em tempo real. Assim, você não precisa de um filtro de
caminho no túnel. Se você usa o Endereço externo como endereço para instâncias parceiras,
esse endereço único atende ao aplicativo e ao tráfego entre instâncias parceiras. Todos os outros endereços, incluindo
`localhost`, nunca atendem ao tráfego entre instâncias parceiras.

Um proxy ou túnel na frente do endereço para instâncias parceiras deve aceitar corpos de solicitação de
72 MiB. Um configurado para o antigo limite de 48 MiB recusa mensagens grandes.

Não use o endereço dedicado de compartilhamento para instâncias parceiras. O TwiCC limita esse hostname
às rotas de compartilhamento. A página Configurações recusa um endereço para instâncias parceiras nesse host. Veja [como acessar
o TwiCC de qualquer lugar](help/external-url) para a configuração geral do túnel e
da senha.

### Parear duas instâncias

As conexões entre instâncias parceiras usam um fluxo de solicitação e aprovação. Um usuário envia uma solicitação
de pareamento por **Gerenciar instâncias parceiras**. O outro usuário recebe a solicitação e vê um
código de verificação de seis dígitos.

O usuário que recebe compartilha esse código por um canal em que já confia, como
uma chamada ou um chat privado. O solicitante insere o código na própria instância.
Isso confirma que a solicitação chegou à pessoa correta.

A verificação apenas libera a aprovação. O usuário que recebe ainda escolhe se
aceita ou recusa a conexão. Cada usuário também escolhe seu próprio nome local
para a instância parceira.

Apenas usuários gerenciam conexões entre instâncias parceiras. Agentes não podem criá-las, aprová-las, recusá-las,
renomeá-las, revogá-las nem reconectá-las.

### Enviar uma mensagem

Um agente pode listar instâncias parceiras ativas e enviar uma mensagem com título para uma delas. Uma
mensagem pode conter texto e arquivos de qualquer tipo. Cada arquivo mantém seu nome. Todos
os arquivos de uma mensagem têm limite total de 50 MiB. Para um arquivo maior, o
agente o coloca em um serviço de armazenamento e informa sua URL no texto da
mensagem. Nenhuma confirmação adicional do remetente é necessária depois que o usuário
aprova a conexão com a instância parceira.

Uma instância parceira com uma versão antiga do TwiCC recusa mensagens com arquivos. Seu usuário precisa
atualizar o TwiCC. Mensagens apenas com texto continuam chegando.

Você também pode escrever uma mensagem por conta própria, em **Gerenciar instâncias parceiras**, na caixa de entrada
ou como resposta durante a revisão de uma mensagem. Esse formulário aceita apenas título e
texto. Ele não inclui anexos nem mantém rascunhos: fechá-lo descarta o que você
escreve. Para outros recursos, peça a um agente. Ele anexa arquivos e escreve a mensagem
para você.

O título deve indicar o assunto claramente. O texto deve incluir todo o contexto
necessário para a pessoa e o agente que recebem a mensagem.

Um envio bem-sucedido significa que a mensagem já está armazenada na outra instância. Isso não
significa que o usuário que a recebe já a tenha entregue a uma sessão.

### A caixa de entrada de instâncias parceiras

Abra a caixa de entrada em **Configurações → Instâncias parceiras** ou pelo botão de caixa de entrada ao lado de
**Configurações**. Esse botão aparece assim que o sistema de instâncias parceiras está configurado, mesmo
com a caixa vazia. Quando uma solicitação ou mensagem exige sua atenção, o TwiCC
também mostra um indicador na caixa de entrada de instâncias parceiras.

A caixa de entrada contém solicitações de pareamento, mensagens aguardando revisão e histórico
de mensagens. Você pode filtrar mensagens por instância parceira ou pelo título e texto completo.

Uma notificação de mensagem recebida pode abrir a caixa de entrada. Ela não pode entregar a
mensagem nem iniciar um agente.

O TwiCC também pode alertar você fora do aplicativo quando chega uma mensagem ou solicitação
de pareamento: um som, uma notificação do navegador ou uma notificação nos seus dispositivos. Ative
em **Configurações → Notificações → Uma instância parceira precisa de você**. Nenhum outro evento de instância parceira
envia alertas. Eles aguardam sua próxima visita.

### Revisar uma mensagem recebida

A revisão mostra remetente, título, mensagem, anexos e qualquer relação de
resposta. Ela também mostra quando o usuário da instância parceira escreve a mensagem em vez do
agente. Textos ou anexos grandes exigem uma ação adicional de carregamento antes de
o TwiCC renderizar ou baixar o conteúdo.

Depois, escolha uma ação na lista de ações da revisão:

- colocar a mensagem no campo de mensagem de uma sessão existente;
- criar um novo rascunho de sessão com a mensagem no campo de mensagem;
- marcar a mensagem como concluída quando você resolve o assunto ou não há nada
  a fazer. Nenhum agente a recebe;
- responder manualmente. Veja abaixo;
- recusar a mensagem.

Colocar uma mensagem no campo de mensagem marca a mensagem como entregue. Isso ainda não envia
o conteúdo nem inicia o agente. Ao entregar, você pode adicionar uma nota opcional
para seu agente. O TwiCC mantém essa nota separada da mensagem da instância parceira e
a identifica como texto seu.

Uma recusa não inclui texto. Para explicar, use **Responder manualmente** e
recuse na mesma etapa.

O remetente só recebe sua primeira decisão. Por isso, marcar como concluída e recusar
só estão disponíveis enquanto a mensagem está pendente. Uma mensagem resolvida ainda pode
ser entregue novamente pelo histórico.

### Respostas e sugestões de sessão

Uma mensagem de uma instância parceira pode responder a uma mensagem anterior. A caixa de entrada e a revisão mostram a
qual mensagem ela responde. A revisão permite abrir essa mensagem. Uma mensagem que
recebe respostas mostra quem responde: o usuário da instância parceira, o agente dele, você ou seu
agente.

Você pode responder por conta própria com **Responder manualmente** na lista de ações da
revisão. O TwiCC sugere o título da mensagem respondida com o prefixo `Re:`. Enquanto a mensagem respondida
ainda aguarda sua decisão, o formulário permite mantê-la aberta, marcá-la como concluída
ou recusá-la na mesma etapa. A escolha só se aplica após a resposta chegar
à instância parceira.

Quando possível, o TwiCC sugere a sessão local usada na mensagem anterior.
Isso é apenas uma sugestão. O usuário que recebe pode escolher outra sessão ou um
novo rascunho.

Toda resposta passa pela mesma revisão humana. Uma resposta nunca ignora a
aprovação do usuário que a recebe.

### Status da mensagem

As mensagens enviadas usam cinco estados:

- **pendente** — o usuário que recebe ainda não tomou uma decisão;
- **entregue** — o usuário colocou a mensagem no campo de mensagem de uma sessão. Algo
  pode chegar depois, pelo agente dele;
- **concluída** — o usuário resolveu a mensagem por conta própria. Nenhum agente a recebeu,
  e nenhum agente vai enviar mais nada. Se o usuário tinha algo a dizer, isso
  chegou como resposta;
- **recusada** — o usuário recusou a mensagem;
- **falhou** — o remetente não recebeu confirmação de aceitação da outra
  instância.

O status registra a primeira decisão do usuário que recebe. Ele pode alterá-la
depois. O remetente não é informado.

Um agente pode verificar o status atual de uma mensagem que enviou. Um status de falha pode
ser incerto: a outra instância pode ter armazenado a mensagem antes de a
conexão falhar.

### Nova entrega e histórico

Uma mensagem resolvida continua disponível no histórico da caixa de entrada. O usuário que a recebe
pode colocá-la no campo de mensagem novamente, qualquer que seja a decisão anterior. Isso ajuda
após escolher a sessão errada ou limpar um rascunho.

O TwiCC remove os dados dos anexos sete dias após a decisão mais recente. Uma nova
decisão reinicia esse prazo até os dados serem removidos. Uma mensagem pendente
mantém seus anexos. Texto, título, detalhes dos anexos e relação de resposta
sempre permanecem no histórico. Por isso, uma nova entrega posterior pode incluir
apenas texto.

### Revogação e reconexão

Cada lado controla sua própria conexão com a instância parceira. Qualquer usuário pode revogá-la a qualquer
momento. A revogação faz parar as mensagens e limpa as credenciais da conexão, mas
mantém o nome local da instância parceira e o histórico completo de mensagens.

A revogação é silenciosa. A outra instância descobre quando uma solicitação posterior
à instância parceira é rejeitada. Sua cópia da conexão passa a aparecer como **com falha**.

Uma instância parceira também pode ficar com falha quando você altera ou desativa seu endereço para instâncias parceiras.
Defina um endereço acessível e reconecte cada instância parceira afetada manualmente.

A reconexão usa um novo código de verificação e exige a aprovação do outro usuário,
como o pareamento original. Ela restaura a conexão existente em vez de
criar outra. Assim, o nome local e o histórico de mensagens continuam disponíveis.

A revisão humana é o principal limite de segurança. Ela não torna seguro um conteúdo não confiável.
Leia cada mensagem antes de colocá-la no campo de mensagem.
