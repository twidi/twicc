# GitLog — Plan de portage — Phase 0

> Fait partie de la spécification [GitLog — Port de @tomplum/react-git-log vers Vue 3](./1-contexte.md)
> Document principal : [Plan de portage — Index](./4-plan-index.md)

---

### Phase 0 : Préparation de l'environnement TypeScript

Le projet existant est entièrement en JavaScript (pas de `tsconfig.json`, pas de fichiers `.ts`). Le composant GitLog sera écrit en TypeScript, scopé à son propre dossier, sans impact sur le reste du projet.

#### Phase 0.1 : Setup TypeScript scopé à GitLog

**Entrée** : Projet Vue.js existant (100% JavaScript, Vite, pas de tsconfig)
**Sortie** : Environnement TypeScript fonctionnel pour le dossier `src/components/GitLog/`

- Ajouter `typescript` en devDependency (`npm install -D typescript`)
- Créer `frontend/tsconfig.json` minimal scopé au dossier GitLog :
  ```json
  {
    "compilerOptions": {
      "target": "ESNext",
      "module": "ESNext",
      "moduleResolution": "bundler",
      "strict": true,
      "noEmit": true,
      "esModuleInterop": true,
      "skipLibCheck": true,
      "forceConsistentCasingInFileNames": true,
      "jsx": "preserve",
      "paths": {
        "@/*": ["./src/*"]
      },
      "baseUrl": ".",
      "types": ["vite/client"]
    },
    "include": [
      "src/components/GitLog/**/*.ts",
      "src/components/GitLog/**/*.vue"
    ]
  }
  ```
- Le `include` limite le scope de `tsc` au seul dossier GitLog — les fichiers `.js` existants ne sont pas concernés
- Vérifier que `npx tsc --noEmit` fonctionne (devrait passer sans erreur sur un projet vide)
- Vérifier que `npm run dev` (Vite) continue de fonctionner normalement — Vite n'utilise pas `tsconfig.json` pour la transpilation, il utilise esbuild en interne et sait gérer le `.ts` nativement
- Créer le dossier `src/components/GitLog/` vide (point de montage pour les phases suivantes)

**Critère de validation** : `npx tsc --noEmit` passe. `npm run dev` fonctionne. Le code JS existant n'est pas affecté.

---

## Tasks tracking

- [ ] Phase 0.1: Setup TypeScript scopé à GitLog
