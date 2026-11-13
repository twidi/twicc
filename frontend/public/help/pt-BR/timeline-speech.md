Use o **botão de alto-falante ao lado de Linha do tempo** para ler automaticamente novas mensagens
do assistente. É necessária uma configuração de leitura em voz alta disponível. O botão pressionado
indica que essa linha do tempo monitora novas mensagens.

**Ler apenas mensagens de fim de turno** fica ativado por padrão. O botão aparece antes do botão de ajuda
quando a leitura em voz alta está disponível e essa linha do tempo monitora mensagens, inclusive durante uma pausa. A preferência permanece
ao recarregar e ativar a escuta novamente. TwiCC lê a mensagem final exata após um fim de turno oficial.
Mensagens de progresso visíveis podem permanecer em silêncio. O fim do turno precisa ser estritamente
posterior ao início da escuta. Desative a opção para ler também mensagens intermediárias.
A leitura manual continua disponível nos dois modos.

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
- Com a opção desativada, as mensagens precisam ter uma data nativa **estritamente posterior ao clique de
  ativação**. O histórico existente não é reproduzido quando você ativa a escuta.
- Com a opção desativada, mensagens que chegam com atraso continuam elegíveis até **cinco segundos** antes da última
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

## Onde a leitura continua

O que acontece quando você sai da página depende de onde você usa o TwiCC.

- **App Android**: a escuta continua com a tela apagada e quando você troca para outro
  app. Uma notificação mostra o estado e oferece **Pular** e **Parar tudo**. Abra o TwiCC
  antes de ativar a escuta. Ao ativá-la, o Android pode pedir que você permita notificações,
  que os botões da notificação exigem, e mostrar um aviso sobre a bateria. O uso irrestrito
  da bateria é opcional. Ele torna a escuta em segundo plano mais confiável. Altere ambos
  depois em Configurações do Android → Apps → TwiCC.
- **Web em celular ou tablet**: mantenha o TwiCC em primeiro plano, com a tela ligada. O
  navegador pode congelar a página quando você troca de app ou bloqueia a tela. O áudio para
  então.
- **Web em computador**: mantenha a aba aberta. Ela pode ficar em segundo plano, atrás de
  outras abas ou janelas. O navegador pode deixar uma aba oculta mais lenta, mas a escuta
  normalmente continua.

Para configurar a voz, veja [Leitura em voz alta](help/text-to-speech).
