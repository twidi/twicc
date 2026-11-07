La messagerie de pairs relie deux instances TwiCC indépendantes. Elle sert surtout à deux
personnes qui travaillent ensemble depuis leurs propres machines. Vous pouvez aussi appairer deux de
vos propres instances, mais ce n'est pas le cas d'usage principal.

Un message de pair n'entre jamais de lui-même dans une session de l'autre instance. L'utilisateur
destinataire doit d'abord le lire. Il peut le refuser ou le placer dans la zone de saisie d'une session.

Même alors, TwiCC n'envoie pas le message à l'agent. L'utilisateur peut relire ou modifier la zone
de saisie, puis décider de l'envoyer.

### À quoi servent les pairs

Les pairs offrent aux agents un moyen contrôlé de se transmettre du travail entre personnes. Par exemple,
un agent peut envoyer :

- un résumé du travail terminé ;
- un contrat d'API ou une question technique ;
- une capture d'écran, un document ou un autre fichier utile ;
- une réponse à un message de pair précédent.

Vous pouvez aussi écrire un message vous-même, lorsque l'instruction vous appartient entièrement
et qu'un agent n'apporterait rien.

Le message doit être autonome. L'autre instance ne partage ni la mémoire ni la conversation
de la session émettrice.

### Ce que les pairs ne partagent pas

L'appairage ne donne à aucune des deux personnes l'accès à l'autre instance. Il ne partage ni les projets,
ni les sessions, ni les fichiers, ni les terminaux, ni les paramètres, ni le contrôle des agents.

Seuls le titre, le texte, les pièces jointes, l'heure et le lien de réponse du message passent
d'une instance à l'autre. Les détails locaux des sessions restent locaux.

### Avant l'appairage

Ouvrez **Paramètres → Pairs** pour définir votre nom et votre adresse. Votre nom identifie
votre instance dans les demandes d'appairage. Votre adresse doit être accessible depuis
l'autre machine TwiCC.

Laisser l'adresse vide désactive la messagerie de pairs. Utilisez HTTPS autant que possible.
Les appels entre pairs sont des appels de machine à machine. Si votre tunnel TwiCC habituel demande aux visiteurs
de se connecter par e-mail, par SSO ou par une autre vérification interactive, n'utilisez pas
cette adresse pour les pairs. L'autre instance TwiCC ne peut pas passer cette vérification.

Créez une seconde adresse de tunnel sans cette barrière, et utilisez-la comme adresse de pair.
Gardez le mot de passe propre à TwiCC activé.

Une adresse de pair différente de votre adresse externe ne sert que le trafic des pairs.
Elle ne sert ni page ni connexion en direct, donc vous n'avez pas besoin de filtre de chemin
sur le tunnel. Si vous saisissez votre adresse externe comme adresse de pair, cette adresse unique
sert l'application et le trafic des pairs. Toute autre adresse, y compris
`localhost`, ne sert jamais le trafic des pairs.

Un proxy ou un tunnel placé devant votre adresse de pair doit accepter des corps de requête
de 72 Mio. Un proxy dimensionné pour l'ancienne limite de 48 Mio refuse les messages volumineux.

