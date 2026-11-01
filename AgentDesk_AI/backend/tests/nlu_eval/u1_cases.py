"""U1 evaluation set: what the recogniser/parsers hand to the name matcher, and the name that was meant.

Each line: kind | heard | expected | mispronounced(m/-) | language
  expected = a name in the fixture index; "ASK:name" = several names fit, the system must ask and offer `name`;
             "NONE" = nothing in the index fits, the system must not open anything.
  kind     = app | file | folder | risky (delete/send style command: only an exact name may be acted on)
`heard` strings are written the way Whisper tends to get them wrong (split words, voicing, vowel swaps), plus Roman Urdu
word order ("firefox kholo", "documents wala folder"). DEV was used while tuning; HOLDOUT was not looked at until the
final run (see docs/eval/u1_*.json for the numbers).
"""

DEV = """
app|firefox|Firefox|-|en
app|fire fox|Firefox|m|en
app|fayer fox|Firefox|m|en
app|firefocks|Firefox|m|en
app|chrome|Google Chrome|-|en
app|google chrome|Google Chrome|-|en
app|crome|Google Chrome|m|en
app|google krome|Google Chrome|m|en
app|chrome kholo|Google Chrome|-|ur
app|note pad|Notepad|m|en
app|not pad|Notepad|m|en
app|notepad|Notepad|-|en
app|notepad kholo|Notepad|-|ur
app|calculator|Calculator|-|en
app|calculater|Calculator|m|en
app|calc later|Calculator|m|en
app|calculator kholo|Calculator|-|ur
app|vs cold|Visual Studio Code|m|en
app|v s code|Visual Studio Code|m|en
app|vs code|Visual Studio Code|-|en
app|visual studio coat|Visual Studio Code|m|en
app|visual studio code|Visual Studio Code|-|en
app|excel|Excel|-|en
app|exel|Excel|m|en
app|x cel|Excel|m|en
app|power point|PowerPoint|m|en
app|powerpoint|PowerPoint|-|en
app|out look|Outlook|m|en
app|outlook|Outlook|-|en
app|spotifi|Spotify|m|en
app|spotify|Spotify|-|en
app|spotify chalao|Spotify|-|ur
app|whats app|WhatsApp|m|en
app|watsapp|WhatsApp|m|en
app|whatsapp|WhatsApp|-|en
app|tele gram|Telegram|m|en
app|telegraam|Telegram|m|ur
app|zoom|Zoom|-|en
app|zom|Zoom|m|en
app|dis cord|Discord|m|en
app|discord|Discord|-|en
app|task manager|Task Manager|-|en
app|task manger|Task Manager|m|en
app|comand prompt|Command Prompt|m|en
app|command prompt|Command Prompt|-|en
app|power shell|PowerShell|m|en
app|file explorar|File Explorer|m|en
app|file explorer|File Explorer|-|en
app|sniping tool|Snipping Tool|m|en
app|paint|Paint|-|en
app|one note|OneNote|m|en
app|adobe acrobatt|Adobe Acrobat|m|en
app|steem|Steam|m|en
app|git hub desktop|GitHub Desktop|m|en
app|post man|Postman|m|en
app|seven zip|7-Zip|m|en
app|android studio|Android Studio|-|en
app|pie charm|PyCharm|m|en
app|slak|Slack|m|en
app|v l c|VLC media player|m|en
app|vlc|VLC media player|-|en
app|o b s studio|OBS Studio|m|en
app|doker desktop|Docker Desktop|m|en
app|my sequel workbench|MySQL Workbench|m|en
app|control pannel|Control Panel|m|en
app|windows termnal|Windows Terminal|m|en
app|word|Word|-|en
app|microsoft word|Word|-|en
app|wurd|Word|m|en
app|settings|Settings|-|en
app|setings|Settings|m|en
app|word pad|WordPad|-|en
app|blorptastic|NONE|-|en
app|zzqx|NONE|-|en
app|quantum flux editor|NONE|-|en
app|visual studio|ASK:Visual Studio 2022|-|en
file|fyp report|ASK:FYP Report final.docx|-|en
file|fee wipe report|ASK:FYP Report final.docx|m|en
file|f y p report|ASK:FYP Report v2.docx|m|en
file|resume nabiha|Resume Nabiha.pdf|-|en
file|resume nabiha pdf|Resume Nabiha.pdf|-|en
file|rezume nabiha|Resume Nabiha.pdf|m|en
file|invoice march|Invoice March.xlsx|-|en
file|in voice march|Invoice March.xlsx|m|en
file|thesis chapter 1|Thesis Chapter 1.docx|-|en
file|thesis chapter one|Thesis Chapter 1.docx|m|en
file|lab manual|Lab Manual.pdf|-|en
file|lab manuel|Lab Manual.pdf|m|en
file|project proposal|Project Proposal.pptx|-|en
file|project proposel|Project Proposal.pptx|m|en
file|attendance sheet|Attendance Sheet.xlsx|-|en
file|attendence sheet|Attendance Sheet.xlsx|m|en
file|meeting notes|Meeting Notes.txt|-|en
file|meeting nodes|Meeting Notes.txt|m|en
file|budget 2025|Budget 2025.xlsx|-|en
file|budget twenty twenty five|Budget 2025.xlsx|m|en
file|cover letter|Cover Letter.docx|-|en
file|cover leter|Cover Letter.docx|m|en
file|semester schedule|Semester Schedule.pdf|-|en
file|database assignment|Database Assignment.docx|-|en
file|data base assignment|Database Assignment.docx|m|en
file|python notes|Python Notes.txt|-|en
file|pythan nots|Python Notes.txt|m|en
file|tax return|Tax Return.pdf|-|en
file|research paper|Research Paper.pdf|-|en
file|final presentation|Final Presentation.pptx|-|en
file|class timetable|Class Timetable.xlsx|-|en
file|class time table|Class Timetable.xlsx|m|en
file|taxi receipt|NONE|-|en
file|holiday photos|NONE|-|en
folder|documents|Documents|-|en
folder|documents wala folder|Documents|-|ur
folder|dokuments|Documents|m|en
folder|downloads|Downloads|-|en
folder|download|Downloads|-|en
folder|down loads|Downloads|m|en
folder|desktop|Desktop|-|en
folder|desk top|Desktop|m|en
folder|pictures|Pictures|-|en
folder|pikchers|Pictures|m|en
folder|videos|Videos|-|en
folder|music|Music|-|en
folder|fyp|FYP|-|en
folder|f y p|FYP|m|en
folder|backend|Backend|-|en
folder|back end|Backend|m|en
folder|frontend|Frontend|-|en
folder|front end|Frontend|m|en
folder|university|University|-|en
folder|yunivarsity|University|m|en
folder|assignments|Assignments|-|en
folder|assignment|Assignments|-|en
folder|semester five|Semester Five|-|en
folder|semester 5|Semester Five|m|en
folder|projects|Projects|-|en
folder|lecture slides|Lecture Slides|-|en
folder|lecture slide|Lecture Slides|-|en
folder|backup|Backup|-|en
folder|screen shots|Screenshots|m|en
folder|screenshots|Screenshots|-|en
folder|zebra crossing|NONE|-|en
risky|fyp report final|FYP Report final.docx|-|en
risky|fee wipe report final|ASK:FYP Report final.docx|m|en
risky|resume nabiha pdf|Resume Nabiha.pdf|-|en
risky|rezume nabiha|ASK:Resume Nabiha.pdf|m|en
risky|invoice march|Invoice March.xlsx|-|en
risky|in voice march|ASK:Invoice March.xlsx|m|en
risky|backup|Backup|-|en
risky|back up|Backup|m|en
risky|tax return|Tax Return.pdf|-|en
risky|tax returns|ASK:Tax Return.pdf|m|en
risky|taxi receipt|NONE|-|en
risky|lab manuel|ASK:Lab Manual.pdf|m|en
""".strip().splitlines()

