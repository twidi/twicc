Use um **modelo** para informar ao TwiCC onde criar novas worktrees do Git.
Use placeholders que são resolvidos para cada projeto.
`{git_root}` indica sua raiz Git.
`{project_name}` indica seu nome ou o nome da pasta, se o projeto não tem nome.
`{project_basedir}` indica o nome da pasta.
Por exemplo, `{git_root}/.worktrees` mantém as worktrees dentro do repositório.
Já `/home/me/worktrees/{project_name}` reúne as worktrees de todos os projetos em um só lugar.
O diálogo de criação de worktree preenche o caminho a partir desse modelo. Você ainda pode alterá-lo a cada criação.

Defina o modelo em **Configurações → Geral → Modelo de diretório de worktrees**.
Para definir outro diretório em um projeto, informe um caminho absoluto no diálogo de edição desse projeto.

Saiba mais no diálogo de ajuda: [Trabalhar com worktrees](help/worktrees).
