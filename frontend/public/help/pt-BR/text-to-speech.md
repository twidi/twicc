O TwiCC pode **ler as mensagens do chat em voz alta**. Escolha uma configuração em **Configurações →
Leitura em voz alta**, teste, confirme que você ouviu e ative-a. Um botão de alto-falante
aparece nas mensagens.

Há dois tipos de configuração:

- **OpenRouter**: um modelo de fala do OpenRouter, com suas vozes.
- **Script**: um comando shell seu que transforma o texto em áudio.

## Ler novas mensagens da linha do tempo

Use o **botão de alto-falante ao lado de Linha do tempo** para ler automaticamente novas mensagens
do assistente. É necessária uma configuração de leitura em voz alta disponível. O botão pressionado
indica que essa linha do tempo monitora novas mensagens.

- A escuta usa o escopo da linha do tempo: todos os projetos, um espaço de trabalho, um projeto
  ou um worktree. As sessões silenciadas ficam ocultas e são ignoradas por padrão. Selecione
  **Filtros → Mostrar sessões silenciadas** para mostrá-las e lê-las nessa linha do tempo.
  Outros filtros de visualização, incluindo a visualização e o filtro de sessões selecionados,
  não afetam a escuta.
- Desativar **Mostrar sessões silenciadas** remove as mensagens dessas sessões da fila.
  A leitura já selecionada pode terminar. Ativar a opção não reproduz mensagens
  ignoradas enquanto ela estava desativada. A leitura manual continua disponível nas sessões silenciadas.
- A escuta continua quando você navega para outra página, muda de aba do aplicativo
  ou abre uma sessão. Apenas uma linha do tempo monitora novas mensagens por aba do navegador.
- O botão da linha do tempo na barra de ícones mostra um pequeno alto-falante enquanto a escuta está
  ativada. Seu rótulo identifica a linha do tempo monitorada. Clique nele para abrir
  essa linha do tempo sem alterar a escuta.
- As mensagens precisam ter uma data nativa **estritamente posterior ao clique de
  ativação**. O histórico existente não é reproduzido quando você ativa a escuta.
- Mensagens que chegam com atraso continuam elegíveis até **cinco segundos** antes da última
  mensagem cuja leitura começa. Uma mensagem com atraso de exatamente cinco segundos ainda é elegível.
  As mensagens aceitas ficam na fila até chegar a vez delas.
- A leitura manual tem prioridade. A leitura automática espera enquanto uma solicitação
  ou reprodução manual está em andamento. O alto-falante da linha do tempo na barra de ícones permanece visível durante essa espera
  e entre as mensagens.
- Clique no alto-falante pressionado da linha do tempo para desativar a escuta e limpar sua fila.
  Isso faz parar a leitura automática e mantém outras leituras manuais em andamento.
  Ativar outra linha do tempo limpa a fila da linha do tempo anterior.
- O botão **Parar a leitura em voz alta** na barra de ícones e o botão **Parar a
  leitura** da mensagem param a leitura atual. Durante a leitura automática, eles
  também desativam a escuta e limpam sua fila.
- A escuta e sua fila ficam na memória. Recarregar a página remove ambas.

A leitura automática usa a configuração ativa de leitura em voz alta. Em **Configurações →
Resumos**, **Ler resumos em voz alta na linha do tempo** controla os resumos automáticos.
Essa opção fica ativada por padrão. Mensagens elegíveis contêm pelo menos **1.000 caracteres
de conteúdo**. Mensagens menores usam a configuração existente de adaptação de texto.
Os anúncios de projeto e sessão mantêm seu texto habitual.

O TwiCC prepara até dois resumos de texto da fila enquanto outra leitura é reproduzida.
Ele gera o áudio apenas quando chega a vez da mensagem. Uma leitura selecionada
mantém suas configurações até terminar. Desativar a opção libera a preparação
desnecessária e faz as mensagens pendentes usarem seus textos originais. Uma falha no resumo
mostra um erro e ignora essa mensagem. A escuta continua com a próxima mensagem.

