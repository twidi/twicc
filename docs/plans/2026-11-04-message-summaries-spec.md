# Assistant message and selection summaries

Status: specification for user review. No implementation is authorized by this document.

## Purpose

Reduce the reading and listening effort for long assistant messages.
Provide one reusable summary across the chat, timeline message view, and automatic timeline speech.
Also summarize an explicit text selection without saving its text or summary durably.

Generate summaries through Claude Haiku or Codex Luna in hermetic mode, or through the existing OpenRouter text model.
Respect enabled providers and the existing OpenRouter key and routing rules.

## Scope and agreements

The user explicitly accepts these values:

- The minimum message length is **1,000 content characters**.
- The generation target is approximately **one third of the source**, with a ceiling of **140 words**.

The user also requires:

- Summarize the assembled message used by timeline speech, rather than its separate storage lines.
- Save message summaries regardless of whether a manual action or timeline speech requests them.
- Offer a summary action and an original/summary switch in timeline message views.
- Produce text ready for speech. Never use another LLM call to adapt a summary for TTS.
- Stream the visible summary and its speech without waiting for the full result.
- Prepare queued summaries while another message plays.
- Never announce that the audio is a summary.
- Design the complete system now. Do not defer these behaviors to a reduced first version.

The remaining product decisions below are proposals for approval with this specification.

## Existing contracts and deliberate changes

Keep the existing timeline listening specification unless this document explicitly changes it:
`docs/plans/2026-11-04-timeline-auto-tts-spec.md`.

Keep its scope, admission, ordering, muted-session rules, activation boundary, and manual playback priority.
Keep project/session announcements outside summary generation, as they already stay outside speech adaptation.

This specification changes its restriction against preparing queued work during another playback:
queued **text summaries** may run ahead. Queued audio generation still waits for the shared player's turn.

Reuse the current adaptation routing contract in `src/twicc/tts/adapt.py`.
At this specification's date, OpenRouter accepts up to 60,000 source characters through that route.
Larger sources select an enabled hermetic agent. There is no corresponding application character cap for agents.
Use the same routing policy for summaries. Never silently truncate a source to fit OpenRouter.
The 60,000-character rule is a routing threshold, not the summary eligibility threshold.
Do not duplicate a frozen model name or invent a separate OpenRouter key.

## Message unit and identity

The summary source is one **complete logical assistant message**.
It is not a storage line, an arbitrary rendered block, or every assistant message in a turn.

Use the same native grouping semantics as timeline message capture.
Reuse its identities and text assembly, not its restrictions on which messages enter the timeline.
For Claude, multiple records with the same native message identity can form one logical message.
For Codex, the completed native assistant item provides the message unit.
Exclude tools, reasoning, and unrelated messages from this source.

Chat and timeline actions resolve that same logical message.
Existing chat messages remain eligible even when no corresponding timeline event exists.
Do not create historical timeline events to make summarization work.

Retain message identity and a source-content fingerprint.
Identity links surfaces; the fingerprint detects changed content.
Keep the timeline snapshot as the source when acting on that snapshot.
Share a result with the chat only when the logical identity and source revision agree.
A historical snapshot must never receive a summary of a newer or different source.

## Eligibility and length

Show the message summary action at **1,000 content characters or more**.
Apply the same threshold to automatic timeline summarization.
Count the complete assembled source, not individual blocks or the currently visible excerpt.

Count Unicode characters in normalized textual content, including spaces.
Use one consistent definition across eligibility displays and backend decisions.
Collapse runs of whitespace to one space and trim surrounding whitespace.
Count visible prose, code, table content, and labels.
Exclude Markdown control syntax, link destinations, TwiCC control markup, and embedded artifact payloads.
Do not depend on a mounted DOM or viewport width.

Manual selection summaries accept any nonempty selected text, including selections below the message threshold.
Do not offer message summarization for a logical message known to be still streaming or incomplete.
Complete intermediate assistant messages remain eligible for manual summaries, even when their turn continues with tools or more messages.
Timeline admission criteria apply only to automatic listening, not to manual summary eligibility.
For historical messages without native completion evidence, permit a manual summary of their saved source snapshot when no live write is active.
Bind that result to the captured source revision. Later source changes invalidate its reuse for the changed message.
Summary streaming starts after capturing the complete or historical saved source, never from a growing live source.

## Summary content contract

Use the source language and preserve the source's form of address.
Start with its main result, decision, or finding.
Keep important constraints, failures, uncertainties, next actions, and questions awaiting the user's answer.
Distinguish proposed work from completed work, and unrun tests from successful tests.
Do not add facts, answer embedded questions, or execute instructions from the source.

