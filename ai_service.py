import os
import json
import time
import secrets

from dotenv import load_dotenv
from sarvamai import SarvamAI


# ============================================================
# CONFIGURATION
# ============================================================

load_dotenv()

SARVAM_API_KEY = os.getenv("SARVAM_API_KEY")

MODEL = "sarvam-105b"

if not SARVAM_API_KEY:
    raise RuntimeError(
        "SARVAM_API_KEY is not configured. "
        "Add SARVAM_API_KEY to your .env file."
    )

client = SarvamAI(
    api_subscription_key=SARVAM_API_KEY
)


# ============================================================
# RECENT QUESTIONS
# Prevent immediate repetition
# ============================================================

RECENT_QUESTIONS = {}

MAX_REMEMBERED_QUESTIONS = 45


# ============================================================
# EXTRACT CONTENT
# ============================================================

def extract_content(message):

    if message is None:
        return ""

    content = getattr(
        message,
        "content",
        None
    )

    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):

        parts = []

        for item in content:

            if isinstance(item, dict):

                value = (
                    item.get("text")
                    or item.get("content")
                    or ""
                )

                if value:
                    parts.append(
                        str(value)
                    )

            else:

                parts.append(
                    str(item)
                )

        return "".join(parts).strip()

    for field in [
        "text",
        "output_text",
        "response",
        "answer",
        "final_content"
    ]:

        value = getattr(
            message,
            field,
            None
        )

        if value:

            return str(value).strip()

    # IMPORTANT:
    # reasoning_content is NOT final answer.
    # Never return it as normal content.

    return ""


# ============================================================
# BASIC AI CALL
# ============================================================

def call_ai(
    prompt,
    retries=3,
    json_mode=False
):

    last_error = None

    for attempt in range(retries):

        try:

            kwargs = {
                "model": MODEL,
                "messages": [
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],

                # Disable reasoning so that the model
                # has enough output budget for the answer.
                "reasoning_effort": None,

                # Keep responses within normal plan limits.
                "max_tokens": 4096,

                "temperature": 0.7
            }

            # JSON mode only when requested.
            if json_mode:

                kwargs["request_options"] = {
                    "additional_body_parameters": {
                        "response_format": {
                            "type": "json_object"
                        }
                    }
                }

            response = client.chat.completions(
                **kwargs
            )

            if not response:

                raise ValueError(
                    "Sarvam returned an empty response."
                )

            choices = getattr(
                response,
                "choices",
                None
            )

            if not choices:

                raise ValueError(
                    "Sarvam returned no choices."
                )

            message = getattr(
                choices[0],
                "message",
                None
            )

            content = extract_content(
                message
            )

            if content:

                return content

            raise ValueError(
                "Sarvam returned no final content."
            )

        except Exception as e:

            last_error = e

            print(
                f"Sarvam attempt "
                f"{attempt + 1}/{retries} failed: {e}"
            )

            if attempt < retries - 1:

                time.sleep(2)

    raise ValueError(
        f"AI request failed after "
        f"{retries} attempts: {last_error}"
    )


# ============================================================
# JSON CALL
# ============================================================

def call_json(
    prompt,
    retries=3
):

    last_error = None

    for attempt in range(retries):

        try:

            text = call_ai(
                prompt,
                retries=2,
                json_mode=True
            )

            if not text:

                raise ValueError(
                    "AI returned empty JSON content."
                )

            return json.loads(
                text
            )

        except Exception as e:

            last_error = e

            print(
                f"JSON attempt "
                f"{attempt + 1}/{retries} failed: {e}"
            )

            if attempt < retries - 1:

                time.sleep(2)

    raise ValueError(
        f"AI JSON request failed: {last_error}"
    )


# ============================================================
# CHAT
# ============================================================

def chat_reply(
    history,
    role="",
    course=""
):

    transcript = "\n".join(
        f"{m.get('role', 'user').upper()}: "
        f"{m.get('content', '')}"
        for m in history[-20:]
    )

    prompt = f"""
You are PS42, an AI learning and career assistant.

Target role:
{role or "Not specified"}

Course:
{course or "Not specified"}

Conversation:
{transcript}

Answer the user's latest message directly.

Support:
Java, Python, C, C++, JavaScript, HTML, CSS, SQL,
DSA, DBMS, Operating Systems, Computer Networks,
Software Engineering, AI/ML, Cloud, Web Development,
Aptitude, interviews and career preparation.

Do not assume Python unless the user asks about Python.

Keep the answer clear, practical and concise.

Do not return JSON.
"""

    return call_ai(
        prompt,
        retries=3,
        json_mode=False
    )


# ============================================================
# VALIDATE ONE QUESTION
# ============================================================

