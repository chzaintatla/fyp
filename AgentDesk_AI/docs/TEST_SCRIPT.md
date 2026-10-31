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
