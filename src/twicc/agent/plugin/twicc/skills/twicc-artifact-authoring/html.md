# HTML pages — network and saved data

How an HTML artifact talks to the network and saves data. Read `SKILL.md` first: it holds the directory, folder, and link rules. For the look, read `design.md`.

## Network

Call `fetch` / `XMLHttpRequest` normally. Requests run server-side, so there is no browser CORS. The first contact with a host prompts the user to approve it.

## Saved data

An HTML artifact can save files under its own `data/` subfolder: `await window.twicc.data.set('config.json', obj)`, `.get`, `.list()`, `.remove` — or plain `fetch('data/x.json', {method: 'PUT', body})`.

- Use it to let the user make choices you read back later: seed `<artifact dir>/data/config.json` yourself, have the page load it, edit it, save it — then Read the file.
- This turns an artifact into a rich form for decisions, settings, selections on ANY topic: hand the user the link, ask them to tell you when they are done, then read their choices from `data/`.
- Writes are silent, confined to `data/`, and capped (10 MB per file, 100 MB total). The user sees the files in the Artifacts tab.
