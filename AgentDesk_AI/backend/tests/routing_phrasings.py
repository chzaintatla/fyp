"""Step 1e: 60 phrasings per routed intent (20 English, 20 Roman Urdu, 10 Urdu, 10 Hindi).

Written by hand, NOT tuned to the regex fast path: the tests report how many the fast path catches and require that
whatever it does catch is routed to the right place. The rest must be routed by the planner's `intent` field.
Intents: memory_remember, memory_recall, memory_forget, summarise, web_answer, chat.
"""
from __future__ import annotations

U = "https://example.com/report"
P = "C:/Users/me/Documents/notes.pdf"

PHRASINGS: dict[str, dict[str, list[str]]] = {
    "memory_remember": {
        "en": [
            "remember my exam is on 12 December", "please remember that my sister's birthday is in May",
            "remember I am allergic to peanuts", "remember that the wifi password is on the router",
            "remember my locker number is 214", "hey remember I prefer tea over coffee",
            "remember that Ali's flat is on the third floor", "remember my dentist appointment is Friday at 4",
            "please remember my favourite colour is green", "remember I have a meeting with Sara tomorrow",
            "remember that I park on level two", "remember the project deadline is the 20th",
            "can you remember that my passport expires in 2028", "I want you to remember my blood group is B positive",
            "make a note: remember my gym day is Tuesday", "remember I take the 8:15 bus",
            "save this in memory: my professor is Dr Khan", "remember that my laptop charger is in the blue bag",
            "store a note that I am vegetarian", "keep in mind that I wake up at six",
        ],
        "roman": [
            "mera exam 12 December ko hai yaad rakho", "yaad rakho meri behan ki birthday May mein hai",
            "yaad rakhna mujhe moongfali se allergy hai", "mera locker number 214 hai yaad rakho",
            "yaad rakho ke Ali ka flat teesri manzil par hai", "meri dentist appointment Friday ko hai yaad rakhna",
            "yaad kar lo mera pasandida rang hara hai", "yaad rakho kal Sara se meeting hai",
            "mera project deadline 20 tareekh hai yaad rakho", "yaad rakho main chai pasand karta hoon",
            "mujhe yaad rakhna ke mera passport 2028 mein expire hoga", "yaad rakho meri gym Tuesday ko hoti hai",
            "yaad rakho mera blood group B positive hai", "meri professor ka naam Dr Khan hai yaad rakho",
            "yaad rakho main subah chhe baje uthta hoon", "mera laptop charger neeley bag mein hai yaad rakhna",
            "yaad rakho main vegetarian hoon", "yaad rakho meri bus 8:15 par aati hai",
            "ye baat yaad rakho ke mera bhai Lahore mein rehta hai", "mera shoe size 42 hai yaad rakho",
        ],
        "ur": [
            "یاد رکھو میرا امتحان بارہ دسمبر کو ہے", "یاد رکھو میری بہن کی سالگرہ مئی میں ہے",
            "یاد رکھنا مجھے مونگ پھلی سے الرجی ہے", "میرا لاکر نمبر دو سو چودہ ہے یاد رکھو",
            "یاد رکھو کہ کل ثارہ سے ملاقات ہے", "یاد کر لو میرا پسندیدہ رنگ ہرا ہے",
            "میرا پراجیکٹ بیس تاریخ کو جمع ہوگا یاد رکھو", "یاد رکھو میں چائے پسند کرتا ہوں",
            "یاد رکھو میرا بلڈ گروپ بی پازیٹو ہے", "میرے استاد کا نام ڈاکٹر خان ہے یاد رکھنا",
        ],
        "hi": [
            "याद रखो मेरा एग्ज़ाम बारह दिसंबर को है", "याद रखना मेरी बहन का जन्मदिन मई में है",
            "याद रखो मुझे मूंगफली से एलर्जी है", "मेरा लॉकर नंबर 214 है याद रखो",
            "याद रखो कल सारा से मीटिंग है", "मेरा पसंदीदा रंग हरा है याद रखना",
            "याद रखो मेरा प्रोजेक्ट बीस तारीख को जमा होगा", "मैं चाय पसंद करता हूँ यह याद रखो",
            "याद रखो मेरा ब्लड ग्रुप बी पॉज़िटिव है", "मेरे प्रोफेसर का नाम डॉ खान है याद रखना",
        ],
    },
    "memory_recall": {
        "en": [
            "what do you remember about me", "what do you know about me", "what have you remembered about me",
            "what did you save about me", "what do you remember", "show me my memories",
            "show my memory", "what have you learned about me so far", "tell me what you stored about me",
            "what do you have saved about me", "do you remember anything about me", "list what you remember about me",
            "what do you remember about me, please", "read out my memories", "what is stored in your memory about me",
            "what have you got on me", "which things did I ask you to remember", "what notes do you have of mine",
            "can you tell me what you know about me", "say what you remember about me",
        ],
        "roman": [
            "mere baare mein kya yaad hai", "mere bare mein kya pata hai", "tum mere baare mein kya jaante ho",
            "tum mere bare mein kya jante ho", "meray baray mein kya yaad hai", "mere baare mein kuch yaad hai",
            "mere baare mein kuch pata hai tumhein", "mujhe batao tumhein mere baare mein kya yaad hai",
            "mera memory dikhao", "meri yaaddashtein dikhao", "tum ne mere baare mein kya save kiya hai",
            "mere baare mein jo yaad rakha wo batao", "kya kya yaad hai mere baare mein", "meri saari yaad rakhi baatein sunao",
            "mere baare mein kya kya jaante ho tum", "jo maine yaad rakhwaya tha wo batao",
            "tumhein mere baare mein kitna pata hai", "mere memory mein kya kya hai", "mere baare mein kya sab pata hai",
            "batao mere baare mein tumhein kya yaad hai",
        ],
        "ur": [
            "تمہیں میرے بارے میں کیا یاد ہے", "میرے بارے میں کیا جانتے ہو", "میری یادداشتیں دکھاؤ",
            "تم نے میرے بارے میں کیا محفوظ کیا ہے", "میرے بارے میں جو یاد رکھا وہ بتاؤ",
            "میری میموری میں کیا ہے", "کیا تمہیں میرے بارے میں کچھ یاد ہے", "میرے بارے میں سب کچھ بتاؤ جو تمہیں پتا ہے",
            "جو میں نے یاد رکھوایا تھا وہ سناؤ", "میرے بارے میں تمہارے پاس کیا ہے",
        ],
        "hi": [
            "तुम्हें मेरे बारे में क्या याद है", "मेरे बारे में क्या जानते हो", "मेरी यादें दिखाओ",
            "तुमने मेरे बारे में क्या सेव किया है", "मेरे बारे में जो याद रखा वो बताओ",
            "मेरी मेमोरी में क्या है", "क्या तुम्हें मेरे बारे में कुछ याद है", "मेरे बारे में सब कुछ बताओ जो तुम्हें पता है",
            "जो मैंने याद रखवाया था वो सुनाओ", "मेरे बारे में तुम्हारे पास क्या है",
        ],
    },
    "memory_forget": {
        "en": [
            "forget my exam date", "please forget about my dentist appointment", "forget that my locker number is 214",
            "forget the wifi password note", "forget my gym day", "forget about the meeting with Sara",
            "please forget my passport note", "forget what I told you about my sister", "forget my favourite colour",
            "forget the bus time", "forget that I am vegetarian", "forget my blood group",
            "forget the project deadline", "forget about Ali's flat", "forget the note about the charger",
            "forget my shoe size", "forget my professor's name", "please forget what I said about tea",
            "forget the thing about parking", "forget my wake up time",
        ],
        "roman": [
            "bhool jao mera exam", "bhul jao meri dentist appointment", "bhool jao mera locker number",
            "wifi password wala note bhool jao", "bhool jao meri gym ka din", "Sara wali meeting bhool jao",
            "mera passport wala note bhool jao", "meri behan ke baare mein jo bataya tha wo bhool jao",
            "bhul jao mera pasandida rang", "bus ka time bhool jao", "bhool jao ke main vegetarian hoon",
            "mera blood group bhool jao", "project deadline bhool jao", "Ali ke flat wali baat bhool jao",
            "charger wala note bhool jao", "mera shoe size bhul jao", "professor ka naam bhool jao",
            "chai wali baat bhool jao", "parking wali baat bhool jao", "mera uthne ka time bhool jao",
        ],
        "ur": [
            "میرا امتحان بھول جاؤ", "میری ڈینٹسٹ اپائنٹمنٹ بھول جاؤ", "میرا لاکر نمبر بھول جاؤ",
            "وائی فائی پاس ورڈ والا نوٹ بھول جاؤ", "ثارہ والی میٹنگ بھول جاؤ", "میرا پسندیدہ رنگ بھول جاؤ",
            "بس کا وقت بھول جاؤ", "میرا بلڈ گروپ بھول جاؤ", "پراجیکٹ کی آخری تاریخ بھول جاؤ", "چائے والی بات بھول جاؤ",
        ],
        "hi": [
            "मेरा एग्ज़ाम भूल जाओ", "मेरी डेंटिस्ट अपॉइंटमेंट भूल जाओ", "मेरा लॉकर नंबर भूल जाओ",
            "वाईफाई पासवर्ड वाला नोट भूल जाओ", "सारा वाली मीटिंग भूल जाओ", "मेरा पसंदीदा रंग भूल जाओ",
            "बस का समय भूल जाओ", "मेरा ब्लड ग्रुप भूल जाओ", "प्रोजेक्ट की आखिरी तारीख भूल जाओ", "चाय वाली बात भूल जाओ",
        ],
    },
    "summarise": {
        "en": [
            f"summarise {U}", f"please summarise {U}", f"summarize the page {U}", f"summarise this page {U}",
            f"summary of {U}", f"summarise the file {P}", f"please summarise {P}", f"summarize the document {P}",
            f"summarise {U}/news", f"summarise {U}/2024/annual", f"summary of the file {P}",
            f"give me a summary of {U}", f"can you summarise {U}", f"I need a short summary of {P}",
            f"sum up the article at {U}", f"tl;dr of {U}", f"summarise {P} in two lines", f"what does {U} say, in brief",
            f"boil down {P} for me", f"shorten this page for me {U}",
        ],
        "roman": [
            f"summarise karo {U}", f"is page ka khulasa do {U}", f"khulasa {U}", f"mukhtasar karo {U}",
            f"{U} ka khulasa batao", f"{P} ka khulasa karo", f"is file ka summary do {P}", f"summarise kar do {P}",
            f"mukhtasar {P}", f"{U}/news ka khulasa", f"{U} ka summary sunao", f"is link ka mukhtasar batao {U}",
            f"{P} ko mukhtasar karke batao", f"{U} mein kya likha hai mukhtasar mein", f"summary of karo {U}",
            f"khulasa karo {P}", f"{P} ka mukhtasar do", f"{U}/2024 ka khulasa chahiye", f"ye page summarise kar do {U}",
            f"is document ka khulasa kar do {P}",
        ],
        "ur": [
            f"{U} کا خلاصہ کرو", f"اس صفحے کا خلاصہ بتاؤ {U}", f"{P} کا خلاصہ بتاؤ", f"{U} مختصر کرو",
            f"اس فائل کا خلاصہ دو {P}", f"{U}/news کا خلاصہ سناؤ", f"{P} کو مختصر کرکے بتاؤ", f"اس لنک کا خلاصہ کر دو {U}",
            f"{U} میں کیا لکھا ہے مختصراً بتاؤ", f"{P} کا خلاصہ چاہیے",
        ],
        "hi": [
            f"{U} का सारांश दो", f"इस पेज का सारांश बताओ {U}", f"{P} का सारांश बताओ", f"{U} को संक्षेप में बताओ",
            f"इस फ़ाइल का सारांश दो {P}", f"{U}/news का सारांश सुनाओ", f"{P} को छोटा करके बताओ", f"इस लिंक का सारांश कर दो {U}",
            f"{U} में क्या लिखा है संक्षेप में बताओ", f"{P} का सारांश चाहिए",
        ],
    },
    "web_answer": {
        "en": [
            "what is the weather in Karachi today", "latest news about AI", "who won the match yesterday",
            "how much is a dollar in rupees right now", "what is the price of gold today", "what is the exchange rate of euro",
            "give me the headlines", "what is the score of the cricket match", "what is the forecast for tomorrow in Lahore",
            "who won the election", "how much does a iphone cost right now", "what is the stock price of Apple",
            "what is the release date of the new Galaxy phone", "look up the best laptops this year",
            "search the web for scholarships in Germany", "google the opening hours of the national library",
            "what happened in the news this week", "latest updates on the budget", "current petrol price in Pakistan",
            "what is today's top story",
        ],
        "roman": [
            "aaj ka mausam kaisa hai", "taaza khabrein batao", "aaj ka headline kya hai", "dollar ka rate kya hai abhi",
            "sone ka rate kya hai aaj", "petrol ki qeemat kitni hai abhi", "Karachi ka mausam kaisa hai aaj",
            "kal Lahore ka mausam kaisa rahega", "taza khabar sunao", "cricket match ka score kya hai",
            "iphone kitne ka hai abhi", "euro ka rate kya hai", "aaj ka taaza news batao", "abhi ka dollar rate batao",
            "is hafte ki khabrein batao", "aaj ki top khabar kya hai", "bijli ka naya rate kya hai", "sona kitne ka hai aaj",
            "Islamabad ka mausam aaj", "Pakistan ki taaza khabrein",
        ],
        "ur": [
            "آج کا موسم کیسا ہے", "تازہ خبریں بتاؤ", "آج کی سرخیاں کیا ہیں", "ڈالر کا ریٹ کیا ہے ابھی",
            "سونے کا ریٹ کیا ہے آج", "کراچی کا موسم کیسا ہے", "کل لاہور کا موسم کیسا رہے گا", "تازہ خبر سناؤ",
            "کرکٹ میچ کا اسکور کیا ہے", "پاکستان کی تازہ خبریں",
        ],
        "hi": [
            "आज का मौसम कैसा है", "ताज़ा खबरें बताओ", "आज की सुर्खियां क्या हैं", "डॉलर का रेट क्या है अभी",
            "सोने का रेट क्या है आज", "दिल्ली का मौसम कैसा है", "कल मुंबई का मौसम कैसा रहेगा", "ताज़ा खबर सुनाओ",
            "क्रिकेट मैच का स्कोर क्या है", "भारत की ताज़ा खबरें",
        ],
    },
    "chat": {
        "en": [
            "hello", "hi there", "hey", "good morning", "good evening", "how are you", "thank you", "thanks a lot",
            "tell me a joke", "what is photosynthesis", "who wrote Hamlet", "why is the sky blue",
            "how do airplanes fly", "explain recursion in simple words", "what is the capital of France",
            "define inflation", "difference between a virus and a bacterium", "can you help me relax",
            "what is a prime number", "describe how a rainbow forms",
        ],
        "roman": [
            "salam", "assalam o alaikum", "shukriya", "aap kaise hain", "ek joke sunao", "photosynthesis kya hota hai",
            "Hamlet kisne likha", "aasman neela kyun hota hai", "jahaz kaise udta hai", "recursion asaan alfaz mein samjhao",
            "France ka dar-ul-hukumat kya hai", "inflation kya hoti hai", "virus aur bacteria mein kya farq hai",
            "prime number kya hota hai", "kya tum meri madad kar sakte ho", "ghazal kise kehte hain",
            "pani kyun behta hai", "dil kaise kaam karta hai", "paheli kya hoti hai", "kya tum wahan ho",
        ],
        "ur": [
            "سلام", "شکریہ", "آپ کیسے ہیں", "ایک لطیفہ سناؤ", "فوٹو سنتھیسز کیا ہوتا ہے", "ہیملیٹ کس نے لکھا",
            "آسمان نیلا کیوں ہوتا ہے", "ہوائی جہاز کیسے اڑتا ہے", "مہنگائی کیا ہوتی ہے", "کیا تم میری مدد کر سکتے ہو",
        ],
        "hi": [
            "नमस्ते", "धन्यवाद", "आप कैसे हैं", "एक चुटकुला सुनाओ", "प्रकाश संश्लेषण क्या होता है", "हैमलेट किसने लिखा",
            "आसमान नीला क्यों होता है", "हवाई जहाज कैसे उड़ता है", "महंगाई क्या होती है", "क्या तुम मेरी मदद कर सकते हो",
        ],
    },
}

EXPECTED_COUNTS = {"en": 20, "roman": 20, "ur": 10, "hi": 10}
