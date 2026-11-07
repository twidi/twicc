O seletor de modelos é uma **matriz**: uma linha por modelo e uma coluna por
esforço de raciocínio. Cada célula selecionável mostra uma **pontuação de 0 a 100**.
Ela indica a adequação do modelo, nesse esforço, à tarefa descrita pelos
controles abaixo da matriz. Quanto maior, melhor. Um **?** discreto indica que não há
dados utilizáveis para essa combinação.

A matriz usa algumas indicações visuais:

- o **fundo** da célula ganha mais cor conforme a pontuação aumenta;
- uma **marca de seleção** indica a célula selecionada. Um pequeno **ponto** indica
  a célula padrão;
- um **anel** destaca a melhor pontuação de cada provedor. Ele é contínuo para seu
  provedor padrão e tracejado para os outros quando vários provedores são
  exibidos.

## De onde vêm os dados

As pontuações usam as avaliações publicadas pelo
**[Artificial Analysis](https://artificialanalysis.ai/)**, um site independente
de benchmarks. Ele mede cada modelo, em cada esforço de raciocínio, em
várias avaliações. Também mede o **custo** e o **tempo** de cada tarefa.

O TwiCC inclui uma cópia desses resultados obtida em **2026-10-29**, na
versão 5.0 do Intelligence Index. Ela não é atualizada automaticamente.

## Os controles

- **Tipo de tarefa** — o tipo de trabalho que você vai fazer. Veja abaixo.
  *Geral (Intelligence Index)* usa o Intelligence Index geral do Artificial Analysis.
- **Dificuldade da tarefa** — a capacidade necessária do modelo. Aumente para uma
  tarefa difícil e diminua para uma tarefa fácil.
- **Priorizar** — **Custo** favorece combinações mais baratas. **Velocidade** favorece combinações
  mais rápidas.
- **Selecionar o melhor automaticamente** — seleciona a célula com a maior pontuação sempre que um
  controle muda, como se você tivesse clicado nela. Quando vários provedores
  são exibidos, **Somente o provider padrão** limita a escolha ao seu provedor
  padrão.
- **Selecionar o melhor automaticamente** e **Mostrar modelos antigos** são desativados sempre que o
  seletor, o painel de Configurações ou um formulário de predefinição abre ou fecha.

## O que a pontuação significa

A dificuldade define um **nível desejado** na escala de capacidade do tipo de tarefa.
Uma combinação **no nível desejado ou acima** atende à tarefa. Entre essas combinações, vence a mais barata
ou a mais rápida. Uma combinação um pouco **abaixo** do nível desejado perde alguns
pontos. Uma combinação muito abaixo perde tantos pontos que um preço baixo não a compensa.

- **100** é a melhor combinação para esta tarefa.
- **50** representa uma desvantagem de um fator de dois: o dobro do custo ou tempo da melhor combinação,
  ou uma capacidade suficientemente abaixo do nível desejado para ter o mesmo peso.
- Combinações mais distantes mantêm pontuações **pequenas, mas maiores que zero**. Várias delas
  podem mostrar o mesmo número. O **anel** continua indicando a melhor
  combinação de cada provedor. Assim, você pode escolher a melhor combinação de um único provedor.
- As pontuações comparam **todos os provedores ativados** juntos. Assim, você pode escolher
  entre Claude Code e Codex. Provedores desativados, modelos retirados ou desativados
  e esforços não compatíveis com um modelo nunca entram no cálculo.
- A cópia dos dados cobre **apenas os modelos mais recentes**. A versão 5.0 do
  Intelligence Index não mede modelos antigos. Com **Mostrar modelos
  antigos** ativado, as células deles mostram **?**.

## Tipos de tarefa

| Tipo de tarefa | Base |
|---|---|
| Geral (Intelligence Index) | Intelligence Index (pontuação geral do Artificial Analysis); custo e tempo do Terminal-Bench 4.0 |
| Programação e terminal | Terminal-Bench 4.0 (programação por agentes em um terminal), FrontierCode 2.0 (programação) |
| Escritório e trabalho intelectual | AA-Briefcase (entregas de trabalho intelectual), GDP.pdf (raciocínio sobre documentos) |
| Automação e ferramentas | AutomationBench-AA (workflows SaaS com ferramentas) |
| Ciência e raciocínio | HLE Diamond (raciocínio difícil), CritPt v2 Hard (física), Terminal-Bench-Science (trabalho científico em um terminal) |
| Conhecimento factual | AA-Omniscience (conhecimento e alucinação) |

Para um tipo baseado em várias avaliações, a capacidade é a média delas,
cada uma na escala de 0–100. O custo e o tempo também usam a média.

## Detalhes internos, para quem tem curiosidade

- O controle deslizante associa **0** à combinação mais fraca e **100** à mais forte
  para o tipo de tarefa escolhido. A curva sobe rapidamente no início:
  o meio do controle já exige um modelo capaz.
- **Nenhuma penalidade acima do nível desejado**: uma combinação que o excede só recebe o peso de
  seu custo ou tempo.
- Ficar **2 pontos do Intelligence Index** abaixo do nível desejado pesa tanto quanto
  pagar o dobro. Outros tipos de tarefa ajustam esse peso à sua própria faixa.
  A penalidade cresce com a **4ª potência** da diferença. Ficar 4 pontos abaixo
  pesa tanto quanto pagar 2¹⁶ vezes mais.
- A pontuação cai de 100 para 50 na primeira duplicação. Depois, cai cada vez
  mais devagar, de modo que nunca chega a 0.
- O custo e o tempo são comparados em uma escala **logarítmica**. O que importa é
  a proporção: passar de $1 para $2 equivale a passar de $10 para $20.
- O tempo é o tempo por tarefa do Artificial Analysis: o tempo gasto para gerar
  a resposta, sem atrasos de inicialização.