HOLDOUT = """
app|fire foxx|Firefox|m|en
app|mozilla firefox|Firefox|-|en
app|googel chrome|Google Chrome|m|en
app|chromee|Google Chrome|m|en
app|notpad|Notepad|m|en
app|note pat|Notepad|m|en
app|kalkulator|Calculator|m|en
app|calculator kholen|Calculator|-|ur
app|vee ess code|Visual Studio Code|m|en
app|vscode|Visual Studio Code|-|en
app|excell|Excel|m|en
app|microsoft excel|Excel|-|en
app|powerpoint kholo|PowerPoint|-|ur
app|power pint|PowerPoint|m|en
app|outlok|Outlook|m|en
app|spotifyy|Spotify|m|en
app|what's up app|NONE|-|en
app|whatsap|WhatsApp|m|en
app|telegram|Telegram|-|en
app|disc cord|Discord|m|en
app|zoom meeting|Zoom|-|en
app|tasks manager|Task Manager|m|en
app|commandprompt|Command Prompt|-|en
app|powershel|PowerShell|m|en
app|snipping toll|Snipping Tool|m|en
app|onenote|OneNote|-|en
app|git hub|GitHub Desktop|m|en
app|postmen|Postman|m|en
app|7 zip|7-Zip|-|en
app|android studeo|Android Studio|m|en
app|slack|Slack|-|en
app|obs|OBS Studio|-|en
app|docker|Docker Desktop|-|en
app|my sql workbench|MySQL Workbench|m|en
app|control panel|Control Panel|-|en
app|windows terminal|Windows Terminal|-|en
app|wordpad|WordPad|-|en
app|flibbertigibbet|NONE|-|en
file|fyp report|ASK:FYP Report final.docx|-|en
file|resume of nabiha|Resume Nabiha.pdf|m|en
file|invoice marsh|Invoice March.xlsx|m|en
file|thesis chapter won|Thesis Chapter 1.docx|m|en
file|lab manual pdf|Lab Manual.pdf|-|en
file|project proposal slides|Project Proposal.pptx|-|en
file|attendance|Attendance Sheet.xlsx|-|en
file|metting notes|Meeting Notes.txt|m|en
file|cover later|Cover Letter.docx|m|en
file|python nots|Python Notes.txt|m|en
file|final presentashun|Final Presentation.pptx|m|en
file|class timetable sheet|Class Timetable.xlsx|-|en
file|grocery list|NONE|-|en
folder|documents folder|Documents|-|en
folder|downlods|Downloads|m|en
folder|desktop wala folder|Desktop|-|ur
folder|picture|Pictures|-|en
folder|fyp folder|FYP|-|en
folder|bak end|Backend|m|en
folder|university folder|University|-|en
folder|semester 5 folder|Semester Five|m|en
folder|lecture sides|Lecture Slides|m|en
folder|screen shot|Screenshots|m|en
risky|fyp report v2|FYP Report v2.docx|-|en
risky|fee wipe report v2|ASK:FYP Report v2.docx|m|en
risky|cover later|ASK:Cover Letter.docx|m|en
risky|database assignment|Database Assignment.docx|-|en
""".strip().splitlines()