## Resumir uma mensagem ou seleção

Use **Resumir mensagem** em uma mensagem elegível do assistente no chat ou na visualização de mensagens
da linha do tempo. O limite conta a mensagem lógica completa, incluindo seus blocos de código e
texto. Ele exclui o conteúdo de artefatos incorporados e as marcações de controle.
O resumo visa cerca de um terço do texto original, com um máximo de 140 palavras.

Os resumos de mensagens são salvos. O chat, as visualizações da linha do tempo e a escuta automática compartilham
o mesmo resultado quando o texto original e a política do modelo correspondem. A preparação automática
também salva os resumos gerados com sucesso. O resultado de uma política anterior permanece disponível.
Gerar novamente substitui um resultado apenas após a geração bem-sucedida.

Use **Mostrar resumo** para alternar entre o texto original e o resumo. Copiar e **Ler em
voz alta** usam essa visualização. A fala do texto original no chat mantém o escopo original dos blocos.
A fala do texto original na linha do tempo usa o snapshot montado da linha do tempo. A fala manual
interrompe a fala automática e funciona independentemente da opção de resumo automático.
Os anúncios habituais de projeto e sessão nunca identificam um resumo.

Selecione texto e use **Resumir seleção** nas ações de seleção para obter um resumo
temporário. Seu diálogo oferece Copiar, Ler em voz alta, Tentar novamente, Cancelar e Fechar. Fechar
o diálogo libera os recursos da geração e faz parar a leitura correspondente. Os resumos de seleção
e o teste de configurações nunca são salvos como resumos de mensagens.

O texto do resumo aparece conforme é gerado. A fala começa com as partes disponíveis do resumo,
sem uma segunda chamada de adaptação. Se a geração falha após o início da fala,
a reprodução é parada com um erro. O TwiCC não reproduz o texto original nem salva um
resumo parcial.

### Modelo de resumo

Escolha **Automático**, **Claude Haiku 5.5**, **GPT-6 Luna** ou **OpenRouter** em
**Configurações → Resumos**. Automático seleciona um agente ativado em execução: primeiro Claude,
depois Codex. Se o agente selecionado está indisponível, o outro agente o substitui.

O OpenRouter usa o modelo de texto e a chave do OpenRouter já configurados. Suas chamadas são cobradas
na sua conta do OpenRouter. Sem a chave, ou acima de **60.000 caracteres no texto
original**, o TwiCC seleciona um agente ativado. Ele mantém o texto original completo.
Se o OpenRouter falha antes de o texto do resumo ser enviado para gerar o áudio, um agente ativado pode substituir
seu texto provisório. Sem um modelo disponível, a geração de resumo mostra um erro.
A opção automática da linha do tempo não desativa os resumos manuais.

## Testar, confirmar e ativar

**Testar** lê o texto de teste em voz alta com a configuração e pergunta **"Você ouviu
corretamente?"**. Responda **Sim** para validar a configuração. Apenas uma configuração validada
pode ser ativada. As mensagens usam a configuração ativa.

Alterar o que produz o som (modelo, voz, formato, comando etc.)
remove a validação. Teste a configuração novamente.

## Configurações do OpenRouter

Elas precisam de uma chave do OpenRouter. Adicione `TWICC_OPENROUTER_API_KEY` ao arquivo `.env`
do diretório de dados do TwiCC e reinicie o TwiCC. A chave nunca sai
do servidor.

Os preços mostrados são os preços de tabela do OpenRouter. Após um teste, a configuração mostra
o custo real da fala do teste.

## Mensagens longas

Uma mensagem longa é lida **em partes**, uma após a outra, em um único áudio. Cada
parte é gerada quando a reprodução precisa dela, com cerca de um minuto de antecedência. O som
começa rapidamente, e parar a leitura evita gerar e pagar pelo restante. As transições
entre as partes podem ser audíveis.

## Ler uma seleção

