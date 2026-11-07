# Interface translation glossary

This glossary fixes one translation for each recurring TwiCC term. It keeps
the catalogs consistent across translation updates. The catalogs are
`frontend/src/i18n/locales/{en,fr,es,de,pt-BR}.json`. The same terms apply to
the translated help and tip Markdown bodies.

## How to use it

- Read this file before you translate or review any interface text.
- Use the listed word every time the English term has the listed meaning.
- The glossary works per concept, not per word. A word that is listed as
  "avoid" for one English term can still be right for another term.
- Add a row when you introduce a recurring domain term. A term is recurring
  when it appears in several messages. Fill the cells for every supported language.
- When you change a row, update the catalogs and the help and tip bodies in
  the same pass.
- Do not use this file for generic words (name, title, open, close,
  create, copy, save, send). Translate them in context.
- The English column shows the lowercase source form. Plural forms appear in
  the notes only when they are irregular or when the plural rule matters.
- Register per language: fr uses "vous", es uses "tú", de uses "du", pt-BR uses "você".
- Keep identifiers, API field names, command names and keyboard chords in
  English. Examples are `provider`, `peer`, `/goal` and `origin.panel`.

See [Internationalization](internationalization.md) for the catalog rules.

## Core objects

| English | Meaning in TwiCC | fr | es | de | pt-BR | Avoid / notes |
| --- | --- | --- | --- | --- | --- | --- |
| session | A conversation thread with an agent, tracked by TwiCC | session | sesión | Sitzung | sessão | Do not use "conversation" for the object. de: never keep the English word "Session" in prose. |
| ephemeral session | A session that TwiCC and the provider do not save as a local transcript | éphémère | efímera | flüchtig | efêmera | Avoid temporaire, temporal, temporär, temporária. |
| draft | A new session or a set of answers that is not sent yet | brouillon | borrador | Entwurf | rascunho | - |
| parent / child (session) | Sessions linked in a session tree (orchestration) | parent / enfant | padre / hijo | übergeordnet / untergeordnet | pai / filho | pt-BR: use "pai" for every parent (session, directory, element, project, scope); avoid "superior". "nível superior" (level) is another concept. |
| message | Text that a user, an agent or a peer sends | message | mensaje | Nachricht | mensagem | Avoid de "Meldung". Avoid es and pt-BR "aviso" (it names a toast or an alert). |
| conversation | The flow of messages inside a session | conversation | conversación | Unterhaltung | conversa | Do not use for the session object. |
| chat | The Chat tab of a session | chat | chat | Chat | chat | - |
| assistant | The agent side of a conversation (assistant message) | assistant | asistente | Assistent | assistente | Do not use for "agent". |
| turn | One cycle of agent work after a user message | tour | turno | Durchgang | turno | de compound: Nachrichtendurchgang. Avoid de "Runde" and "Turn". fr: zero and one take the singular. |
| project | A registered directory with its own defaults | projet | proyecto | Projekt | projeto | - |
| workspace | A named group of projects | espace de travail | espacio de trabajo | Arbeitsbereich | espaço de trabalho | Do not keep "workspace" in prose. Avoid pt-BR "área de trabalho". |
| worktree | A Git worktree that TwiCC creates for a project | worktree | worktree | Worktree | worktree | Keep the English word in all languages. Plural: worktrees. Do not translate. |
| provider | An AI backend that TwiCC drives (Claude Code, Codex) | fournisseur | proveedor | Anbieter | provedor | Keep `provider` only as a literal API field name. Avoid pt-BR "fornecedor". |
| agent | The AI that works in a session | agent | agente | Agent | agente | de plural: Agenten. |
| subagent | An agent that another agent starts | sous-agent | subagente | Subagent | subagente | de plural: Subagenten. Avoid de "Unteragent". |
| model | The AI model of a session | modèle | modelo | Modell | modelo | - |
| effort | The reasoning effort setting of a model | effort | esfuerzo | Aufwand | esforço | Keep the fr word "effort". |
| thinking | The extended thinking setting and its content block | réflexion | razonamiento | Thinking | raciocínio | de: "Thinking" for the setting, the block header and the content; avoid "Denkvorgang" and "Denkinhalt". Progress verb: fr "réfléchit", es "razonando", de "denkt nach", pt-BR "pensando". |
| goal | The objective that an agent works toward (`/goal`) | objectif | objetivo | Ziel | objetivo | Keep the command text `goal` in English. Avoid es and pt-BR "meta" (it is also the Meta key) and fr "but". |
| plan | The plan document that an agent proposes | plan | plan | Plan | plano | es and pt-BR use the same word as the subscription plan. Context tells them apart. |
| plan mode | The permission mode in which the agent only plans | mode plan | modo plan | Plan-Modus | modo de planejamento | Use "planejamento" only in the mode name. |
| subscription plan | The paid plan of a provider account | forfait | plan | Tarif | plano | es and pt-BR use the same word as the agent plan (context tells them apart). Avoid de "Plan" for this meaning. |
| hybrid mode | Mode that runs the real Claude CLI in tmux instead of the programmatic API | mode hybride | modo híbrido | Hybrid-Modus | modo híbrido | - |
| fast mode | Faster generation, billed on extra usage credits | mode rapide | modo rápido | Fast-Modus | modo rápido | - |
| mode | A named working state (permission, plan, fast, hybrid) | mode | modo | Modus | modo | - |

## Agent control and approvals

