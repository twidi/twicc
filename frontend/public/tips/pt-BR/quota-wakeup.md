A janela de cota de 5 horas de um provedor começa na sua **primeira solicitação** e reinicia 5
horas depois. Quanto mais cedo essa primeira solicitação ocorre, mais janelas completas
cabem no seu dia.

Suponha que você trabalhe das 9h às 19h. Se começar às 9h, terá **duas** janelas: 9h–14h e 14h–19h.
Se a primeira janela começar às **6h**, o mesmo período de trabalho passa a abranger **três**:
6h–11h, 11h–16h, 16h–21h — uma janela extra para o mesmo trabalho.

Defina um horário diário de **Início antecipado da cota** para cada provedor em **Configurações → Uso**. O TwiCC
envia uma pequena solicitação descartável nesse horário para iniciar a janela mais cedo.
Isso só ocorre enquanto o TwiCC está em execução. A solicitação não é enviada se uma janela já estiver ativa.
