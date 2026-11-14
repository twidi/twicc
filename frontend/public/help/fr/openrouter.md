**OpenRouter** est un service en ligne. Un compte et une clé d'API donnent accès à de nombreux modèles d'IA. TwiCC peut l'utiliser comme moteur optionnel à quelques endroits. Le reste de TwiCC n'en a jamais besoin.

## Où TwiCC l'utilise

| Où | Ce que fait OpenRouter |
|---|---|
| **Paramètres → Synthèse vocale** | Lit les messages à voix haute avec une **configuration OpenRouter**. Il adapte aussi le texte avant la lecture. |
| **Paramètres → Résumés** | Écrit les résumés des messages et des sélections. |
| **Paramètres → Titres** | Suggère le titre d'une session. Le dialogue de renommage peut aussi l'essayer pour un seul titre. |

Pour l'adaptation, les résumés et les titres, TwiCC utilise un modèle de texte rapide : aujourd'hui, Qwen3.8 27B servi par Cerebras.

Pour la voix, vous choisissez le modèle vocal et la voix dans **Paramètres → Synthèse vocale**. TwiCC charge les listes de modèles et de voix depuis OpenRouter quand vous en avez besoin. Voir [Synthèse vocale](help/text-to-speech).

## L'activer

1. Créez un compte sur [openrouter.ai](https://openrouter.ai) et ajoutez des crédits.
2. Créez une clé d'API dans la [page des clés](https://openrouter.ai/settings/keys).
3. Ajoutez la ligne `TWICC_OPENROUTER_API_KEY=your-key` au fichier `.env` du dossier de données de TwiCC (`~/.twicc/.env` par défaut).

4. Redémarrez TwiCC. Redémarrez-le à nouveau chaque fois que vous changez la clé.

La clé reste sur le serveur. TwiCC ne l'envoie jamais au navigateur.

Sans la clé, les choix OpenRouter sont désactivés. TwiCC garde votre choix et le réapplique quand la clé est définie.

## Confidentialité et coût

- TwiCC envoie le texte à OpenRouter : le message pour la voix et les résumés, les messages de l'utilisateur pour les titres.
- Vous payez sur votre compte OpenRouter. Les prix affichés dans les paramètres sont les prix catalogue d'OpenRouter. Le coût réel peut différer.
- OpenRouter a ses propres conditions et sa propre politique de données. Lisez-les avant de l'utiliser.

## Quand OpenRouter échoue

Pour l'adaptation, les résumés et les titres, un agent activé prend le relais : Claude d'abord, puis Codex. Si aucun agent ne peut tourner, la fonction affiche une erreur, ou le texte est lu tel quel.
Une configuration vocale qui utilise OpenRouter n'a pas de repli. Testez-la dans **Paramètres → Synthèse vocale** pour voir l'erreur.