| English | Meaning in TwiCC | fr | es | de | pt-BR | Avoid / notes |
| --- | --- | --- | --- | --- | --- | --- |
| permission | Access that a tool or an agent needs | autorisation | permiso | Berechtigung | permissão | - |
| permission mode | The setting that controls tool permissions | mode d'autorisation | modo de permisos | Berechtigungsmodus | modo de permissão | - |
| allow | Let a tool, host or request through | autoriser | permitir | erlauben | permitir | de: "Erlaubnisregel" (allow rule) and "Erlaubnisliste" (allow list) are the allow terms; "Berechtigungsregel" is the generic permission rule. |
| deny | Block a tool, host or request | refuser | denegar | ablehnen | negar | fr: same word as refuse. de: "ablehnen" is also refuse and reject; the object tells them apart; avoid "verweigern". pt-BR: "negar"; reserve "recusar" for refuse. |
| approval / approve | The user accepts a tool call or a request | approbation / approuver | aprobación / aprobar | Genehmigung / genehmigen | aprovação / aprovar | fr: "approuver" also names the trust decision on a project (see trust). The object tells them apart: approve a request or tool call, versus approve (trust) a project. |
| trust | The human decision that a project is safe | confiance (approuver un projet) | confianza (confiar) | Vertrauen (vertrauen) | confiança (confiar) | fr: noun "confiance", verb "approuver" (shared with approve; the object tells them apart). |
| trusted | State of a project that the user trusts | approuvé | de confianza | vertrauenswürdig | confiável | fr: "non de confiance" is ungrammatical, so "approuvé" is the target. The collision with approve is accepted; the object tells them apart. |
| untrusted | State of a project that the user has not approved | non approuvé | sin confianza | nicht vertrauenswürdig | não confiável | fr: "non approuvé"; "non de confiance" is ungrammatical. es: "sin confianza"; avoid "no confiable". |
| question | A question that the agent asks the user | question | pregunta | Frage | pergunta | - |
| answer / response | The reply to a question or a prompt | réponse | respuesta | Antwort | resposta | - |
| request (human) | An ask to a person: pairing, MCP connection, approval | demande | solicitud | Anfrage | solicitação | fr: "demande"; avoid "requête" for peer, MCP and permission requests. pt-BR: avoid "pedido". |
| request (network) | An HTTP or API call | requête | solicitud | Anfrage | solicitação | fr: "requête" for HTTP and API calls, including text-to-speech calls; avoid "demande". |
| pending | Waiting for a decision or an answer | en attente | pendiente | ausstehend | pendente | de: "ausstehend"; avoid "offen" for pending requests. |
| refuse | Decline a peer message or a request, or block a request | refuser | rechazar | ablehnen | recusar | es and de use the same word for refuse and reject. Tell them apart by the object: the user refuses a message or request; a remote instance or the server rejects. pt-BR: reserve "recusar" for refuse. |
| reject | A remote instance or the server turns something down | rejeter | rechazar | ablehnen | rejeitar | Same word as refuse in es and de. See the refuse row. pt-BR: "rejeitar", not "recusar". |
| steer | Redirect a running agent with a new message | orienter | dirigir | steuern | orientar | - |
| interrupt | Stop the current turn but keep the agent | interrompre | interrumpir | unterbrechen | interromper | Reserve the verb for interrupt. Do not use it for stop. |
| stop | Stop the agent or the process | arrêter | detener | stoppen | parar | pt-BR: reserve "interromper" for interrupt. de: reserve "beenden" for kill and for finishing work; use "stoppen" for every stop, including stopping speech or a selection. |
| kill | Force-stop a process or a background shell | tuer | terminar | beenden | encerrar | fr: "tuer"; reserve "interrompre" for interrupt. es "terminar", pt-BR "encerrar" and de "beenden" also mean finish or close; the object tells them apart. Force-kill labels: fr "forcer l'arrêt", es "forzar cierre", de "Beenden erzwingen", pt-BR "forçar encerramento". |

## Peers (cross-instance messaging)

