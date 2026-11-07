Le MCP externe permet à un agent extérieur à TwiCC de travailler avec votre instance. Par exemple,
vous pouvez connecter un client MCP pour consulter des projets, créer des sessions ou envoyer
des messages avec des images.

TwiCC reste une application mono-utilisateur. Vous approuvez chaque connexion à votre
instance. Vous ne créez pas de compte utilisateur supplémentaire pour le client qui se connecte.

### MCP interne et externe

Les agents qui s'exécutent dans TwiCC disposent déjà d'une connexion MCP interne. Ils
n'ont pas besoin de cette configuration ni d'une approbation OAuth. Désactiver le MCP externe laisse leur
accès interne disponible.

Les clients externes se connectent par une adresse publique dédiée. Ils utilisent OAuth
pour obtenir l'accès après votre approbation.

### Ce qu'une connexion peut faire

Une connexion approuvée a un accès complet aux outils du MCP externe. Elle peut lire
et modifier vos données TwiCC, gérer les projets et les sessions, envoyer des messages et
gérer les partages. Il n'existe pas de choix d'autorisations par connexion.

L'approbation couvre la connexion, pas chaque appel d'outil. Le client peut agir sans
nouvelle approbation TwiCC pour chaque message ou session qu'il crée.

Un client externe n'est pas une session TwiCC. Il ne peut pas utiliser `whoami`, `self` ou
`parent` pour identifier une session appelante. Il doit fournir des identifiants de session explicites
là où l'outil attend une session. Les opérations relatives à une session exigent ce
contexte. Elles ne peuvent pas le déduire de la connexion externe.

### Pièces jointes

Un client externe joint un fichier de sa propre machine avec un data URI base64 dans
`attach` : `data:<mime>;name=<percent-encoded file name>;base64,<data>`. `name=`
conserve le nom du fichier. Les données en ligne sont limitées à 50 Mio par commande. Pour un fichier
plus volumineux, placez-le sur un service de stockage de fichiers et indiquez son URL dans le texte du message,
ou indiquez un chemin absolu vers un fichier déjà présent sur l'hôte TwiCC. Un message de pair
envoie chaque fichier en ligne, donc seule l'URL fonctionne dans ce cas.

### Avant de connecter un client

Démarrez TwiCC avec un mot de passe. Le MCP externe ne peut pas être activé sans mot de passe.
Si vous redémarrez TwiCC sans mot de passe, TwiCC désactive le MCP externe et conserve les
autorisations existantes. L'URL dédiée reste enregistrée. Après avoir rétabli un mot de passe,
activez de nouveau le MCP externe. Les clients peuvent reprendre avec leurs identifiants existants,
sous réserve de leur expiration normale. Les autorisations précédemment révoquées restent révoquées.

Pour définir un mot de passe, exécutez :

```
<twicc> password set
```

Pour `<twicc>`, utilisez votre manière habituelle de démarrer TwiCC. Redémarrez TwiCC après avoir défini
le mot de passe.

Ouvrez **Paramètres → MCP externe**. Saisissez votre **URL MCP dédiée**, puis sélectionnez
**Appliquer**. Saisissez l'origine HTTPS, par exemple `https://mcp.example.com`, sans
`/mcp`. Activez ensuite **Accepter les clients MCP externes**.

Utilisez un nom d'hôte dédié, différent de vos adresses Externe, Partage et Pairs. Ce nom d'hôte sert uniquement le MCP et ses points de terminaison OAuth. Il ne
sert pas votre interface TwiCC, vos fichiers ni les autres routes de l'application.

L'adresse doit être joignable depuis le client. Les appels MCP sont de machine à machine.
Si votre tunnel habituel demande aux visiteurs de se connecter par e-mail, SSO ou un autre
contrôle interactif, ne l'utilisez pas pour le MCP. Le client ne peut pas passer ce contrôle.

Créez une adresse de tunnel distincte, sans ce contrôle, et dirigez-la vers votre serveur
TwiCC. Gardez la protection sur votre adresse TwiCC habituelle. TwiCC protège l'adresse
MCP avec OAuth et l'approbation de votre connexion.

