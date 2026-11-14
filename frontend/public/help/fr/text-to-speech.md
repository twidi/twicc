TwiCC peut **lire les messages du chat à voix haute**. Choisissez une configuration dans **Paramètres →
Synthèse vocale**, testez-la, confirmez que vous l'avez entendue, puis activez-la : un bouton
haut-parleur apparaît alors sur les messages.

Il existe deux types de configurations :

- **OpenRouter** : un modèle vocal d'[OpenRouter](help/openrouter), avec ses voix.
- **Script** : une commande shell de votre choix, qui transforme le texte en audio.

## Lire les nouveaux messages de la chronologie

Le **bouton haut-parleur à côté de Chronologie** lit à voix haute les nouveaux messages de l'assistant à mesure qu'ils arrivent. Le périmètre, la file d'attente, les résumés et ce qui se passe quand vous quittez la page ou l'application sont sur leur propre page : [Lecture de la chronologie](help/timeline-speech).

## Résumer un message ou une sélection

Utilisez **Résumer** sur un message éligible de l'assistant dans le chat ou dans une vue de message de la chronologie.
Le seuil compte le message logique complet, y compris ses blocs de code et
de texte. Il exclut les contenus d'artefact intégrés et le balisage de contrôle.
Le résumé vise environ un tiers de la source, avec un maximum de 140 mots.

Les résumés de message sont enregistrés. Le chat, les vues de la chronologie et l'écoute automatique partagent
le même résultat quand la source et la politique de modèle correspondent. La préparation automatique
enregistre aussi les résumés réussis. Un résultat issu d'une politique plus ancienne reste disponible.
La régénération ne remplace un résultat qu'en cas de succès.

Utilisez **Afficher le résumé** pour basculer entre le texte original et son résumé. La copie et **Lire
à voix haute** utilisent cette vue. La lecture du chat original garde sa portée d'origine par bloc.
La lecture de la chronologie originale utilise l'instantané assemblé de la chronologie. La lecture manuelle
interrompt la lecture automatique et fonctionne indépendamment de l'option de résumé
automatique. Les annonces habituelles de projet et de session n'identifient jamais un résumé.

Sélectionnez du texte et utilisez **Résumer** dans les actions de sélection pour obtenir un résumé
temporaire. Sa boîte de dialogue propose Copier, Lire à voix haute, Réessayer, Annuler et Fermer. Fermer
la boîte de dialogue libère sa génération et arrête la lecture correspondante. Les résumés
de sélection et le test des paramètres ne sont jamais enregistrés comme résumés de message.

Le texte du résumé apparaît au fur et à mesure du streaming. La lecture démarre à partir des parties de résumé
disponibles, sans second appel d'adaptation. Si la génération échoue après le début de la lecture,
celle-ci s'arrête avec une erreur. TwiCC ne relit pas l'original et n'enregistre pas
de résumé partiel.

### Modèle de résumé

Choisissez **Auto**, **Claude Haiku 5.5**, **GPT-6 Luna** ou **OpenRouter** dans
**Paramètres → Résumés**. Auto sélectionne un agent actif et activé, Claude en premier,
puis Codex. Un agent sélectionné mais indisponible cède la place à l'autre agent.

OpenRouter nécessite sa clé : voir [OpenRouter](help/openrouter). Sans clé, ou au-delà de **60 000 caractères
de source**, TwiCC sélectionne un agent activé. Il conserve la source complète.
Si OpenRouter échoue avant la validation de la lecture du résumé, un agent activé peut remplacer
son texte provisoire. Sans modèle disponible, le résumé affiche une erreur.
L'option automatique de la chronologie ne désactive pas les résumés manuels.

## Tester, confirmer, activer

**Tester** lit à voix haute le texte de test avec la configuration, puis demande **« L'avez-vous
bien entendu ? »**. Répondez **Oui** pour valider la configuration. Seule une configuration
validée peut être activée, et la configuration active est celle que les messages utilisent.

Modifier ce qui produit le son (le modèle, la voix, le format, la commande…)
retire la validation : testez de nouveau la configuration.

## Configurations OpenRouter

Elles nécessitent une clé OpenRouter. [OpenRouter](help/openrouter) explique comment la définir.

Les prix affichés sont les prix catalogue d'OpenRouter. Après un test, la configuration affiche
ce que la lecture du test a réellement coûté.

## Messages longs

