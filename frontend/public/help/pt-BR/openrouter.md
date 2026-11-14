O **OpenRouter** é um serviço online. Uma conta e uma chave de API dão acesso a muitos modelos de IA. O TwiCC pode usá-lo como mecanismo opcional em poucos lugares. O restante do TwiCC nunca precisa dele.

## Onde o TwiCC o usa

| Onde | O que o OpenRouter faz |
|---|---|
| **Configurações → Leitura em voz alta** | Lê as mensagens em voz alta com uma **configuração do OpenRouter**. Também adapta o texto antes da leitura. |
| **Configurações → Resumos** | Escreve os resumos de mensagens e seleções. |
| **Configurações → Títulos** | Sugere o título de uma sessão. O diálogo de renomear também pode testá-lo para um único título. |

Para a adaptação, os resumos e os títulos, o TwiCC usa um modelo de texto rápido: hoje, o Qwen3.8 27B servido pela Cerebras.

Para a voz, você escolhe o modelo de voz e a voz em **Configurações → Leitura em voz alta**. O TwiCC carrega as listas de modelos e de vozes do OpenRouter quando você precisa delas. Veja [Leitura em voz alta](help/text-to-speech).

## Ativar

1. Crie uma conta em [openrouter.ai](https://openrouter.ai) e adicione créditos.
2. Crie uma chave de API na [página de chaves](https://openrouter.ai/settings/keys).
3. Adicione a linha `TWICC_OPENROUTER_API_KEY=your-key` ao arquivo `.env` do diretório de dados do TwiCC (`~/.twicc/.env` por padrão).

4. Reinicie o TwiCC. Reinicie-o de novo cada vez que mudar a chave.

A chave fica no servidor. O TwiCC nunca a envia ao navegador.

Sem a chave, as opções do OpenRouter ficam desativadas. O TwiCC guarda sua escolha e a aplica de novo quando a chave é definida.

## Privacidade e custo

- O TwiCC envia o texto ao OpenRouter: a mensagem para a voz e os resumos, as mensagens do usuário para os títulos.
- Você paga na sua conta do OpenRouter. Os preços mostrados nas configurações são os preços de tabela do OpenRouter. O custo real pode diferir.
- O OpenRouter tem seus próprios termos e sua própria política de dados. Leia-os antes de usá-lo.

## Quando o OpenRouter falha

Para a adaptação, os resumos e os títulos, um agente ativado assume: primeiro o Claude, depois o Codex. Se nenhum agente puder rodar, a função mostra um erro, ou o texto é lido como está.
Uma configuração de voz que usa o OpenRouter não tem alternativa. Teste-a em **Configurações → Leitura em voz alta** para ver o erro.
