Les artefacts sont des contenus qu'un agent crée pour vous, sur demande. Ils sont conservés en
dehors des fichiers du projet, qui reste donc intact.

L'agent construit chacun sous forme de fichier et l'enregistre dans le dossier d'artefacts de la
session — images, pages web (bacs à sable interactifs, mini-applications, etc.), PDF,
Markdown, diagrammes, audio, vidéo. Demandez d'emblée à l'agent d'en faire un artefact, et il y a
plus de chances que ce soit le cas. Être explicite l'aide aussi à comprendre ce que vous
attendez.

Vous les trouverez dans l'onglet **Artefacts** de cette session, où TwiCC les affiche pour vous.
Cet onglet n'apparaît que lorsque la session compte au moins un artefact.

À partir de là, vous pouvez mettre n'importe quel artefact en **favori**, en lui donnant un nom
et une portée : le projet auquel appartient la session, l'espace de travail de ce projet, ou
partout.

Les pages interactives peuvent aussi **enregistrer des données** : ce que vous faites dans la
page (choix, paramètres, notes, état d'un jeu…) est stocké dans un dossier `data/` à côté de
l'artefact, et survit à sa fermeture et à sa réouverture. L'agent peut relire ces fichiers, si
bien qu'une page peut servir de formulaire riche : l'agent vous remet un artefact, vous faites vos
choix et enregistrez, vous dites à l'agent que vous avez terminé — il les lit à cet endroit. Les
fichiers apparaissent dans l'arborescence des artefacts comme les autres, de sorte que vous
pouvez toujours inspecter ou supprimer ce qu'une page a stocké.

Pour les pages web affichées, le bouton rond d'**outils** dans le coin déploie des actions
supplémentaires : un **mode adaptatif** pour prévisualiser la page à une taille d'appareil
précise, et un mode de **sélection d'élément** pour cliquer sur n'importe quel élément de la page
et donner à l'agent une description précise de celui-ci — avec une note et une capture d'écran
facultatives — au lieu de le décrire avec des mots. Ils fonctionnent exactement comme dans
l'[onglet Navigateur](help/browser-tab).

La **vue Artefacts** (ouvrez-la avec le bouton **Artefacts** de la barre d'icônes) regroupe les
artefacts que vous avez mis en favori. Chaque artefact apparaît selon la portée que vous lui avez
choisie. Lorsque vous créez des favoris, donnez-leur des noms clairs et parlants pour les
retrouver facilement.
