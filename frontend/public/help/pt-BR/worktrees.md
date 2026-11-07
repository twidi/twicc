Um worktree é uma segunda cópia de trabalho do mesmo repositório, em um
**branch diferente** e em sua **própria pasta**, que compartilha o histórico do repositório. Ele
permite que você ou um agente trabalhe em um branch de funcionalidade sem alterar seu
checkout principal.

No TwiCC, um worktree aparece como um [projeto](help/projects) **próprio**,
vinculado automaticamente ao repositório principal. Suas sessões, seu custo e sua atividade **são reunidos na visualização do
projeto principal**, para você ver tudo junto.

### Onde os worktrees são criados

A configuração de **diretório de worktree** define onde o TwiCC cria novos worktrees
e preenche o caminho quando você cria um. A configuração global é um **modelo**
com placeholders resolvidos para cada projeto: `{git_root}` (sua raiz git),
`{project_name}` (seu nome, ou o nome da pasta se não tiver nome) e `{project_basedir}`
(o nome da pasta). Assim, `{git_root}/.worktrees` mantém os worktrees dentro de cada repositório,
enquanto `/home/me/worktrees/{project_name}` reúne todos no mesmo local. Cada
projeto pode substituir essa configuração com seu próprio caminho absoluto. Defina a configuração global em
Configurações e a de um projeto na caixa de diálogo de edição dele.

### Criar um worktree

Você cria um worktree ao iniciar uma nova sessão nele. No menu **Nova sessão**,
cada linha de projeto tem um botão de worktree à direita, com um branch e um
**+**. Clique nele para abrir a caixa de diálogo de worktree. O botão só aparece para
projetos que são repositórios git.

Na caixa de diálogo:

- **Novo worktree** — o TwiCC cria o worktree e seu branch no
  local escolhido.
- **Worktree existente** — adote um worktree que já existe no disco, mas que o TwiCC ainda
  não conhece, na aba "Worktree existente".

Em ambos os casos, você pode iniciar imediatamente um rascunho de sessão no novo worktree.

### Limpar

Um worktree continua no seletor de projetos e nos menus Nova sessão até
você **arquivá-lo**, na caixa de diálogo de edição ou no menu **⋮** do seletor de projetos
no topo da barra lateral. Arquivar oculta o worktree sem excluir
nada. Isso apenas o oculta no TwiCC. Encerrar o worktree de fato,
com o merge do branch e a remoção no disco, é uma tarefa sua ou do seu agente.
O TwiCC não faz isso.