Consultez [accéder à TwiCC de n'importe où](help/external-url) pour la configuration générale du tunnel
et du mot de passe.

### Connecter un client

Copiez l'**URL MCP** depuis les paramètres et saisissez-la dans la configuration MCP distante
de votre client. Cette adresse inclut `/mcp`, par exemple
`https://mcp.example.com/mcp`.

Utilisez un client qui prend en charge le MCP distant avec OAuth. Les contrôles de configuration exacts
dépendent du client. TwiCC fournit les informations d'autorisation par les points de terminaison
de découverte MCP standard.

Les clients de bureau peuvent recevoir le rappel du navigateur sur `http://127.0.0.1` ou
`http://[::1]` avec un port disponible différent à chaque connexion. L'adresse enregistrée,
le chemin et la requête doivent toujours correspondre. Cette exception de port ne s'applique pas aux rappels HTTPS
distants ni au nom d'hôte `localhost`.

Démarrez la connexion depuis le client. Il ouvre une page d'autorisation avec un code
de vérification. Gardez cette page ouverte jusqu'à la fin de la connexion.

TwiCC affiche une notification persistante de demande de connexion sur vos appareils
connectés. Sélectionnez **Examiner**, puis saisissez le code affiché dans le navigateur qui se connecte.
Le code relie votre approbation à la demande que vous avez lancée.

Vous pouvez approuver depuis un autre appareil. Le navigateur qui a démarré la connexion
termine tout de même le flux OAuth. La notification disparaît de vos autres
appareils dès que la demande est traitée.

Examinez les détails du client avant de sélectionner **Autoriser**. Sélectionnez **Refuser**
si vous n'avez pas lancé la demande. Les demandes expirent après dix minutes. Si la demande expire,
recommencez depuis le client.

### Noms et en-têtes de message

Vous pouvez donner un nom facultatif à chaque connexion, par exemple **ChatGPT** ou
**Assistant de bureau**. Vous pouvez le modifier plus tard dans les détails de la connexion.

Lors de l'examen, le champ du nom reprend le nom que le client déclare pour lui-même.
Confirmez-le, modifiez-le ou effacez-le avant d'autoriser. TwiCC n'utilise que le nom
que vous avez approuvé. Un client ne peut pas se nommer lui-même dans vos conversations.

Les messages envoyés par cette connexion portent un en-tête tel que **Message via
ChatGPT**. Sans nom, l'en-tête indique **Message via MCP externe**.
L'en-tête identifie la connexion, pas une autre session TwiCC.

L'**ID du client** dans les détails est un identifiant technique. Ce n'est ni un jeton d'accès
ni le code de vérification. Vous n'avez pas besoin de le copier pour approuver une demande.

### Gérer les connexions

Ouvrez **Paramètres → MCP externe → Gérer les connexions**. La liste affiche trois sections
quand elles contiennent des entrées :

- **Demandes en attente** — demandes qui attendent votre décision ;
- **Active** — connexions approuvées, y compris celles qui terminent encore OAuth ;
- **Révoquée** — connexions qui n'ont plus d'accès.

Sélectionnez **Examiner** pour inspecter une demande, ou **Détails** pour inspecter une
connexion existante. Fermer les détails ramène à la liste si vous les avez ouverts depuis celle-ci.
Ouvrir une demande depuis sa notification mène directement à ses détails. Fermer
cette boîte de dialogue n'ouvre pas la liste.

Plusieurs clients peuvent se connecter indépendamment. Révoquer une connexion ne révoque pas
les autres.

### Révoquer l'accès

Sélectionnez **Révoquer** pour empêcher une connexion de faire d'autres appels MCP. Ses
identifiants existants n'accordent plus d'accès. La révocation n'annule pas le travail
déjà effectué et n'arrête pas les sessions que le client a précédemment démarrées.

La connexion révoquée reste dans la liste. Pour vous reconnecter, démarrez une nouvelle
connexion depuis le client et approuvez sa nouvelle demande.

Modifier l'URL MCP dédiée ou désactiver l'accès externe révoque les connexions
existantes. Configurez la nouvelle adresse dans vos clients et reconnectez-les.

### Protection contre les abus OAuth

TwiCC limite les nouvelles demandes OAuth par client et par source réseau. Un client
peut avoir trois demandes en attente, une source dix. Une source peut conserver
vingt clients enregistrés qui n'ont jamais reçu d'autorisation.

Des refus d'admission répétés ou un trafic d'admission inhabituellement élevé déclenchent
une pause de dix minutes sur les nouveaux enregistrements et les demandes d'autorisation. Le propriétaire
reçoit une notification **Abus OAuth suspecté**. Les paramètres et le gestionnaire de connexions
affichent la raison, les compteurs récents et le temps de pause restant.

La pause laisse disponibles les connexions MCP existantes, les renouvellements de jeton, la révocation
et les demandes d'autorisation déjà en cours. Elle expire automatiquement.
L'alerte reste affichée jusqu'à ce que vous l'ignoriez. Un trafic élevé est un soupçon d'abus,
pas une preuve d'attaque. Plusieurs clients peuvent partager une source réseau.

Sélectionnez **Suspendre tout accès externe** pour arrêter toutes les nouvelles requêtes MCP externes et demandes OAuth
sans révoquer les autorisations existantes. Les requêtes déjà en cours d'exécution
peuvent se terminer. Pour reprendre, activez de nouveau l'accès externe dans les paramètres. Cette suspension
manuelle persiste après un redémarrage.

Les compteurs de trafic, les alertes et les pauses automatiques sont réinitialisés au redémarrage de TwiCC.
Les quotas d'enregistrement et de demandes en attente restent enregistrés. L'expiration normale des jetons et
des demandes s'applique pendant toute la suspension.

Les limites de débit utilisent l'adresse client fournie par le serveur ASGI. Un proxy doit
transmettre la véritable adresse du client et être approuvé par le serveur. TwiCC ne fait jamais
confiance à des en-têtes de transfert arbitraires. Les adresses IPv6 d'un même /64 partagent un
quota de source. Seul un hachage à clé de la source est stocké, pas l'adresse IP.

### Si une connexion ne se termine pas

Vérifiez que le MCP externe est activé et que le client utilise l'URL copiée,
y compris `/mcp`. Vérifiez que le nom d'hôte dédié atteint cette instance TwiCC
et n'affiche pas une page de connexion du fournisseur de tunnel.

Gardez le navigateur qui se connecte ouvert pendant que vous approuvez. Si la liste indique
**connexion en cours**, l'approbation a réussi, mais le client n'a pas encore terminé OAuth.
Si la demande expire, démarrez une nouvelle connexion depuis le client.

### Commandes de lot

Utilisez `batch_read` pour regrouper des lectures indépendantes en un seul appel MCP. Il exécute jusqu'à quatre
commandes à la fois par défaut. Définissez `mode` sur `sequential` pour les exécuter dans l'ordre.
Utilisez `batch` pour des commandes ordonnées, y compris des modifications. Il s'exécute toujours séquentiellement.
Les mêmes outils sont disponibles pour les agents qui s'exécutent dans TwiCC.

Découvrez d'abord le schéma de chaque commande. Fournissez son nom MCP normal et ses arguments :

```json
{
  "calls": [
    {"id": "recent", "name": "sessions", "arguments": {"limit": 5}},
    {"id": "projects", "name": "projects", "arguments": {}}
  ]
}
```

Chaque appel nécessite un `id` unique, un `name` de commande et un objet `arguments`.
TwiCC valide le lot complet avant de démarrer une commande. Un seul appel invalide
rejette le lot sans exécuter ses autres commandes. Les contrôles d'autorisation normaux
s'appliquent toujours à chaque commande. Les appels externes exigent toujours des identifiants de session explicites.

Les résultats arrivent ensemble, dans l'ordre d'entrée. Chaque enregistrement inclut son identifiant, son état
d'exécution et la réponse normale `{exit_code, result, error}` de la commande.
Vérifiez le champ agrégé `ok` et chaque enregistrement. Un lot terminé peut contenir
des commandes en échec ou ignorées. Une commande peut aussi réussir alors que sa réponse volumineuse
est omise (`response_omitted: true`) ; dans ce cas, le champ agrégé `ok` vaut false.

`batch` s'arrête par défaut après une erreur d'exécution. Définissez `on_error` sur `continue`
pour tenter d'exécuter les commandes restantes. `batch_read` utilise `continue` par défaut.
L'exécution parallèle exige `continue` ; utilisez le mode séquentiel pour `stop`.
L'omission d'une sortie n'arrête pas les commandes suivantes. Les modifications terminées restent appliquées :
il n'y a ni annulation, ni nouvelle tentative automatique, ni transfert des résultats vers les arguments suivants.
Les lots imbriqués ne sont pas pris en charge.

Un lot accepte jusqu'à 20 commandes. Les deux connexions MCP partagent une limite de quatre
lots actifs et de huit commandes de lot actives pour toute l'instance. Les lots supplémentaires
reçoivent `server_busy`. Les outils individuels restent hors de cette limite d'admission des lots.
La requête complète est limitée à 72 Mio, pièces jointes comprises. Chaque commande accepte au plus 50 Mio de données jointes en ligne (voir Pièces jointes ci-dessus pour un fichier plus volumineux).
Les réponses de commande de plus de 384 Kio sont explicitement omises. Le résultat d'outil combiné,
texte et données structurées compris, reste inférieur à 16 Mio. Les clients peuvent avoir des limites plus basses.

Gardez les lots courts et utilisez des appels séparés pour les longues attentes. Les délais d'expiration
des commandes existantes s'appliquent toujours, sans délai supplémentaire pour le lot. De longues attentes peuvent occuper toute la capacité
des lots, même après l'annulation de la requête par le client. Une annulation ou un délai d'expiration ne
prouve pas que les écritures se sont arrêtées. Inspectez les ressources concernées avant de réessayer une
écriture incertaine. Les identifiants de lot servent à identifier les appels pour le diagnostic. Ils n'empêchent pas le travail en double.

TwiCC vérifie l'autorisation externe avant de démarrer chaque commande. L'expiration,
la révocation ou la désactivation de l'accès externe arrêtent les commandes restantes.
Si TwiCC ne peut pas vérifier l'autorisation, il les arrête aussi. Les commandes déjà démarrées peuvent
se terminer, et leurs résultats collectés sont conservés.