| English | Meaning in TwiCC | fr | es | de | pt-BR | Avoid / notes |
| --- | --- | --- | --- | --- | --- | --- |
| peer | Another TwiCC instance that the user has approved | pair | par | Peer | instância parceira | Plural: pairs / pares / Peers / instâncias parceiras. Keep `peer` only as an API field name. |
| peer messaging | Messages between two TwiCC instances | messagerie entre pairs | mensajería entre pares | Peer-Messaging | mensagens entre instâncias parceiras | - |
| pairing | The steps that link two instances | appairage (s'appairer) | emparejamiento (emparejarse) | Kopplung (koppeln) | pareamento (parear) | pt-BR: "pareamento / parear", so that "conexão" keeps the meaning of connection; avoid "parceria", "conectar". fr: "appairer"; avoid "associer". |
| relationship | The link between two peers | relation | relación | Beziehung | conexão | pt-BR: "conexão" is also the word for connection (see connection). |
| connection | A network connection (peer, MCP, server) | connexion | conexión | Verbindung | conexão | pt-BR: "conexão" is also the target for relationship (see pairing and relationship). |
| reconnect | Connect again after a loss or a revoke | reconnecter | reconectar | neu verbinden | reconectar | - |
| revoke | Cancel the access of a peer or an MCP client | révoquer | revocar | widerrufen | revogar | - |
| instance | One running TwiCC installation | instance | instancia | Instanz | instância | - |
| remote | On another instance or machine | distant(e) | remoto(a) | Remote | remoto(a) | de: compounds such as "Remote-Instanz". |
| inbox | The list of incoming peer messages | boîte de réception | bandeja de entrada | Posteingang | caixa de entrada | - |
| verification code | The code that confirms a pairing or an MCP connection | code de vérification | código de verificación | Verifizierungscode | código de verificação | - |
| code verification | The check that a verification code matches | vérification du code | verificación del código | Code-Verifizierung | verificação do código | Process, not the code itself. |
| credentials | Secrets that a peer exchanges | identifiants | credenciales | Zugangsdaten | credenciais | - |

## Files, content and tools

| English | Meaning in TwiCC | fr | es | de | pt-BR | Avoid / notes |
| --- | --- | --- | --- | --- | --- | --- |
| artifact | A viewable file that an agent produces | artefact | artefacto | Artefakt | artefato | - |
| bookmark | The saved state of an artifact | favori | favorito | Lesezeichen | favorito | Avoid fr "signet", es and pt-BR "marcador" (es "marcador" is the word for placeholder). |
| attachment | A file that is attached to a message | pièce jointe | adjunto | Anhang | anexo | de plural: Anhänge. |
| attach (a file) | Add a file to a message | joindre | adjuntar | anhängen | anexar | es, de and pt-BR use the same verb for files and terminals. The object tells them apart. |
| attach (a terminal) | Connect a terminal from another session | attacher | adjuntar | anhängen | anexar | fr: use "rattacher" for a project or a session tree. es, de and pt-BR: same verb as for files. |
| directory | A directory on disk (English "directory") | répertoire | directorio | Verzeichnis | diretório | Use the folder word only where English says "folder" (see folder). |
| folder | The word "folder" in English text | dossier | carpeta | Ordner | pasta | Use only where the English says "folder". |
| path | A file or directory path | chemin | ruta | Pfad | caminho | - |
| diff | The difference between two file versions | diff | diff | Diff | diff | Keep the English word. |
| branch | A Git branch | branche | rama | Branch | branch | Keep the English word in de and pt-BR. fr and es use the official Git word ("branche", "rama"). See Technical terms. |
| repository | A Git repository | dépôt | repositorio | Repository | repositório | - |
| snapshot | A frozen copy of an artifact, as opposed to live | instantané | instantánea | Snapshot | snapshot | Keep the value `snapshot` in English in API messages. |
| group | A named group of snippets, combos or projects | groupe | grupo | Gruppe | grupo | - |
| link | A URL link, such as a share link | lien | enlace | Link | link | - |
| upload | Send a file to TwiCC | téléversement (téléverser) | subida (subir) | Upload (hochladen) | upload (enviar) | Avoid fr "téléchargement" (it means download). pt-BR: noun "upload", not "envio"; the verb "enviar" is also the generic "send". |
| snippet | A saved text block that the user inserts | snippet | snippet | Snippet | snippet | Keep the English word. Avoid "extrait", "fragmento", "Textbaustein", "trecho". |
| combo | A saved key sequence for the terminal | combo | combo | Kombination | combinação | de: "Kombination"; avoid "Combo". |
| insert | Put a snippet or a placeholder into the composer | insérer | insertar | einfügen | inserir | - |
| placeholder | A variable that is resolved when a snippet is inserted | variable | marcador | Platzhalter | placeholder | es: "marcador" is the placeholder word; it is not used for bookmark. |
| comment | A comment on a selection or on code | commentaire | comentario | Kommentar | comentário | - |
| preview | A rendered view of a file or an artifact | aperçu (prévisualiser) | vista previa | Vorschau | prévia | pt-BR: "prévia"; verb "pré-visualizar"; avoid "visualização". |
| view | A view or display mode of a screen | vue | vista | Ansicht | visualização | - |
| summary | A short text that summarizes a session | résumé | resumen | Zusammenfassung | resumo | - |
| timeline | The ordered list of events of a session | chronologie | cronología | Zeitachse | linha do tempo | de: "Zeitachse"; avoid "Zeitleiste". es: avoid "línea de tiempo". |
| compact | Compact view; compact a conversation context | compact (compacter) | compacto (compactar) | kompakt (komprimieren) | compacto (compactar) | de: "komprimieren" for the context action; avoid "verdichten". |
| companion | The TwiCC companion script on a dev page | compagnon | Companion | Companion | Companion | fr translates it; es, de and pt-BR keep the English word. |
| telemetry | Anonymous usage telemetry | télémétrie | telemetría | Telemetrie | telemetria | - |
| score | The model score shown beside effort | score | puntuación | Score | pontuação | - |
| history | The past messages or usage of a scope | historique | historial | Verlauf | histórico | - |
| context | The context window of a model | contexte | contexto | Kontext | contexto | - |
| prompt | The text that the user sends to start work | prompt | prompt | Prompt | prompt | Keep the English word. |
| tool | A tool that an agent calls | outil | herramienta | Tool | ferramenta | Avoid de "Werkzeug". |
| command | A slash command or a command-palette entry | commande | comando | Befehl | comando | - |
| workflow | A multi-phase automated run | workflow | flujo de trabajo | Workflow | workflow | The English product word stays in fr, de and pt-BR. |
| script | A script run by TwiCC: a text-to-speech script, or the companion script added to a dev page | script | script | Skript | script | de: always "Skript", never "Script". |

## Processes and tools in a session

| English | Meaning in TwiCC | fr | es | de | pt-BR | Avoid / notes |
| --- | --- | --- | --- | --- | --- | --- |
| process | The operating-system process of a provider | processus | proceso | Prozess | processo | - |
| terminal | A terminal tab in a session | terminal | terminal | Terminal | terminal | Avoid "console", "consola", "Konsole". |
| browser | The Browser tab (embedded page) | navigateur | navegador | Browser | navegador | de keeps "Browser". Keep `browser` only in literal CLI flags. |
| background shell | A shell command that runs in the background | shell en arrière-plan | shell en segundo plano | Shell im Hintergrund | shell em segundo plano | fr: avoid "tâche" for a shell. |
| cron job | A scheduled job of a session | cron | cron | Cron-Job | cron job | Keep the word "cron" in every language. fr and es plural: "crons". Avoid es "tarea programada". |
| task | An item of the agent's task list (Tasks tab) | tâche | tarea | Aufgabe | tarefa | - |

## Layout and interface

| English | Meaning in TwiCC | fr | es | de | pt-BR | Avoid / notes |
| --- | --- | --- | --- | --- | --- | --- |
| pane | An area of the session layout (the chat or a docked tool panel) | panneau | panel | Bereich | painel | fr: always "panneau", never "volet". de: "Bereich" is also part of "Arbeitsbereich" (workspace) and "Geltungsbereich" (scope); context tells them apart. |
| panel | A tool view (Git, Files, Settings) | panneau | panel | Bereich | painel | Same word as pane in every language. de: same word as pane; context tells them apart. |
| dock | Fix a panel into a pane | ancrer (ancré) | acoplar (acoplado) | andocken (angedockt) | encaixar (encaixado) | pt-BR: avoid "organizar" and "fixar" (pin). |
| gutter | The edge strip that holds minimized panels | gouttière | margen | Randleiste | margem | es "margen" and pt-BR "margem" can read as "margin"; no clearly better word exists. fr: "gouttière" is rare in UI French; keep it. de: "Randleiste", not bare "Leiste". es: avoid "borde". |
| layout | A saved arrangement of panes | disposition | layout | Layout | layout | Avoid fr "mise en page". es keeps the English word because "diseño" also means design. fr keeps "disposition" (no ambiguity, the VS Code fr word). |
| tab | A tab in a tab bar | onglet | pestaña | Tab | aba | Avoid de "Registerkarte", "Reiter". Avoid pt-BR "guia". |
| sidebar | The session list on the side | barre latérale | barra lateral | Seitenleiste | barra lateral | - |
| display (noun, section label) | A settings or command section about how things appear (not the view-mode concept) | affichage | visualización | Anzeige | exibição | pt-BR: "Exibição", "Exibição de horário"; keep "visualização" for view. |
| shortcut | A keyboard shortcut | raccourci | atajo | Tastenkürzel | atalho | - |
| window | A quota window or a context window | fenêtre | ventana | Fenster | janela | pt-BR: "janela" is right here; it is not the word for a dialog. |
| dialog | A modal window | boîte de dialogue | cuadro de diálogo | Dialog | diálogo | pt-BR: avoid "janela" for a dialog. |
| toast | A short in-app message | notification dans l'application | aviso | Hinweis | aviso | fr: the toast shares the word "notification". |
| composer | The message input area of a session | zone de saisie | campo de mensaje | Eingabefeld | campo de mensagem | es: "campo de mensaje"; avoid "editor de mensajes" and "cuadro de redacción". |
| notification | An alert outside the page (system, push) | notification | notificación | Benachrichtigung | notificação | Avoid de "Mitteilung". |

## Actions

| English | Meaning in TwiCC | fr | es | de | pt-BR | Avoid / notes |
| --- | --- | --- | --- | --- | --- | --- |
| archive / unarchive | Move an item out of the active lists | archiver / désarchiver | archivar / desarchivar | archivieren / dearchivieren | arquivar / desarquivar | - |
| pin | Mark a session as pinned | épingler | fijar | anheften | fixar | - |
| mute | Silence the notifications of a session | mettre en sourdine | silenciar | stummschalten | silenciar | - |
| hide | Remove an item from the lists without deleting it | masquer | ocultar | ausblenden | ocultar | fr: dot-prefixed files and directories use "cachés". |
| share | Create a public link (verb) | partager | compartir | teilen | compartilhar | - |
| sharing / share (noun) | The sharing feature, a share link or a share host | partage | compartir (compartido) | Freigabe | compartilhamento | de: "Freigabe" for the noun, "teilen" for the verb. |
| dismiss | Close a hint or alert without acting | ignorer | descartar | schließen | dispensar | de: "schließen"; "verwerfen" is for discard and "ausblenden" for hide. es: same word as discard; context tells them apart. |
| discard | Throw away unsaved work | abandonner | descartar | verwerfen | descartar | es: same word as dismiss. de: "verwerfen" is for discard; dismiss targets "schließen". |
| delete | Destroy an item permanently | supprimer | eliminar | löschen | excluir | fr: "supprimer" is for delete only. Use "retirer" for remove. |
| remove | Take an item out of a list or a group; the item itself survives | retirer | quitar | entfernen | remover | Delete vs remove: delete destroys the item, remove only detaches it. fr: avoid "supprimer" for remove. de: avoid "löschen" for remove. |
| restart | Start a process again | redémarrer | reiniciar | neu starten | reiniciar | es: do not use "reiniciar" for reset. |
| reset | Return to defaults | réinitialiser | restablecer | zurücksetzen | redefinir | pt-BR: avoid "restaurar" (restore). es: avoid "reiniciar" (restart). |
| refresh | Fetch current data again | actualiser | actualizar | aktualisieren | atualizar | de: "aktualisieren"; use "neu laden" for reload only (also for "force refresh" wording that means a reload). |
| reload | Load the page or the data again | recharger | recargar | neu laden | recarregar | - |
| update | Change to a newer version or newer data | mettre à jour | actualizar | aktualisieren | atualizar | es, de and pt-BR use the same verb as for refresh. The object tells them apart. |
| retry | Try an action again | réessayer | reintentar | erneut versuchen | tentar novamente | - |
| rename | Change a name | renommer | renombrar | umbenennen | renomear | - |
| switch | Move to another tab, view, mode or provider | basculer | cambiar | wechseln | mudar (para) | fr: "basculer"; avoid "passer" and "changer". pt-BR: use "mudar (para)" for every switch; reserve "alternar" for toggle. |
| toggle | Turn something on or off | afficher/masquer, activer/désactiver | alternar | umschalten | alternar | fr: avoid "basculer" for toggle. |
| search | Look for text | rechercher | buscar | suchen | pesquisar | pt-BR: avoid "buscar". |
| show | Make an item visible | afficher | mostrar | anzeigen | mostrar | pt-BR: avoid "exibir". |

## States

| English | Meaning in TwiCC | fr | es | de | pt-BR | Avoid / notes |
| --- | --- | --- | --- | --- | --- | --- |
| enabled / disabled | A feature is on or off | activé / désactivé | activado / desactivado | aktiviert / deaktiviert | ativado / desativado | es: "activado"; avoid "habilitado", "deshabilitado", "desabilitado". |
| unread | A session with news since the last visit | non lu | no leído | ungelesen | não lida | pt-BR: agree with the noun (session is feminine). |
| scope | The level at which a value applies | portée | ámbito | Geltungsbereich | escopo | de: "Geltungsbereich" contains "Bereich" (see pane). |

## Settings, usage and network

| English | Meaning in TwiCC | fr | es | de | pt-BR | Avoid / notes |
| --- | --- | --- | --- | --- | --- | --- |
| settings | The settings of the app, a provider or an agent | paramètres | configuración | Einstellungen | configurações | Avoid fr "réglages" and es "ajustes". Presets keep their own word (see preset). |
| agent settings | The per-session agent options (model, effort, permission) | paramètres de l'agent | configuración del agente | Agent-Einstellungen | configurações do agente | - |
| preset | A saved set of agent settings | préréglage | ajuste predefinido | Preset | predefinição | es: "ajuste predefinido" for presets, while settings use "configuración". fr: "préréglage". |
| quota | A usage limit of a provider | quota | cuota | Kontingent | cota | - |
| usage | How much of a provider is consumed | utilisation | uso | Nutzung | uso | de: "Nutzung" for usage. "Verbrauchsrate" is the burn rate and "verbrauchen" is fine for consuming credits; avoid "Verbrauch" as the noun for usage. |
| burn rate | How fast a quota is consumed | rythme de consommation | tasa de consumo | Verbrauchsrate | taxa de consumo | es: avoid "ritmo de consumo". |
| quota wake-up | A scheduled start of a quota window | réveil du quota | reactivación | Aufwecken | início antecipado | es: avoid "despertador". pt-BR: avoid "retomada". |
| extra usage | Usage billed beyond the plan quota | utilisation supplémentaire | uso adicional | Zusatznutzung | uso extra | es: avoid "uso extra". pt-BR keeps "uso extra". |
| cost | The money cost of a session | coût | costo | Kosten | custo | - |
| token (auth) | An access token for authentication. Same word as the LLM token | token | token | Token | token | Keep the English word for both senses. Avoid fr "jeton". |
| password | A password | mot de passe | contraseña | Passwort | senha | - |
| credits | A balance of provider credits | crédits | créditos | Guthaben | créditos | - |
| host | A host that serves shares or artifacts | hôte | host | Host | host | fr: "hôte" is the standard French IT word. Use it everywhere, including backend messages. |
| hostname | The name of a host | nom d'hôte | nombre de host | Hostname | hostname | - |
| address | The URL or network address of an instance | adresse | dirección | Adresse | endereço | - |
| origin | The scheme, host and port of a URL (HTTP origin); also the source of an upload | origine | origen | Origin | origem | de: "Origin"; avoid "Ursprung". |
| MCP | The Model Context Protocol and its clients | MCP | MCP | MCP | MCP | Keep the English abbreviation. |
| commit | A Git commit | commit | commit | Commit | commit | Keep the English word. Verb: see Technical terms. Avoid fr "valider" and es and pt-BR "confirmar" (they mean confirm). |
| viewer / visitor | A person who opens a shared link | visiteur | visitante | Betrachter | visitante | - |
| clipboard | The system clipboard | presse-papiers | portapapeles | Zwischenablage | área de transferência | - |
| text-to-speech | The speech output feature | synthèse vocale | texto a voz | Sprachausgabe | leitura em voz alta | pt-BR: avoid "conversão de texto em fala". |
| read aloud | Speak a message | lire à voix haute | leer en voz alta | vorlesen | ler em voz alta | - |
| orchestration | Sessions that spawn and manage other sessions | orchestration | orquestación | Orchestrierung | orquestração | - |


## Technical terms

The users of TwiCC are developers. Developers say many technical words in
English, whatever their language. The interface must not rename the words
that they use every day. It must still be clear for a reader who does not
know the term.

### Rules

- **Default:** keep an English technical term when developers say it in English
  (commit, diff, prompt, token, shell). Keep it in the interface, the help and
  the tips.
- **Translate** a term when it is an interface concept or a common word with a
  natural, unambiguous word in the language (file, path, tab, clipboard, query).
- **Translate** a term when the kept word would clash with another word of the
  language. Example: fr "stage" means internship, so fr uses "indexer".
- **Official convention:** when a language has a strong official convention (Git
  localization, the VS Code language pack), a row can differ between languages.
  The notes column gives the reason. These differences are intentional.
- **Marker:** `(kept)` in a language cell means the English word stays verbatim
  in that language. A cell without the marker holds a translation, or a word
  that is spelled the same way in the language. Rows in the other sections do
  not use the marker. Their notes say "Keep the English word".
- **Kept terms stay verbatim.** Do not put them in quotes or italics. Do not
  respell them. Do not add a gloss in brackets.
- **Case:** a kept term is lowercase in running text and capitalized at the
  start of a label. de capitalizes every noun, so "Commit", "Branch", "Diff".
  Acronyms and product names keep their form: CLI, MCP, API, OAuth, HEAD, JSON,
  WebSocket, tmux (lowercase).
- **Code spans** stay verbatim: `git log`, `git restore --staged`, `/goal`,
  `bypassPermissions`. Keep key chords in English: `Alt+Shift+B`, never fr
  "Alt+Maj+B".
- **Example paths** stay verbatim in every language: `/path/to/tmux.conf`.
- **Plural:**
  - fr: add "s" (commits, diffs, worktrees). No English irregular plural.
  - es: "s" after a vowel, "es" after a consonant (commits, tags, hashes, regexes, stashes).
  - de: use the forms in the notes (Commits, Branches, Tags, Diffs, Hashes,
    Prompts, Tokens, Proxys, Agenten). Do not invent others.
  - pt-BR: add "s" after the article (os commits, os branches, as tags).
- **Gender of kept terms:**
  - fr: masculine, except "une skill", "une regex", "une sandbox", "la CLI".
  - es: masculine ("el commit", "el tag", "el diff", "el hash", "el prompt",
    "el token", "el shell", "el log", "el cron", "el stage", "el push"), except
    "la skill", "la regex", "la CLI", "la API".
  - de: "der Commit, Branch, Tag, Diff, Hash, Prompt, Cache, Proxy, Stream, Host,
    Port, Tab, Skill, Worktree", "die Shell, CLI", "das Snippet, Token".
  - pt-BR: masculine ("o commit", "o branch", "o diff", "o stash", "o token",
    "o log"), except "a tag", "a skill", "a CLI", "a API".
- **Verbs from kept nouns:**
  - fr: prefer a French verb plus the noun: "créer un commit", "faire un push".
    Avoid "commiter", "pusher", "stasher".
  - es and pt-BR: "hacer" or "fazer" plus the noun: "hacer commit", "fazer push".
    Avoid "commitar", "pushar", "stagear".
  - de: the verbs listed in the notes (committen, stagen, auschecken, pushen).
- **Imperative labels** use the infinitive in fr, es, de and pt-BR.
- **Stay consistent in a pair:** when a row says to translate, never write the
  English word in prose (es "rama", never "branch"). When a row says to keep,
  never translate it in some places.

### Git

| English | Meaning in TwiCC | fr | es | de | pt-BR | Avoid / notes |
| --- | --- | --- | --- | --- | --- | --- |
| committer | The person who applied a commit | committer (kept) | committer (kept) | Committer (kept) | committer (kept) | No standard translation exists. |
| author | The person who wrote a commit | auteur | autor | Autor | autor | - |
| branch tip | The newest commit of a branch | pointe de la branche | punta de la rama | Branch-Spitze | ponta do branch | - |
| tag (Git) | A Git tag | tag (kept) | tag (kept) | Tag (kept) | tag (kept) | Avoid "étiquette", "etiqueta", "Etikett" and pt-BR "marca": they clash with the generic label. pt-BR gender: "a tag". |
| stage (verb) | Add changes to the Git staging area | indexer | hacer stage | stagen | adicionar ao stage | fr: "indexer" is the official Git fr and VS Code fr word; "stage" means internship in French, so it is not kept. Labels: "Tout indexer", "Hacer stage de todo", "Alle stagen", "Adicionar tudo ao stage". Avoid es and pt-BR "preparar" and de "bereitstellen". |
| unstage (verb) | Remove changes from the staging area | désindexer | sacar del stage | unstagen | remover do stage | Labels: "Désindexer", "Sacar todo del stage", "Alle unstagen", "Remover tudo do stage". |
| staged / unstaged | The state badge of a file | indexé / non indexé | en stage / sin stage | Staged / Unstaged | no stage / fora do stage | de: capitalized only on a badge; in prose "gestagte Datei", "ungestagt". Backend messages use the same words: "fichier indexé", "gestagte Datei", "no stage". |
| staged (pending apply) | Not Git: a setting that waits to be applied (hybrid switch) | programmée | pendiente | vorgemerkt | pendente | Never reuse the Git stage word for this meaning. |
| untracked | A file that Git does not track | non suivi | sin seguimiento | nicht verfolgt | não rastreado | - |
| ignored (by Git) | A file that Git ignores | ignoré par Git | ignorado por Git | von Git ignoriert | ignorado pelo Git | - |
| conflict | A merge conflict | conflit | conflicto | Konflikt | conflito | - |
| merge | Combine two branches; unmerged state | fusionner / non fusionné | fusionar / sin fusionar | zusammenführen / nicht zusammengeführt | merge (kept): fazer merge / sem merge | Intentional difference: official Git and GitHub in fr, es and de translate; Git pt-BR keeps "merge" and Brazilian developers say it. Avoid pt-BR "mesclar", "mesclagem". |
| revert | Drop the unsaved edits of an open file and reload the saved version (also the Git revert of a commit) | rétablir | revertir | revertieren | reverter | fr has no cognate verb: "rétablir" is the VS Code fr word for "Revert File"; avoid "restaurer" (it names restore) and "annuler" (undo). de: avoid "Zurücksetzen" (reset) and "Rückgängig machen" (undo). |
| checkout | Switch the working copy to a branch or a commit; the working copy itself | checkout (kept) | checkout (kept) | Checkout (kept) | checkout (kept) | Verb: fr "faire un checkout", es "hacer checkout", de "auschecken" ("ausgecheckt"), pt-BR "fazer checkout". State "checked out": fr "en checkout", es "en checkout", de "ausgecheckt", pt-BR "em checkout". Source checkout: fr "checkout des sources", es "checkout del código fuente", de "Quell-Checkout", pt-BR "checkout do código-fonte". Avoid fr "extraire". |
| git log | The Git command, quoted in a backend error | `git log` (kept) | `git log` (kept) | `git log` (kept) | `git log` (kept) | Always a code span. A UI label that means the commit history uses the history word: "historique Git", "historial de Git", "Git-Verlauf", "histórico do Git". |
| commit graph | The drawn graph of commits | graphe | gráfico | Graph (kept) | grafo | de keeps the cognate "Graph". |
| Git root | The root directory of a repository | racine Git | raíz de Git | Git-Stammverzeichnis | raiz do Git | - |
| clone | Copy a repository | cloner | clonar | klonen | clonar | - |
| upstream | The remote branch that a branch follows | upstream (kept) | upstream (kept) | Upstream (kept) | upstream (kept) | - |
| pull / push / fetch (Git) | Git network operations | pull / push / fetch (kept) | pull / push / fetch (kept) | Pull / Push / Fetch (kept) | pull / push / fetch (kept) | Verbs: fr "faire un push", es "hacer push", de "pushen", "pullen", "fetchen", pt-BR "fazer push". |
| rebase | Replay commits on another base | rebase (kept) | rebase (kept) | Rebase (kept) | rebase (kept) | Verb: fr "faire un rebase", es "hacer rebase", de "rebasen", pt-BR "fazer rebase". |
| stash | Save changes aside | stash (kept) | stash (kept) | Stash (kept) | stash (kept) | Verb: es "hacer stash", de "stashen", pt-BR "fazer stash". Avoid fr "remisage". |
| cherry-pick | Apply one commit to another branch | cherry-pick (kept) | cherry-pick (kept) | Cherry-Pick (kept) | cherry-pick (kept) | Keep the hyphen. de verb: "cherry-picken". |
| blame | Per-line authorship view | blame (kept) | blame (kept) | Blame (kept) | blame (kept) | - |
| hunk | One block of changes in a diff | hunk (kept) | hunk (kept) | Hunk (kept) | hunk (kept) | Avoid es "fragmento", pt-BR "trecho" and "pedaço". |
| hash | A commit hash | hash (kept) | hash (kept) | Hash (kept) | hash (kept) | es plural "hashes", de plural "Hashes". |
| HEAD | The Git reference to the current commit | HEAD (kept) | HEAD (kept) | HEAD (kept) | HEAD (kept) | Never translate. fr: masculine, "le HEAD actuel". |
| parent / child (commit) | Commits linked by ancestry | parents / enfants | padres / hijos | Eltern-Commit / Kind-Commit | commits pais / filhos | - |
| push (share update) | Not Git: send an update to a share link | publier | enviar actualización | senden | enviar atualização | Never use the Git word "push" for this meaning. |
| push (notification) | A push notification to a device | notification push | notificación push | Push-Benachrichtigung | notificação push | Keep "push" in every language. |

### Development and operations

| English | Meaning in TwiCC | fr | es | de | pt-BR | Avoid / notes |
| --- | --- | --- | --- | --- | --- | --- |
| shell | A command shell | shell (kept) | shell (kept) | Shell (kept) | shell (kept) | See also background shell. |
| CLI | The command-line interface | CLI (kept) | CLI (kept) | CLI (kept) | CLI (kept) | Feminine everywhere: fr "la CLI", es "la CLI", de "die CLI", pt-BR "a CLI". Avoid fr "le CLI" and es "el CLI". Hybrid: "la CLI hybride", "la CLI híbrida", "Hybrid-CLI", "a CLI híbrida". |
| permission prompt | The dialog that asks to approve a tool call | demande d'autorisation | solicitud de permiso | Berechtigungsabfrage | solicitação de permissão | The word "prompt" alone means the user text (see prompt). |
| system prompt | The system instructions of a session | prompt système | prompt de sistema | System-Prompt | prompt de sistema | - |
| token (LLM) | A unit of model text | token (kept) | token (kept) | Token (kept) | token (kept) | Same word as token (auth). de plural: "Tokens"; neuter "das Token". |
| API key | A secret key for an API | clé d'API | clave de API | API-Schlüssel | chave de API | - |
| API, SDK, OAuth, MCP, URL, UUID, HTTP, HTTPS, WebSocket, localhost | Acronyms and protocol names | API, SDK, OAuth, MCP, URL, UUID, HTTP, HTTPS, WebSocket, localhost (kept) | same (kept) | same (kept) | same (kept) | Verbatim. Gender: fr "l'API", es "la API", pt-BR "a API". |
| MCP client | A client of the TwiCC MCP server | client MCP | cliente MCP | MCP-Client | cliente MCP | - |
| endpoint | A URL that serves an API | endpoint (kept) | endpoint (kept) | Endpoint (kept) | endpoint (kept) | Avoid fr "point de terminaison", es "punto de conexión", de "Endpunkt". |
| tunnel | A network tunnel to reach TwiCC | tunnel | túnel | Tunnel | túnel | Same word as English in fr and de. |
| remote access | Reach TwiCC from another machine | accès à distance | acceso remoto | Fernzugriff | acesso remoto | - |
| proxy | A proxy server | proxy (kept) | proxy (kept) | Proxy (kept) | proxy (kept) | de plural: "Proxys". |
| cache | A cache | cache (kept) | caché | Cache (kept) | cache (kept) | es: "caché" is the standard Spanish spelling of the same word. |
| daemon | A background service process | daemon (kept) | daemon (kept) | Daemon (kept) | daemon (kept) | - |
| PID | The operating-system process number | PID (kept) | PID (kept) | PID (kept) | PID (kept) | Avoid fr "ID du processus", de "Prozess-ID" and pt-BR "ID do processo" when the label means the PID. |
| ID | An identifier | ID | ID | ID | ID | One word everywhere, also in backend errors. Avoid fr "identifiant". es: "el ID". |
| LAN / loopback | Network kinds of an address | réseau local / loopback (kept) | red local / loopback (kept) | lokales Netzwerk / Loopback (kept) | rede local / loopback (kept) | - |
| socket | A network or tmux socket | socket (kept) | socket (kept) | Socket (kept) | socket (kept) | fr: "le socket". |
| example path | A placeholder path in an input hint | `/path/to/...` (kept) | `/path/to/...` (kept) | `/path/to/...` (kept) | `/path/to/...` (kept) | Do not translate the segments (fr "/chemin/vers/", es "/ruta/a/"). |
| payload | The data of a request or a message | payload (kept) | payload (kept) | Payload (kept) | payload (kept) | Use the plain word "données" / "datos" / "Daten" / "dados" in prose where no technical term is needed. Do not mix renderings for the same label. |
| stream / streaming | Live output of a response; the streaming feature | stream / streaming (kept) | stream / streaming (kept) | Stream / Streaming (kept) | stream / streaming (kept) | Avoid fr "flux", es "flujo", pt-BR "fluxo" (es "flujo" clashes with "flujo de trabajo"). de verb: "streamen". |
| log (noun) | A log of events or output | log (kept) | log (kept) | Log (kept) | log (kept) | Plural "logs" / "Logs". Avoid fr "journal", es "registro", de "Protokoll". pt-BR: "histórico" is only for history. |
| debug | Debug mode or view | débogage | depuración | Debug (kept) | depuração | Intentional difference: "Debuggen" is the German IT standard; fr, es and pt-BR use the official IDE word. |
| dump | A raw dump of data to a file | dump (kept) | dump (kept) | Dump (kept) | dump (kept) | Verb: fr "écrire un dump", es "generar un dump", de "Dump erstellen", pt-BR "gerar um dump". Avoid "export" (a different feature), es "volcado", pt-BR "gravação". |
| lint | Static code check | lint (kept) | lint (kept) | Lint (kept) | lint (kept) | de verb: "linten". |
| plugin | A TwiCC or provider plugin | plugin (kept) | plugin (kept) | Plugin (kept) | plugin (kept) | - |
| skill | An agent skill | skill (kept) | skill (kept) | Skill (kept) | skill (kept) | Feminine in fr, es, pt-BR ("une skill", "la skill", "uma skill"); de "der Skill". Avoid "compétence", "habilidad", "Fähigkeit", "habilidade". |
| sandbox | The Codex execution sandbox | sandbox (kept) | sandbox (kept) | Sandbox (kept) | sandbox (kept) | fr: "une sandbox". Avoid "bac à sable", "caja de arena", "caixa de areia". |
| regex | A regular expression | regex (kept) | regex (kept) | Regex (kept) | regex (kept) | fr: "une regex". es plural "regexes", de plural "Regexes". Avoid fr "regexp", pt-BR "expressão regular". |
| glob | A file pattern with wildcards | glob (kept) | glob (kept) | Glob (kept) | glob (kept) | - |
| wildcard | The `*` character of a pattern | caractère générique | comodín | Platzhalter | curinga | - |
| changelog | The list of release changes | changelog (kept) | changelog (kept) | Changelog (kept) | changelog (kept) | Avoid fr "journal des modifications", es "registro de cambios", de "Änderungsprotokoll". |
| fallback | A model that is used when the first one fails | de secours | alternativo | Ersatz- | alternativo | Translate in sentences: fr "modèle de secours", es "modelo alternativo", de "Ersatzmodell", pt-BR "modelo alternativo". Avoid de "Fallback". |
| spawn | An agent starts another session | lancer ("lancée par") | generar ("generada por") | starten ("gestartet von") | criar ("criada por") | Do not use "spawn" or "spawnen" in prose. |
| frame / iframe | An embedded page frame | frame / iframe (kept) | frame / iframe (kept) | Frame / iframe (kept) | frame / iframe (kept) | Avoid es "marco". |
| header (HTTP) | An HTTP header | en-tête | encabezado | Header (kept) | cabeçalho | de: avoid "Kopfzeile". |
| screenshot | An image of the screen | capture d'écran | captura de pantalla | Screenshot (kept) | captura de tela | Intentional difference: fr, es and pt-BR use the term of their operating systems; de uses the German IT standard. |
| app | The Android application | application | aplicación | App (kept) | aplicativo | One word per language. Avoid es "app" and pt-BR "app". |
| Chrome MCP | The Chrome MCP server | Chrome MCP | Chrome MCP | Chrome-MCP | Chrome MCP | Product name: fr and es keep the English order. Avoid fr "MCP Chrome" and es "MCP de Chrome". |
| Markdown, JSON, HTML, SVG, PDF, Mermaid, Android, Chrome | Format and product names | verbatim | verbatim | verbatim | verbatim | Capitalize "Markdown". |

### Interface words

| English | Meaning in TwiCC | fr | es | de | pt-BR | Avoid / notes |
| --- | --- | --- | --- | --- | --- | --- |
| popover | A small floating panel | popover (kept) | popover (kept) | Popover (kept) | popover (kept) | Avoid fr "fenêtre" and es "menú" for a popover. |
| overlay | A layer that covers part of the screen | superposition | superposición | Overlay (kept) | sobreposição | Intentional difference: "Overlay" is the German UI standard. |
| inline | Shown inside the chat, not in a separate view | inline (kept) | inline (kept) | Inline (kept) | inline (kept) | Avoid fr "en ligne" and es "en línea": they read as "online". Avoid fr "incorporé". |
| template | A text pattern with variables (worktree directory template) | template (kept) | plantilla | Vorlage | template (kept) | fr and pt-BR keep the English word because "modèle" and "modelo" name the AI model. |
| slash command | A `/command` typed in the composer | commande slash | comando slash | Slash-Befehl | comando slash | The `/word` stays verbatim. |
| viewport | The visible area of a page | viewport (kept) | viewport (kept) | Viewport (kept) | viewport (kept) | Avoid fr "fenêtre d'affichage". |
| scroll | Move the visible part of a list | défiler | desplazar | scrollen (kept) | rolar | Prefer a label such as "go to top" / "go to bottom". Intentional difference: no natural German verb exists. |
| hover | Pointer over an element | survol | al pasar el cursor | Hover (kept) in compounds; "darüberfahren" as verb | passar o mouse | Do not leave the English word in fr, es and pt-BR. |
| icon | A small picture that stands for an item | icône | icono | Icon | ícone | de: "Icon", never "Symbol". |
| badge | A small status label | badge (kept) | badge (kept) | Badge (kept) | badge (kept) | - |
| chip | A small pill-shaped control | chip (kept) | chip (kept) | Chip (kept) | chip (kept) | - |
| tooltip | A small hint on hover | infobulle | información sobre herramientas | Tooltip (kept) | dica de ferramenta | Intentional difference: "Tooltip" is the German UI standard. |
| key names | Names of keyboard keys | touche Meta | tecla Meta | Meta-Taste | tecla Meta | Chords stay English: `Alt+Shift+B`, `Ctrl+K`. |