Selecione texto no chat ou no visualizador de arquivos. O widget flutuante mostra o
**botão de leitura**, com o ícone de alto-falante, ao lado dos botões Comentar e Copiar. Ele
não aparece no terminal, no painel do navegador nem nos cartões de elementos.
Ele aparece apenas quando o serviço de fala está disponível, como o botão de alto-falante de
uma mensagem.

- O botão lê o texto selecionado com a configuração ativa. Seu título é
  "Ler em voz alta".
- Durante a leitura, o mesmo botão mostra um **ícone de parar**, com o título "Parar a leitura",
  e permanece visível. Clique nele para cancelar a solicitação ou parar o som.
- O som continua quando o widget fecha. Pare-o pelo indicador de fala
  da barra de ícones, como em uma mensagem.
- Um texto sem conteúdo para ler em voz alta após a remoção da formatação é
  recusado, como em uma mensagem.

## Adaptar o texto

Um modelo pequeno pode **reescrever um texto para facilitar a escuta** antes da leitura, quando o
texto não é simples. Uma tabela vira frases, uma lista vira uma enumeração falada e um
bloco de código vira uma descrição breve. Hashes, caminhos longos e a sintaxe Markdown são
removidos. O conteúdo e o idioma permanecem iguais. Um **texto simples é lido
imediatamente**, sem suas marcações Markdown e sem usar um modelo. O som começa
mais cedo, e não há cobrança pela reescrita. Um link Markdown é lido pelo seu
texto.

O teste de uma configuração nunca usa a leitura direta. Ele passa pelo
modelo sempre que o modelo pode ser executado, para mostrar a adaptação. A última
execução do teste mostra o texto adaptado. Com **Desativado**, ou sem modelo disponível, o texto
é lido como está escrito.

Por exemplo, estes textos **não são simples** e passam pelo modelo:

- tabelas, código e texto semelhante a código (qualquer texto com símbolos de código como `$`,
  `=`, `|` ou chaves, nomes `snake_case`, `#123`) e HTML;
- endereços, caminhos, hashes e números longos;
- listas longas: mais de cinco itens, itens longos ou sublistas;
- uma seleção com quebra de linha ou tabulação no chat e qualquer seleção no visualizador
  de arquivos.

Quando as regras não reconhecem um texto com certeza, o modelo decide.

Escolha o modelo em **Adaptar o texto para leitura em voz alta**, no topo de **Configurações →
Leitura em voz alta**: **Claude Haiku 5.5**, **GPT-6 Luna**, **OpenRouter** ou
**Desativado**, que lê todos os textos como estão escritos. Sem uma escolha, o TwiCC usa
Claude ou, na ausência dele, Codex. Um modelo Claude ou Codex cujo provedor está desativado é
substituído pelo outro agente. Sua escolha retorna quando você ativa o
provedor novamente.

Claude Haiku 5.5 e GPT-6 Luna usam sua assinatura Claude ou Codex.
**OpenRouter** chama um modelo rápido no OpenRouter: atualmente, Qwen3.8 27B servido pela
Cerebras. Ele precisa da chave do OpenRouter, como as configurações do OpenRouter. Ele envia
o texto ao OpenRouter, com cobrança na sua conta do OpenRouter. Com a
chave configurada, ele adapta o texto mesmo quando nenhum provedor está em execução.
Sem a chave, a opção padrão o substitui. Sua escolha retorna quando você
configura a chave e reinicia o TwiCC.

Se o OpenRouter falha antes do início da leitura, o primeiro agente em execução
(Claude ou, na ausência dele, Codex) adapta o texto. Se nenhum modelo pode ser executado, se o texto é
muito longo ou se o modelo falha antes do início da leitura (no caso do OpenRouter: se
esse agente também falha ou se nenhum provedor está em execução), o texto é lido **como
está escrito**. Se o modelo falha depois, a leitura é parada com um erro.

A última execução do teste de uma configuração mostra o modelo que adapta o texto ou cada
modelo que falha e o motivo. Uma chave incorreta ou uma conta OpenRouter sem créditos aparece
nesse local. O custo de um teste inclui apenas a fala. A adaptação pelo OpenRouter
não está incluída.

