"""Write the four journey diagrams (docs/diagrams/j1.html .. j4.html). Then run render.sh.

Replies are trimmed from the Mac journey run of 12 November 2026 on the Class 6 to 8
index (tests/journeys/report_mac_en.md). Update them here after a new run.
"""
import html
import os

HERE = os.path.dirname(os.path.abspath(__file__))

ROUTE = {
    "tap": "Tap to talk", "asr": "Speech to text", "cmd": "Spoken commands", "mt": "To English",
    "safety": "Safety check", "core": "Textbook core", "questions": "Textbook questions",
    "example": "Example", "judge": "Judge the answer", "memory": "Memory", "out": "Speak and show",
    "rewrite": "Follow-up rewrite",
}

JOURNEYS = [
    {
        "id": "j1", "cls": "j1", "title": "Journey 1: Teacher prep",
        "who": "A teacher, before class. Says what the class covered, asks for tomorrow's lesson plan, clears up a doubt, and takes notes.",
        "status": [("pass", "Mac, English: PASS, 3 of 3"), ("pending", "Hindi: waits for the Jetson")],
        "route": ["tap", "asr", "cmd", "mt", "safety", "memory", "core", "questions", "example", "out"],
        "turns": [
            ("Teacher", "Yesterday we covered nutrition in plants.",
             ["Lesson plan mode notes what was covered, in memory only"],
             "Noted. Ask me for a lesson plan for the next class when you are ready.", False),
            ("Teacher", "Give me a lesson plan on photosynthesis for tomorrow's class.",
             ["Recap built from the teacher's own words",
              "Key ideas: the textbook core on \"Explain photosynthesis\"",
              "A simpler version for students who need more help",
              "A check question and a stretch question, quoted from the textbook",
              "One checked example from village life"],
             "Here is a lesson plan on photosynthesis, for a class at different levels. Start with a short recap of "
             "nutrition in plants … Photosynthesis is a process where plants use sunlight to turn water and carbon "
             "dioxide into food … For example, during the day, crops like rice in a village field use sunlight …", False),
            ("Teacher", "Why do leaves need it to make food?",
             ["Follow-up rewritten from the last 3 turns: \"it\" is chlorophyll", "Textbook core, then checked"],
             "Leaves need chlorophyll to capture sunlight, which is essential for photosynthesis.", False),
            ("Teacher", "Slower please.",
             ["Spoken command: replays the saved audio at 80% speed, with a pause after each sentence"],
             "(the last answer again, slowly, for taking notes)", False),
        ],
        "notes": [
            ("Also works", "\"Lesson plan for weak students\" or \"for students who are ahead\" gives one level. "
             "No topic named: \"Which topic is the lesson on?\". A plan takes about 13 to 30 s on the Mac, first sentence in about 3 s."),
            ("Left, 1 dev day", "Run it in Hindi on the Jetson and fix the wording. No activities: almost none in the "
             "scanned textbooks can be quoted cleanly."),
        ],
    },
    {
        "id": "j2", "cls": "j2", "title": "Journey 2: Multi-grade revision",
        "who": "A teacher in a room with two or three classes. Revises what one class just learnt, then the same topic with another class.",
        "status": [("fail", "Mac, English: FAIL, 0 of 3"), ("pending", "Hindi: waits for the Jetson")],
        "route": ["tap", "asr", "cmd", "mt", "safety", "memory", "core", "example", "questions", "out"],
        "turns": [
            ("Teacher", "I just taught class 7 science, photosynthesis. Please revise it with the students.",
             ["Revision mode reads the class and the topic",
              "Textbook core searches Class 7 books first, then shared books, then all",
              "One checked example", "Recall questions from Class 7 pages"],
             "Here is a quick revision of photosynthesis for class 7. Photosynthesis is a process where plants make "
             "their own food using sunlight, water, and carbon dioxide … For example, imagine a field of wheat plants "
             "in the morning … What are the two products formed during the process of photosynthesis?", False),
            ("Teacher", "Now do the same for class 8.",
             ["Same topic from memory, Class 8 books first",
              "No clean Class 8 question, so students explain it in their own words"],
             "Here is a quick revision of photosynthesis for class 8. Plants use sunlight, water, and carbon dioxide to "
             "make their own food … Now ask two or three students to explain photosynthesis in their own words.", False),
            ("Teacher", "I have just taught class 6 science, light and shadows. Revise it with them.",
             ["Textbook core: no sentence of the answer survived the check"],
             "I can't answer that from the approved DIKSHA textbook content on this device.", True),
        ],
        "notes": [
            ("Why it fails", "The Class 8 revision shares 68% of its words with the Class 7 one (the test allows 60%). "
             "The acids revision came with no example. \"Light and shadows\" for Class 6 is refused."),
            ("Left, 1 dev day", "Make sure every revision has an example, keep two-word topics whole, make the second "
             "class's revision differ, then run it in Hindi on the Jetson."),
        ],
    },
    {
        "id": "j3", "cls": "j3", "title": "Journey 3: Student group quiz",
        "who": "A small group of students, with a teacher present. They pick a topic, answer out loud, and hear whether they were right.",
        "status": [("partial", "Mac, English: PARTIAL, 2 of 3"), ("pending", "Spoken answers: Jetson")],
        "route": ["tap", "asr", "cmd", "mt", "safety", "memory", "questions", "judge", "core", "out"],
        "turns": [
            ("Students", "We are class 7 science. Quiz us on photosynthesis.",
             ["Quiz mode picks questions quoted from textbook pages on the topic",
              "The answer model never writes a question"],
             "Here is a quiz on photosynthesis, with questions from the textbook. Say stop the quiz to finish early. "
             "First question. What are the two products formed during the process of photosynthesis?", False),
            ("Student", "Glucose and oxygen.",
             ["Safety check, like any request", "Judged against the passages, then against the device's own checked answer"],
             "That's right. Next question. How are photosynthesis and respiration different?", False),
            ("Student", "Plants get bigger.",
             ["Not supported, so the checked answer becomes the feedback"],
             "Not quite. Here is what the textbook says. Photosynthesis is the process by which plants convert light "
             "energy into chemical energy. The main products are glucose (a type of sugar) and oxygen.", False),
            ("Students", "Stop the quiz.", ["Quiz cleared from memory. No scores are kept"],
             "Okay, the quiz is over.", False),
        ],
        "notes": [
            ("Why partial", "In one script, \"Xylem and phloem\" was the planned answer, but the quiz drew \"Why is "
             "transport of materials necessary?\", so \"not quite\" was fair. On questions it was not tuned on, it judges "
             "16 of 24 right answers right and 23 of 26 wrong answers wrong."),
            ("Left, 1 dev day", "Spoken answers on the Jetson, scripts that follow the question drawn, and the "
             "safeguarding review before students use it without a teacher."),
        ],
    },
    {
        "id": "j4", "cls": "j4", "title": "Journey 4: Doubts in Hindi or English",
        "who": "A student asks in their home language, hears the answer in the same language, and asks a follow-up.",
        "status": [("partial", "Mac, English: PARTIAL"), ("pending", "Hindi: waits for the Jetson")],
        "route": ["tap", "asr", "cmd", "mt", "safety", "rewrite", "core", "example", "out"],
        "turns": [
            ("Student", "प्रकाश संश्लेषण क्या है? मुझे समझाइए।",
             ["Language ID: Hindi, so Bhashini turns speech into text",
              "Glossary, then Bhashini translates it to English",
              "Textbook core, then each kept sentence is translated back to Hindi and spoken"],
             "(the answer in Hindi, spoken by Flite and shown on the screen)", False),
            ("Student", "Why do plants need sunlight for it?",
             ["Rewritten from the last 3 turns: \"What is the role of sunlight in photosynthesis?\"",
              "Textbook core, then checked"],
             "Sunlight is crucial for photosynthesis. Chlorophyll, a green pigment in plants, captures sunlight, "
             "converting it into chemical energy to produce food for the plant.", False),
            ("Student", "मुझे हीट एंडऑक्शन के बारे में बताएं",
             ["Heard like this on the Jetson, 12 November: \"heat endaction\" is in no textbook",
              "Since the fix (Mac so far), the gate offers a textbook word spelt like it that goes with \"heat\""],
             "Sorry, I did not catch that. Did you mean heat conduction? Please ask again. (in Hindi)", False),
        ],
        "notes": [
            ("Why partial", "\"Please explain photosynthesis\" kept only one weak sentence, \"This process helps plants "
             "grow and makes oxygen for us to breathe\". \"Can you give some examples of acids?\" was refused."),
            ("Left, 1 dev day", "Set the language thresholds from real Hindi, English and Hinglish recordings. "
             "Children's voices are untested. Students use it with a teacher until the safeguarding review."),
        ],
    },
]

PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><link rel="stylesheet" href="style.css">
<style>
  .route {{ display: flex; flex-wrap: wrap; align-items: center; gap: 4px; margin: 4px 0 20px; font-size: 13px; }}
  .route span.step {{ border: 1.5px solid var(--box-line); background: var(--box); border-radius: 12px; padding: 3px 10px; }}
  .route span.step.safety {{ border-color: var(--red); color: var(--red); background: var(--red-bg); }}
  .route span.step.mode {{ font-weight: bold; }}
  .route span.to {{ color: var(--muted); }}
  .said {{ border-color: var(--{cls}); }}
  .said small {{ color: var(--{cls}); }}
  .turn {{ grid-template-columns: 290px 54px minmax(0,1fr) 54px 360px; }}
</style></head>
<body><div class="page">
<div class="head2"><h1 class="{cls}" style="margin:0">{title}</h1>{status}</div>
<p class="sub">{who}</p>
<div class="route"><b style="margin-right:6px">Route:</b>{route}</div>
{turns}
<div class="cols2" style="margin-top:8px">{notes}</div>
<p class="sub" style="font-size:13px;margin:14px 0 0">Replies trimmed from the Mac journey run, 12 November 2026, Class 6 to 8 index, Qwen2.5-1.5B. See tests/journeys/report_mac_en.md.</p>
</div></body></html>
"""


def esc(text):
    return html.escape(text, quote=False)


def build(j):
    status = "".join(f'<span class="status {k}">{esc(t)}</span>' for k, t in j["status"])
    route = []
    for i, step in enumerate(j["route"]):
        extra = " safety" if step == "safety" else ""
        route.append(f'<span class="step{extra}">{esc(ROUTE[step])}</span>')
        if i < len(j["route"]) - 1:
            route.append('<span class="to">›</span>')
    turns = []
    for who, said, steps, reply, refused in j["turns"]:
        items = "".join(f"<li>{esc(s)}</li>" for s in steps)
        turns.append(
            f'<div class="turn"><div class="said"><small>{esc(who)}</small>{esc(said)}</div>'
            f'<div class="mini"></div><div class="steps"><ol>{items}</ol></div><div class="mini"></div>'
            f'<div class="reply{" refused" if refused else ""}"><small>The Specialist</small>{esc(reply)}</div></div>')
    notes = "".join(f'<div class="note"><b>{esc(h)}</b><br>{esc(t)}</div>' for h, t in j["notes"])
    return PAGE.format(cls=j["cls"], title=esc(j["title"]), status=status, who=esc(j["who"]),
                       route="".join(route), turns="\n".join(turns), notes=notes)


if __name__ == "__main__":
    for j in JOURNEYS:
        with open(os.path.join(HERE, j["id"] + ".html"), "w", encoding="utf-8") as f:
            f.write(build(j))
        print("wrote", j["id"] + ".html")