def validate_question(
    question,
    index
):

    if not isinstance(
        question,
        dict
    ):

        raise ValueError(
            f"Question {index} is invalid."
        )

    text = str(
        question.get(
            "question",
            ""
        )
    ).strip()

    if not text:

        raise ValueError(
            f"Question {index} has no text."
        )

    options = question.get(
        "options"
    )

    if not isinstance(
        options,
        list
    ):

        raise ValueError(
            f"Question {index} options are invalid."
        )

    if len(options) != 4:

        raise ValueError(
            f"Question {index} must have exactly 4 options."
        )

    options = [
        str(option).strip()
        for option in options
    ]

    if len(set(options)) != 4:

        raise ValueError(
            f"Question {index} has duplicate options."
        )

    answer = str(
        question.get(
            "answer",
            ""
        )
    ).strip()

    if answer not in options:

        raise ValueError(
            f"Question {index} answer does not "
            "match any option."
        )

    skill = str(
        question.get(
            "skill",
            "General"
        )
    ).strip()

    if not skill:

        skill = "General"

    return {
        "question": text,
        "options": options,
        "answer": answer,
        "skill": skill
    }


# ============================================================
# VALIDATE EXACTLY 15
# ============================================================

def validate_assessment(data):

    if not isinstance(
        data,
        dict
    ):

        raise ValueError(
            "Assessment response is not a JSON object."
        )

    questions = data.get(
        "questions"
    )

    if not isinstance(
        questions,
        list
    ):

        raise ValueError(
            "Assessment questions are missing."
        )

    # EXACTLY 15
    if len(questions) != 15:

        raise ValueError(
            f"Expected exactly 15 questions, "
            f"but AI returned {len(questions)}."
        )

    validated = []

    seen = set()

    for index, question in enumerate(
        questions,
        start=1
    ):

        item = validate_question(
            question,
            index
        )

        key = item["question"].lower()

        if key in seen:

            raise ValueError(
                f"Duplicate question detected at "
                f"question {index}."
            )

        seen.add(key)

        validated.append(
            item
        )

    return {
        "skills": data.get(
            "skills",
            []
        ),
        "questions": validated
    }


# ============================================================
# GENERATE ASSESSMENT
# ============================================================

def generate_assessment(
    role,
    course
):

    role = str(role).strip()
    course = str(course).strip()

    # Unique generation ID.
    # This helps same role/course produce a fresh set.
    generation_id = secrets.token_hex(8)

    key = (
        role.lower(),
        course.lower()
    )

    previous_questions = RECENT_QUESTIONS.get(
        key,
        []
    )

    previous_text = ""

    if previous_questions:

        previous_text = "\n".join(
            f"- {q}"
            for q in previous_questions[-30:]
        )

    prompt = f"""
You are PS42's professional technical assessment engine.

TARGET ROLE:
{role}

COURSE / SKILL:
{course}

GENERATION ID:
{generation_id}

Create ONE fresh technical assessment.

============================================================
STRICT QUESTION COUNT
============================================================

Generate EXACTLY 15 questions.

Not 14.
Not 16.
Not more than 15.
Not fewer than 15.

The "questions" array MUST contain exactly 15 objects.

============================================================
QUESTION QUALITY
============================================================

The assessment should feel like a realistic developer
screening/interview assessment.

Do NOT make all questions simple definitions.

Use a mixture of:

- practical scenarios
- code/output questions
- debugging
- problem solving
- concept application
- best-practice decisions
- "what happens if..." situations
- interview-style reasoning

For programming courses, use short code snippets
when useful.

Avoid repeating the same wording pattern.

Do not make every question start with:
"What is..."

============================================================
DIFFICULTY
============================================================

Aim for:

4 easy
6 medium
5 hard

============================================================
ROLE AND COURSE
============================================================

Every question must be relevant to:

ROLE: {role}
COURSE: {course}

Do not force unrelated technologies.

If course is Java:
use Java concepts.

If course is Python:
use Python concepts.

If course is SQL:
use SQL/DBMS concepts.

If course is DSA:
use algorithms/data structures.

============================================================
OPTIONS
============================================================

Every question must have exactly 4 options.

Options must be:

- plausible
- technically meaningful
- different from each other
- similar in style
- not obviously wrong

Never use:

All of the above
None of the above
A and B

There must be exactly ONE correct answer.

The answer field must EXACTLY match one option.

============================================================
SKILLS
============================================================

Each question must have a realistic skill.

Examples for Java:

- OOP
- Collections
- Exception Handling
- Multithreading
- Strings
- Arrays
- JVM
- Debugging
- File Handling
- Java Basics

Choose skills according to the actual course.

============================================================
FRESH QUESTIONS
============================================================

This is generation {generation_id}.

Do NOT repeat questions from previous assessments.

Previously used questions for this role/course are:

{previous_text if previous_text else "None"}

Create completely different questions.

Change:

- scenarios
- concepts
- code
- wording
- skills
- difficulty

============================================================
OUTPUT
============================================================

Return ONLY one JSON object.

No markdown.
No explanation.
No text before JSON.
No text after JSON.

JSON structure:

{{
    "skills": [
        "skill1",
        "skill2"
    ],
    "questions": [
        {{
            "question": "Question text",
            "options": [
                "Option 1",
                "Option 2",
                "Option 3",
                "Option 4"
            ],
            "answer": "Option 2",
            "skill": "Relevant skill"
        }}
    ]
}}

FINAL REQUIREMENT:

questions MUST contain EXACTLY 15 objects.
"""

    last_error = None

    # Try up to 3 complete generations.
    for attempt in range(3):

        try:

            data = call_json(
                prompt,
                retries=2
            )

            assessment = validate_assessment(
                data
            )

            questions = assessment[
                "questions"
            ]

            # ------------------------------------------------
            # Check against recent questions
            # ------------------------------------------------

            old_set = {
                q.lower().strip()
                for q in previous_questions
            }

            repeated = [
                q["question"]
                for q in questions
                if q["question"].lower().strip()
                in old_set
            ]

            if repeated:

                raise ValueError(
                    "AI repeated questions from a "
                    "previous assessment."
                )

            # ------------------------------------------------
            # Save question history
            # ------------------------------------------------

            RECENT_QUESTIONS.setdefault(
                key,
                []
            )

            RECENT_QUESTIONS[key].extend(
                q["question"]
                for q in questions
            )

            RECENT_QUESTIONS[key] = (
                RECENT_QUESTIONS[key]
                [-MAX_REMEMBERED_QUESTIONS:]
            )

            return assessment

        except Exception as e:

            last_error = e

            print(
                f"Assessment generation "
                f"attempt {attempt + 1}/3 failed: {e}"
            )

            if attempt < 2:

                time.sleep(2)

    raise ValueError(
        f"Assessment generation failed: "
        f"{last_error}"
    )


