O MCP externo permite que um agente fora do TwiCC trabalhe com sua instância. Por exemplo,
você pode conectar um cliente MCP para consultar projetos, criar sessões ou enviar
mensagens com imagens.

O TwiCC continua sendo um aplicativo para um único usuário. Você aprova cada conexão com sua
instância. Você não cria outra conta de usuário para o cliente que se conecta.

### MCP interno e externo

Os agentes que funcionam dentro do TwiCC já têm uma conexão MCP interna. Eles não
precisam desta configuração nem de aprovação OAuth. Desativar o MCP externo mantém o
acesso interno disponível.

Clientes externos se conectam por um endereço público dedicado. Eles usam OAuth
para obter acesso após sua aprovação.

### O que uma conexão pode fazer

Uma conexão aprovada tem acesso total às ferramentas MCP externas. Ela pode ler
e alterar seus dados do TwiCC, gerenciar projetos e sessões, enviar mensagens e
gerenciar compartilhamentos. Não há opções de permissão por conexão.

A aprovação vale para a conexão, não para cada chamada de ferramenta. O cliente pode agir sem
outra aprovação do TwiCC para cada mensagem ou sessão que cria.

Um cliente externo não é uma sessão do TwiCC. Ele não pode usar `whoami`, `self` ou
`parent` para identificar uma sessão que faz a chamada. Ele precisa fornecer IDs de sessão explícitos
quando a ferramenta exige uma sessão. Operações relativas à sessão exigem esse
contexto. Elas não podem deduzi-lo da conexão externa.

### Anexos

Um cliente externo anexa um arquivo da própria máquina com uma data URI em base64 no
campo `attach`: `data:<mime>;name=<percent-encoded file name>;base64,<data>`. `name=`
preserva o nome do arquivo. Os dados inline têm limite de 50 MiB por comando. Para um arquivo
maior, coloque-o em um serviço de armazenamento e informe sua URL no texto da mensagem.
Você também pode informar o caminho absoluto de um arquivo que já esteja no host do TwiCC.
Uma mensagem para uma instância parceira envia todos os arquivos inline. Nesse caso, apenas a URL funciona para arquivos maiores.

### Antes de conectar

Inicie o TwiCC com uma senha. O MCP externo não pode ser ativado sem ela.
Se você reinicia o TwiCC sem senha, ele desativa o MCP externo e preserva
as autorizações existentes. A URL dedicada continua salva. Após restaurar uma senha,
ative o MCP externo novamente. Os clientes podem retomar o acesso com as credenciais existentes,
sujeitas à expiração normal. Autorizações já revogadas continuam revogadas.

Para definir uma senha, execute:

```
<twicc> password set
```

Para `<twicc>`, use sua forma habitual de iniciar o TwiCC. Reinicie o TwiCC após definir
a senha.

Abra **Configurações → MCP externo**. Insira a **URL dedicada ao MCP** e selecione
**Aplicar**. Insira a origem HTTPS, como `https://mcp.example.com`, sem
`/mcp`. Depois, ative o **Acesso externo** ao MCP.

Use um hostname dedicado, diferente dos endereços externo, de compartilhamento e de instâncias parceiras.
Esse hostname atende apenas ao MCP e seus endpoints OAuth. Ele não
fornece a interface do TwiCC, arquivos nem outras rotas do aplicativo.

O endereço deve ser acessível pelo cliente. As chamadas MCP ocorrem entre máquinas.
Se seu túnel habitual exige login por email, SSO ou outra
verificação interativa, não o use para MCP. O cliente não consegue concluir essa verificação.

Crie um endereço de túnel separado, sem essa barreira, e aponte-o para seu servidor
TwiCC. Mantenha a proteção no endereço habitual do TwiCC. O TwiCC protege o
endereço MCP com OAuth e sua aprovação da conexão.

Veja [como acessar o TwiCC de qualquer lugar](help/external-url) para a configuração geral
do túnel e da senha.

### Conectar um cliente

Copie o **Endpoint MCP** de Configurações e insira-o na configuração MCP remota
do cliente. Esse endereço inclui `/mcp`, por exemplo:
`https://mcp.example.com/mcp`.

Use um cliente compatível com MCP remoto e OAuth. Os controles exatos de configuração
dependem do cliente. O TwiCC fornece as informações de autorização pelos
endpoints padrão de descoberta MCP.

