Un **lien de partage** est une URL publique en lecture seule vers l'une de vos sessions ou l'un
de vos artefacts. Toute personne qui possède le lien peut l'ouvrir dans un navigateur — sans
compte TwiCC, sans connexion. Elle peut seulement **regarder** : un lien ne peut jamais envoyer
de message, exécuter un outil ni modifier quoi que ce soit chez vous.

## Ce que vous pouvez partager

- **Un artefact** — un fichier en favori dans l'onglet Artefacts d'une session, affiché seul
  (une page HTML, un graphique, un document…).
- **Une session** — la conversation, affichée en lecture seule. Les appels d'outils et les diffs
  s'affichent comme vous les voyez.

> **Une session est partagée telle quelle** — rien n'y est masqué ni nettoyé. Avant d'en
> partager une, assurez-vous qu'elle ne contient ni secret ni donnée privée, et ne donnez le
> lien qu'à des personnes de confiance. Un artefact est un fichier unique que vous avez
> choisi, il y a donc bien moins de choses à vérifier.

## Avant de pouvoir partager

Le partage nécessite un **hôte de partage dédié** — un nom d'hôte distinct de celui que vous
utilisez pour TwiCC, pointant vers la même instance. Définissez-le dans
**Paramètres → Partage**. Tant qu'il n'est pas configuré, les boutons de partage restent
désactivés.

Gardez à l'esprit que **chaque visite est servie par la machine qui exécute TwiCC** — votre
propre ordinateur, via votre tunnel. Il n'existe aucune copie hébergée. Évitez donc de
distribuer les liens à grande échelle : tout ce trafic arrive directement sur votre machine.

## Vos liens

Lorsque vous créez un lien, vous pouvez lui donner un **libellé**, le protéger par un **mot de
passe** et définir une **expiration**. Vous pouvez créer **plusieurs liens** vers la même
session ou le même artefact, chacun avec son propre mot de passe et sa propre expiration. Donnez
des liens différents à des personnes différentes et suivez séparément le journal des visites de
chacun. Un lien par personne est pratique : vous pouvez révoquer l'accès d'une personne sans
toucher aux autres.

Gérez-les depuis le bouton de partage (ou **Paramètres → Partage → Liens partagés**), où chaque
lien indique aussi combien de fois il a été ouvert :

- **Révoquer** désactive temporairement un lien — les visiteurs obtiennent une erreur 404 —
  (mais conserve son journal des visites), ce qui vous permet de le réactiver plus tard.
- **Supprimer** le retire définitivement, journal des visites compris.

## Agents et liens de partage

Par défaut, vous seul pouvez créer ou gérer des liens de partage — pas les agents. Deux
interrupteurs dans **Paramètres → Partage** changent cela, un par type (liens de session, liens
d'artefact). Les deux sont **désactivés** tant que vous ne les activez pas.

Activer **Partages de sessions** permet aux agents de créer des liens de session pour leur
propre session ou pour toute session de leur sous-arbre de sessions lancées. Activer **Partages
d'artefacts** permet aux agents de créer des liens d'artefact pour les favoris appartenant à
leur propre session ou à toute session de leur sous-arbre de création.

Pour le type activé, les agents peuvent aussi :

- **gérer** (mettre à jour, supprimer, republier) les liens créés par eux-mêmes ou par des
  agents de leur sous-arbre de création ;
- **révoquer** tout lien existant de ce type — y compris les liens que vous avez créés vous-même
  (dépublier est toujours considéré comme sans risque) ;
- **lire l'URL** de tous les liens existants de ce type, y compris les vôtres.

Les agents ne peuvent jamais effacer le mot de passe d'un lien, ni partager avec le mode
d'affichage `debug`. Leurs nouveaux liens de session sont des instantanés figés, sauf s'ils
demandent explicitement un lien en direct.

## Artefacts et accès réseau

Un artefact partagé s'exécute chez le visiteur **sans demande de consentement**. S'il effectue
des appels réseau, seuls les hôtes **que vous avez autorisés** sont accessibles. Tout autre
hôte est bloqué, et le visiteur voit une courte note « bloqué par le propriétaire ». Vous
autorisez ou refusez des hôtes depuis le **favori** de l'artefact. Les hôtes que les visiteurs
ont tenté de joindre y sont listés, pour que vous puissiez les approuver après coup.

Si vous modifiez les fichiers d'un artefact après l'avoir partagé, utilisez **Publier la mise
à jour** sur le lien pour actualiser ce que voient les visiteurs.