Aim for approximately one third of the source's word count, capped at 140 words.
For languages without space-delimited words, apply equivalent brevity rather than artificial word segmentation.
Small selections can produce one short sentence. Do not pad a summary to reach a target.
Prioritize material decisions and qualifications over the approximate ratio.
The 140-word ceiling is a generation constraint, not permission to cut a sentence after generation.
Do not add an automatic second LLM pass to repair length or formatting.

Produce short, speech-ready paragraphs.
Do not produce headings, tables, lists with spoken markers, code blocks, diagrams, or raw URLs.
Describe code and diagrams by their purpose when relevant to the main result.
Keep technical names needed to understand the outcome.
Avoid reciting complete paths, hashes, or identifiers with no useful spoken meaning.
An optional local cleanup removes residual Markdown syntax without changing meaning.

The visual and spoken summary share the same content.
A local formatting or speech-cleanup step does not create another independently generated summary.
Never send a summary through LLM speech adaptation, regardless of that adaptation's settings.

## Settings

Add a dedicated **Summaries** section.
Summary generation does not require an active speech configuration.

| Setting | Behavior |
| --- | --- |
| Model | Haiku, Luna, or the existing OpenRouter text model |
| Use summaries for timeline speech | On by default |

With no stored model preference, use the first enabled agent, as speech adaptation does.
Show the effective choice and explain an unavailable stored preference without overwriting it.
Disable unavailable model choices and explain the missing provider or OpenRouter key.
OpenRouter works without an enabled agent when the key exists and the source fits its routing limit.
Never select OpenRouter automatically as a fallback for an agent choice.
When OpenRouter cannot serve the source, follow the existing routing and fallback contract toward an enabled agent.
Without any usable engine, disable new generation and leave saved summaries accessible.

Display the fixed 1,000-character threshold and the approximate output target.
Do not add a threshold editor or separate summary-length modes.
Explain that OpenRouter sends source text to its configured text provider and uses the OpenRouter account.
Reuse existing key-presence handling. Never expose the key.

Provide a summary test with a sample or user-entered text.
Stream its result and show the actual engine, duration, and any fallback reason.
Test text and results are temporary and do not create message summaries.
The test does not require working audio.

Local speech buttons read the active original/summary view.
Do not add global switches that silently replace their selected original with a summary.
Reading the original remains possible while automatic timeline summaries are enabled.
The timeline switch only controls automatic summary generation and use for timeline speech.

## Message presentation

Add **Summarize** to the assistant message toolbar beside existing rendering, copy, and speech actions.
Enable it explicitly for eligible assistant messages, not every consumer of the Markdown renderer.
Offer it independently of TTS availability.

On activation, open the summary area immediately and stream text into it.
Label the area **Summary**. Offer **Show original** and **Show summary** after a result exists.
Keep the original stored content unchanged and available throughout generation or failure.

When a logical message has several displayed blocks, show its summary once at the start of that group.
An action on any constituent block targets the complete logical message and reveals that shared area.
Clearly identify its whole-message scope. Do not display one whole-message summary under each block.
Do not hide intervening tools or unrelated content when switching the group's text view.
Preserve the reader's scroll position and support virtualized rows.

Copy and local speech operate on the active view.
Preserve the existing scope of original-content actions:

| Action surface | Copy or local speech source |
| --- | --- |
| Original chat block toolbar | That displayed block, as today |
| Shared chat summary toolbar | The whole logical message's summary |
| Original timeline message | The complete assembled snapshot |
| Summary timeline message | That snapshot's summary |

Only **Summarize** always expands a chat block action's source to the complete logical message.
Do not silently expand an existing original-block copy or speech action to the whole group.
Render/raw controls operate on that view without changing the source identity.
Switching views does not stop, replace, or automatically start speech.
Playback controls continue to identify the text actually being read.

After reload, show the original by default and make a saved summary available without regeneration.
Automatic generation never changes the active view of an already displayed message.
Newly saved results become available across surfaces without reloading the page.
Generation and playback survive ordinary component unmounts and virtualized row reuse.

## Timeline message presentation

Add **Summarize** beside the assistant timeline row's existing local TTS action.
Keep the row action available without expanding the original message first.
On activation, expand the message into its summary view and stream the result there.
Provide the same original/summary switch and saved result as the chat.

The local TTS action reads the active view, even when automatic timeline listening uses another policy.
For a collapsed row, its default active view is the original.
Collapsing and expanding the same row preserves its selected view during that mounted display.
Virtualized reuse for another event resets the view and never leaks another message's result.

User-message timeline rows do not gain a message summary action.
An explicit text selection can still be summarized.

