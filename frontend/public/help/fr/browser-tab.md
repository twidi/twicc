L'onglet Navigateur intègre une page de votre choix, généralement le serveur de développement du projet sur lequel vous travaillez, juste à côté de la conversation. La page s'exécute exactement comme dans un onglet de navigateur normal : réseau direct, rechargement à chaud, service workers. TwiCC ne la passe pas par un proxy et ne l'isole pas dans une sandbox.

**Pour qu'un agent configure votre serveur de développement,** indiquez-lui cette page :
<https://github.com/twidi/twicc/blob/main/frontend/public/help/browser-tab.md>.
Elle contient tout le nécessaire : intégration en frame, cookies et script compagnon.
L'agent a aussi besoin de l'adresse à laquelle vous accédez à TwiCC. Les messages de configuration de l'onglet proposent **Ajouter les instructions au message** et **Copier les instructions pour un agent**, un prompt prêt à l'emploi avec cette page, cette adresse et le snippet du compagnon.

### Ouvrir une page

Saisissez n'importe quelle URL dans la barre d'adresse. Le bouton **Accueil** ouvre l'URL par défaut enregistrée. Utilisez le menu des **favoris** pour enregistrer l'URL actuelle comme URL par défaut du projet (ou de l'un de ses espaces de travail) : l'onglet s'ouvre alors directement dessus. L'URL où vous vous trouviez est aussi mémorisée par session.

### Limites de l'intégration

Certaines pages ne peuvent pas être intégrées :

- Les sites qui refusent d'être affichés dans une frame (la plupart des sites publics) affichent un avertissement. Utilisez plutôt **Ouvrir dans un nouvel onglet du navigateur**.
- Lorsque TwiCC lui-même est servi en `https`, les pages `http://` simples sont bloquées par le navigateur (contenu mixte).

Vos serveurs de développement locaux sont le cas idéal : c'est pour eux que l'onglet existe.

### Connexions et stockage

Le fait que la page intégrée conserve votre connexion dépend de la position de votre serveur de développement par rapport à TwiCC.

**Même hôte** : TwiCC et votre application sont tous deux sur `localhost`, quels que soient les ports. Rien à faire : la page partage exactement les cookies et le stockage qu'elle utilise dans un onglet normal, donc une connexion faite là-bas s'applique déjà. Les ports n'ont aucune importance ici : `localhost:5173` et `localhost:3000` comptent comme le même site.

**Domaines différents** : par exemple, TwiCC sur un tunnel et votre application sur un autre. Le navigateur isole la page intégrée. Vous démarrez donc déconnecté, et quelques paramètres **réservés au développement** sont nécessaires :

- **Autoriser l'intégration en frame** : de nombreux frameworks l'interdisent par défaut avec `X-Frame-Options` / `frame-ancestors` (Django, Rails…). Assouplissez cette règle en développement.
- **Cookies** : définissez votre cookie de session avec `SameSite=None; Secure`. `Secure` fonctionne en `https` et sur `http://localhost`, mais pas sur une adresse IP de réseau local en http simple.
- Ensuite, **connectez-vous une fois dans l'onglet**. La page intégrée dispose de sa propre zone privée de cookies et de stockage, conservée d'un rechargement à l'autre. `localStorage` et `IndexedDB` ne demandent aucune configuration : même isolation, ils fonctionnent simplement.

Gardez ces paramètres derrière un indicateur de développement : vous ne voulez ni `SameSite=None` ni l'intégration en frame ouverte en production.

### Le script compagnon

Pour TwiCC, une page intégrée est une boîte noire : les liens suivis à l'intérieur sont invisibles, donc Précédent / Suivant ne suivent que les URL saisies dans la barre d'outils. TwiCC fournit un **script compagnon** à ajouter à votre page de développement. Il supprime cette limite et débloque des fonctions plus riches :

- un vrai suivi de la navigation : Précédent / Suivant / Actualiser pilotent l'historique propre à la page, et la navigation interne (SPA) met à jour la barre d'adresse ;
- la **sélection d'élément** : au lieu de décrire « le troisième bouton de l'en-tête » en espérant que l'agent le trouve, activez le mode avec le bouton en forme de **curseur de souris** dans la barre d'outils, cliquez sur l'élément directement dans la page, puis appuyez sur le bouton **commentaire**. Un widget s'ouvre, où vous pouvez ajouter une note facultative (et inclure une capture d'écran de l'élément) avant de coller le tout dans la zone de saisie du chat. Vous obtenez une description précise de l'élément, adaptée au code (balise, identifiants, classes : des éléments que l'agent peut retrouver dans vos sources), accompagnée de votre commentaire. Idéal pour « agrandissez ceci » ou « ce libellé est incorrect » ;
- les **erreurs de la page** : les exceptions non interceptées et les appels à `console.error` sont collectés, comptés dans la barre d'outils, et à un clic de votre message.

Le snippet est une simple balise `<script>` qui pointe vers `/_twicc/browser-companion.js` sur votre hôte TwiCC :

```html
<script src="https://your-twicc-host/_twicc/browser-companion.js" defer></script>
```

Ajoutez-la dans l'en-tête de votre page `<html>`, **le plus tôt possible** : idéalement comme toute première balise du `<head>`, avant tout autre script. Plus il se charge tôt, plus tôt la navigation et les erreurs sont capturées (il utilise `defer`, donc il ne bloque jamais votre page). Il est invisible pour la page elle-même et ne fait rien lorsque la page s'exécute en dehors de TwiCC, vous pouvez donc le laisser sans risque dans votre modèle de développement.

Vous n'avez pas à saisir l'URL à la main : lorsque la page intégrée n'a pas de compagnon, un bandeau dans l'onglet (et le bouton en forme de **prise**) propose ce snippet exact, déjà rempli avec le bon hôte, prêt à copier. Si vous accédez à TwiCC depuis une autre machine, configurez l'[adresse externe](help/external-url) afin que le snippet copié pointe vers une adresse que votre navigateur peut atteindre.

Le bouton en forme de **prise** indique l'état du compagnon : couleur de la marque lorsqu'il est connecté, rouge lorsque le script est absent, atténué pendant la détection. Un clic dessus ouvre cette page d'aide.

### Mode adaptatif

Le bouton en forme de **téléphone** donne à la page un viewport aux dimensions exactes d'un appareil : choisissez un préréglage (ou saisissez une largeur × une hauteur), inversez les deux valeurs, ou faites glisser les poignées autour du cadre. La page se réorganise exactement comme dans une fenêtre de cette taille. Aucun compagnon n'est nécessaire.

### Tout le reste

- **Plein écran** agrandit l'onglet entier, barre d'outils comprise.
- **Ouvrir dans un nouvel onglet du navigateur** transmet l'URL actuelle à votre vrai navigateur.
- Les erreurs, la sélection et les commentaires arrivent toujours dans le message que vous rédigez : rien n'est envoyé à l'agent sans vous.