## Configurações de script

O TwiCC executa o **Comando** no seu shell, como um login shell não interativo no
seu diretório pessoal, e grava o texto na **entrada padrão**. O
comando grava o áudio na **saída padrão** e termina com status 0.
O texto nunca entra na linha de comando.

| Saída | O que o comando grava | Quando o som começa |
|---|---|---|
| **Streaming de áudio** | o áudio, enquanto é gerado | nos primeiros bytes |
| **Áudio completo** | o áudio, ao final | quando a execução termina |
| **Caminho do arquivo** | um caminho absoluto para um novo arquivo a cada execução | quando a execução termina |

| Formato | O que o comando fornece |
|---|---|
| **MP3** | MP3 |
| **WAV** | WAV PCM de 16 bits (um WAV gravado durante a geração, com tamanhos desconhecidos, funciona) |
| **PCM** | PCM bruto de 16 bits little-endian: informe a taxa de amostragem e os canais |

**Máximo de caracteres por execução**: se vazio, todo o texto vai para uma execução. Caso contrário,
o texto é dividido em partes com no máximo esse tamanho, uma execução por parte. Com **Áudio
completo** ou **Caminho do arquivo**, defina esse valor. Assim, o som começa após a primeira parte,
sem esperar o texto inteiro.

**Excluir o arquivo após a leitura** (apenas Caminho do arquivo, ativado por padrão): o TwiCC
exclui o arquivo após lê-lo, se a execução termina com sucesso. Ele verifica apenas
se o caminho é um arquivo regular, sem verificar sua localização. Verifique se o comando
retorna seu próprio arquivo novo. Uma execução com falha mantém seu arquivo.

### Em caso de falha

- Um comando que termina com status diferente de zero mostra a **última linha da sua
  saída de erro**.
- Nos demais casos, o TwiCC informa o problema: uma saída diferente do formato escolhido,
  ausência de áudio, um caminho que não é um único caminho absoluto etc.
- **Nada mais pode ser impresso na saída padrão**: uma mensagem do perfil do seu shell
  ou uma linha de progresso corrompe o áudio.
- O que `~/.bashrc` ou `~/.zshrc` define costuma estar ausente (aliases, adições ao `PATH`):
  use caminhos completos ou um script que configure o que precisa.

### Informações úteis

- Parar a leitura encerra o comando inteiro, incluindo pipelines.
- Várias leituras podem executar o comando ao mesmo tempo (um teste e uma mensagem,
  dois dispositivos).
- Um streaming de áudio que não envia nada por um minuto após o primeiro áudio é
  parado. O áudio de uma leitura tem um limite de 512 MB.
- O TwiCC vê apenas a linha de comando. Após editar um script que o comando
  executa, teste a configuração novamente. Caso contrário, uma mensagem pode reproduzir um áudio em cache
  por até 10 minutos.

### Exemplos

Um tom de teste que ignora o texto (**Streaming de áudio**, **WAV**):

```
ffmpeg -nostdin -loglevel error -f lavfi -i sine=frequency=440:duration=3 -ac 1 -ar 22050 -f wav -
```

[eSpeak NG](https://github.com/espeak-ng/espeak-ng) (**Streaming de áudio**,
**WAV**):

```
espeak-ng --stdin --stdout -v en
```

O mesmo, por meio de um arquivo (**Caminho do arquivo**, **WAV**):

```
f=$(mktemp) && espeak-ng --stdin -v en -w "$f" && echo "$f"
```

[Piper](https://github.com/rhasspy/piper) (**Streaming de áudio**, **PCM**, a
taxa de amostragem da voz, geralmente 22050 Hz, 1 canal):

```
piper --model ~/voices/en_US-lessac-medium.onnx --output-raw
```

## Limitações conhecidas

- No iPhone ou iPad (e no Safari para MP3), o áudio é baixado
  por inteiro antes de ser reproduzido. O som começa mais tarde.
- No Android, uma leitura longa em PCM ou WAV pode parar logo após o bloqueio
  da tela. O navegador congela a página.