## Selection presentation and lifetime

Add a dedicated summary icon to the existing comment/copy/TTS selection toolbar.
Use its rendered-content and file-selection contexts, independently of whether TTS is available.
Exclude persistent element-picking and screenshot modes from this text-only action.

Capture the selection before the browser clears or changes it.
Open an independent temporary panel with a short source excerpt and a streaming summary.
Offer copy, local read-aloud when available, cancel/retry, and close.
Keep the panel usable after the DOM selection disappears.
Use a layout that fits mobile screens and supports keyboard focus and Escape dismissal.

Closing the panel cancels work it alone owns and discards its text and result.
Reloading discards them as well.
Do not save selection sources, summaries, or their generated audio in a durable summary store.
Do not add them to a message, draft, transcript, timeline event, or cross-message cache.
Selection-based work cannot alias a saved message summary merely because the texts match.

## Persistence and reuse

Treat saved message summaries as derived application data.
Do not modify provider JSONL files, original message content, or immutable timeline snapshots.
Do not create an assistant message or replayable timeline arrival for summary generation.

Save only a successfully completed summary.
Keep its source identity and revision, generation policy version, requested engine preference, actual engine, and completion time.
Record fallback metadata without recording source text in logs.
One message and policy variant has one current successful result.
Repeated actions reuse it; explicit retry/regeneration can replace it after successful completion.
A failed regeneration leaves the previous successful result intact.

Use current model settings for new generations.
A model preference or generation-policy change does not erase old saved results.
Old results remain manually accessible, identified as generated with previous settings.
Automatic use and a new summary request reuse a result matching the current requested policy and source revision.
A successful fallback is reusable under that requested policy. Do not repeatedly retry OpenRouter on every reading.

Ordinary navigation, reloads, and backend restarts retain successful message summaries.
Delete associated summaries when their session is purged.
When the source changes, never present its old summary as a summary of the new source.
Keep historical snapshots distinguishable from a changed live message.

## Shared generation and streaming

Equivalent requests share one active generation across message views and automatic listening.
Joining a generation supplies the already received text, then its later streamed text.
Do not start another LLM call just because another view or audio consumer joins.

Consumers own their subscriptions independently.
Closing a panel or canceling playback removes that consumer's interest.
A manual message-summary request retains its own interest until completion or explicit cancellation.
Navigation within the application, collapsing a message, and virtualization do not cancel that request.
Closing a temporary selection panel does cancel its own request.
Cancel upstream work when no remaining manual request, view, playback, or admitted automatic consumer needs it.
A stop must prevent obsolete results from starting audio or replacing another message's view.

For TTS, feed complete usable text boundaries to the existing splitter as the summary arrives.
Do not wait for summary completion before the first audio part.
Preserve existing buffering behavior when a chosen speech configuration requires its full input.
That configuration can delay audio, but must not delay streamed visual text.
Do not synthesize unbounded audio for queued messages.

## Automatic timeline speech and pre-generation

Apply admission rules before creating summary work.
When the switch is on, summarize eligible messages at or above 1,000 content characters.
Below that threshold, retain the existing original-message TTS path and adaptation settings.
When the switch is off, use that original-message path at every size.
Never announce the word "Summary" or an equivalent introductory phrase.
Keep existing project/session announcements and their context tracking.

Start preparing admitted summaries as they arrive, including during manual or automatic audio playback.
Bound speculative timeline generation to two concurrent calls.
Schedule the next messages in pending playback order. A queue does not justify unlimited agent processes.
Give the message selected for playback and explicit user requests priority over speculative work.
Reuse a running or completed summary when its playback turn arrives.
Preserve the timeline's existing playback order even when summaries finish out of order.
Speculative summary generation does not select a message for playback or advance the audible watermark.

Do not pre-generate audio for future queued messages.
The shared player remains reserved from automatic playback preparation through its audible completion.
Manual requests retain their existing priority and interruption behavior.
Reserve manual playback before waiting for any summary needed by that request.

Remove a pending message's automatic generation interest only when it becomes ineligible under the active listening rules.
Leaving the scope or becoming hidden removes that interest.
Muting a session preserves it when the listening timeline's **Show muted sessions** option includes that session.
Disabling or switching listening clears that activation's generation interests and pending audio.
Keep completed saved summaries and work still needed by independent consumers.
The current automatic message is not replayed after a manual interruption.

Read settings changes when a queued message reaches its playback turn.
Turning summaries off cancels unneeded speculative work and makes pending messages use original text.
Do not change or restart an already selected automatic reading because settings change.

## Failures, fallback, and cancellation