Clientes desktop podem receber o callback do navegador em `http://127.0.0.1` ou
`http://[::1]`, com uma porta disponível diferente a cada conexão. O endereço,
caminho e query registrados ainda precisam corresponder. Essa exceção de porta não se aplica a
callbacks HTTPS remotos nem ao hostname `localhost`.

Inicie a conexão pelo cliente. Ele abre uma página de autorização com um
código de verificação. Mantenha essa página aberta até a conexão terminar.

O TwiCC mostra uma notificação persistente de solicitação de conexão nos dispositivos em que você fez login.
Selecione **Revisar** e insira o código do navegador que solicita a conexão.
O código vincula sua aprovação à solicitação que você iniciou.

Você pode aprovar em outro dispositivo. O navegador que iniciou a conexão
ainda conclui o fluxo OAuth. A notificação desaparece dos outros
dispositivos quando a solicitação é resolvida.

Revise os detalhes do cliente antes de selecionar **Autorizar**. Selecione **Recusar**
se você não iniciou a solicitação. As solicitações expiram após dez minutos. Inicie
novamente pelo cliente se a solicitação expirar.

### Nomes e cabeçalhos de mensagens

Você pode dar um nome opcional a cada conexão, como **ChatGPT** ou
**Assistente desktop**. Você pode alterá-lo depois nos detalhes da conexão.

A revisão preenche o campo de nome com o nome declarado pelo cliente.
Confirme, altere ou limpe o campo antes de autorizar. O TwiCC usa apenas o
nome que você aprova. Um cliente não pode definir seu próprio nome nas suas conversas.

Mensagens enviadas por essa conexão recebem um cabeçalho como **Mensagem via
ChatGPT**. Sem nome, o cabeçalho mostra **Mensagem via MCP externo**.
O cabeçalho identifica a conexão, não outra sessão do TwiCC.

O **ID do cliente** nos detalhes é um identificador técnico. Ele não é um token de
acesso nem o código de verificação. Você não precisa copiá-lo para aprovar uma solicitação.

### Gerenciar conexões

Abra **Configurações → MCP externo → Gerenciar conexões**. A lista mostra três seções
quando elas contêm entradas:

- **Solicitações pendentes** — solicitações que aguardam sua decisão;
- **Ativa** — conexões aprovadas, incluindo as que ainda concluem o OAuth;
- **Revogada** — conexões que não têm mais acesso.

Selecione **Revisar** para examinar uma solicitação ou **Detalhes** para examinar uma conexão
existente. Fechar os detalhes retorna à lista quando você os abre por ela.
Abrir uma solicitação pela notificação leva diretamente aos detalhes. Fechar
esse diálogo não abre a lista.

Vários clientes podem se conectar de forma independente. Revogar uma conexão não
revoga as outras.

### Revogar acesso

Selecione **Revogar** para impedir novas chamadas MCP de uma conexão. As
credenciais existentes deixam de conceder acesso. A revogação não desfaz o trabalho
já feito e não faz parar as sessões que o cliente iniciou anteriormente.

A conexão revogada continua na lista. Para conectar novamente, inicie uma nova
conexão pelo cliente e aprove a nova solicitação.

Alterar a URL dedicada ao MCP ou desativar o acesso externo revoga as conexões
existentes. Configure o novo endereço nos clientes e conecte novamente.

### Proteção contra abuso de OAuth

O TwiCC limita novas solicitações OAuth por cliente e por origem de rede. Um cliente
pode ter três solicitações pendentes. Uma origem pode ter dez. Uma origem pode manter
vinte clientes registrados que nunca receberam uma autorização.

Recusas repetidas de admissão ou tráfego de admissão acima do normal acionam uma
pausa de dez minutos para novos registros e solicitações de autorização. O proprietário
recebe uma notificação de **Suspeita de abuso de OAuth**. Configurações e o gerenciador
de conexões mostram o motivo, os contadores recentes e o tempo restante da pausa.

A pausa mantém disponíveis as conexões MCP existentes, renovações de tokens, revogação e
solicitações de autorização já em andamento. Ela termina automaticamente.
O alerta permanece até você dispensá-lo. Tráfego alto indica suspeita de abuso,
não prova de ataque. Vários clientes podem compartilhar uma origem de rede.

Selecione **Suspender todo o acesso externo** para parar todas as novas solicitações externas de MCP e OAuth,
sem revogar autorizações existentes. Solicitações já em execução
podem terminar. Para retomar, ative o acesso externo novamente em Configurações. Essa suspensão
manual persiste entre reinicializações.

