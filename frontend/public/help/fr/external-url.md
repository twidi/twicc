L'**adresse externe** est celle qui permet d'accéder à *cette* instance de TwiCC depuis vos
appareils. TwiCC l'utilise aussi pour construire des liens vers vos sessions. Une
notification externe peut alors ouvrir la bonne conversation, au lieu d'une adresse
`localhost` inutilisable.

### À quoi elle sert

- Ouvrir une session depuis votre **téléphone ou un autre ordinateur**, pendant que TwiCC
  continue de tourner sur votre machine personnelle.
- Elle est aussi intégrée aux **notifications externes** (Apprise) : une alerte envoyée
  peut alors renvoyer directement vers la session concernée.

Si vous la laissez vide, TwiCC omet simplement ces liens. Rien d'autre ne se casse.

### Pourquoi un tunnel est nécessaire

TwiCC s'exécute comme un processus local. L'adresse `http://localhost:…` n'a donc de sens que
sur la machine où il tourne. Pour y accéder depuis ailleurs, vous l'exposez via un
**tunnel** : un service qui donne à votre instance locale une adresse publique stable et
qui lui renvoie le trafic. Cette adresse publique est exactement ce que vous saisissez dans
l'adresse externe. Voici quelques options courantes :

- **Cloudflare Tunnel** (`cloudflared`) — un tunnel nommé avec un nom d'hôte stable.
- **Tailscale** — un VPN maillé privé entre vos propres appareils, avec une exposition
  publique facultative (Funnel).
- **ngrok** — des URL publiques rapides pour un port local.

### Protégez votre tunnel

Un tunnel peut rendre votre instance accessible depuis l'internet ouvert. Prenez-le au
sérieux. Chaque fournisseur propose des moyens de le verrouiller. Utilisez-les :

- **Cloudflare Tunnel** → placez **Cloudflare Access** devant (code à usage unique par
  e-mail, SSO Google/GitHub), ajoutez des règles IP ou un WAF.
- **Tailscale** → gardez-le sur votre **tailnet privé**, pour que seuls vos propres appareils
  y accèdent (évitez Funnel public sauf besoin réel) ; renforcez avec des ACL d'appareils.
- **ngrok** → activez son **Basic Auth** intégré, **OAuth** (Google, GitHub…) ou des listes
  d'adresses IP autorisées.

### Le mot de passe de TwiCC passe en premier

Point le plus important : **TwiCC refuse toute connexion non locale tant que vous n'avez pas
défini de mot de passe.** Une adresse externe ne fonctionne donc réellement qu'après avoir
exécuté `twicc password set`. Ce mot de passe est votre protection principale.

(La protection du fournisseur de tunnel s'ajoute *par-dessus*. C'est une seconde couche,
en plus du mot de passe. Elle reste importante : elle tient le trafic indésirable à l'écart
de TwiCC, avant même qu'il atteigne l'écran de connexion.)
