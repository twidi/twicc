Um **link de compartilhamento** é uma URL pública, somente leitura, para uma das suas sessões ou
um dos seus artefatos. Qualquer pessoa com o link pode abri-lo em um navegador, sem uma conta
no TwiCC e sem fazer login. Ela só pode **visualizar**: um link nunca pode enviar uma
mensagem, executar uma ferramenta nem alterar algo no seu ambiente.

## O que você pode compartilhar

- **Um artefato** — um arquivo adicionado aos favoritos na aba Artefatos de uma sessão,
  renderizado separadamente (uma página HTML, um gráfico, um documento…).
- **Uma sessão** — a conversa, renderizada em modo somente leitura. As chamadas de ferramentas e
  os diffs aparecem como você os vê.

> **Uma sessão é compartilhada como está**. Nenhum conteúdo é ocultado ou removido.
> Antes de compartilhar uma sessão, verifique se ela não contém segredos nem dados privados.
> Entregue o link apenas a pessoas em quem você confia. Um artefato é um único arquivo
> escolhido por você, então há muito menos conteúdo para revisar.

## Antes de compartilhar

O compartilhamento exige um **host exclusivo de compartilhamento**: um hostname diferente daquele
que você usa para acessar o TwiCC, apontando para a mesma instância. Configure-o em
**Configurações → Compartilhamento**. Até você configurá-lo, os botões de compartilhamento ficam
desativados.

Lembre-se de que **a máquina que executa o TwiCC atende a cada visita**:
seu próprio computador, pelo seu túnel. Não há uma cópia hospedada. Evite
distribuir links em grande escala, pois todo o tráfego chega diretamente à sua máquina.

## Seus links

Ao criar um link, você pode dar a ele um **rótulo**, protegê-lo com uma
**senha** e definir uma **data de expiração**. Você pode criar **vários links** para a
mesma sessão ou artefato, cada um com sua senha e expiração. Entregue
links diferentes a pessoas diferentes e acompanhe os logs de visualização de cada link
separadamente. Um link por pessoa permite revogar o acesso de uma pessoa
sem alterar os demais.

Gerencie os links pelo botão de compartilhamento ou em **Configurações → Compartilhamento → Links
compartilhados**. Cada link também mostra quantas vezes foi aberto:

- **Revogar** desativa um link temporariamente. Os visitantes recebem um erro 404, mas os
  logs de visualização permanecem. Você pode reativá-lo depois.
- **Excluir** remove o link permanentemente, incluindo seus logs de visualização.

## Agentes e links de compartilhamento

Por padrão, apenas você pode criar ou gerenciar links de compartilhamento. Os agentes não podem.
Duas opções em **Configurações → Compartilhamento** alteram isso, uma para cada tipo: links de
sessões e de artefatos. Ambas ficam **desativadas** até você ativá-las.

Ativar **Compartilhamentos de sessões** permite que agentes criem links para sua própria
sessão ou qualquer sessão na árvore de agentes que eles iniciam. Ativar **Compartilhamentos de artefatos**
permite que agentes criem links para artefatos favoritos da sua própria sessão
ou de qualquer sessão na árvore de agentes que eles iniciam.

Para o tipo ativado, os agentes também podem:

- **gerenciar** (atualizar, excluir e republicar) links criados por eles ou por
  agentes na árvore de agentes que eles iniciam;
- **revogar** qualquer link existente desse tipo, incluindo links que você cria
  pessoalmente (retirar uma publicação é sempre considerado seguro);
- **ler a URL** de qualquer link existente desse tipo, incluindo os seus.

Os agentes nunca podem remover a senha de um link nem compartilhar com o modo de visualização `debug`.
Seus novos links de sessão são snapshots fixos, a menos que eles
peçam explicitamente um link ao vivo.

## Artefatos e acesso à rede

Um artefato compartilhado é executado para o visitante **sem solicitações de consentimento**. Se ele
faz chamadas de rede, apenas os hosts **permitidos por você** ficam acessíveis.
Qualquer outro host é bloqueado, e o visitante vê uma breve mensagem de "bloqueado pelo
proprietário". Você permite ou nega hosts no **favorito** do artefato.
Os hosts que os visitantes tentam acessar aparecem nesse local para você aprová-los
depois.

Se você alterar os arquivos de um artefato após compartilhá-lo, use **Enviar atualização** no
link para atualizar o conteúdo que os visitantes veem.