Os contadores de tráfego, alertas e pausas automáticas são redefinidos quando o TwiCC reinicia.
As cotas de registro e de solicitações pendentes continuam armazenadas. A expiração normal de tokens e
solicitações continua válida durante toda a suspensão.

Os limites de taxa usam o endereço do cliente fornecido pelo servidor ASGI. Um proxy deve
informar o endereço real do cliente e ser confiável para o servidor. O próprio TwiCC nunca confia em
cabeçalhos arbitrários de encaminhamento. Endereços IPv6 no mesmo /64 compartilham uma
cota de origem. Apenas um hash da origem calculado com chave é armazenado, não o endereço IP.

### Se uma conexão não terminar

Verifique se o MCP externo está ativado e se o cliente usa a URL copiada,
incluindo `/mcp`. Verifique se o hostname dedicado chega a esta instância do TwiCC
e não mostra uma página de login do provedor do túnel.

Mantenha aberto o navegador que solicita a conexão durante a aprovação. Se a lista mostra
**conectando**, a conexão está aprovada, mas o cliente ainda precisa concluir o OAuth.
Se a solicitação expirar, inicie uma nova conexão pelo cliente.

### Comandos em lote

Use `batch_read` para reunir leituras independentes em uma chamada MCP. Por padrão, ele executa até quatro
comandos ao mesmo tempo. Defina `mode` como `sequential` para executá-los em ordem.
Use `batch` para comandos ordenados, incluindo alterações. Ele sempre executa em sequência.
As mesmas ferramentas estão disponíveis para agentes que funcionam dentro do TwiCC.

Consulte primeiro o schema de cada comando. Forneça seu nome MCP normal e seus argumentos:

```json
{
  "calls": [
    {"id": "recent", "name": "sessions", "arguments": {"limit": 5}},
    {"id": "projects", "name": "projects", "arguments": {}}
  ]
}
```

Cada chamada precisa de um `id` exclusivo, um `name` de comando e um objeto `arguments`.
O TwiCC valida o lote completo antes de iniciar qualquer comando. Uma chamada inválida
rejeita o lote sem executar os outros comandos. As verificações normais de permissão
continuam válidas para cada comando. Chamadas externas ainda exigem IDs de sessão explícitos.

Os resultados chegam juntos, na ordem de entrada. Cada registro inclui seu ID, status de execução
e a resposta normal do comando: `{exit_code, result, error}`.
Verifique o campo agregado `ok` e cada registro. Um lote concluído pode conter
comandos com falha ou ignorados. Um comando também pode funcionar, mas ter sua resposta grande
omitida (`response_omitted: true`). Nesse caso, o `ok` agregado é falso.

Por padrão, `batch` é interrompido após um erro de execução. Defina `on_error` como `continue`
para tentar os comandos restantes. O padrão de `batch_read` é `continue`.
A execução paralela exige `continue`. Use o modo sequencial para `stop`.
A omissão de saída não faz parar os comandos seguintes. Alterações concluídas continuam aplicadas:
não há rollback, nova tentativa automática nem transferência de resultados para argumentos posteriores.
Lotes aninhados não são compatíveis.

Um lote aceita até 20 comandos. As duas conexões MCP compartilham o limite de quatro
lotes ativos e oito comandos de lote ativos em toda a instância. Lotes excedentes
recebem `server_busy`. Ferramentas individuais ficam fora desse limite de admissão de lotes.
A solicitação completa tem limite de 72 MiB, incluindo anexos. Cada comando aceita no máximo 50 MiB de dados de anexos inline. Veja Anexos acima para arquivos maiores.
Respostas de comandos acima de 384 KiB são explicitamente omitidas. O resultado combinado da ferramenta,
incluindo texto e dados estruturados, fica abaixo de 16 MiB. Clientes podem ter limites menores.

Mantenha os lotes curtos e use chamadas separadas para esperas longas. Os tempos limite existentes
dos comandos continuam válidos. Não há tempo limite adicional para o lote. Esperas longas podem ocupar toda a
capacidade dos lotes, mesmo após o cliente cancelar a solicitação. Um cancelamento ou tempo limite não
prova que as gravações pararam. Examine os recursos afetados antes de repetir uma gravação com resultado
incerto. Os IDs de lote identificam chamadas para diagnóstico. Eles não impedem trabalho duplicado.

O TwiCC verifica a autorização externa antes de iniciar cada comando. Expiração,
revogação ou desativação do acesso externo faz parar os comandos restantes. Se o TwiCC
não consegue verificar a autorização, também faz parar os comandos. Comandos já iniciados podem
terminar, e seus resultados coletados são preservados.
