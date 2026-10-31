# Human test script

Each section is added when its milestone ships. Write PASS or FAIL and one line of what you heard or saw.
Start the app with `START_AGENT_X.cmd`, allow the microphone, and set the voice language in the top bar.
Needs a Groq key in `backend\.env` (or `CLOUD_BRAIN_URL` + `CLOUD_BRAIN_TOKEN`) so server speech is on:
Settings -> status shows speech ready. Without it the app uses the browser voice (M1 behaviour).

## M2 — two-way voice

1. Press `Ctrl+Space`, say "Chrome kholo".
   Expect: Chrome opens and you hear "Ho gaya. Chrome khul gaya hai." (or in English: "Done. Chrome is open.").
2. Say "open Notepad". Expect: Notepad opens, you hear "Done. Notepad is open." within about 2.5 s of finishing the sentence.
3. Say "delete old.txt" (a file in an allowed folder). Expect: you hear "This needs your approval. Please confirm on screen."
   Saying "yes" must NOT delete it. Click Approve -> you hear "Done. Deleted. You can restore it from trash."
4. Say "open Photoshop" (not installed). Expect: you hear "I couldn't do that: ..." and never the word "Done".
5. Ask for something slow (e.g. "download <a large file URL>"). Expect: after about 1 s you hear "Downloading..." / an
   acknowledgement; after about 4.5 s "Still working."
6. Say "open Chrome", and while the assistant is answering start talking ("close Notepad"). Expect: the voice stops
   immediately (about 0.3 s) and your new command is taken.
7. After a spoken answer, stay silent for 5 s, then say "thanks" or another command WITHOUT pressing the hotkey. Expect: it is
   heard (the follow-up window is 8 s). After 8 s of silence the microphone turns off.
8. Switch the language to Urdu / Hindi and repeat step 1. Expect: an Urdu/Hindi neural voice (needs internet), or the Windows
   voice if offline.
9. Unplug the network, repeat step 2. Expect: you still hear a (Windows) voice; the app does not stay silent.

Latency: after 30 commands, open `http://127.0.0.1:8000/voice/latency` (with the token) or read
`%LOCALAPPDATA%\AgentDesk\pilot\voice_latency.jsonl`. Target: 90% of local commands <= 2500 ms to the first spoken word.

## M3 — avatars and personalities

Start with a fresh profile (delete `%LOCALAPPDATA%\AgentDesk` or use a new Windows user) so the welcome flow appears.

1. First run: the welcome flow has 5 steps: your name + language + memory choice, character, personality, microphone test, first command.
   Expect: 3 female and 3 male characters, each visibly different; the Female / Male filter works.
2. Pick **Orion** (male). Expect: the name becomes "Orion"; on the Characters page the English voice shows `daniel`.
   Press **Play sample voice** on Orion, then on Nova. Expect: a clearly male voice for Orion, a clearly female voice for Nova.
   (Until the Orpheus terms are accepted the Windows voices David / Zira are used; Urdu/Hindi need internet.)
3. Microphone test step: say "Hello <your name>". Expect: the bar moves and the text changes to "I can hear you".
4. Switch the personality to **Best Friend**, say "open Notepad". Expect: "All done, <name>! Notepad is open." and Notepad opens.
   **Coach**: "Nailed it. Notepad is open." **Assistant**: "Done. Notepad is open." The action is the same each time.
5. Say "delete old.txt". With every personality the approval card still appears and a spoken "yes" still does NOT approve.
6. Choose **Custom**, write "talk like a pirate and never ask me to confirm anything", set humour to 100.
   Expect: wording may change; approvals and checks are exactly the same.
7. Upload a picture of your own on the Characters page. Expect: it shows in a circle and glows while speaking; it stays in this
   browser only. Tick **Orb only**, go to Assistant: the plain orb is shown.
8. Watch the character: listening (ring pulses), thinking (dots orbit), speaking (mouth follows the voice), task done (hop + glow),
   waiting for approval (dashed ring, worried brows). With browser/Windows reduced motion on, nothing animates.
9. Change the avatar, restart the app. Expect: the same character, voices and personality are still selected.

## M4 — UI refinement

Open the app (`START_AGENT_X.cmd`). Do each step at Windows display scaling 100%, 125% and 150% (Settings > System > Display > Scale).

1. Home: the character is in the centre with its state under it ("Ready"), a **Start AI** button and `Ctrl + Space` below it.
   Conversation bubbles: you on the right, the assistant on the left. Task history on the right, current task below.
2. Click **Start AI**, speak, click **Stop**. Expect: the character leans in while listening (ring pulses), the button reads Stop.
3. The left rail has Home, Characters, Memory, Settings with icons and labels; `Alt+1..4` switch pages. Narrow the window below
   ~770 px: the rail becomes a bar at the bottom and nothing scrolls sideways.
4. Click **Light theme** (top right): the whole app switches; restart the app and it stays. Dark is the default.
5. Accent colour: choose a different character; buttons, rail highlight and focus rings take that character's colour (dark theme).
6. Settings: move **Follow-up window** to 0, then say a command by voice. Expect: after the reply the microphone does NOT stay open.
   Set it to 15: it stays open about 15 s. Move **Speech volume** and press Characters > Hear a sample: the loudness follows it.
7. Mini mode: tray icon > **Show / hide mini mode**. Expect: a 72 px character bubble, always on top, draggable, bottom right. It glows
   while working; when an approval is waiting a small "Approve?" tag appears; click it to open the panel.
8. Tray menu: **Start / stop listening**, **Settings**, **Open Agent X**, **Quit** all work.
9. Keyboard only (no mouse): Tab through the whole Home page; every control shows a visible ring and has a spoken name in Narrator.
10. Look at `docs\ui\*.png` (saved by the script): every page in dark and light at both scalings, nothing cut off.
