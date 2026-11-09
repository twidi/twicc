Utilisez le **bouton haut-parleur à côté de Chronologie** pour lire automatiquement les nouveaux
messages de l'assistant. Une configuration de synthèse vocale disponible est nécessaire. Le bouton
enfoncé signifie que cette chronologie écoute les nouveaux messages.

- L'écoute utilise la portée de la chronologie : tous les projets, un espace de travail, un projet
  ou un worktree. Les sessions en sourdine sont masquées et ignorées par défaut. Sélectionnez
  **Filtres → Afficher les sessions en sourdine** pour les afficher et les lire dans cette chronologie.
  Les autres filtres d'affichage, y compris la vue sélectionnée et le filtre de session, n'ont
  pas d'effet sur l'écoute.
- Désactiver **Afficher les sessions en sourdine** retire leurs messages en file d'attente. La lecture
  déjà sélectionnée peut se terminer. Le réactiver ne relit pas les messages
  ignorés pendant qu'il était désactivé. La lecture manuelle reste disponible dans les sessions en sourdine.
- L'écoute continue quand vous naviguez vers une autre page, basculez d'onglet de l'application
  ou ouvrez une session. Une seule chronologie écoute par onglet de navigateur.
- Le bouton de la chronologie dans le rail affiche un petit haut-parleur tant que l'écoute est
  activée. Son libellé identifie la chronologie qui écoute. Cliquez dessus pour ouvrir
  cette chronologie sans modifier l'écoute.
- Les messages doivent avoir une date de message native **strictement postérieure au clic
  d'activation**. L'historique existant n'est pas lu quand vous activez l'écoute.
- Les messages arrivés en retard restent éligibles jusqu'à **cinq secondes** derrière le dernier
  message dont la lecture démarre. Exactement cinq secondes reste éligible.
  Les messages acceptés restent en file d'attente jusqu'à leur tour.
- La lecture manuelle est prioritaire. La lecture automatique attend pendant qu'une requête
  ou une lecture manuelle est en cours. Le haut-parleur de la chronologie dans le rail reste visible pendant cette attente
  et entre les messages.
- Cliquez sur le haut-parleur enfoncé de la chronologie pour désactiver l'écoute et vider sa file d'attente.
  Cela arrête sa lecture automatique et laisse se poursuivre toute lecture manuelle sans rapport.
  Activer une autre chronologie vide la file d'attente de la précédente.
- Le bouton **Arrêter la lecture à voix haute** du rail et le bouton **Arrêter
  la lecture** du message arrêtent la lecture en cours. Pendant une lecture automatique, ils
  désactivent aussi l'écoute et vident sa file d'attente.
- L'écoute et sa file d'attente restent en mémoire. Un rechargement de la page les efface.

La lecture automatique utilise la configuration de synthèse vocale active. Dans **Paramètres →
Résumés**, **Lire les résumés dans la lecture de la chronologie** contrôle les résumés automatiques.
Cette option est activée par défaut. Les messages éligibles contiennent au moins **1 000 caractères
de contenu**. Les messages plus courts utilisent le paramètre d'adaptation du texte existant.
Les annonces de projet et de session gardent leur formulation habituelle.

TwiCC prépare jusqu'à deux résumés de texte en file d'attente pendant qu'une autre lecture est en cours.
Il ne génère l'audio que lorsqu'un message arrive à son tour. Une lecture sélectionnée
conserve ses paramètres jusqu'à sa fin. Désactiver l'option libère la préparation inutile
et fait lire les messages en attente dans leur version originale. Un résumé en échec
affiche une erreur et ignore ce message. L'écoute continue avec le message suivant.

## Où la lecture continue

Ce qui se passe quand vous quittez la page dépend de l'endroit où vous utilisez TwiCC.

- **Application Android** : l'écoute continue écran éteint et quand vous passez à une
  autre application. Une notification indique l'état et propose **Passer** et **Tout
  arrêter**. Ouvrez TwiCC avant d'activer l'écoute. À l'activation, Android peut
  vous demander d'autoriser les notifications, dont les boutons de la notification ont besoin,
  et afficher un conseil sur la batterie. L'utilisation illimitée de la batterie est facultative.
  Elle rend l'écoute en arrière-plan plus fiable. Modifiez les deux plus tard dans
  Paramètres Android → Applications → TwiCC.
- **Web sur téléphone ou tablette** : gardez TwiCC au premier plan, écran allumé.
  Le navigateur peut geler la page quand vous changez d'application ou verrouillez l'écran.
  L'audio s'arrête alors.
- **Web sur ordinateur** : gardez l'onglet ouvert. Il peut rester en arrière-plan,
  derrière d'autres onglets ou fenêtres. Le navigateur peut ralentir un onglet masqué,
  mais l'écoute continue normalement.

Pour configurer la voix, voir [Synthèse vocale](help/text-to-speech).
