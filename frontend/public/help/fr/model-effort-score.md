Le sélecteur de modèle est une **matrice** : une ligne par modèle, une colonne par effort de
réflexion. Chaque cellule sélectionnable affiche un **score de 0 à 100** : dans quelle mesure
ce modèle, à cet effort, convient à la tâche que vous décrivez avec les commandes sous la
matrice. Plus il est élevé, mieux c'est. Un **?** discret signifie qu'aucune donnée
exploitable n'existe pour cette paire.

La matrice comporte quelques repères visuels :

- l'**arrière-plan** de la cellule se colore davantage quand le score monte ;
- une **coche** marque la cellule actuellement sélectionnée, un petit **point** celle par
  défaut ;
- un **anneau** met en évidence le meilleur score de chaque fournisseur — plein pour votre
  fournisseur par défaut, en pointillés pour les autres quand plusieurs fournisseurs sont
  affichés.

## D'où viennent les données

Les scores sont construits à partir des évaluations publiées par
**[Artificial Analysis](https://artificialanalysis.ai/)**, un site de benchmarks indépendant.
Il mesure chaque modèle, à chaque effort de réflexion, sur de nombreuses évaluations, avec le
**coût** et la **durée** de chaque tâche.

TwiCC fournit un instantané de ces résultats, pris le **10/11/2026**, sur la version 5.0.1 de
l'Intelligence Index. Il n'est pas actualisé automatiquement.

## Les commandes

- **Type de tâche** — le genre de travail que vous allez effectuer (voir plus bas).
  *Général* utilise l'Intelligence Index global d'Artificial Analysis.
- **Difficulté de la tâche** — le niveau de capacité requis du modèle. Montez le curseur pour
  une tâche difficile, descendez-le pour une tâche facile.
- **Privilégier** — **Coût** pour préférer les paires moins chères, **Vitesse** pour préférer
  les plus rapides.
- **Sélection auto du meilleur** — sélectionne pour vous la cellule au meilleur
  score à chaque changement d'une commande, exactement comme si vous aviez cliqué dessus.
  Quand plusieurs fournisseurs sont affichés, **Fournisseur par défaut uniquement** limite ce
  choix à votre fournisseur par défaut.
- **Sélection auto du meilleur** et **Afficher les modèles plus anciens** se
  désactivent chaque fois que le sélecteur, le panneau Paramètres ou un formulaire de
  préréglage s'ouvre ou se ferme.

## Ce que signifie le score

La difficulté fixe un **niveau cible** sur l'échelle de capacité du type de tâche. Une paire
**égale ou supérieure** à la cible fait le travail : parmi elles, la moins chère (ou la plus
rapide) l'emporte. Une paire légèrement **sous** la cible perd quelques points. Une paire très
en dessous en perd tant qu'un prix bas ne peut pas la sauver.

- **100** est la meilleure paire pour cette tâche.
- **50** correspond à « un doublement moins bon » : deux fois le coût (ou la durée) de la
  meilleure, ou un écart sous la cible qui pèse autant.
- Les paires plus éloignées gardent des scores **faibles, mais non nuls**. Plusieurs peuvent
  afficher le même nombre : l'**anneau** marque toujours la meilleure paire de chaque
  fournisseur, ce qui vous permet de choisir la meilleure d'un seul fournisseur.
- Les scores comparent **tous les fournisseurs activés** ensemble, pour que vous puissiez
  choisir entre Claude Code et Codex. Les fournisseurs désactivés, les modèles retirés ou
  désactivés et les efforts qu'un modèle ne prend pas en charge ne comptent jamais.
- Les modèles plus anciens ne comptent que lorsque **Afficher les modèles plus anciens** est
  activé. Sinon, les scores comparent les modèles les plus récents : le 100 est donc toujours
  sur une cellule visible. Un modèle plus ancien déjà sélectionné reste visible et reçoit un
  score face aux modèles les plus récents (100 au maximum), mais il n'a jamais l'anneau ni la
  **Sélection auto du meilleur**.
- La version 5.0.1 de l'Intelligence Index ne mesure que quelques modèles plus anciens, souvent
  à leur effort le plus élevé uniquement. Leurs autres cellules affichent **?**.

## Types de tâche

| Type de tâche | Basé sur |
|---|---|
| Général | Intelligence Index (score global d'Artificial Analysis) ; coût et durée issus de Terminal-Bench 4.0 |
| Code et terminal | Terminal-Bench 4.0 (code agentique dans un terminal), FrontierCode 2.0 (code) |
| Bureautique et travail intellectuel | AA-Briefcase (livrables de travail intellectuel), GDP.pdf (raisonnement sur documents) |
| Automatisation et outils | AutomationBench-AA (workflows SaaS avec outils) |
| Science et raisonnement | HLE Diamond (raisonnement difficile), CritPt v2 Hard (physique), Terminal-Bench-Science (travail scientifique dans un terminal) |
| Connaissances factuelles | AA-Omniscience (connaissances et hallucinations) |

Pour un type fondé sur plusieurs évaluations, la capacité est leur moyenne (chacune sur une
échelle de 0 à 100). Il en va de même pour le coût et la durée.

## Sous le capot (pour les curieux)

- Le curseur associe **0** à la paire la plus faible et **100** à la plus forte pour le type de
  tâche choisi, selon une courbe qui monte vite au début : le milieu du curseur exige déjà un
  modèle capable.
- **Aucune pénalité au-dessus de la cible** : une paire qui la dépasse ne paie que son coût
  (ou sa durée).
- Être **2 points d'Intelligence Index** sous la cible pèse autant que payer deux fois plus
  cher ; les autres types de tâche adaptent ce rapport à leur propre étendue. La pénalité
  croît avec la **puissance 4** de l'écart : 4 points sous la cible pèsent autant que payer
  2¹⁶ fois plus.
- Le score passe de 100 à 50 sur le premier doublement, puis diminue de plus en plus
  lentement, de sorte qu'il ne s'effondre jamais à 0.
- Le coût et la durée sont comparés sur une échelle **logarithmique** : ce qui compte est le
  rapport (de 1 $ à 2 $ compte comme de 10 $ à 20 $).
- La durée est celle d'Artificial Analysis par tâche : le temps passé à générer la réponse,
  sans les délais de démarrage.
