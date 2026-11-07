O TwiCC dá nomes às suas sessões automaticamente. Para controlar o estilo, abra
**Configurações** e depois **Sugestão de título**. Escolha se cada sessão usa seu
provedor, o Claude Haiku ou o GPT-6 Luna.

Edite o prompt para pedir um idioma, limite de tamanho, prefixo ou outro estilo.

Use **{text}** no prompt como placeholder para o conteúdo da sessão.

A opção **Títulos automáticos** controla tanto o primeiro título quanto as atualizações posteriores.
A geração de títulos também precisa estar ativada. As sessões reais recebem títulos do
backend, mesmo quando nenhuma aba da sessão está aberta. As alterações aparecem discretamente na
barra lateral e no cabeçalho.

A primeira verificação exige uma mensagem relevante do usuário. As verificações posteriores exigem pelo menos
**6 novas mensagens e 15 minutos** desde a última tentativa. Uma parada manual ou o
arquivamento aciona uma verificação final com **3 novas mensagens** desde a última
verificação bem-sucedida, independentemente do tempo. Uma saída normal da CLI não aciona essa verificação.
Slash commands sem argumentos não contam; comandos com argumentos podem contar.

O TwiCC mantém o título atual, a menos que os assuntos principais da conversa precisem
ser representados melhor. Uma verificação que falha mantém as mensagens para uma tentativa posterior.
As verificações acompanham a atividade da sessão; apenas a passagem do tempo não aciona uma verificação.

Um terminal híbrido em execução pode receber seu primeiro título. As atualizações automáticas esperam
até o terminal parar ou a sessão continuar fora do terminal.
Uma sessão híbrida parada pode receber atualizações normalmente.

As sessões efêmeras mantêm as sugestões do frontend porque ainda não têm uma
sessão salva. Os títulos anteriores a esse recurso permanecem sem alterações.
Sessões ocultas, desatualizadas e de subagentes não recebem títulos automáticos.
Sessões elegíveis criadas por agentes podem recebê-los quando nenhum título é fornecido.
