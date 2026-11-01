# E-mail and chat messages by voice (Gmail, Outlook, WhatsApp, Telegram, Teams)

## What it does

"Write an email to Ali" -> "What is the subject?" -> you say it -> "Go ahead with the message." -> you dictate sentence by sentence (the draft shows on screen as you speak)
-> "stop dictation" -> "send it" -> the assistant reads the **recipient, the subject and the first line**, asks "Shall I send it?" -> you say "yes, send it" -> it is sent,
looked up in the Sent folder, and only then: "Email sent to Ali."

While a draft is open, every sentence you say is part of the message, except whole-phrase commands: "read it back", "delete the last sentence", "undo",
"change tomorrow to Monday", "change the subject to ...", "send it to Sara", "make it more formal / shorter / friendlier" (the cloud AI rewrites it; you hear it and say yes or no),
"attach the report" (it says the file name and size and waits for yes), "cancel the email". Roman Urdu works too ("Ali ko email bhejo", "bas", "haan, bhej do").

## The send rule (cannot be switched off)

- Nothing is sent without the read-back and a spoken sentence that contains "send" ("yes, send it", "send it", "haan bhej do"). A bare "yes", "okay" or a dictated sentence is not consent.
- The confirmation is a secret the program makes when it reads the message back. It is tied to the exact recipients, subject, text and attachments: any edit voids it and the message is read back again. It expires after 10 minutes. A message can be sent once.
- The AI model cannot plan, name or approve the send (the tool is hidden from it); text in pages, files or e-mails is never an instruction.
- After sending, the program looks in the provider's Sent folder; "sent" is said only if it is there.
- Rewriting ("make it more formal") sends the e-mail text to the cloud AI, so it needs the "cloud answers" permission (Settings).

## Connect an account (once)

You need an OAuth client id from the provider (free). The ids are not committed to the project; put them in `backend\.env`:

**Gmail**: Google Cloud Console -> a project -> APIs: enable *Gmail API* and *People API* -> OAuth consent screen (External, add your own address as a test user) -> Credentials -> *OAuth client ID*, type **Web application**, authorised redirect URI
`http://127.0.0.1:8000/mail/oauth/callback` (and `http://localhost:5173/mail/oauth/callback` if you use the dev page). Then:

    GOOGLE_OAUTH_CLIENT_ID=...apps.googleusercontent.com
    GOOGLE_OAUTH_CLIENT_SECRET=...

Scopes requested: `gmail.send`, `gmail.metadata` (to check the Sent label of the message just sent), `contacts.readonly`, `contacts.other.readonly`. It cannot read your inbox.

**Outlook / Microsoft 365**: Entra admin center -> App registrations -> New registration (accounts in any organisational directory and personal accounts), redirect URI type *Public client / native* `http://127.0.0.1:8000/mail/oauth/callback` -> copy the Application (client) ID:

    MS_OAUTH_CLIENT_ID=...

Scopes: `Mail.Send`, `Mail.Read` (to find the message in Sent Items), `Contacts.Read`, `People.Read`, `offline_access`, `User.Read`.

Restart the app, open **Settings -> Email**, press **Connect**, sign in on the provider's page, and choose Gmail or Outlook. Tokens are stored only in the Windows Credential Manager
(service `AgentDesk-mail`); **Disconnect** deletes them. You can also revoke access in your Google / Microsoft account.

## Test it (test mailbox only)

    python backend\scripts\mail_live.py --provider gmail --to your.test.mailbox@gmail.com --i-am-using-a-test-mailbox

or `scripts\check_u6.ps1 -Provider gmail -To your.test.mailbox@gmail.com`. It sends one clearly marked message and checks Sent.

## Chat messages

"In WhatsApp, message Ahmed 'I'm on my way'" -> read-back -> "yes, send it". WhatsApp and Telegram numbers come from the Memory page ("Ahmed: +923001234567"; Telegram also accepts "Sara: @sara_k"); an unknown name is asked for.
A Teams recipient is found in the connected e-mail account's contacts. The app is opened on the chat with the text typed in (deep link), **Send** is pressed through UI Automation, and the chat is read again: the message must
appear in the conversation. If any step cannot be confirmed, the reply says so. The same send rule applies. (The older `whatsapp_open_chat` tool still only opens a draft for you to send yourself.)
