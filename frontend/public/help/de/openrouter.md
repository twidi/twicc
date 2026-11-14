**OpenRouter** ist ein Online-Dienst. Ein Konto und ein API-Schlüssel geben Zugang zu vielen KI-Modellen. TwiCC kann ihn an wenigen Stellen als optionale Engine nutzen. Der Rest von TwiCC braucht ihn nie.

## Wo TwiCC ihn nutzt

| Wo | Was OpenRouter tut |
|---|---|
| **Einstellungen → Sprachausgabe** | Liest Nachrichten mit einer **OpenRouter-Konfiguration** laut vor. Er passt auch den Text vor dem Vorlesen an. |
| **Einstellungen → Zusammenfassungen** | Schreibt die Zusammenfassungen von Nachrichten und Auswahlen. |
| **Einstellungen → Titel** | Schlägt den Titel einer Sitzung vor. Der Umbenennen-Dialog kann ihn auch für einen einzelnen Titel ausprobieren. |

Für Anpassung, Zusammenfassungen und Titel nutzt TwiCC ein schnelles Textmodell: heute Qwen3.8 27B, bereitgestellt von Cerebras.

Für die Sprache wählst du das Sprachmodell und die Stimme unter **Einstellungen → Sprachausgabe**. TwiCC lädt die Listen der Modelle und Stimmen von OpenRouter, wenn du sie brauchst. Siehe [Sprachausgabe](help/text-to-speech).

## Aktivieren

1. Erstelle ein Konto auf [openrouter.ai](https://openrouter.ai) und lade Guthaben auf.
2. Erstelle einen API-Schlüssel auf der [Seite der Schlüssel](https://openrouter.ai/settings/keys).
3. Füge die Zeile `TWICC_OPENROUTER_API_KEY=your-key` in die `.env`-Datei des TwiCC-Datenverzeichnisses ein (standardmäßig `~/.twicc/.env`).

4. Starte TwiCC neu. Starte es bei jeder Änderung des Schlüssels erneut neu.

Der Schlüssel bleibt auf dem Server. TwiCC sendet ihn nie an den Browser.

Ohne Schlüssel sind die OpenRouter-Auswahlen deaktiviert. TwiCC behält deine Auswahl und wendet sie wieder an, sobald der Schlüssel gesetzt ist.

## Datenschutz und Kosten

- TwiCC sendet den Text an OpenRouter: die Nachricht für Sprache und Zusammenfassungen, die Nutzernachrichten für Titel.
- Du zahlst über dein OpenRouter-Konto. Die in den Einstellungen angezeigten Preise sind die Listenpreise von OpenRouter. Die echten Kosten können abweichen.
- OpenRouter hat eigene Bedingungen und eine eigene Datenschutzrichtlinie. Lies sie, bevor du ihn nutzt.

## Wenn OpenRouter ausfällt

Bei Anpassung, Zusammenfassungen und Titeln übernimmt ein aktivierter Agent: zuerst Claude, dann Codex. Kann kein Agent laufen, zeigt die Funktion einen Fehler, oder der Text wird unverändert vorgelesen.
Eine Sprachkonfiguration mit OpenRouter hat keinen Ersatz. Teste sie unter **Einstellungen → Sprachausgabe**, um den Fehler zu sehen.
