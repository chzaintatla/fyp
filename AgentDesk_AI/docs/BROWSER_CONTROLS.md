# Browser tabs and YouTube playback

## Enable the browser bridge once

Open http://127.0.0.1:8000/browser-extension/setup.html or Settings -> Browser tab controls -> Chrome / Edge setup.

1. In the intended Chrome profile open chrome://extensions (Edge: edge://extensions).
2. Enable Developer mode and choose Load unpacked.
3. Select AgentDesk_AI/browser-extension from this project.
4. **Pair it:** open the setup page from the link inside the Agent X app ("Chrome/Edge setup"). The link carries your local API token in the URL fragment, which is never sent to the server; the setup page hands it to the extension once and shows "Paired". Until paired, the extension badge shows PAIR and it does not contact the backend.
5. With the backend running, the badge becomes ON (click the icon to retry). The app then shows Browser connected.

The extension is pinned to `http://127.0.0.1:8000`; if you run the app on another port, pairing and connection will not work.

Enable the bridge in one browser profile at a time; multiple connected profiles are reported as ambiguous rather than silently choosing one. This extension works in Chrome/Edge, including background tabs in that profile. It is not installed automatically and cannot be loaded into the Codex in-app browser.

## Commands (no Groq requests)

- video pause kar do / video rok do / pause video
- video chala do / resume video
- Google Drive ka tab band kar do
- YouTube ka tab band kar do
- close Gmail tab / close WhatsApp tab / close OpenAI tab
- list tabs
- close tab 123 / pause tab 123 / resume tab 123
- play Python tutorial on youtube
- youtube par Python tutorial chala do

Urdu/Hindi variants of the media and tab-close commands are recognized by the local parser. Named-video requests use the existing local web lookup and may show YouTube search results when no confident match is found. Browser autoplay policy can require a manual Play click; opening a page is not reported as verified playback.

## Matching and verification

The extension supplies actual tab IDs, titles and URLs. Known sites match real hostnames: Drive is drive.google.com, Gmail is mail.google.com, WhatsApp is web.whatsapp.com. Lookalike domains and unrelated active tabs are not substituted.

If several tabs match a close request, the task asks for a title or ID. Media commands may select a unique audible YouTube tab; otherwise multiple matches also need a choice. Pause and resume call video.pause()/video.play() and inspect video.paused, so a repeated pause does not accidentally resume. Closing verifies the exact tab disappeared. A navigation between inspection and execution aborts the action using the expected URL guard.

Trust model: `/browser/connect-token` requires the local API bearer token (obtained only through pairing) plus the bridge header; the WebSocket accepts only `chrome-extension://` origins on a local Host and then requires the per-server bridge token. A local process that has the API token file could still impersonate the extension, and any extension that obtains the API token could pair; the bridge does not pin one extension ID. No cloud service or browser history upload is involved. Tab URLs shown in the UI or sent to an AI provider have their query string and fragment removed (the exact URL stays inside the backend to guard the close). Malformed or oversized tab records from the extension are discarded. The extension reads tab metadata, can close the requested tab, and has scripting permissions only for YouTube. Incognito tabs are excluded. Browser responses are time-bounded; timed-out operations are not automatically repeated.

## Verification and remaining manual step

Backend tests cover local command parsing, exact URL/ID selection, lookalike domains, duplicate tab ambiguity, missing extension behavior, and authenticated WebSocket request/result round trips. Node tests mock the Chrome APIs to check idempotent media actions, stale-navigation guards and exact tab close. Actual user's browser tabs have not been controlled because the extension still needs the user's one-time Load unpacked action.

Official APIs used:
- https://developer.chrome.com/docs/extensions/reference/api/tabs
- https://developer.chrome.com/docs/extensions/reference/api/scripting
- https://developer.chrome.com/docs/extensions/how-to/web-platform/websockets


## Updates without a manual Reload (extension 1.1+)

On connect the extension sends its version and capabilities. If the backend ships a newer
`browser-extension/manifest.json`, it sends `reload` and the extension calls `chrome.runtime.reload()`,
which loads the updated files of the unpacked install. `GET /browser/status` shows `version`,
`expected_version` and `outdated`.

An extension older than 1.1 cannot be reloaded remotely and has no `open` command. For those,
"open X in Chrome" launches Chrome and then confirms through the extension's `inspect` that the tab really
appeared in the connected profile (verified, not assumed). Reload once at chrome://extensions to get 1.1;
later updates load themselves.

Fixed in 1.1: "open" failed with "Browser did not confirm the requested tab" because `https://site` was
compared with the browser's normalised `https://site/`; closing a tab that was still loading failed with
"Tab changed since inspection" because a loading tab has only `pendingUrl`. Both now compare normalised URLs.

## Real-browser test notes

`frontend/e2e/extension_real.test.mjs` runs the real unpacked extension in real Chromium (new headless mode,
`--load-extension`) against a real backend on port 8000, pairs it through the real setup page and drives it
through the task API: list tabs, open Gmail in Chrome (extension `open`), close that exact tab. Every step must
be verified by the backend verifier. Each run writes `docs/live_runs/<date>-browser-extension.json`.

```
cd frontend
npm run build
npm run test:browser      # needs port 8000 free; stop the normal app first
```

Recorded run (container, Chromium 153 headless): pair + handshake (1.1.0, not outdated) -> list tabs (verified,
2 tabs) -> open Gmail via extension (verified: tab id seen by re-inspect) -> close tab (verified: extension
confirmed close and the tab is gone). The container has no internet, so the Gmail tab shows an error page;
tab creation, identity and closing are real.

Still to check by hand in your own Chrome/Edge profile (not covered by the automated run): YouTube pause/resume
on a playing video, autoplay blocking, two profiles connected at once, and the 1.0 -> 1.1 upgrade path.

## YouTube and tab controls added in extension 1.2

Say these with a YouTube tab open (the one playing sound, or the one in front, is used; two idle YouTube tabs make the assistant ask):

- skip 10 seconds / forward 30 / skip 1 minute / rewind 10 seconds / go back 10 seconds / 10 second aage karo / 5 second peeche kar do
- speed 1.5 / playback speed 2x / normal speed
- youtube volume 40 (the player's volume; plain "volume down" stays the PC's volume) / mute the video / unmute youtube
- full screen / exit full screen (Chrome may refuse: it only allows it after a real key press, and the assistant says so)
- next video
- search YouTube for X, then: read the results / play result 2 / "play the second one" (right after a YouTube search)
- youtube scroll down / up (scrolling other sites is done with the UI Automation tools: "scroll down in Chrome")
- switch to YouTube tab / switch to Gmail / what page is this (reads the title of the tab in front)

Every one is verified from the page itself: the extension reads the DOM again after acting and the backend compares it with what was asked
(the video's real `currentTime`, `playbackRate`, `volume`, `muted`, `fullscreenElement`, the new video id after "next" or "play result N",
the page's `scrollY`). A page that did not change is reported as not done. All of them are low risk (media and navigation).

Limits: page controls work on YouTube only (the extension has no permission on other sites). Tested with a fake DOM (`browser-extension/tests/test_youtube.cjs`);
YouTube's own page markup can change, so the selectors (`ytd-video-renderer a#video-title`, `.ytp-next-button`) need a check on the real site.
An extension older than 1.2 is told to update itself (the backend asks for a reload when it connects).