N'utilisez pas l'adresse de partage dédiée pour les pairs. TwiCC limite ce nom d'hôte
aux routes de partage, et les Paramètres refusent une adresse de pair sur celui-ci. Consultez [accéder à
TwiCC de n'importe où](help/external-url) pour la configuration générale du tunnel et du mot de passe.

### Appairer deux instances

Les relations entre pairs suivent un flux de demande et d'approbation. Un utilisateur envoie une demande
d'appairage depuis **Gérer les pairs**. L'autre utilisateur reçoit la demande et voit un
code de vérification à six chiffres.

L'utilisateur destinataire communique ce code par un canal qu'il juge déjà fiable, par exemple
un appel ou une conversation privée. Le demandeur le saisit sur sa propre instance.
Cela confirme que la demande est bien parvenue à la bonne personne.

La vérification ne fait que débloquer l'approbation. L'utilisateur destinataire choisit toujours d'accepter
ou de refuser la relation. Chaque utilisateur choisit aussi le nom local qu'il donne au pair.

Seuls les utilisateurs gèrent les relations entre pairs. Les agents ne peuvent ni les créer, ni les approuver,
ni les refuser, ni les renommer, ni les révoquer, ni les reconnecter.

### Envoyer un message

Un agent peut lister les pairs actifs et envoyer un message titré à l'un d'eux. Un
message peut contenir du texte et des fichiers de tout type ; chaque fichier garde son nom. Tous les fichiers
d'un même message sont limités à 50 Mio au total ; pour un fichier plus gros, l'agent
le dépose sur un service de stockage de fichiers et indique son URL dans le texte du message.
Aucune confirmation supplémentaire côté émetteur n'est requise une fois que l'utilisateur
a approuvé la relation de pair.

Un pair qui utilise une ancienne version de TwiCC refuse un message contenant des fichiers : son utilisateur doit
la mettre à jour. Un message contenant seulement du texte lui parvient quand même.

Vous pouvez aussi écrire un message vous-même, depuis **Gérer les pairs**, depuis la boîte de réception,
ou sous forme de réponse pendant que vous examinez un message. Ce formulaire accepte uniquement un titre et un texte.
Il ne porte aucune pièce jointe et ne garde aucun brouillon : le fermer abandonne ce que vous avez écrit.
Pour tout le reste, demandez à un agent : il joint les fichiers et rédige le message pour vous.

Le titre doit énoncer clairement le sujet. Le texte doit inclure tout le contexte dont la personne
et l'agent destinataires ont besoin.

Un envoi réussi signifie que l'autre instance a stocké le message. Cela ne signifie pas
que l'utilisateur destinataire l'a remis à une session.

### La boîte de réception des pairs

Ouvrez la boîte de réception depuis **Paramètres → Pairs**, ou avec le bouton de boîte de réception à côté de
**Paramètres**. Ce bouton apparaît dès que le système de pairs est configuré, même avec une
boîte de réception vide. Lorsqu'une demande ou un message requiert votre attention, TwiCC
affiche aussi un badge sur la boîte de réception des pairs.

La boîte de réception contient les demandes d'appairage, les messages en attente d'examen et l'historique
des messages. Vous pouvez filtrer les messages par pair, ou par leur titre et leur texte intégral.

Une notification de message entrant peut ouvrir la boîte de réception. Elle ne peut ni remettre
le message ni démarrer un agent.

TwiCC peut aussi vous alerter en dehors de l'application lorsqu'un message ou une demande d'appairage
arrive : un son, une notification du navigateur ou une notification push sur vos appareils. Activez-la
dans **Paramètres → Notifications → Un pair a besoin de vous**. Aucun autre événement de pair
n'envoie d'alerte : ils attendent votre prochaine visite.

### Examiner un message entrant

L'examen affiche l'expéditeur, le titre, le message, les pièces jointes et tout lien de réponse.
Il indique aussi lorsque l'utilisateur du pair a écrit le message lui-même au lieu de son agent. Un texte
ou des pièces jointes volumineux nécessitent une action de chargement supplémentaire avant que
TwiCC ne les affiche ou ne les télécharge.

Vous choisissez ensuite une action dans la liste d'actions de l'examen :

- placer le message dans la zone de saisie d'une session existante ;
- créer une nouvelle session brouillon avec le message dans sa zone de saisie ;
- marquer le message comme traité, lorsque vous l'avez géré vous-même ou qu'il n'y a rien
  à faire : aucun agent ne le reçoit ;
- répondre manuellement (voir ci-dessous) ;
- refuser le message.

Placer un message dans une zone de saisie le marque comme remis. Cela n'envoie toujours pas
la zone de saisie et ne démarre pas l'agent. Lors de la remise, vous pouvez ajouter une note facultative
pour votre agent. TwiCC garde cette note séparée du message du pair et
l'identifie comme votre propre texte.

Un refus ne porte aucun mot. Pour en expliquer un, répondez avec **Répondre manuellement** et
refusez dans la même étape.

L'expéditeur n'apprend que votre première décision. Marquer comme traité et refuser ne sont donc
proposés que tant que le message est en attente. Un message résolu peut quand même être remis à nouveau,
depuis son entrée d'historique.

### Réponses et suggestions de session

Un message de pair peut répondre à un message précédent. La boîte de réception et l'examen indiquent à quel
message il répond, et l'examen peut ouvrir ce message. Un message qui a reçu des réponses montre
qui y a répondu : le pair, son agent, vous ou votre agent.

Vous pouvez répondre vous-même à un message, avec **Répondre manuellement** dans la liste d'actions
de l'examen. TwiCC propose le titre du message auquel vous répondez, précédé de `Re:`. Tant que le message
auquel vous répondez attend encore votre décision, le formulaire de réponse vous permet de le laisser ouvert,
de le marquer comme traité ou de le refuser dans la même étape. Ce choix ne s'applique qu'une fois la réponse
arrivée chez le pair.

Lorsque c'est possible, TwiCC suggère la session locale utilisée pour le message précédent.
Ce n'est qu'une suggestion. L'utilisateur destinataire peut choisir une autre session ou un
nouveau brouillon.

Chaque réponse passe par le même examen humain. Une réponse ne contourne jamais l'approbation
de l'utilisateur destinataire.

### État du message

Les messages envoyés ont cinq états :

- **en attente** : l'utilisateur destinataire n'a pas encore décidé ;
- **remis** : l'utilisateur a placé le message dans la zone de saisie d'une session. Une suite
  peut venir de son agent ;
- **traité** : l'utilisateur a géré le message lui-même. Aucun agent ne l'a reçu,
  et aucun agent n'y donnera suite. S'il avait quelque chose à vous dire, cela est arrivé
  sous forme de réponse ;
- **refusé** : l'utilisateur a décliné le message ;
- **échoué** : l'émetteur n'a pas reçu de confirmation d'acceptation de la part de l'autre
  instance.

L'état enregistre la première décision de l'utilisateur destinataire. Il peut la changer
plus tard ; l'émetteur n'en est pas informé.

Un agent peut vérifier l'état actuel d'un message qu'il a envoyé. Un état échoué peut être
incertain : l'autre instance a peut-être stocké le message avant la coupure de la
connexion.

### Nouvelle remise et historique

Un message résolu reste disponible dans l'historique de la boîte de réception. L'utilisateur destinataire
peut le placer à nouveau dans une zone de saisie, quelle que soit la décision précédente. Cela aide
après un mauvais choix de session ou l'effacement d'un brouillon.

TwiCC supprime le contenu des pièces jointes sept jours après la dernière décision. Une nouvelle
décision relance ce délai, tant que le contenu n'a pas été supprimé. Un message en attente
garde ses pièces jointes. Le texte, le titre, les détails des pièces jointes et le lien de réponse
restent toujours dans l'historique. Une remise ultérieure peut donc ne contenir que du texte.

### Révocation et reconnexion

Chaque côté contrôle sa propre relation de pair. Chaque utilisateur peut la révoquer à tout
moment. La révocation arrête la messagerie et efface les identifiants de la relation,
mais conserve le nom local du pair et l'historique complet des messages.

La révocation est silencieuse. L'autre instance l'apprend lorsqu'une requête ultérieure
du pair est rejetée. Sa copie de la relation apparaît alors comme **rompue**.

Un pair peut aussi devenir rompu lorsque vous modifiez ou désactivez votre propre adresse de pair.
Définissez une adresse accessible, puis reconnectez manuellement chaque pair concerné.

La reconnexion utilise un nouveau code de vérification et requiert l'approbation de l'autre utilisateur,
comme l'appairage initial. Elle restaure la relation existante au lieu d'en créer une autre, donc son nom local
et l'historique des messages restent disponibles.

L'examen humain est la principale barrière de sécurité. Il ne rend pas sûr un contenu non fiable.
Lisez chaque message avant de le placer dans une zone de saisie.
