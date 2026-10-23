"""The evaluation set.

Each question names the sections that answer it (see evaluation/metrics.py
for the label format). Labels are section headings, not chunk ids, so they
survive re-chunking; they must be updated only if a document's headings
change. `python -m scripts.inspect_chunks pyramid-yogshala` lists the sections.

Kinds, because retrieval fails differently on each:

  semantic      the answer uses different words from the question
  keyword       the answer hinges on an exact term (a pose, an airport)
  programme     about one named course or retreat: its fees, dates, syllabus
  comparison    about two or more programmes; every side must be found
  follow_up     the programme was named in an earlier message, not this one
                (`history`). Retrieval sees only the current message today,
                so these measure the gap programme-aware retrieval closes.
  general       about the school rather than one programme (halls, Wi-Fi,
                laundry, food, travel), sometimes asked while a programme
                is in focus
  trap          a case where tracking the programme could go wrong: a
                number that is not a programme, a general question after a
                specific one, a switch to a newly named programme
  list          asks for a list or a count; the bot must not add
                plausible items or state a number the material does not
                give (the judge is told to check counts)
  language      asked in another language; answered in it, from the same
                English material
  unanswerable  nothing covers it; the right behaviour is to say so

`programme` names the programme a question is about ("" for general or
undecided), so results can be read per programme.

Small and hand-written. Grow it from real visitor questions
(`python -m scripts.unanswered`), not synthetic ones.
"""

from dataclasses import dataclass, field

# Documents
OLD = "course-200hr |"            # the 200-hour Naturopathy course document
WEB = "pyramid-yogshala-website |"


def old(section: str) -> str:
    return f"course-200hr | {section}"


def web(section: str) -> str:
    return f"pyramid-yogshala-website | {section}"


# School-wide sections. Being moved into their own document (school-general);
# labels list both places so one question set measures before and after.
def gen(section: str) -> str:
    return f"school-general | {section}"


# Programme sections in the website document
P100 = web("100-Hour Yoga Teacher Training (Rishikesh)")
PKUN = web("200-Hour Traditional Hatha, Kundalini and Ayurveda TTC (Rishikesh)")
PSPA = web("200-Hour Hatha, Ashtanga, Kundalini and Ayurveda TTC in Spanish (Rishikesh)")
PHAV = web("200-Hour Multi-Style TTC, Havelock Island (Andaman)")
PMYS = web("200-Hour Multi-Style TTC, Mysore")
P300 = web("300-Hour Ashtanga Vinyasa Flow and Ayurveda TTC (Rishikesh)")
R4 = web("4-Day Yoga Retreat (Rishikesh)")
R7 = web("7-Day Yoga Retreat (Rishikesh)")
R11 = web("11-Day Yoga and Meditation Retreat (Havelock Island)")
R14 = web("14-Day Yoga Retreat (Rishikesh)")
R28 = web("28-Day Yoga, Pranayama and Meditation Retreat (Rishikesh)")

# Each programme's overview paragraph only, without its sub-sections.
def overview(programme: str) -> str:
    return "=" + programme


def sub(programme: str, section: str) -> str:
    return f"{programme} > {section}"


INCLUDED = "What is included and not included"


@dataclass
class EvalQuestion:
    question: str
    # Groups of interchangeable sections; every group must be found.
    answers_in: list[list[str]] = field(default_factory=list)
    kind: str = "semantic"
    programme: str = ""
    # Earlier visitor messages in the same conversation, for follow-ups.
    history: list[str] = field(default_factory=list)


Q = EvalQuestion

