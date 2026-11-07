TwiCC nomme vos sessions automatiquement. Pour contrôler le style, ouvrez
**Paramètres**, puis **Suggestion de titre**. Choisissez si chaque session utilise son
fournisseur, Claude Haiku ou GPT-6 Luna.

Modifiez le prompt pour demander une langue, une limite de longueur, un préfixe ou un autre style.

Utilisez **{text}** dans le prompt comme espace réservé pour le contenu de la session.

**Titres automatiques** contrôle à la fois le premier titre et les mises à jour ultérieures.
La génération de titres doit aussi être activée. Les sessions réelles reçoivent leurs titres du
backend, même quand aucun onglet de session n'est ouvert. Les changements apparaissent discrètement dans la
barre latérale et dans l'en-tête.

La première vérification nécessite un message utilisateur pertinent. Les vérifications suivantes nécessitent au moins
**6 nouveaux messages et 15 minutes** depuis la dernière tentative. Un arrêt manuel ou un
archivage déclenche une vérification de clôture avec **3 nouveaux messages** depuis la dernière
vérification réussie, quel que soit le temps écoulé. Une sortie normale de la CLI ne la déclenche pas.
Les slash commands seules ne comptent pas, mais celles qui ont des arguments peuvent compter.

TwiCC conserve le titre actuel, sauf si les sujets principaux de la conversation
doivent être mieux couverts. Une vérification en échec conserve les messages pour une tentative ultérieure.
Les vérifications suivent l'activité de la session. Le temps écoulé seul ne déclenche pas de vérification.

Un terminal hybride actif peut recevoir son premier titre. Les mises à jour automatiques attendent
que le terminal s'arrête ou que la session continue en dehors du terminal.
Une session hybride arrêtée peut recevoir des mises à jour normalement.

Les sessions éphémères conservent les suggestions du frontend, car elles n'ont pas encore de
session enregistrée. Les titres antérieurs à cette fonctionnalité restent figés.
Les sessions masquées, obsolètes et de sous-agents ne reçoivent pas de titres automatiques.
Les sessions éligibles créées par des agents peuvent en recevoir quand aucun titre n'a été fourni.