Un long message est lu **en plusieurs parties**, l'une après l'autre, dans un seul audio. Chaque
partie est générée quand la lecture en a besoin, environ une minute à l'avance : le son
démarre rapidement, et un arrêt ne génère pas (ni ne fait payer) le reste. Les raccords
entre les parties peuvent être audibles.

## Lire une sélection

Sélectionnez du texte dans le chat ou dans la visionneuse de fichiers : le widget flottant affiche le
**bouton Lire** (l'icône haut-parleur), à côté des boutons Commenter et Copier. Il
n'apparaît pas dans le terminal, dans le panneau navigateur ni sur les cartes d'éléments.
Il n'apparaît que lorsque le service vocal est disponible, comme le bouton haut-parleur d'un
message.

- Le bouton lit le texte sélectionné avec la configuration active. Son titre est
  « Lire à voix haute ».
- Pendant la lecture, le même bouton devient une **icône Arrêt** (titre « Arrêter la lecture »),
  et il reste visible. Cliquez dessus pour annuler la requête ou arrêter le son.
- Le son continue quand le widget se ferme. Arrêtez-le depuis l'indicateur vocal
  du rail, comme pour un message.
- Un texte qui ne contient rien à lire à voix haute une fois sa mise en forme retirée est
  refusé, comme pour un message.

## Adapter le texte

Un petit modèle peut **réécrire un texte pour l'oreille** avant sa lecture, quand le
texte n'est pas simple : un tableau devient des phrases, une liste une énumération parlée, un
bloc de code une courte description ; les hachages, les longs chemins et la syntaxe Markdown
disparaissent. Le contenu et la langue restent les mêmes. Un **texte simple est lu
immédiatement**, sans ses marques Markdown et sans modèle : le son démarre
plus tôt, et la réécriture ne coûte rien. Un lien Markdown est lu par son texte.

Le test d'une configuration n'utilise jamais la lecture directe : il passe par le
modèle chaque fois que celui-ci peut s'exécuter, ce qui montre l'adaptation, et sa dernière
exécution de test affiche le texte adapté. Avec **Désactivé**, ou sans modèle disponible, le texte
est lu tel qu'il est écrit.

Par exemple, ces textes ne sont **pas simples** et passent par le modèle :

- les tableaux, le code et les textes proches du code (tout ce qui contient des symboles de code tels que `$`,
  `=`, `|` ou des accolades, les noms en `snake_case`, `#123`), et le HTML ;
- les adresses, les chemins, les hachages et les longs nombres ;
- les longues listes : plus de cinq éléments, des éléments longs ou des sous-listes ;
- une sélection avec un saut de ligne ou une tabulation dans le chat, et toute sélection dans la visionneuse
  de fichiers.

Quand les règles ne reconnaissent pas un texte avec certitude, le modèle décide.

Choisissez le modèle dans **Adapter le texte pour la voix**, en haut de **Paramètres →
Synthèse vocale** : **Claude Haiku 5.5**, **GPT-6 Luna**, **OpenRouter** ou
**Désactivé**, qui lit chaque texte tel qu'il est écrit. Sans choix, TwiCC prend
Claude, sinon Codex. Un modèle Claude ou Codex dont le fournisseur est désactivé est
remplacé par l'autre agent, et votre choix revient quand vous réactivez
son fournisseur.

Claude Haiku 5.5 et GPT-6 Luna utilisent votre forfait Claude ou Codex.
**OpenRouter** appelle un modèle rapide sur [OpenRouter](help/openrouter). Il nécessite
la clé OpenRouter, comme les configurations OpenRouter. Quand la clé est définie,
il adapte le texte même si aucun fournisseur ne tourne.
Sans la clé, la valeur par défaut le remplace, et votre choix revient une fois la clé
définie et TwiCC redémarré.

Si OpenRouter échoue avant le début de la lecture, le premier agent actif
(Claude, sinon Codex) adapte le texte. Si aucun modèle ne peut s'exécuter, si le texte est
trop long, ou si le modèle échoue avant le début de la lecture (pour OpenRouter : si
cet agent échoue aussi, ou si aucun fournisseur ne tourne), le texte est lu **tel qu'il
est écrit**. Si le modèle échoue plus tard, la lecture s'arrête avec une erreur.

La dernière exécution de test d'une configuration affiche le modèle qui a adapté le texte, ou chaque
modèle qui a échoué et pourquoi : une clé incorrecte ou un compte OpenRouter vide apparaît
là. Le coût d'un test est celui de la lecture seule : l'adaptation par OpenRouter
n'y est pas incluse.

## Configurations Script

TwiCC exécute la **Commande** dans votre shell, comme un shell de connexion non interactif
dans votre répertoire personnel, et écrit le texte sur son **entrée standard**. La
commande écrit l'audio sur sa **sortie standard** et se termine avec le statut 0.
Le texte n'est jamais placé dans la ligne de commande.

| Sortie | La commande écrit | Le son démarre |
|---|---|---|
| **Stream audio** | l'audio, pendant sa génération | dès ses premiers octets |
| **Audio complet** | l'audio, à la fin | à la fin de l'exécution |
| **Chemin du fichier** | un chemin absolu, vers un nouveau fichier à chaque exécution | à la fin de l'exécution |

| Format | Ce que la commande fournit |
|---|---|
| **MP3** | MP3 |
| **WAV** | WAV PCM 16 bits (un WAV écrit au fil de l'eau, avec des tailles inconnues, convient) |
| **PCM** | PCM brut 16 bits little-endian : indiquez sa fréquence d'échantillonnage et ses canaux |

**Nombre max. de caractères par exécution** : si le champ est vide, tout le texte va dans une seule exécution. Sinon,
le texte est découpé en parties de cette taille au plus, une exécution chacune. Avec **Audio
complet** ou **Chemin du fichier**, renseignez-le : le son démarre alors après la première partie,
et non après le texte entier.

**Supprimer le fichier après la lecture** (Chemin du fichier uniquement, activé par défaut) : TwiCC
supprime le fichier une fois qu'il l'a lu et que l'exécution a réussi. Il vérifie seulement
que le chemin est un fichier ordinaire, pas où il se trouve : assurez-vous que la commande
renvoie son propre nouveau fichier. Une exécution en échec conserve son fichier.

### En cas d'échec

- Une commande qui se termine avec un statut non nul affiche la **dernière ligne de sa
  sortie d'erreur**.
- Sinon, TwiCC indique ce qui ne va pas : une sortie qui n'est pas au format choisi,
  aucun audio, un chemin qui n'est pas un unique chemin absolu…
- **Rien d'autre ne doit s'afficher sur la sortie standard** : un message du profil de votre shell,
  ou une ligne de progression, corrompt l'audio.
- Ce que `~/.bashrc` ou `~/.zshrc` définit est généralement absent (alias, ajouts à
  `PATH`) : utilisez des chemins complets, ou un script qui définit ce dont il a besoin.

### Bon à savoir

- Un arrêt termine toute la commande, pipelines compris.
- Plusieurs lectures peuvent exécuter la commande en même temps (un test et un message,
  deux appareils).
- Un streaming audio qui n'envoie rien pendant une minute après son premier audio est
  arrêté. L'audio d'une lecture est limité à 512 Mo.
- TwiCC ne voit que la ligne de commande : après avoir modifié un script que la commande
  exécute, testez de nouveau la configuration (sinon un message peut lire un audio mis en cache
  pendant 10 minutes au plus).

### Exemples

Un son de test, qui ignore le texte (**Streaming de l'audio**, **WAV**) :

```
ffmpeg -nostdin -loglevel error -f lavfi -i sine=frequency=440:duration=3 -ac 1 -ar 22050 -f wav -
```

[eSpeak NG](https://github.com/espeak-ng/espeak-ng) (**Streaming de l'audio**,
**WAV**) :

```
espeak-ng --stdin --stdout -v en
```

La même chose, via un fichier (**Chemin du fichier**, **WAV**) :

```
f=$(mktemp) && espeak-ng --stdin -v en -w "$f" && echo "$f"
```

[Piper](https://github.com/rhasspy/piper) (**Streaming de l'audio**, **PCM**, la
fréquence d'échantillonnage de la voix, souvent 22050 Hz, 1 canal) :

```
piper --model ~/voices/en_US-lessac-medium.onnx --output-raw
```

## Limites connues

- Sur un iPhone ou un iPad (et dans Safari pour le MP3), l'audio est téléchargé
  en entier avant la lecture : le son démarre plus tard.
- Sur Android, une longue lecture en PCM (ou WAV) peut s'arrêter peu après le verrouillage
  de l'écran : le navigateur gèle la page.