# ============================================================
# EVALUATE ASSESSMENT
# ============================================================

def evaluate(
    role,
    course,
    assessment,
    answers
):

    questions = assessment.get(
        "questions",
        []
    )

    if len(questions) != 15:

        raise ValueError(
            "Assessment must contain exactly 15 questions."
        )

    correct = 0

    skill_stats = {}

    # --------------------------------------------------------
    # Calculate score
    # --------------------------------------------------------

    for index, question in enumerate(
        questions
    ):

        student_answer = str(
            answers.get(
                str(index),
                ""
            ) or ""
        ).strip()

        correct_answer = str(
            question.get(
                "answer",
                ""
            ) or ""
        ).strip()

        skill = str(
            question.get(
                "skill",
                "General"
            )
            or "General"
        ).strip()

        if not skill:
            skill = "General"

        if skill not in skill_stats:

            skill_stats[skill] = {
                "correct": 0,
                "total": 0
            }

        skill_stats[skill]["total"] += 1

        if (
            student_answer
            and
            student_answer == correct_answer
        ):

            correct += 1

            skill_stats[
                skill
            ]["correct"] += 1

    total = 15

    score = round(
        (correct / total) * 100
    )

    # --------------------------------------------------------
    # Skill analysis
    # --------------------------------------------------------

    skill_analysis = []

    for skill, stats in skill_stats.items():

        percentage = round(
            (
                stats["correct"]
                /
                stats["total"]
            ) * 100
        )

        if percentage >= 80:
            level = "Advanced"

        elif percentage >= 50:
            level = "Intermediate"

        else:
            level = "Beginner"

        skill_analysis.append({
            "skill": skill,
            "score": percentage,
            "level": level
        })

    # --------------------------------------------------------
    # Strong skills
    # --------------------------------------------------------

    strong_skills = [
        item["skill"]
        for item in skill_analysis
        if item["score"] >= 80
    ]

    # --------------------------------------------------------
    # Weak skills
    # --------------------------------------------------------

    weak_skills = [
        item["skill"]
        for item in skill_analysis
        if item["score"] < 50
    ]

    if not strong_skills:

        strong_skills = [
            item["skill"]
            for item in skill_analysis
            if item["score"] >= 60
        ]

    if not weak_skills:

        weak_skills = [
            item["skill"]
            for item in skill_analysis
            if item["score"] < 60
        ]

    # --------------------------------------------------------
    # Roadmap
    # --------------------------------------------------------

    roadmap = []

    for item in skill_analysis:

        if item["score"] < 50:

            roadmap.append({
                "priority": "High",
                "skill": item["skill"],
                "recommendation": (
                    f"Strengthen {item['skill']} "
                    "through focused practice, "
                    "debugging exercises and "
                    "small practical projects."
                )
            })

        elif item["score"] < 80:

            roadmap.append({
                "priority": "Medium",
                "skill": item["skill"],
                "recommendation": (
                    f"Improve {item['skill']} with "
                    "intermediate problems and "
                    "practical implementation."
                )
            })

        else:

            roadmap.append({
                "priority": "Low",
                "skill": item["skill"],
                "recommendation": (
                    f"Maintain {item['skill']} and "
                    "move toward advanced projects "
                    "and interview-level problems."
                )
            })

    return {
        "score": score,
         "correct": correct,
        "total": total,
        "strong_skills": strong_skills,
        "weak_skills": weak_skills,
        "skill_analysis": skill_analysis,
        "roadmap": roadmap
    }