La fenêtre de quota de 5 heures d'un fournisseur s'ouvre à votre **première requête** et se réinitialise 5
heures plus tard : plus cette première requête arrive tôt, plus votre journée compte de fenêtres
complètes.

Supposons que vous travailliez de 9 h à 19 h. Si vous commencez à 9 h, vous obtenez **deux** fenêtres : 9 h–14 h et 14 h–19 h. Si la
première s'ouvre à **6 h**, les mêmes heures en contiennent **trois** :
6 h–11 h, 11 h–16 h, 16 h–21 h : une fenêtre de plus, pour le même travail.

Définissez une heure de **Réveil du quota** quotidienne par fournisseur dans **Paramètres → Utilisation**. TwiCC
envoie une minuscule requête jetable à cette heure pour ouvrir la fenêtre plus tôt, uniquement
lorsqu'il est en cours d'exécution, et sans rien faire si une fenêtre est déjà active.