QUESTIONS: list[EvalQuestion] = [
    # --- Semantic: the wording differs from the source text ---
    Q("What will I learn about the human body on the 200-hour course?",
      [[old("Curriculum > Yoga Anatomy")]], "semantic", "200-naturopathy"),
    Q("How do you teach breathing techniques?",
      [[old("Curriculum > Pranayama"), sub(P300, "Curriculum"), sub(P100, "Curriculum")]],
      "semantic"),
    Q("What is the food like?",
      [[old("Food"), web("About Pyramid Yogshala"), gen("Food and diets")]], "semantic"),
    Q("Where will I be sleeping?",
      [[old("Accommodation"), web("About Pyramid Yogshala"), gen("Rooms")]], "semantic"),
    Q("What does a typical day look like on the 200-hour course?",
      [[old("Daily Schedule")]], "semantic", "200-naturopathy"),
    Q("Will I be able to teach after the 200-hour course?",
      [[old("Certification"), web("Certification"), gen("Certification and Yoga Alliance")]], "semantic", "200-naturopathy"),
    Q("I have never done yoga seriously. Is the 200-hour course too advanced?",
      [[old("Who the course is for")]], "semantic", "200-naturopathy"),
    Q("What extra costs should I budget for on the 200-hour course?",
      [[old("What is Not Included")]], "semantic", "200-naturopathy"),
    Q("Do you cover traditional Indian medicine?",
      [[old("Curriculum > Naturopathy"), old("Curriculum > Ayurveda"),
        sub(PKUN, "Curriculum"), sub(P300, "Curriculum")]], "semantic"),
    Q("What is there to do in my free time?",
      [[old("Activities Nearby"), gen("Things to do nearby")]], "semantic"),
    Q("How long has the school been running?",
      [[web("About Pyramid Yogshala"), gen("About the school")]], "semantic"),
    Q("Who teaches anatomy?",
      [[web("Our teachers"), gen("Our teachers")]], "semantic"),

    # --- Keyword: hinges on an exact term ---
    Q("Is the 200-hour course RYT 200 certified?",
      [[old("Certification"), web("Certification"), gen("Certification and Yoga Alliance")]], "keyword", "200-naturopathy"),
    Q("Do you teach Shatkarma?",
      [[old("Curriculum > Kriya and Shatkarma"), sub(P100, "Curriculum"), sub(P300, "Curriculum")]],
      "keyword"),
    Q("Is Kapotasana part of the syllabus?",
      [[old("Curriculum > Ashtanga Vinyasa")]], "keyword"),
    Q("Which airport is Jolly Grant?",
      [[old("Getting There"), gen("Getting to Rishikesh")]], "keyword"),
    Q("Do I get a Rudraksha mala?",
      [[old("What is Included"), gen("Yoga equipment and what is provided"),
        sub(P100, INCLUDED), sub(PKUN, INCLUDED),
        sub(PMYS, INCLUDED), sub(P300, INCLUDED)]], "keyword"),
    Q("Can I arrive via Haridwar?",
      [[old("Getting There > Arriving by train"), gen("Getting to Rishikesh > By train")]],
      "keyword"),
    Q("How do I reach you on WhatsApp?",
      [[web("Contact"), old("Contact"), gen("Contact")]], "keyword"),

    # --- Programme: about one named course or retreat ---
    Q("How much is the 100-hour course?", [[sub(P100, "Fees")]], "programme", "100"),
    Q("What does the 100-hour course cover?", [[sub(P100, "Curriculum")]], "programme", "100"),
    Q("What subjects are covered in the 300-hour course?",
      [[sub(P300, "Curriculum")]], "programme", "300"),
    Q("When does the 300-hour course start?", [[sub(P300, "Dates")]], "programme", "300"),
    Q("What does the 300-hour TTC cost with a private room?",
      [[sub(P300, "Fees")]], "programme", "300"),
    Q("Can I join the 300-hour course after a 200-hour from another school?",
      [[overview(P300), web("Certification"), gen("Certification and Yoga Alliance")]], "programme", "300"),
    Q("What do I learn in the Kundalini teacher training?",
      [[sub(PKUN, "Curriculum")]], "programme", "200-kundalini"),
    Q("How much is the Kundalini teacher training?",
      [[sub(PKUN, "Fees")]], "programme", "200-kundalini"),
    Q("Do you have a teacher training taught in Spanish?",
      [[overview(PSPA), web("Programmes at a glance")]], "programme", "200-spanish"),
    Q("When is the next teacher training on Havelock Island?",
      [[sub(PHAV, "Dates")]], "programme", "200-havelock"),
    Q("How do I get to Havelock Island for the teacher training?",
      [[sub(PHAV, "Getting there"), sub(R11, "Getting there")]], "programme", "200-havelock"),
    Q("Where is the Mysore teacher training held?",
      [[overview(PMYS)]], "programme", "200-mysore"),
    Q("How far is the Mysore ashram from Bengaluru airport?",
      [[sub(PMYS, "Getting there")]], "programme", "200-mysore"),
    Q("How much does the 4-day retreat cost?", [[sub(R4, "Fees")]], "programme", "retreat-4"),
    Q("What is the daily schedule on the 7-day retreat?",
      [[sub(R7, "Daily schedule")]], "programme", "retreat-7"),
    Q("What is included in the 14-day retreat?", [[sub(R14, INCLUDED)]], "programme", "retreat-14"),
    Q("How many massages do I get on the 14-day retreat?",
      [[sub(R14, INCLUDED)]], "programme", "retreat-14"),
    Q("What should I pack for the Havelock retreat?",
      [[sub(R11, "What to pack")]], "programme", "retreat-11"),
    Q("What is the deposit for the 11-day Havelock retreat?",
      [[sub(R11, "Deposit and payment")]], "programme", "retreat-11"),

    # --- General ---
    Q("Who are the teachers?", [[web("Our teachers"), gen("Our teachers")]], "semantic"),
    Q("What courses do you offer?", [[web("Programmes at a glance")]], "semantic"),
    Q("What is your refund policy?",
      [[web("Booking, deposit and rescheduling"), old("Booking and Cancellation"),
        gen("Booking, deposit and payment")]], "semantic"),

    # --- Comparison: every side must be found ---
    Q("What is the difference between the 4-day and the 7-day retreat?",
      [[R4], [R7]], "comparison"),
    Q("Should I do the 200-hour in Rishikesh or on Havelock Island?",
      # The overview lists both Rishikesh 200-hour courses with place and
      # length, which is what this choice needs.
      [[OLD, PKUN, web("Programmes at a glance")], [PHAV]], "comparison"),
    Q("Which is cheaper, the 14-day or the 28-day retreat?",
      [[sub(R14, "Fees")], [sub(R28, "Fees")]], "comparison"),
    Q("I'm a beginner, which programme suits me?",
      [[web("Programmes at a glance")]], "comparison"),
    Q("Can I do the 100-hour now and the 200-hour later?",
      [[overview(P100)]], "comparison"),

    # --- Follow-up: the programme was named earlier, not in this message ---
    Q("How much does it cost?", [[sub(P300, "Fees")]], "follow_up", "300",
      history=["Tell me about the 300-hour course"]),
    Q("How do I get there?", [[sub(PMYS, "Getting there")]], "follow_up", "200-mysore",
      history=["I'm interested in the Mysore teacher training"]),
    Q("What's included?", [[sub(R7, INCLUDED)]], "follow_up", "retreat-7",
      history=["What is the 7-day retreat like?"]),
    Q("When does it start?", [[sub(PHAV, "Dates")]], "follow_up", "200-havelock",
      history=["Tell me about the teacher training on Havelock Island"]),
    Q("What will I learn?", [[sub(P100, "Curriculum")]], "follow_up", "100",
      history=["I want to do the 100-hour course"]),

    # --- Traps: where tracking the programme could go wrong ---
    # "7 days" is a length, not the 7-day retreat.
    Q("I can only come for 7 days. Which teacher training fits me?",
      [[web("Programmes at a glance")]], "trap"),
    # A general question after a specific one must not stay on the 300-hour.
    Q("What retreats do you have?", [[web("Programmes at a glance")]], "trap",
      history=["Tell me about the 300-hour course"]),
    # Naming a new programme switches to it.
    Q("And what's included in the 7-day retreat?", [[sub(R7, INCLUDED)]], "trap",
      "retreat-7", history=["Tell me about the 300-hour course"]),
    # Food on the Mysore course; the Rishikesh food section would be wrong.
    Q("What is the food like?", [[sub(PMYS, INCLUDED)]], "trap", "200-mysore",
      history=["I'm interested in the Mysore teacher training"]),
    # A comparison where the second programme is only implied.
    Q("How does it compare with the 7-day one?", [[R4], [R7]], "trap",
      history=["Tell me about the 4-day retreat"]),

    # --- General: about the school, not one programme ---
    # The weak side in real chats (2026-10-16). Some are asked while a
    # programme is in focus, which is where they failed: the focus took
    # the slots the school-wide section needed.
    Q("How many yoga halls do you have?",
      [[web("About Pyramid Yogshala > Campus in Rishikesh"), gen("Yoga halls and class sizes")]], "general"),
    Q("How many halls do you have?",
      [[web("About Pyramid Yogshala > Campus in Rishikesh"), gen("Yoga halls and class sizes")]], "general",
      history=["Do you teach the 300-hour course?"]),
    Q("How many students are in one batch?",
      [[web("About Pyramid Yogshala > Campus in Rishikesh"), gen("Yoga halls and class sizes")]], "general"),
    Q("Is there Wi-Fi?",
      [[web("About Pyramid Yogshala > Rooms, Wi-Fi, laundry and amenities in Rishikesh"), gen("Wi-Fi")]], "general",
      history=["Do you teach the 300-hour course?"]),
    Q("Can I do my laundry there?",
      [[web("About Pyramid Yogshala > Rooms, Wi-Fi, laundry and amenities in Rishikesh"), old("Accommodation"), gen("Laundry")]], "general",
      history=["Tell me about the 300-hour course"]),
    Q("Do the rooms have air conditioning?",
      [[web("About Pyramid Yogshala > Rooms, Wi-Fi, laundry and amenities in Rishikesh"), gen("Rooms")]], "general"),
    Q("Is the food vegetarian?",
      [[old("Food"), gen("Food and diets")]], "general",
      history=["What does the 300-hour course cost?"]),
    Q("How do I get to Rishikesh from Delhi?",
      [[old("Getting There"), gen("Getting to Rishikesh")]], "general"),
    Q("Where exactly is the school?",
      [[web("About Pyramid Yogshala > Campus in Rishikesh"), old("Facilities and Location"), gen("Campus and location")]], "general"),
    Q("Is the owner a yoga teacher?",
      [[web("Our teachers"), gen("Our teachers")]], "general",
      history=["Tell me about the 300-hour course"]),
    Q("Do I need to bring my own yoga mat?",
      [[old("What is Included"), gen("Yoga equipment and what is provided")]], "general"),
    Q("How do I pay the deposit?",
      [[web("Booking, deposit and rescheduling"), old("Booking and Cancellation"),
        gen("Booking, deposit and payment")]], "general"),
    # Too broad for a focus ("200 hour" names five courses): the overview
    # table is what keeps the bot from giving one course's length to all.
    Q("What about the 200-hour courses?",
      [[web("Programmes at a glance")]], "general",
      history=["Do you teach the 300-hour course?"]),

    # --- List: lists and counts, where a model pads or counts ---
    Q("List everything included in the 14-day retreat.",
      [[sub(R14, INCLUDED)]], "list", "retreat-14"),
    Q("Give me a full list of every course and retreat you run.",
      [[web("Programmes at a glance")]], "list"),
    Q("How many teachers do you have?",
      [[web("Our teachers"), gen("Our teachers")]], "list"),
    Q("Please list all the things not included in the 300-hour course fee.",
      [[sub(P300, INCLUDED)]], "list", "300"),
    Q("Put the curriculum in a bullet list for me.",
      [[sub(P100, "Curriculum")]], "list", "100",
      history=["Tell me about the 100-hour course"]),

    # --- Language: the same material, asked in another language ---
    Q("¿Cuánto cuesta el curso de 300 horas?",
      [[sub(P300, "Fees")]], "language", "300"),
    Q("14 दिन के रिट्रीट में क्या शामिल है?",
      [[sub(R14, INCLUDED)]], "language", "retreat-14"),
    Q("Wann beginnt der 100-Stunden-Kurs?",
      [[sub(P100, "Dates")]], "language", "100"),

    # --- Unanswerable: nothing covers these ---
    Q("Can I bring my dog?", kind="unanswerable"),
    Q("Is there an exam at the end of the course?", kind="unanswerable"),
    Q("Can I get an internship here?", kind="unanswerable"),
    # Near misses from a real chat (2026-10-15): the material covers a
    # related case, not this one.
    Q("Can I reschedule my course midway?", kind="unanswerable"),
    Q("Can a friend who is not doing the course stay with me?", kind="unanswerable"),
]


def question_set_id() -> str:
    """A short fingerprint of the questions and their labels.

    Saved with each run, so scores are only compared between runs that
    asked the same questions.
    """
    import hashlib

    text = repr([(q.question, q.answers_in, q.kind, q.history) for q in QUESTIONS])
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]