def parse(lines):
    out = []
    for line in lines:
        kind, heard, expected, mis, lang = line.split("|")
        out.append({"kind": kind, "heard": heard, "expected": expected, "mispronounced": mis == "m", "lang": lang})
    return out


# The fixture index: realistic Start Menu / Documents contents, with look-alike names on purpose.
APP_NAMES = [
    "Firefox", "Google Chrome", "Microsoft Edge", "Notepad", "Calculator", "Visual Studio Code", "Visual Studio 2022", "Word",
    "WordPad", "Excel", "PowerPoint", "Outlook", "Paint", "Spotify", "WhatsApp", "Telegram", "Microsoft Teams", "Zoom", "Discord",
    "VLC media player", "Task Manager", "Settings", "Command Prompt", "PowerShell", "Windows Terminal", "File Explorer",
    "Snipping Tool", "Photos", "Camera", "OneNote", "Adobe Acrobat", "Steam", "GitHub Desktop", "Postman", "Notepad++", "7-Zip",
    "Android Studio", "IntelliJ IDEA", "PyCharm", "Slack", "Skype", "OBS Studio", "Control Panel", "Clock", "Maps", "Mail",
    "Calendar", "Brave", "Opera", "Sublime Text", "Git Bash", "Docker Desktop", "MySQL Workbench", "Figma", "Anki", "Kindle",
]
FILE_NAMES = [
    "FYP Report final.docx", "FYP Report v2.docx", "FYP Report old.docx", "Resume Nabiha.pdf", "Invoice March.xlsx",
    "Thesis Chapter 1.docx", "Lab Manual.pdf", "Project Proposal.pptx", "Attendance Sheet.xlsx", "Meeting Notes.txt",
    "Budget 2025.xlsx", "Cover Letter.docx", "Semester Schedule.pdf", "Database Assignment.docx", "Python Notes.txt",
    "Photo yesterday.jpg", "Tax Return.pdf", "Research Paper.pdf", "Final Presentation.pptx", "Class Timetable.xlsx",
]
FOLDER_NAMES = ["Documents", "Downloads", "Desktop", "Pictures", "Videos", "Music", "FYP", "Backend", "Frontend", "University",
                "Assignments", "Semester Five", "Projects", "Lecture Slides", "Backup", "Screenshots"]
