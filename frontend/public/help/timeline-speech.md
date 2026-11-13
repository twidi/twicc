---
title: "Timeline reading"
---

Use the **speaker button beside Timeline** to read new assistant messages
automatically. An available text-to-speech config is required. The pressed
button means that this timeline listens for new messages.

**Read only end-of-turn messages** is on by default. The switch appears before the
help button when text-to-speech is available and this timeline listens, including when listening is paused. It saves
one preference across reloads and listening activations. TwiCC reads the exact final
message after an official turn ending. Visible progress messages can remain silent.
The ending must be strictly after listening starts. Turn the switch off to read
intermediate messages too. Manual reading stays available in either mode.

- Listening uses the timeline's scope: all projects, a workspace, a project,
  or one worktree. Muted sessions are hidden and skipped by default. Select
  **Filters → Show muted sessions** to display and read them in that timeline.
  Other display filters, including the selected view and session filter, do
  not affect listening.
- Turning off **Show muted sessions** removes their queued messages. The
  reading already selected can finish. Turning it on does not replay messages
  skipped while it was off. Manual reading remains available in muted sessions.
- Listening continues when you navigate to another page, switch application
  tabs, or open a session. Only one timeline listens per browser tab.
- The timeline button in the rail shows a small speaker while listening is
  enabled. Its label identifies the listening timeline. Click it to open
  that timeline without changing listening.
- With the switch off, messages must have a native message date **strictly after the activation
  click**. Existing history does not play when you enable listening.
- With the switch off, late arrivals remain eligible up to **five seconds** behind the latest
  message whose reading starts. Exactly five seconds remains eligible.
  Accepted messages stay queued until their turn.
- Manual reading has priority. Automatic reading waits while a manual request
  or playback runs. The rail's timeline speaker stays visible during this wait
  and between messages.
- Click the pressed timeline speaker to disable listening and clear its queue.
  This stops its automatic reading and leaves unrelated manual reading running.
  Enabling another timeline clears the previous timeline's queue.
- The existing rail **Stop reading aloud** button and the message's **Stop
  reading** button stop the current reading. During automatic reading, they
  also disable listening and clear its queue.
- Listening and its queue stay in memory. A page reload clears them.

Automatic reading uses the active text-to-speech config. In **Settings →
Summaries**, **Read summaries in timeline speech** controls automatic summaries.
It is on by default. Eligible messages contain at least **1,000 content
characters**. Shorter messages use the existing text adaptation setting.
Project and session announcements keep their usual wording.

TwiCC prepares up to two queued text summaries while another reading plays.
It generates audio only when a message reaches its turn. A selected reading
keeps its settings until it finishes. Turning the switch off releases unneeded
preparation and makes pending messages use their originals. A failed summary
shows an error and skips that message. Listening continues with the next message.

## Where it keeps playing

What happens when you leave the page depends on where you use TwiCC.

- **Android app**: listening continues with the screen off and when you switch to
  another app. A notification shows the state and offers **Skip** and **Stop
  all**. Open TwiCC before you enable listening. When you enable it, Android can
  ask you to allow notifications, which the notification buttons need, and show
  battery advice. Unrestricted battery use is optional. It makes background
  listening more reliable. Change both later in Android Settings → Apps → TwiCC.
- **Web on a phone or tablet**: keep TwiCC in the foreground with the screen on.
  The browser can freeze the page when you switch apps or lock the screen. The
  audio then stops.
- **Web on a computer**: keep the tab open. It can stay in the background,
  behind other tabs or windows. The browser can slow a hidden tab, but listening
  normally continues.

To set up the voice, see [Text to speech](help/text-to-speech).