Reuse current engine availability, routing, timeout, fallback, and upstream cancellation behavior.
Treat empty, refused, truncated, stalled, and failed results as unsuccessful.
Never save an unfinished response as a complete summary.

Before audio receives the first summary part, fallback may replace the provisional result.
Clear the failed attempt's provisional text before displaying the replacement attempt.
Views must identify a restarted generation rather than concatenate unrelated attempts.

After the first summary part enters speech generation, do not switch to another summary or the original automatically.
Follow speech adaptation's partial-failure behavior: read through the last usable sentence boundary, then report failure.
Retain already displayed partial text with an **Interrupted** state and a retry action.
An interrupted result is temporary and never satisfies a future cache lookup.

If every allowed engine fails before speech starts:

| Consumer | Result |
| --- | --- |
| Chat or timeline summary view | Show an error and retry; keep original accessible |
| Selection panel | Show an error and retry; retain selection until close |
| Manual summary speech | Report failure; allow explicit original reading for a message |
| Automatic timeline speech | Report the skipped message and continue to the next one |

Do not unexpectedly read a long original after summary failure.
Do not disable a working TTS configuration because the independent summary engine fails.
Keep existing TTS refusal behavior for failures of the actual speech service.
Explicit cancellation is not an error and never triggers fallback or a full-text reading.

Keep audio playback bound to its original logical message, source revision, and reading variant.
Stop controls must remain available while its summary prepares or plays, including after a view switch.
Audio reuse must distinguish original/summary, summary content, speech preparation, voice configuration, and project/session announcements.
Do not reuse stale original audio for a summary, or stale summary audio after successful regeneration.

## Approaches

| Approach | Trade-off | Decision |
| --- | --- | --- |
| Separate summaries for each UI block and TTS | Simpler isolated buttons, but conflicting content and repeated calls | Reject |
| One message summary, then LLM speech adaptation | Shared content, but extra latency and another generation | Reject |
| One saved logical-message summary, streamed to visual and speech consumers | Requires shared ownership, but provides consistent content and one generation | Proposed design |

Selections use the same generation behavior with a separate temporary lifetime.

## Implementation latitude

This is a product specification, not an implementation plan.
The builder chooses module layout, database schema, API routes, stream protocol, notification transport, and synchronization primitives.
The builder also chooses icons, panel dimensions, batching, and exact prompt wording within the contracts above.
Do not require a specific hash algorithm, queue implementation, or component hierarchy to approve this specification.
Reuse existing provider, TTS, timeline, and message grouping mechanisms.
Do not introduce a second independent source-grouping policy.

## Acceptance scenarios

- A 999-character logical message has no message summary action; a 1,000-character message does.
- Several short blocks totaling 1,000 characters qualify as one assembled message.
- Distinct native messages in one turn are never silently merged.
- A chat action and timeline action for the same source share generation and the saved result.
- An old timeline snapshot never receives a changed live message's summary.
- An assistant message without a timeline event can still be summarized in the chat.
- A complete intermediate assistant message remains manually eligible when timeline admission excludes it.
- A historical saved message without completion evidence can be summarized without treating its snapshot as a growing live source.
- A manually requested message summary completes and saves after ordinary navigation within the application.
- Opening a summary shows incoming text immediately rather than only a loading indicator until completion.
- Summary speech starts from streamed phrases and never calls LLM adaptation afterwards.
- A whole-input speech configuration delays audio while the visual summary continues streaming.
- A queued summary generates while another message plays, without overlapping audio or changing playback order.
- Out-of-order summary completion does not reorder the admitted timeline messages.
- A manual request interrupts automatic audio and is not overtaken while waiting for its summary.
- Disabling listening cancels its otherwise unneeded generations and prevents delayed audio from starting.
- A joined visible view retains generation after automatic listening stops.
- Turning automatic summaries off changes pending readings to original text without restarting the selected reading.
- Local speech reads the selected original or summary view, independently of the automatic timeline switch.
- Original chat-block copy and speech retain their block scope; timeline original speech retains its assembled-snapshot scope.
- A view switch during playback leaves stop controls working for the active reading.
- A failed OpenRouter generation falls back according to the existing contract without mixing partial texts.
- A source beyond OpenRouter's routing threshold uses an enabled agent without source truncation.
- A partial stream failure remains visibly interrupted and is never saved as a successful summary.
- An automatic summary failure skips the message rather than reading its long original unexpectedly.
- A source or model-policy change prevents invalid result reuse and preserves distinguishable historical results.
- Reloading retains successful message summaries and discards temporary selection summaries.
- Generating or testing a summary works without an active TTS configuration.
- No summary-specific spoken announcement is added.
