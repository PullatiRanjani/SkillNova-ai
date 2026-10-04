import os
from pathlib import Path
from urllib.parse import quote
import sqlite3
import secrets
import json
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, date, timedelta
from functools import wraps

from flask import (
    Flask, request, redirect, url_for, session,
    render_template_string, flash
)
from werkzeug.security import generate_password_hash, check_password_hash
from markupsafe import escape

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "skillnova-development-secret-change-this")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB = os.getenv("SKILLNOVA_DB_PATH", os.path.join(BASE_DIR, "skillnova.db"))
CODE_RUNNER = os.path.join(BASE_DIR, "code_runner.py")
JAVASCRIPT_RUNNER = os.path.join(BASE_DIR, "javascript_runner.js")
NODE_EXECUTABLE = shutil.which("node")
CODE_RUN_TIMEOUT_SECONDS = 2
CODE_RUN_MAX_OUTPUT_CHARS = 4096


def run_python_test_case(code, test_input):
    """Run one Python test in a separate, short-lived worker process.

    This is deliberately not run inside Flask.  The worker validates the
    submitted AST, exposes a small built-in set, uses an empty temporary
    directory, and is terminated after a strict timeout.
    """
    payload = json.dumps({"code": code, "input": test_input})
    clean_env = {
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", r"C:\\Windows"),
        "WINDIR": os.environ.get("WINDIR", r"C:\\Windows"),
        "PYTHONIOENCODING": "utf-8",
    }

    try:
        with tempfile.TemporaryDirectory(prefix="skillnova-code-") as work_dir:
            completed = subprocess.run(
                [sys.executable, "-I", CODE_RUNNER],
                input=payload,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=CODE_RUN_TIMEOUT_SECONDS,
                cwd=work_dir,
                env=clean_env,
            )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Time limit exceeded (2 seconds)."}
    except OSError as exc:
        return {"ok": False, "error": f"Could not start the code runner: {exc}"}

    if len(completed.stdout) > CODE_RUN_MAX_OUTPUT_CHARS:
        return {"ok": False, "error": "Output limit exceeded."}

    try:
        response = json.loads(completed.stdout)
    except json.JSONDecodeError:
        detail = completed.stderr.strip()[:300] or "The code runner returned an invalid response."
        return {"ok": False, "error": detail}

    if not response.get("ok"):
        response["error"] = str(response.get("error", "Code execution failed."))[:500]
    return response


def run_javascript_test_case(code, test_input):
    """Run one JavaScript test in the same short-lived local-worker model."""
    if not NODE_EXECUTABLE:
        return {"ok": False, "error": "Node.js is not installed on this computer."}

    payload = json.dumps({"code": code, "input": test_input})
    clean_env = {
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", r"C:\\Windows"),
        "WINDIR": os.environ.get("WINDIR", r"C:\\Windows"),
    }
    try:
        with tempfile.TemporaryDirectory(prefix="skillnova-js-") as work_dir:
            completed = subprocess.run(
                [NODE_EXECUTABLE, "--disallow-code-generation-from-strings", JAVASCRIPT_RUNNER],
                input=payload,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=CODE_RUN_TIMEOUT_SECONDS,
                cwd=work_dir,
                env=clean_env,
            )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Time limit exceeded (2 seconds)."}
    except OSError as exc:
        return {"ok": False, "error": f"Could not start the JavaScript runner: {exc}"}

    if len(completed.stdout) > CODE_RUN_MAX_OUTPUT_CHARS:
        return {"ok": False, "error": "Output limit exceeded."}
    try:
        response = json.loads(completed.stdout)
    except json.JSONDecodeError:
        detail = completed.stderr.strip()[:300] or "The JavaScript runner returned an invalid response."
        return {"ok": False, "error": detail}
    if not response.get("ok"):
        response["error"] = str(response.get("error", "Code execution failed."))[:500]
    return response


# ============================================================
# AI HELPERS
# ============================================================

def skillnova_ai_json(prompt):
    from ai_service import call_json
    return call_json(prompt, retries=1)


def skillnova_ai_chat(history, role):
    from ai_service import chat_reply
    return chat_reply(history, role=role, course=role)


# ============================================================
# DATABASE
# ============================================================

def get_db():
    db_dir = os.path.dirname(DB)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    db = get_db()

    db.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        role TEXT DEFAULT 'student',
        career_goal TEXT DEFAULT 'Full Stack Developer',
        xp INTEGER DEFAULT 0,
        level INTEGER DEFAULT 1,
        streak INTEGER DEFAULT 0,
        longest_streak INTEGER DEFAULT 0,
        last_active TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS skills (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL,
        category TEXT,
        description TEXT
    );

    CREATE TABLE IF NOT EXISTS assessments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        score INTEGER,
        total INTEGER,
        percentage REAL,
        strong_skills TEXT,
        weak_skills TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS coding_problems (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT,
        topic TEXT,
        difficulty TEXT,
        description TEXT,
        example_input TEXT,
        example_output TEXT,
        xp INTEGER DEFAULT 20
    );

    CREATE TABLE IF NOT EXISTS coding_submissions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        problem_id INTEGER,
        answer TEXT,
        status TEXT,
        submitted_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS quizzes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT,
        topic TEXT,
        question TEXT,
        option_a TEXT,
        option_b TEXT,
        option_c TEXT,
        option_d TEXT,
        correct TEXT,
        explanation TEXT
    );

    CREATE TABLE IF NOT EXISTS quiz_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        topic TEXT,
        score INTEGER,
        total INTEGER,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS resources (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT,
        skill TEXT,
        resource_type TEXT,
        description TEXT,
        url TEXT
    );

    CREATE TABLE IF NOT EXISTS assignments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT,
        skill TEXT,
        description TEXT,
        deadline TEXT,
        xp INTEGER DEFAULT 30
    );

    CREATE TABLE IF NOT EXISTS assignment_submissions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        assignment_id INTEGER,
        answer TEXT,
        status TEXT DEFAULT 'Submitted',
        submitted_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS personalized_assignments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        difficulty TEXT NOT NULL,
        title TEXT NOT NULL,
        skill TEXT NOT NULL,
        description TEXT NOT NULL,
        xp INTEGER DEFAULT 30,
        status TEXT DEFAULT 'Open',
        generated_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS personalized_assignment_submissions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        personalized_assignment_id INTEGER NOT NULL,
        answer TEXT NOT NULL,
        submitted_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS badges (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE,
        description TEXT,
        icon TEXT,
        xp INTEGER DEFAULT 50
    );

    CREATE TABLE IF NOT EXISTS user_badges (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        badge_id INTEGER,
        awarded_at TEXT DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(user_id, badge_id)
    );

    CREATE TABLE IF NOT EXISTS roadmap (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        career TEXT,
        phase TEXT,
        skill TEXT,
        description TEXT,
        order_no INTEGER
    );

    CREATE TABLE IF NOT EXISTS interviews (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        category TEXT,
        question TEXT,
        ideal_answer TEXT
    );

    CREATE TABLE IF NOT EXISTS interview_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        category TEXT,
        score INTEGER,
        feedback TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS resumes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER UNIQUE,
        phone TEXT,
        location TEXT,
        summary TEXT,
        education TEXT,
        skills TEXT,
        projects TEXT,
        experience TEXT,
        certifications TEXT,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS certificates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        title TEXT,
        certificate_id TEXT UNIQUE,
        issued_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS course_progress (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        role TEXT NOT NULL,
        course_title TEXT NOT NULL,
        topic TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'Not Started',
        best_score REAL DEFAULT 0,
        completed_at TEXT,
        UNIQUE(user_id, course_title)
    );

    CREATE TABLE IF NOT EXISTS activity (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        activity_date TEXT,
        activity_type TEXT,
        UNIQUE(user_id, activity_date, activity_type)
    );
    """)

    for table in ("coding_problems", "interviews"):
        columns = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
        if "role" not in columns:
            db.execute(f"ALTER TABLE {table} ADD COLUMN role TEXT")

    db.commit()
    db.close()


# ============================================================
# SEED DATA
# ============================================================

def seed_data():
    db = get_db()

    # Clean any duplicates left by older seed code before enforcing title uniqueness.
    duplicate_ids = [row[0] for row in db.execute("""
        SELECT id FROM coding_problems
        WHERE id NOT IN (SELECT MIN(id) FROM coding_problems GROUP BY lower(title), COALESCE(role, ''))
    """).fetchall()]
    if duplicate_ids:
        marks = ",".join("?" for _ in duplicate_ids)
        for dependent in ("coding_test_cases", "coding_submissions", "coding_problem_history"):
            exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (dependent,)).fetchone()
            if exists:
                db.execute(f"DELETE FROM {dependent} WHERE problem_id IN ({marks})", duplicate_ids)
        db.execute(f"DELETE FROM coding_problems WHERE id IN ({marks})", duplicate_ids)
    db.execute("DROP INDEX IF EXISTS idx_coding_problems_title_ci")
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_coding_problems_title_role_ci ON coding_problems(lower(title), COALESCE(role, ''))")

    # Merge duplicate quiz history onto each canonical question before removing copies.
    history_exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='quiz_question_history'").fetchone()
    quiz_groups = db.execute("""
        SELECT lower(trim(topic)), lower(trim(question)), MIN(id), GROUP_CONCAT(id)
        FROM quizzes GROUP BY lower(trim(topic)), lower(trim(question)) HAVING COUNT(*) > 1
    """).fetchall()
    for topic_key, question_key, canonical_id, ids_text in quiz_groups:
        duplicate_ids = [int(value) for value in ids_text.split(",") if int(value) != canonical_id]
        if history_exists:
            for duplicate_id in duplicate_ids:
                for seen in db.execute("SELECT user_id, topic, seen_at FROM quiz_question_history WHERE question_id=?", (duplicate_id,)).fetchall():
                    db.execute("INSERT OR IGNORE INTO quiz_question_history(user_id,topic,question_id,seen_at) VALUES(?,?,?,?)",
                               (seen[0], seen[1], canonical_id, seen[2]))
                db.execute("DELETE FROM quiz_question_history WHERE question_id=?", (duplicate_id,))
        db.executemany("DELETE FROM quizzes WHERE id=?", [(value,) for value in duplicate_ids])

    db.execute("""
        DELETE FROM resources WHERE id NOT IN (
            SELECT MIN(id) FROM resources
            GROUP BY CASE WHEN trim(COALESCE(url,'')) <> ''
                THEN 'url:' || lower(trim(url))
                ELSE 'title:' || lower(trim(COALESCE(title,''))) || '|skill:' || lower(trim(COALESCE(skill,''))) END
        )
    """)
    db.execute("""
        DELETE FROM roadmap WHERE id NOT IN (
            SELECT MIN(id) FROM roadmap
            GROUP BY lower(trim(COALESCE(career,''))), lower(trim(COALESCE(phase,''))),
                     lower(trim(COALESCE(skill,''))), lower(trim(COALESCE(description,'')))
        )
    """)
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_quizzes_topic_question_ci ON quizzes(lower(trim(topic)), lower(trim(question)))")
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_resources_url_ci ON resources(lower(trim(url))) WHERE url IS NOT NULL AND trim(url) <> ''")
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_resources_title_skill_ci ON resources(lower(trim(title)), lower(trim(skill))) WHERE url IS NULL OR trim(url) = ''")
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_roadmap_content_ci ON roadmap(lower(trim(career)), lower(trim(phase)), lower(trim(skill)), lower(trim(description)))")

    skills = [
        ("Python", "Programming", "Python programming and problem solving"),
        ("Java", "Programming", "Java programming and OOP"),
        ("C++", "Programming", "C++ programming and competitive programming"),
        ("JavaScript", "Web", "Modern JavaScript development"),
        ("HTML/CSS", "Web", "Frontend fundamentals"),
        ("SQL", "Database", "Relational databases and SQL"),
        ("Git/GitHub", "Tools", "Version control"),
        ("DSA", "Computer Science", "Data structures and algorithms"),
        ("OOP", "Computer Science", "Object oriented programming"),
        ("DBMS", "Computer Science", "Database management systems"),
        ("Operating Systems", "Computer Science", "Operating system concepts"),
        ("Computer Networks", "Computer Science", "Networking fundamentals"),
        ("React", "Web", "React frontend development"),
        ("Node.js", "Web", "Backend JavaScript"),
        ("Django", "Web", "Python web development"),
        ("Flask", "Web", "Python microframework"),
        ("Machine Learning", "AI", "Machine learning fundamentals"),
        ("Deep Learning", "AI", "Neural networks and deep learning"),
        ("Cloud Computing", "Cloud", "Cloud fundamentals"),
        ("AWS", "Cloud", "AWS cloud services"),
        ("Communication", "Professional", "Professional communication"),
        ("Problem Solving", "Professional", "Logical problem solving"),
    ]

    for s in skills:
        db.execute(
            "INSERT OR IGNORE INTO skills(name,category,description) VALUES(?,?,?)",
            s
        )

    problems = [
        ("Two Sum", "Arrays", "Easy",
         "Given an array and target, find two numbers whose sum equals target.",
         "[2,7,11,15], target=9", "[0,1]", 20),

        ("Reverse String", "Strings", "Easy",
         "Reverse a given string.",
         "hello", "olleh", 15),

        ("Palindrome Check", "Strings", "Easy",
         "Check whether a string is a palindrome.",
         "madam", "true", 15),

        ("Maximum Element", "Arrays", "Easy",
         "Find the largest element in an array.",
         "[4,8,1,9,2]", "9", 15),

        ("Binary Search", "Searching", "Medium",
         "Search for a target in a sorted array using binary search.",
         "[1,3,5,7,9], target=7", "3", 25),

        ("Bubble Sort", "Sorting", "Easy",
         "Sort an array using bubble sort.",
         "[5,2,8,1]", "[1,2,5,8]", 20),

        ("Merge Two Sorted Arrays", "Arrays", "Medium",
         "Merge two sorted arrays into one sorted array.",
         "[1,3], [2,4]", "[1,2,3,4]", 30),

        ("Valid Parentheses", "Stack", "Medium",
         "Determine whether brackets are correctly balanced.",
         "({[]})", "true", 30),

        ("Queue Using Stack", "Queue", "Medium",
         "Implement a queue using stacks.",
         "push 1,2, pop", "1", 30),

        ("Linked List Reverse", "Linked List", "Medium",
         "Reverse a singly linked list.",
         "1->2->3", "3->2->1", 30),

        ("Tree Height", "Trees", "Medium",
         "Find the height of a binary tree.",
         "root=[1,2,3]", "2", 30),

        ("Graph BFS", "Graphs", "Medium",
         "Traverse a graph using Breadth First Search.",
         "A-B-C", "A B C", 35),

        ("Graph DFS", "Graphs", "Medium",
         "Traverse a graph using Depth First Search.",
         "A-B-C", "A B C", 35),

        ("Fibonacci DP", "Dynamic Programming", "Medium",
         "Find the nth Fibonacci number using dynamic programming.",
         "n=10", "55", 35),

        ("Climbing Stairs", "Dynamic Programming", "Medium",
         "Count ways to climb n stairs taking one or two steps.",
         "n=5", "8", 35),

        ("SQL Employee Salary", "SQL", "Easy",
         "Write a query to find employees with salary greater than 50000.",
         "employees table", "SELECT ...", 20),
    ]

    for p in problems:
        db.execute("""
        INSERT OR IGNORE INTO coding_problems
        (title,topic,difficulty,description,example_input,example_output,xp)
        VALUES(?,?,?,?,?,?,?)
        """, p)

    coding_problem_additions = [
        ("Count Even Numbers", "Arrays", "Easy", "Input: n followed by n integers. Return how many are even.", "5\n1 2 3 4 6", "3", 20, [("5\n1 2 3 4 6", "3"), ("4\n1 3 5 7", "0"), ("3\n-2 0 9", "2")]),
        ("Second Largest Distinct", "Arrays", "Easy", "Input: n followed by n integers. Return the second largest distinct value.", "5\n3 1 9 7 9", "7", 20, [("5\n3 1 9 7 9", "7"), ("4\n-5 -2 -9 -3", "-3"), ("3\n4 4 1", "1")]),
        ("Count Distinct Values", "Arrays", "Easy", "Input: n followed by n integers. Return the number of distinct values.", "6\n1 1 2 3 3 3", "3", 20, [("6\n1 1 2 3 3 3", "3"), ("4\n8 8 8 8", "1"), ("5\n1 2 3 4 5", "5")]),
        ("Maximum Subarray Sum", "Arrays", "Medium", "Input: n followed by n integers. Return the largest sum of a contiguous subarray.", "5\n-2 1 -3 4 -1", "4", 30, [("5\n-2 1 -3 4 -1", "4"), ("4\n-5 -2 -3 -1", "-1"), ("3\n2 3 4", "9")]),
        ("Missing Number", "Arrays", "Easy", "Input: n followed by n distinct values from 0 through n. Return the missing value.", "4\n3 0 1 4", "2", 20, [("4\n3 0 1 4", "2"), ("3\n0 1 3", "2"), ("5\n1 2 3 4 5", "0")]),
        ("Greatest Common Divisor", "Math", "Easy", "Input: two integers. Return their greatest common divisor.", "48 18", "6", 20, [("48 18", "6"), ("7 13", "1"), ("0 9", "9")]),
        ("Least Common Multiple", "Math", "Easy", "Input: two positive integers. Return their least common multiple.", "12 18", "36", 20, [("12 18", "36"), ("7 13", "91"), ("4 10", "20")]),
        ("Sum of Digits", "Math", "Easy", "Input: one integer. Return the sum of its decimal digits, ignoring a leading minus sign.", "908", "17", 15, [("908", "17"), ("0", "0"), ("-321", "6")]),
        ("Factorial", "Math", "Easy", "Input: n where 0 <= n <= 12. Return n factorial.", "5", "120", 20, [("5", "120"), ("0", "1"), ("7", "5040")]),
        ("Nth Fibonacci Number", "Dynamic Programming", "Easy", "Input: n. Return the zero-indexed Fibonacci number F(n), where F(0)=0 and F(1)=1.", "7", "13", 20, [("7", "13"), ("0", "0"), ("10", "55")]),
        ("Staircase Ways", "Dynamic Programming", "Easy", "Input: n stairs. Return the number of ways to climb them using steps of 1 or 2.", "5", "8", 20, [("5", "8"), ("1", "1"), ("8", "34")]),
        ("Prime Check", "Math", "Easy", "Input: n. Return 1 if n is prime and 0 otherwise.", "17", "1", 20, [("17", "1"), ("1", "0"), ("21", "0")]),
        ("Count Primes Up To N", "Math", "Medium", "Input: n. Return the number of prime integers from 2 through n.", "10", "4", 25, [("10", "4"), ("2", "1"), ("1", "0")]),
        ("Hamming Distance", "Math", "Easy", "Input: two non-negative integers. Return the number of bit positions where they differ.", "1 4", "2", 20, [("1 4", "2"), ("7 7", "0"), ("0 15", "4")]),
        ("Count Vowels", "Strings", "Easy", "Input: one word. Return the number of vowels (a,e,i,o,u), case-insensitive.", "SkillNova", "3", 15, [("SkillNova", "3"), ("xyz", "0"), ("Education", "5")]),
        ("First Unique Character Index", "Strings", "Easy", "Input: one word. Return the zero-based index of its first non-repeating character, or -1 if none exists.", "swiss", "1", 20, [("swiss", "1"), ("aabbcd", "4"), ("aabb", "-1")]),
        ("Balanced Brackets", "Stack", "Easy", "Input: one string containing only (), [], and {}. Return 1 if brackets are balanced and properly nested, otherwise 0.", "({[]})", "1", 20, [("({[]})", "1"), ("([)]", "0"), ("((())", "0")]),
        ("Count Words", "Strings", "Easy", "Input: a line of text. Return the number of whitespace-separated words.", "learn and build", "3", 15, [("learn and build", "3"), ("one", "1"), ("two words here", "3")]),
        ("Last Word Length", "Strings", "Easy", "Input: a line of text. Return the length of its last word.", "learn to code", "4", 15, [("learn to code", "4"), ("hello", "5"), ("a longer final", "5")]),
        ("Maximum Stock Profit", "Arrays", "Easy", "Input: n followed by n daily prices. Return the maximum profit from one buy before one sell, or 0 if none.", "6\n7 1 5 3 6 4", "5", 20, [("6\n7 1 5 3 6 4", "5"), ("5\n7 6 4 3 1", "0"), ("4\n2 4 1 8", "7")]),
        ("Binary Search Index", "Searching", "Easy", "Input: n, n sorted integers, then target. Return the target's zero-based index or -1.", "5\n1 3 5 7 9\n7", "3", 20, [("5\n1 3 5 7 9\n7", "3"), ("4\n2 4 6 8\n5", "-1"), ("1\n4\n4", "0")]),
        ("Check Sorted Array", "Arrays", "Easy", "Input: n followed by n integers. Return 1 if non-decreasing, otherwise 0.", "4\n1 2 2 5", "1", 15, [("4\n1 2 2 5", "1"), ("3\n3 2 1", "0"), ("1\n9", "1")]),
        ("Numeric Palindrome", "Math", "Easy", "Input: one integer. Return 1 if its decimal representation is a palindrome, otherwise 0.", "1221", "1", 15, [("1221", "1"), ("-121", "0"), ("120", "0")]),
        ("Matrix Diagonal Sum", "Arrays", "Easy", "Input: n followed by n*n matrix integers in row order. Return the main diagonal sum.", "3\n1 2 3 4 5 6 7 8 9", "15", 20, [("3\n1 2 3 4 5 6 7 8 9", "15"), ("1\n-5", "-5"), ("2\n1 2 3 4", "5")]),
    ]
    for title, topic, difficulty, description, example_input, example_output, xp, cases in coding_problem_additions:
        existing = db.execute("SELECT id FROM coding_problems WHERE lower(title)=lower(?) AND COALESCE(role,'')=''", (title,)).fetchone()
        if existing:
            problem_id = existing[0]
        else:
            cursor = db.execute("""INSERT INTO coding_problems(title,topic,difficulty,description,example_input,example_output,xp)
                                  VALUES(?,?,?,?,?,?,?)""",
                                (title, topic, difficulty, description, example_input, example_output, xp))
            problem_id = cursor.lastrowid
        if db.execute("SELECT COUNT(*) FROM coding_test_cases WHERE problem_id=?", (problem_id,)).fetchone()[0] == 0:
            db.executemany("INSERT INTO coding_test_cases(problem_id,input,expected_output) VALUES(?,?,?)",
                           [(problem_id, test_input, expected) for test_input, expected in cases])

    sql_problem = db.execute("SELECT id FROM coding_problems WHERE lower(title)=lower(?) LIMIT 1", ("SQL Employee Salary",)).fetchone()
    if sql_problem:
        problem_id = sql_problem[0]
        db.execute("UPDATE coding_problems SET description=?, example_input=?, example_output=? WHERE id=?",
                   ("Write a read-only SELECT query that returns each employee name and salary for salaries above 50000, ordered by salary ascending. The employees table has id, name, and salary columns.", "employees: (id, name, salary) rows. Select name and salary where salary > 50000.", '[["Mina",60000],["Omar",85000]]', problem_id))
        sql_cases = [
            ({"tables":{"employees":{"columns":[["id","INTEGER"],["name","TEXT"],["salary","INTEGER"]],"rows":[[1,"Asha",40000],[2,"Mina",60000],[3,"Omar",85000]]}}}, '[["Mina",60000],["Omar",85000]]'),
            ({"tables":{"employees":{"columns":[["id","INTEGER"],["name","TEXT"],["salary","INTEGER"]],"rows":[[1,"Ravi",50000],[2,"Sara",50001],[3,"Tia",50000]]}}}, '[["Sara",50001]]'),
            ({"tables":{"employees":{"columns":[["id","INTEGER"],["name","TEXT"],["salary","INTEGER"]],"rows":[[1,"Uma",30000],[2,"Vik",45000]]}}}, '[]'),
        ]
        old_cases = db.execute("SELECT id FROM coding_test_cases WHERE problem_id=? ORDER BY id", (problem_id,)).fetchall()
        for index, (fixture, expected) in enumerate(sql_cases):
            if index < len(old_cases):
                db.execute("UPDATE coding_test_cases SET input=?, expected_output=? WHERE id=?",
                           (json.dumps(fixture, separators=(",", ":")), expected, old_cases[index][0]))
            else:
                db.execute("INSERT INTO coding_test_cases(problem_id,input,expected_output) VALUES(?,?,?)",
                           (problem_id, json.dumps(fixture, separators=(",", ":")), expected))

    quizzes = [
        ("Python Basics", "Python", "Which keyword defines a function?",
         "function", "def", "fun", "define", "B",
         "Python functions are defined using def."),

        ("Python Basics", "Python", "Which data type stores key-value pairs?",
         "List", "Tuple", "Dictionary", "Set", "C",
         "Dictionaries store key-value pairs."),

        ("DSA", "DSA", "Which structure follows FIFO?",
         "Stack", "Queue", "Tree", "Graph", "B",
         "Queue follows First In First Out."),

        ("DSA", "DSA", "Which search works on sorted arrays?",
         "Linear Search", "Binary Search", "DFS", "BFS", "B",
         "Binary search requires sorted data."),

        ("DBMS", "DBMS", "Which language is used to query relational databases?",
         "HTML", "SQL", "CSS", "XML", "B",
         "SQL is used for relational database queries."),

        ("Web Development", "JavaScript", "Which keyword declares a constant in JavaScript?",
         "let", "var", "const", "static", "C",
         "const declares a block-scoped constant."),

        ("Computer Networks", "Computer Networks", "Which protocol is used for web pages?",
         "HTTP", "FTP", "SMTP", "SSH", "A",
         "HTTP is the foundation of web communication."),

        ("Operating Systems", "Operating Systems", "Which is a process scheduling algorithm?",
         "FIFO", "Round Robin", "Binary Search", "Merge Sort", "B",
         "Round Robin is a CPU scheduling algorithm."),

        ("OOP", "OOP", "Which concept hides implementation details?",
         "Inheritance", "Encapsulation", "Compilation", "Iteration", "B",
         "Encapsulation hides internal implementation."),

        ("AI", "Machine Learning", "Which is supervised learning?",
         "Clustering", "Classification", "PCA", "Association", "B",
         "Classification uses labelled training data."),
    ]

    for q in quizzes:
        db.execute("""
        INSERT OR IGNORE INTO quizzes
        (title,topic,question,option_a,option_b,option_c,option_d,correct,explanation)
        VALUES(?,?,?,?,?,?,?,?,?)
        """, q)

    extra_quiz_questions = [
        ("Python", "What does list.append(x) do?", "Adds x to the end", "Sorts the list", "Removes x", "Copies the list", "A", "append adds one item at the end."),
        ("Python", "What does len({'a': 1, 'b': 2}) return?", "1", "2", "3", "An error", "B", "len returns the number of dictionary keys."),
        ("Python", "Which block runs when no exception is raised?", "except", "finally only", "else", "raise", "C", "The else block runs after a successful try block."),
        ("DSA", "Which structure is typically used for breadth-first search?", "Stack", "Queue", "Heap", "Hash set", "B", "A queue processes BFS nodes in discovery order."),
        ("DSA", "What is binary search time on a sorted array?", "O(n)", "O(n log n)", "O(log n)", "O(1) always", "C", "Binary search halves the search space each step."),
        ("DSA", "Which structure naturally supports LIFO?", "Queue", "Stack", "Graph", "Trie", "B", "A stack is last-in, first-out."),
        ("DBMS", "What does a primary key guarantee?", "Rows are uniquely identified", "Columns are encrypted", "Rows are sorted", "Tables are backed up", "A", "A primary key uniquely identifies a row."),
        ("DBMS", "Why normalize a relational database?", "Increase duplicate data", "Reduce redundancy", "Avoid all joins", "Remove keys", "B", "Normalization reduces update anomalies and redundancy."),
        ("DBMS", "A foreign key is used to:", "Compress a table", "Link related tables", "Sort rows", "Encrypt a column", "B", "A foreign key references a key in another table."),
        ("JavaScript", "Which operator checks strict equality?", "=", "==", "===", "!=", "C", "=== compares value and type without coercion."),
        ("JavaScript", "What does Array.map return?", "A new transformed array", "The first element", "A boolean", "The original array only", "A", "map builds a new array from callback results."),
        ("JavaScript", "A Promise represents:", "A CSS rule", "A future async result", "A loop", "A DOM node", "B", "Promises represent eventual completion or failure of async work."),
        ("Computer Networks", "What does DNS resolve?", "Domain names to IP addresses", "Ports to passwords", "Files to URLs", "MAC addresses to users", "A", "DNS resolves human-readable names to IP addresses."),
        ("Computer Networks", "Which protocol provides reliable ordered delivery?", "UDP", "TCP", "ARP", "ICMP", "B", "TCP provides reliable, ordered byte-stream delivery."),
        ("Computer Networks", "HTTPS primarily adds what to HTTP?", "TLS encryption and authentication", "Faster DNS", "A new IP version", "File compression", "A", "HTTPS protects HTTP traffic with TLS."),
        ("Operating Systems", "A thread is best described as:", "A lightweight execution unit in a process", "A storage disk", "A network packet", "A database table", "A", "Threads share a process's resources while executing independently."),
        ("Operating Systems", "Virtual memory lets a system:", "Use storage to extend addressable memory", "Remove the operating system", "Guarantee zero latency", "Skip process scheduling", "A", "Virtual memory maps process addresses to physical memory and storage."),
        ("Operating Systems", "A context switch changes:", "The active execution context", "The source code", "The file format", "The network protocol", "A", "The OS saves one task's state and restores another's."),
        ("OOP", "Polymorphism means:", "One interface can have multiple implementations", "All data is public", "Objects cannot change", "Classes have no methods", "A", "Polymorphism allows a common interface with varying behavior."),
        ("OOP", "A constructor is used to:", "Initialize a new object", "Delete a database", "Compile a loop", "Import a package", "A", "Constructors initialize objects when they are created."),
        ("OOP", "Abstraction focuses on:", "Exposing useful behavior while hiding details", "Duplicating every method", "Disabling inheritance", "Storing only global data", "A", "Abstraction presents essential behavior and hides implementation detail."),
        ("Machine Learning", "Overfitting occurs when a model:", "Fits training data but generalizes poorly", "Has no parameters", "Cannot fit training data", "Uses a test set", "A", "Overfit models memorize training patterns and perform poorly on new data."),
        ("Machine Learning", "Why keep a test set?", "To estimate performance on unseen data", "To train the model twice", "To choose column names", "To remove labels", "A", "A held-out test set measures generalization."),
        ("Machine Learning", "Regression commonly predicts:", "A continuous numeric value", "A database schema", "A source file", "A network route", "A", "Regression models predict numeric quantities."),
        ("Java", "What does the JVM execute?", "Java bytecode", "HTML styles", "SQL tables", "C header files", "A", "Java source compiles to bytecode executed by the JVM."),
        ("Java", "Which keyword declares a class implementing an interface?", "extends", "implements", "inherits", "instanceof", "B", "Java uses implements for interfaces."),
        ("Java", "For objects, what does == usually compare?", "References", "All field values deeply", "Class names", "Method results", "A", "== compares object references; equals can compare logical equality."),
        ("Java", "Method overloading means:", "Same method name with different parameter lists", "Replacing a class at runtime", "Changing a variable type", "Calling a method recursively", "A", "Overloads differ by their parameter lists."),
        ("C++", "Which standard container is a resizable array?", "std::vector", "std::stacktrace", "std::mutex", "std::fstream", "A", "std::vector stores a dynamically sized sequence."),
        ("C++", "A reference is:", "An alias for an existing object", "A heap allocator", "A namespace", "A preprocessor macro", "A", "A reference names an existing object."),
        ("C++", "RAII ties resource release to:", "Object lifetime", "The network address", "A global variable", "The compiler version", "A", "RAII uses object construction and destruction for resource management."),
        ("C++", "What does std::unique_ptr express?", "Exclusive ownership", "Shared ownership", "A raw array only", "A weak reference", "A", "unique_ptr owns a resource exclusively."),
        ("SQL", "Which clause filters rows before grouping?", "HAVING", "WHERE", "ORDER BY", "LIMIT", "B", "WHERE filters input rows before GROUP BY."),
        ("SQL", "Which clause filters groups after aggregation?", "WHERE", "HAVING", "FROM", "JOIN", "B", "HAVING filters grouped results."),
        ("SQL", "What is a database index mainly used for?", "Speeding up lookups", "Encrypting rows", "Replacing primary keys", "Preventing all writes", "A", "Indexes can speed up reads at storage and write-cost tradeoffs."),
        ("SQL", "COUNT(column) normally ignores:", "NULL values", "Zero values", "Negative values", "Duplicate values always", "A", "COUNT(column) counts non-NULL values."),
        ("Statistics", "Which measure is less affected by extreme outliers?", "Mean", "Median", "Range", "Variance", "B", "The median depends on the middle ranked value."),
        ("Statistics", "A correlation near zero means:", "No strong linear association", "The variables are identical", "One causes the other", "The data has no variance", "A", "Correlation measures linear association, not causation."),
        ("Statistics", "Standard deviation measures:", "Spread around the mean", "The sample name", "A median category", "Causal impact", "A", "Standard deviation describes dispersion around the mean."),
        ("Statistics", "A larger representative random sample usually:", "Reduces sampling uncertainty", "Guarantees no bias", "Proves causality", "Removes outliers", "A", "Larger random samples generally reduce standard error."),
        ("Data Analysis", "What is data cleaning?", "Fixing or handling inconsistent and missing data", "Drawing only colorful charts", "Encrypting all columns", "Deleting every row", "A", "Data cleaning addresses quality issues before analysis."),
        ("Data Analysis", "A dashboard is most useful for:", "Monitoring key measures over time", "Replacing source databases", "Compiling code", "Encrypting passwords", "A", "Dashboards summarize key metrics for monitoring and decisions."),
        ("Data Analysis", "What does an inner join return?", "Rows matching in both inputs", "Every row from the left only", "A Cartesian product only", "Only NULL rows", "A", "An inner join keeps rows meeting the join condition on both sides."),
        ("Data Analysis", "Why validate data types during analysis?", "To prevent invalid operations and interpretation", "To increase row count", "To hide missing data", "To avoid documentation", "A", "Correct types support valid calculations and comparisons."),
        ("Cloud Computing", "IaaS provides:", "Virtualized compute, storage, and networking", "Only finished email software", "A spreadsheet formula", "A local compiler", "A", "IaaS supplies infrastructure resources as services."),
        ("Cloud Computing", "Horizontal scaling means:", "Adding more instances", "Adding RAM to one instance only", "Removing backups", "Changing source language", "A", "Horizontal scaling adds instances to share load."),
        ("Cloud Computing", "Object storage is suited to:", "Files and unstructured objects", "CPU registers", "Thread stacks", "SQL transactions only", "A", "Object stores handle blobs and their metadata."),
        ("Cloud Computing", "IAM controls:", "Identities and permissions", "Image dimensions", "Compiler flags", "Database normalization", "A", "Identity and access management governs who can do what."),
        ("HTML/CSS", "Which element is a semantic navigation landmark?", "<nav>", "<span>", "<b>", "<i>", "A", "nav identifies a navigation section."),
        ("HTML/CSS", "Which attribute provides alternative image text?", "alt", "href", "target", "rel", "A", "alt provides text alternatives for images."),
        ("HTML/CSS", "CSS Flexbox is primarily for:", "One-dimensional layout", "Database queries", "Audio encoding", "Server routing", "A", "Flexbox lays out items along a row or column."),
        ("HTML/CSS", "Which selector targets an element by id?", ".name", "#name", "*name", "@name", "B", "CSS uses # for id selectors."),
        ("React", "What is a React component?", "A reusable UI building block", "A SQL index", "A network socket", "A compiler", "A", "Components encapsulate reusable interface elements."),
        ("React", "What does state represent?", "Data that can change and trigger rendering", "A CSS file", "A URL only", "A database backup", "A", "Updating state can cause React to render again."),
        ("React", "Why provide keys when rendering lists?", "To identify items across updates", "To encrypt elements", "To choose CSS colors", "To make all items static", "A", "Keys help React track list items between renders."),
        ("React", "Which hook runs side effects?", "useEffect", "useStyle", "useRouteOnly", "useCompile", "A", "useEffect handles side effects in function components."),
        ("Flask", "Which decorator maps a URL to a view?", "@app.route", "@app.table", "@app.sql", "@app.template", "A", "Flask uses route decorators to register views."),
        ("Flask", "Where does Flask expose submitted form values?", "request.form", "app.filesystem", "session.path", "render_template", "A", "request.form contains submitted form fields."),
        ("Flask", "Which helper renders a template file?", "render_template", "url_for only", "redirect only", "make_response only", "A", "render_template renders a template with context."),
        ("Flask", "Which response format is useful for JSON APIs?", "JSON", "PNG", "CSS", "WAV", "A", "JSON is a common structured format for API responses."),
    ]
    for topic, question, option_a, option_b, option_c, option_d, correct, explanation in extra_quiz_questions:
        db.execute("""INSERT OR IGNORE INTO quizzes
            (title,topic,question,option_a,option_b,option_c,option_d,correct,explanation)
            VALUES(?,?,?,?,?,?,?,?,?)""",
            (topic, topic, question, option_a, option_b, option_c, option_d, correct, explanation))

    resources = [
        ("Python Fundamentals", "Python", "Course",
         "Learn variables, conditions, loops, functions and collections.",
         "https://docs.python.org/3/tutorial/"),

        ("Java Documentation", "Java", "Documentation",
         "Official Java documentation and learning resources.",
         "https://docs.oracle.com/en/java/"),

        ("MDN Web Docs", "JavaScript", "Documentation",
         "HTML, CSS and JavaScript reference.",
         "https://developer.mozilla.org/"),

        ("SQL Tutorial", "SQL", "Tutorial",
         "SQL fundamentals and database queries.",
         "https://www.w3schools.com/sql/"),

        ("Git Documentation", "Git/GitHub", "Documentation",
         "Learn Git version control.",
         "https://git-scm.com/doc"),

        ("React Documentation", "React", "Documentation",
         "Official React documentation.",
         "https://react.dev/"),

        ("Flask Documentation", "Flask", "Documentation",
         "Official Flask documentation.",
         "https://flask.palletsprojects.com/"),

        ("Machine Learning Guide", "Machine Learning", "Learning",
         "Start learning supervised and unsupervised machine learning.",
         "https://scikit-learn.org/stable/user_guide.html"),
    ]

    for r in resources:
        db.execute("""
        INSERT OR IGNORE INTO resources
        (title,skill,resource_type,description,url)
        VALUES(?,?,?,?,?)
        """, r)

    assignments = [
        ("Build a Python Calculator", "Python",
         "Create a command-line calculator supporting +, -, *, /.",
         "2026-10-05", 30),

        ("Student Management System", "Python",
         "Build a simple student management application.",
         "2026-10-10", 40),

        ("Portfolio Website", "HTML/CSS",
         "Create a responsive personal portfolio website.",
         "2026-10-15", 40),

        ("SQL Employee Database", "SQL",
         "Create tables and write CRUD queries for employees.",
         "2026-10-20", 35),

        ("Flask Mini Project", "Flask",
         "Build a Flask application with login and CRUD functionality.",
         "2026-10-25", 50),
    ]

    for a in assignments:
        db.execute("""
        INSERT OR IGNORE INTO assignments
        (title,skill,description,deadline,xp)
        VALUES(?,?,?,?,?)
        """, a)

    badges = [
        ("First Step", "Complete your first learning activity.", "🚀", 20),
        ("Quiz Master", "Complete a quiz.", "🧠", 50),
        ("Code Explorer", "Submit your first coding problem.", "💻", 50),
        ("7 Day Streak", "Maintain a 7 day learning streak.", "🔥", 100),
        ("Resume Ready", "Create your resume.", "📄", 50),
        ("Interview Ready", "Complete a mock interview.", "🎤", 100),
        ("Skill Builder", "Complete an assessment.", "🎯", 75),
    ]

    for b in badges:
        db.execute("""
        INSERT OR IGNORE INTO badges(name,description,icon,xp)
        VALUES(?,?,?,?)
        """, b)

    careers = {
        "Full Stack Developer": [
            ("Foundation", "HTML/CSS", "Learn web page structure and styling."),
            ("Foundation", "JavaScript", "Learn browser programming."),
            ("Backend", "Python", "Build backend applications."),
            ("Backend", "Flask", "Create REST applications."),
            ("Database", "SQL", "Work with relational databases."),
            ("Frontend", "React", "Build modern interfaces."),
            ("Advanced", "Git/GitHub", "Manage professional projects."),
            ("Advanced", "DSA", "Prepare for technical interviews."),
        ],
        "Python Developer": [
            ("Foundation", "Python", "Master Python fundamentals."),
            ("Intermediate", "OOP", "Learn object-oriented design."),
            ("Intermediate", "SQL", "Work with databases."),
            ("Backend", "Flask", "Build APIs."),
            ("Backend", "Django", "Build complete web applications."),
            ("Advanced", "DSA", "Prepare for coding interviews."),
        ],
        "AI/ML Engineer": [
            ("Foundation", "Python", "Master Python."),
            ("Foundation", "Mathematics", "Learn probability and linear algebra."),
            ("Intermediate", "SQL", "Manage datasets."),
            ("Intermediate", "Machine Learning", "Learn ML algorithms."),
            ("Advanced", "Deep Learning", "Build neural network models."),
            ("Advanced", "Problem Solving", "Develop analytical skills."),
        ],
        "Data Analyst": [
            ("Foundation", "Python", "Learn data analysis with Python."),
            ("Foundation", "SQL", "Query databases."),
            ("Intermediate", "Statistics", "Understand statistical concepts."),
            ("Intermediate", "Data Visualization", "Present insights visually."),
            ("Advanced", "Machine Learning", "Apply predictive techniques."),
        ],
        "Java Developer": [
            ("Foundation", "Java", "Master Java syntax."),
            ("Intermediate", "OOP", "Master object-oriented programming."),
            ("Intermediate", "SQL", "Database programming."),
            ("Backend", "Spring", "Build enterprise applications."),
            ("Advanced", "DSA", "Prepare for coding interviews."),
        ]
    }

    for career, rows in careers.items():
        for i, (phase, skill, desc) in enumerate(rows):
            db.execute("""
            INSERT OR IGNORE INTO roadmap
            (career,phase,skill,description,order_no)
            VALUES(?,?,?,?,?)
            """, (career, phase, skill, desc, i + 1))

    interviews = [
        ("Python", "What is the difference between a list and tuple?",
         "Lists are mutable while tuples are immutable."),

        ("Python", "Explain Python dictionaries.",
         "A dictionary stores key-value pairs and provides efficient lookup."),

        ("Java", "Explain OOP principles.",
         "The main principles are encapsulation, inheritance, polymorphism and abstraction."),

        ("Java", "What is JVM?",
         "JVM executes Java bytecode and provides platform independence."),

        ("SQL", "What is the difference between WHERE and HAVING?",
         "WHERE filters rows before grouping, while HAVING filters groups."),

        ("DBMS", "What is normalization?",
         "Normalization organizes relational data to reduce redundancy and improve integrity."),

        ("OS", "What is a process?",
         "A process is a program in execution."),

        ("Computer Networks", "What is the difference between TCP and UDP?",
         "TCP provides reliable connection-oriented communication, while UDP is connectionless."),

        ("HR", "Tell me about yourself.",
         "Give a concise introduction covering education, skills, projects and career goals."),

        ("HR", "What are your strengths?",
         "Mention genuine strengths with a short example showing them in action."),

        ("Full Stack", "Explain the frontend-backend-database relationship.",
         "Frontend handles user interaction, backend handles business logic and APIs, and database stores application data."),

        ("AI/ML", "What is supervised learning?",
         "Supervised learning trains a model using labelled examples."),
    ]

    for i in interviews:
        db.execute("""
        INSERT OR IGNORE INTO interviews(category,question,ideal_answer)
        VALUES(?,?,?)
        """, i)

    db.commit()
    db.close()


# ============================================================
# AUTH / HELPERS
# ============================================================

def current_user():
    if "user_id" not in session:
        return None

    db = get_db()
    user = db.execute(
        "SELECT * FROM users WHERE id=?",
        (session["user_id"],)
    ).fetchone()
    db.close()
    return user


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not current_user():
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper


def add_xp(user_id, amount):
    db = get_db()

    user = db.execute(
        "SELECT xp FROM users WHERE id=?",
        (user_id,)
    ).fetchone()

    xp = (user["xp"] if user else 0) + amount
    level = max(1, xp // 100 + 1)

    db.execute(
        "UPDATE users SET xp=?, level=? WHERE id=?",
        (xp, level, user_id)
    )

    db.commit()
    db.close()


def record_activity(user_id, activity_type="learning"):
    today = date.today().isoformat()

    db = get_db()

    db.execute("""
        INSERT OR IGNORE INTO activity
        (user_id,activity_date,activity_type)
        VALUES(?,?,?)
    """, (user_id, today, activity_type))

    user = db.execute(
        "SELECT streak,longest_streak,last_active FROM users WHERE id=?",
        (user_id,)
    ).fetchone()

    current_streak = user["streak"] or 0
    longest = user["longest_streak"] or 0
    last_active = user["last_active"]

    if last_active == today:
        pass
    elif last_active:
        try:
            previous = date.fromisoformat(last_active)
            difference = (date.today() - previous).days

            if difference == 1:
                current_streak += 1
            else:
                current_streak = 1
        except:
            current_streak = 1
    else:
        current_streak = 1

    longest = max(longest, current_streak)

    db.execute("""
        UPDATE users
        SET streak=?,longest_streak=?,last_active=?
        WHERE id=?
    """, (current_streak, longest, today, user_id))

    db.commit()
    db.close()


def award_badge(user_id, badge_name):
    db = get_db()

    badge = db.execute(
        "SELECT id FROM badges WHERE name=?",
        (badge_name,)
    ).fetchone()

    if badge:
        db.execute("""
            INSERT OR IGNORE INTO user_badges(user_id,badge_id)
            VALUES(?,?)
        """, (user_id, badge["id"]))
        db.commit()

    db.close()


# ============================================================
# UI
# ============================================================

STYLE = """
<style>
:root{--ink:#171b2e;--muted:#70778e;--primary:#5b55dd;--purple:#8c63e8;--mint:#4fc9b0;--paper:#f5f6fb;--card:#fff;--line:#e7e9f2;--shadow:0 14px 38px rgba(35,41,82,.08)}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{position:relative;isolation:isolate;min-height:100vh;overflow-x:hidden;margin:0;background-color:var(--paper);background-image:linear-gradient(180deg,rgba(247,248,253,.88),rgba(247,248,253,.96)),url("/static/skillnova-ambient.svg");background-size:cover;background-position:center top;background-attachment:fixed;color:var(--ink);font:15px/1.6 Inter,"Segoe UI",Arial,sans-serif}body:before{content:"";position:fixed;z-index:-1;inset:-25%;pointer-events:none;background:radial-gradient(ellipse at 18% 30%,#d9d5ff88,transparent 28%),radial-gradient(ellipse at 82% 70%,#b8f2e866,transparent 26%);filter:blur(30px);animation:ambient-drift 20s ease-in-out infinite alternate}a{color:inherit}
nav{position:sticky;top:0;z-index:20;border-bottom:1px solid #ffffff18;background:linear-gradient(100deg,rgba(22,25,49,.98),rgba(39,35,84,.97),rgba(22,25,49,.98));background-size:200% 100%;animation:nav-glow 16s ease infinite;backdrop-filter:blur(14px);padding:12px max(4vw,20px);display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap;box-shadow:0 8px 24px #181a3322}nav>div{display:flex;align-items:center;justify-content:flex-end;gap:4px;flex-wrap:wrap}.brand{color:white!important;font-size:21px!important;font-weight:800!important;letter-spacing:-.5px;white-space:nowrap}nav a{color:#d8daf0;text-decoration:none;margin:2px 3px;padding:8px 10px;border-radius:10px;font-size:13px;font-weight:600;transition:.18s}nav a:hover{background:#ffffff18;color:white}
.container{width:min(1180px,92%);margin:32px auto 60px}.hero{position:relative;isolation:isolate;overflow:hidden;background:linear-gradient(120deg,#514bd1,#7458de 58%,#a16be5);color:white;padding:clamp(28px,5vw,56px);border-radius:26px;margin:0 0 25px;box-shadow:0 18px 42px #675bd32c}.hero:before{content:"";position:absolute;z-index:-1;width:240px;height:240px;right:8%;bottom:-180px;border-radius:50%;background:#b5ffe577;filter:blur(14px);animation:orb-float 8s ease-in-out infinite}.hero:after{content:"";position:absolute;z-index:-1;width:300px;height:300px;border:1px solid #ffffff35;border-radius:50%;right:-70px;top:-140px;box-shadow:0 0 0 35px #ffffff10,0 0 0 75px #ffffff0a;animation:ring-drift 22s linear infinite}.hero h1{font-size:clamp(30px,4vw,44px);letter-spacing:-1.2px;line-height:1.13;margin:0 0 12px}.hero p{max-width:750px;margin:0;color:#f0efff;font-size:16px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,250px),1fr));gap:18px}.card{position:relative;background:rgba(255,255,255,.91);backdrop-filter:blur(10px);padding:24px;border:1px solid rgba(226,228,242,.9);border-radius:19px;box-shadow:var(--shadow);margin:0 0 18px;transition:transform .25s cubic-bezier(.2,.8,.2,1),box-shadow .25s;animation:rise-in .55s both}.grid>.card:hover{transform:translateY(-6px) scale(1.012);box-shadow:0 22px 48px rgba(68,58,153,.16)}.grid>.card:nth-child(2){animation-delay:.07s}.grid>.card:nth-child(3){animation-delay:.14s}.grid>.card:nth-child(4){animation-delay:.21s}.card h2,.card h3{letter-spacing:-.3px;line-height:1.3;margin-top:0}.card p{color:#555e75}.stat{font-size:34px;font-weight:800;letter-spacing:-1px;color:var(--primary)}.muted{color:var(--muted)}
.btn{display:inline-flex;align-items:center;justify-content:center;gap:7px;background:linear-gradient(115deg,var(--primary),var(--purple));color:white;padding:11px 17px;border-radius:11px;text-decoration:none;border:0;cursor:pointer;font-size:14px;font-weight:700;box-shadow:0 7px 16px #6157d32b;transition:transform .15s,filter .15s}.btn:hover{filter:brightness(1.08);transform:translateY(-2px);box-shadow:0 12px 25px #6157d344}.btn:active{transform:translateY(0) scale(.98)}.btn.secondary{background:#333951;box-shadow:none}.btn.success{background:#159b7f;box-shadow:none}
input,textarea,select{width:100%;padding:12px 14px;border:1px solid #dfe2ed;border-radius:11px;margin:7px 0 15px;font:inherit;color:var(--ink);background:#fff;outline:0;transition:border-color .15s,box-shadow .15s}input:focus,textarea:focus,select:focus{border-color:#8179e9;box-shadow:0 0 0 4px #7169dd19}textarea{min-height:120px;resize:vertical}label{font-weight:700;color:#383e55}form.card{background:#fafaff;border-style:dashed;border-color:#d8d6f4}table{width:100%;border-collapse:collapse;background:white;border-radius:14px;overflow:hidden}th,td{padding:13px 15px;border-bottom:1px solid var(--line);text-align:left}th{background:#f1f0ff;color:#4f4aa8;font-size:12px;text-transform:uppercase;letter-spacing:.5px}tr:last-child td{border-bottom:0}tr:hover td{background:#fafaff}
.badge{display:inline-flex;align-items:center;background:#f0efff;color:#514bb8;padding:6px 11px;border:1px solid #e5e2ff;border-radius:30px;margin:4px 5px 4px 0;font-size:12px;font-weight:700}.progress{height:10px;background:#e9eaf3;border-radius:20px;overflow:hidden}.progress div{height:100%;background:linear-gradient(90deg,var(--primary),var(--mint));border-radius:inherit}footer{text-align:center;padding:24px;color:#8a90a4;font-size:13px}.flash{border-left:4px solid var(--primary)}
@keyframes rise-in{from{opacity:0;translate:0 15px}to{opacity:1;translate:0 0}}@keyframes ambient-drift{0%{transform:translate3d(-2%,0,0) scale(1)}100%{transform:translate3d(2%,2%,0) scale(1.08)}}@keyframes orb-float{0%,100%{transform:translateY(0) scale(1)}50%{transform:translateY(-22px) scale(1.08)}}@keyframes ring-drift{to{transform:rotate(360deg)}}@keyframes nav-glow{0%,100%{background-position:0% 50%}50%{background-position:100% 50%}}@media(prefers-reduced-motion:reduce){*,*:before,*:after{scroll-behavior:auto!important;animation-duration:.01ms!important;animation-iteration-count:1!important;transition-duration:.01ms!important}}@media(max-width:760px){nav{position:static;padding:12px 4%;align-items:flex-start}nav>div{justify-content:flex-start;width:100%;gap:2px}nav a{padding:7px 8px;font-size:12px}.container{margin-top:20px}.hero{border-radius:20px}.card{padding:19px}table{display:block;overflow-x:auto;white-space:nowrap}}
@media print{nav,footer,.no-print{display:none!important}body{background:white}.card,.hero{box-shadow:none}}
</style>
"""


def page(title, body, **kwargs):
    user = current_user()

    nav = """
    <nav>
      <a class="brand" href="/">🚀 SkillNova AI</a>
      <div>
    """

    if user:
        nav += f"""
        <a href="/dashboard">Dashboard</a>
        <a href="/coding">Coding</a>
        <a href="/quizzes">Quizzes</a>
        <a href="/resources">Resources</a>
        <a href="/roadmap">Roadmap</a>
        <a href="/ai-tutor">AI Tutor</a>
        <a href="/courses">Courses</a>
        <a href="/history">History</a>
        <a href="/resume">Resume</a>
        <a href="/certificates">Certificates</a>
        <a href="/leaderboard">Leaderboard</a>
        <a href="/profile">{user['name']}</a>
        <a href="/logout">Logout</a>
        """
    else:
        nav += """
        <a href="/login">Login</a>
        <a href="/register">Register</a>
        """

    nav += "</div></nav>"

    return render_template_string("""
    <!doctype html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width,initial-scale=1">
        <title>{{ title }} - SkillNova AI</title>
        {{ style|safe }}
    </head>
    <body>
        {{ nav|safe }}

        <main class="container">
            {% with messages = get_flashed_messages() %}
                {% if messages %}
                    {% for message in messages %}
                    <div class="card">{{ message }}</div>
                    {% endfor %}
                {% endif %}
            {% endwith %}

            {{ body|safe }}
        </main>

        <footer>
            SkillNova AI • Personalized Digital Skill Development Platform
        </footer>
    </body>
    </html>
    """, title=title, body=body, style=STYLE, nav=nav, **kwargs)


# ============================================================
# HOME
# ============================================================

@app.route("/")
def home():
    return page("Home", """
    <section class="hero">
        <h1>🚀 SkillNova AI</h1>
        <p>
            Personalized digital skill development and career readiness
            platform for students.
        </p>
        <br>
        <a class="btn" href="/register">Start Learning</a>
        <a class="btn secondary" href="/login">Login</a>
    </section>

    <div class="grid">
        <div class="card">
            <h3>🤖 AI Tutor</h3>
            <p>Ask questions and receive career-focused learning guidance.</p>
        </div>

        <div class="card">
            <h3>🎯 Skill Assessment</h3>
            <p>Identify your strengths and skill gaps.</p>
        </div>

        <div class="card">
            <h3>💻 Coding Practice</h3>
            <p>Practice programming problems and track submissions.</p>
        </div>

        <div class="card">
            <h3>🤖 Role-aware AI Tutor</h3>
            <p>Ask questions and clarify doubts for your chosen career.</p>
        </div>

        <div class="card">
            <h3>📄 Resume Builder</h3>
            <p>Create a professional resume from your profile.</p>
        </div>

        <div class="card">
            <h3>🏆 Gamification</h3>
            <p>Earn XP, badges, levels and compete on the leaderboard.</p>
        </div>
    </div>
    """)


# ============================================================
# AUTH
# ============================================================

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form["name"].strip()
        email = request.form["email"].strip().lower()
        password = request.form["password"]
        career = request.form.get("career", "Full Stack Developer")

        if not name or not email or not password:
            flash("Please fill all fields.")
            return redirect(url_for("register"))

        db = get_db()

        try:
            cur = db.execute("""
                INSERT INTO users(name,email,password,career_goal)
                VALUES(?,?,?,?)
            """, (
                name,
                email,
                generate_password_hash(password),
                career
            ))

            user_id = cur.lastrowid
            db.commit()

            session["user_id"] = user_id

            record_activity(user_id)

            return redirect(url_for("dashboard"))

        except sqlite3.IntegrityError:
            flash("Email already registered.")
            return redirect(url_for("register"))

        finally:
            db.close()

    return page("Register", """
    <div class="card">
        <h2>🚀 Create Your SkillNova Account</h2>

        <form method="post">
            <label>Name</label>
            <input name="name" required>

            <label>Email</label>
            <input type="email" name="email" required>

            <label>Password</label>
            <input type="password" name="password" required>

            <label>Career Goal</label>
            <select name="career">
                <option>Full Stack Developer</option>
                <option>Python Developer</option>
                <option>Java Developer</option>
                <option>AI/ML Engineer</option>
                <option>Data Analyst</option>
            </select>

            <button class="btn">Create Account</button>
        </form>
    </div>
    """)


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        password = request.form["password"]

        db = get_db()
        user = db.execute(
            "SELECT * FROM users WHERE email=?",
            (email,)
        ).fetchone()
        db.close()

        if user and check_password_hash(user["password"], password):
            session["user_id"] = user["id"]
            record_activity(user["id"])
            return redirect(url_for("dashboard"))

        flash("Invalid email or password.")

    return page("Login", """
    <div class="card">
        <h2>🔐 Login</h2>

        <form method="post">
            <label>Email</label>
            <input type="email" name="email" required>

            <label>Password</label>
            <input type="password" name="password" required>

            <button class="btn">Login</button>
        </form>

        <p>New student? <a href="/register">Create account</a></p>
    </div>
    """)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))


# ============================================================
# DASHBOARD
# ============================================================

@app.route("/dashboard")
@login_required
def dashboard():
    user = current_user()
    db = get_db()

    assessments = db.execute("""
        SELECT * FROM assessments
        WHERE user_id=?
        ORDER BY id DESC
        LIMIT 5
    """, (user["id"],)).fetchall()

    submissions = db.execute("""
        SELECT COUNT(*) c FROM coding_submissions
        WHERE user_id=?
    """, (user["id"],)).fetchone()["c"]

    quizzes = db.execute("""
        SELECT COUNT(*) c FROM quiz_results
        WHERE user_id=?
    """, (user["id"],)).fetchone()["c"]

    badges = db.execute("""
        SELECT b.* FROM badges b
        JOIN user_badges ub ON b.id=ub.badge_id
        WHERE ub.user_id=?
    """, (user["id"],)).fetchall()

    db.close()

    return page("Dashboard", f"""
    <section class="hero">
        <h1>Welcome, {user['name']} 👋</h1>
        <p>Career Goal: <strong>{user['career_goal']}</strong></p>
        <p>Keep learning and build your career step by step.</p>
    </section>

    <div class="grid">
        <div class="card">
            <div class="stat">{user['xp']}</div>
            <div>⭐ XP</div>
        </div>

        <div class="card">
            <div class="stat">{user['level']}</div>
            <div>🏅 Level</div>
        </div>

        <div class="card">
            <div class="stat">🔥 {user['streak']}</div>
            <div>Current Streak</div>
        </div>

        <div class="card">
            <div class="stat">{user['longest_streak']}</div>
            <div>Longest Streak</div>
        </div>

        <div class="card">
            <div class="stat">{submissions}</div>
            <div>Coding Submissions</div>
        </div>

        <div class="card">
            <div class="stat">{quizzes}</div>
            <div>Quizzes Completed</div>
        </div>

        <div class="card">
            <div class="stat">{len(badges)}</div>
            <div>Badges</div>
        </div>
    </div>

    <div class="card">
        <h2>⚡ Quick Actions</h2>
        <a class="btn" href="/assessment">Take Assessment</a>
        <a class="btn" href="/coding">Practice Coding</a>
        <a class="btn" href="/quizzes">Take Quiz</a>
        <a class="btn" href="/ai-tutor">Ask the AI Tutor</a>
        <a class="btn" href="/courses">Role Courses</a>
        <a class="btn" href="/history">Submission History</a>
        <a class="btn" href="/resume">Build Resume</a>
    </div>

    <div class="card">
        <h2>🏅 Your Badges</h2>
        {"".join(
            f"<span class='badge'>{b['icon']} {b['name']}</span>"
            for b in badges
        ) or "<p class='muted'>Complete activities to unlock badges.</p>"}
    </div>

    <div class="card">
        <h2>📊 Recent Assessments</h2>
        {"".join(
            f"<p>Score: <strong>{a['score']}/{a['total']}</strong> "
            f"({a['percentage']:.0f}%)</p>"
            for a in assessments
        ) or "<p>No assessments yet.</p>"}
    </div>
    """)


# ============================================================
# ASSESSMENT
# ============================================================

@app.route("/assessment", methods=["GET", "POST"])
@login_required
def assessment():
    user = current_user()

    import random
    import re

    # =========================================================
    # Assessment question bank
    # =========================================================
    question_bank = [
        # Python
        ("Python", "Which keyword defines a Python function?", "def"),
        ("Python", "Which keyword is used to handle exceptions?", "try"),
        ("Python", "Which data structure stores key-value pairs?", "dictionary"),
        ("Python", "Which symbol is used for comments in Python?", "#"),
        ("Python", "Which function returns the length of a list?", "len"),
        ("Python", "Which keyword is used to create a loop over items?", "for"),
        ("Python", "Which keyword stops a loop immediately?", "break"),
        ("Python", "Which Python collection is ordered and mutable?", "list"),
        ("Python", "Which keyword is used to define a class?", "class"),
        ("Python", "Which operator is used for exponentiation?", "**"),
        ("Python", "Which method adds an item to the end of a list?", "append"),
        ("Python", "Which value represents the absence of a value in Python?", "none"),
        ("Python", "Which function converts a string into an integer?", "int"),
        ("Python", "Which keyword returns a value from a function?", "return"),
        ("Python", "Which data type stores unique unordered values?", "set"),

        # DSA
        ("DSA", "Which data structure follows FIFO?", "queue"),
        ("DSA", "Which data structure follows LIFO?", "stack"),
        ("DSA", "Which algorithm searches a sorted array by repeatedly dividing the range?", "binary search"),
        ("DSA", "What is the worst-case time complexity of linear search?", "o(n)"),
        ("DSA", "Which data structure consists of nodes connected by links?", "linked list"),
        ("DSA", "Which traversal visits root, left subtree, and right subtree?", "preorder"),
        ("DSA", "Which traversal visits left subtree, root, and right subtree?", "inorder"),
        ("DSA", "Which traversal visits left subtree, right subtree, and root?", "postorder"),
        ("DSA", "Which algorithm finds the shortest path in an unweighted graph?", "bfs"),
        ("DSA", "Which data structure is commonly used by BFS?", "queue"),
        ("DSA", "Which data structure is commonly used by DFS?", "stack"),
        ("DSA", "What is the average time complexity of quicksort?", "o(n log n)"),
        ("DSA", "Which technique stores results of overlapping subproblems?", "dynamic programming"),
        ("DSA", "What is the time complexity of accessing an array element by index?", "o(1)"),
        ("DSA", "Which sorting algorithm repeatedly swaps adjacent elements?", "bubble sort"),

        # SQL
        ("SQL", "Which language is used for relational database queries?", "sql"),
        ("SQL", "Which command retrieves data from a table?", "select"),
        ("SQL", "Which command adds a new row to a table?", "insert"),
        ("SQL", "Which command modifies existing records?", "update"),
        ("SQL", "Which command removes records from a table?", "delete"),
        ("SQL", "Which clause filters rows in SQL?", "where"),
        ("SQL", "Which clause sorts query results?", "order by"),
        ("SQL", "Which clause groups rows with the same values?", "group by"),
        ("SQL", "Which function counts rows?", "count"),
        ("SQL", "Which keyword removes duplicate results?", "distinct"),
        ("SQL", "Which key uniquely identifies a row?", "primary key"),
        ("SQL", "Which key creates a relationship between tables?", "foreign key"),
        ("SQL", "Which operator combines results from two queries?", "union"),
        ("SQL", "Which clause filters grouped results?", "having"),
        ("SQL", "Which command creates a new table?", "create"),

        # OOP
        ("OOP", "Which concept hides internal implementation?", "encapsulation"),
        ("OOP", "Which concept allows one class to acquire properties of another?", "inheritance"),
        ("OOP", "Which concept allows the same interface to have different implementations?", "polymorphism"),
        ("OOP", "Which concept represents essential features while hiding unnecessary details?", "abstraction"),
        ("OOP", "What is an instance of a class called?", "object"),
        ("OOP", "Which method is commonly called when an object is created?", "constructor"),
        ("OOP", "What is a blueprint for creating objects?", "class"),
        ("OOP", "Which relationship represents an is-a relationship?", "inheritance"),
        ("OOP", "Which relationship represents a has-a relationship?", "composition"),
        ("OOP", "What allows multiple methods with the same name to behave differently?", "polymorphism"),
        ("OOP", "Which principle keeps data and methods together?", "encapsulation"),
        ("OOP", "Which OOP principle reduces implementation complexity for users?", "abstraction"),
        ("OOP", "What is method overriding?", "redefining inherited method"),
        ("OOP", "What is method overloading?", "same method name with different parameters"),
        ("OOP", "Which feature enables code reuse through parent-child classes?", "inheritance"),

        # Computer Networks
        ("Computer Networks", "Which protocol is commonly used for web communication?", "http"),
        ("Computer Networks", "Which secure protocol is used for encrypted web communication?", "https"),
        ("Computer Networks", "Which protocol translates domain names to IP addresses?", "dns"),
        ("Computer Networks", "Which protocol automatically assigns IP addresses?", "dhcp"),
        ("Computer Networks", "Which device forwards packets between networks?", "router"),
        ("Computer Networks", "Which device connects devices within a LAN?", "switch"),
        ("Computer Networks", "What does IP stand for?", "internet protocol"),
        ("Computer Networks", "Which protocol is connection-oriented?", "tcp"),
        ("Computer Networks", "Which protocol is connectionless?", "udp"),
        ("Computer Networks", "Which OSI layer handles routing?", "network"),
        ("Computer Networks", "Which layer provides reliable end-to-end delivery?", "transport"),
        ("Computer Networks", "How many layers are in the OSI model?", "7"),
        ("Computer Networks", "Which protocol is commonly used to send email?", "smtp"),
        ("Computer Networks", "Which protocol is commonly used to transfer files?", "ftp"),
        ("Computer Networks", "What identifies a device on an IP network?", "ip address"),

        # Operating Systems
        ("Operating Systems", "Which algorithm gives each process a time slice?", "round robin"),
        ("Operating Systems", "Which component manages hardware and system resources?", "operating system"),
        ("Operating Systems", "What is a program currently being executed called?", "process"),
        ("Operating Systems", "What is the smallest unit of CPU execution?", "thread"),
        ("Operating Systems", "Which scheduling algorithm selects the shortest job first?", "sjf"),
        ("Operating Systems", "What occurs when processes wait indefinitely for resources?", "deadlock"),
        ("Operating Systems", "Which memory technique divides memory into fixed-size pages?", "paging"),
        ("Operating Systems", "What is virtual memory?", "memory management technique"),
        ("Operating Systems", "Which component manages files?", "file system"),
        ("Operating Systems", "What is context switching?", "switching between processes"),
        ("Operating Systems", "Which scheduling algorithm uses priorities?", "priority scheduling"),
        ("Operating Systems", "What does CPU stand for?", "central processing unit"),
        ("Operating Systems", "What does RAM stand for?", "random access memory"),
        ("Operating Systems", "Which memory is faster than RAM?", "cache"),
        ("Operating Systems", "What is starvation in operating systems?", "indefinite waiting"),

        # Java
        ("Java", "Which keyword creates an object in Java?", "new"),
        ("Java", "Which keyword defines a class?", "class"),
        ("Java", "Which method is the entry point of a Java application?", "main"),
        ("Java", "Which keyword is used for inheritance?", "extends"),
        ("Java", "Which keyword implements an interface?", "implements"),
        ("Java", "Which keyword prevents inheritance?", "final"),
        ("Java", "Which collection stores key-value pairs?", "hashmap"),
        ("Java", "Which collection does not allow duplicate elements?", "set"),
        ("Java", "Which keyword handles an exception?", "catch"),
        ("Java", "Which keyword explicitly throws an exception?", "throw"),
        ("Java", "Which keyword refers to the current object?", "this"),
        ("Java", "Which package contains ArrayList?", "java.util"),
        ("Java", "Which OOP concept allows different implementations of the same interface?", "polymorphism"),
        ("Java", "Which keyword is used to call the parent class constructor?", "super"),
        ("Java", "Which keyword declares a constant variable?", "final"),

        # Machine Learning
        ("Machine Learning", "Learning from labelled examples is called?", "supervised learning"),
        ("Machine Learning", "Learning without labelled data is called?", "unsupervised learning"),
        ("Machine Learning", "Which algorithm is commonly used for classification?", "decision tree"),
        ("Machine Learning", "What is overfitting?", "poor generalization"),
        ("Machine Learning", "Which technique scales features to a common range?", "normalization"),
        ("Machine Learning", "What is a feature?", "input variable"),
        ("Machine Learning", "What is a label?", "target variable"),
        ("Machine Learning", "Which algorithm clusters similar data points?", "k-means"),
        ("Machine Learning", "What does training data do?", "trains the model"),
        ("Machine Learning", "What does test data evaluate?", "model performance"),
        ("Machine Learning", "Which metric measures correct predictions among all predictions?", "accuracy"),
        ("Machine Learning", "Which algorithm predicts a continuous value?", "linear regression"),
        ("Machine Learning", "What is underfitting?", "model too simple"),
        ("Machine Learning", "Which technique helps reduce overfitting?", "regularization"),
        ("Machine Learning", "What is a model in machine learning?", "learned representation"),

        # Web Development
        ("Web Development", "Which language structures web pages?", "html"),
        ("Web Development", "Which language styles web pages?", "css"),
        ("Web Development", "Which language adds interactivity to web pages?", "javascript"),
        ("Web Development", "What does HTML stand for?", "hypertext markup language"),
        ("Web Development", "What does CSS stand for?", "cascading style sheets"),
        ("Web Development", "Which HTML tag creates a hyperlink?", "a"),
        ("Web Development", "Which HTML tag creates an image?", "img"),
        ("Web Development", "Which CSS property changes text color?", "color"),
        ("Web Development", "Which CSS property changes the background color?", "background-color"),
        ("Web Development", "Which JavaScript keyword declares a block-scoped variable?", "let"),
        ("Web Development", "Which JavaScript keyword declares a constant?", "const"),
        ("Web Development", "What does HTTP stand for?", "hypertext transfer protocol"),
        ("Web Development", "What does URL stand for?", "uniform resource locator"),
        ("Web Development", "Which HTTP method is commonly used to submit data?", "post"),
        ("Web Development", "Which HTTP status code means Not Found?", "404"),
    ]

    # =========================================================
    # Normalize answer
    # =========================================================
    def normalize_answer(value):
        value = (value or "").strip().lower()
        value = value.replace("_", " ")
        value = re.sub(r"\s+", " ", value)
        return value

    # =========================================================
    # Create question-history table
    # =========================================================
    db = get_db()

    db.execute("""
        CREATE TABLE IF NOT EXISTS assessment_question_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            question_text TEXT NOT NULL,
            seen_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, question_text)
        )
    """)
    db.commit()

    # =========================================================
    # Get questions already answered by this user
    # =========================================================
    seen_rows = db.execute(
        """
        SELECT question_text
        FROM assessment_question_history
        WHERE user_id=?
        """,
        (user["id"],)
    ).fetchall()

    seen = {row["question_text"] for row in seen_rows}

    # =========================================================
    # Career-aware topic preference
    # =========================================================
    career = normalize_answer(user["career_goal"] if user["career_goal"] else "")

    if "data" in career or "analyst" in career:
        preferred_topics = [
            "SQL",
            "Python",
            "Machine Learning",
            "DSA",
            "OOP",
            "Computer Networks",
            "Operating Systems",
            "Java",
            "Web Development"
        ]
    elif "software" in career or "developer" in career:
        preferred_topics = [
            "Python",
            "DSA",
            "OOP",
            "Java",
            "Web Development",
            "SQL",
            "Computer Networks",
            "Operating Systems",
            "Machine Learning"
        ]
    else:
        preferred_topics = [
            "Python",
            "DSA",
            "SQL",
            "OOP",
            "Computer Networks",
            "Operating Systems",
            "Java",
            "Machine Learning",
            "Web Development"
        ]

    # =========================================================
    # Randomize question order
    # =========================================================
    topic_questions = {}

    for item in question_bank:
        topic_questions.setdefault(item[0], []).append(item)

    for topic in topic_questions:
        random.shuffle(topic_questions[topic])

    # =========================================================
    # Select 10 unseen questions
    # =========================================================
    questions = []

    for topic in preferred_topics:
        available = [
            q for q in topic_questions.get(topic, [])
            if q[1] not in seen
        ]

        if available:
            questions.append(random.choice(available))

        if len(questions) == 10:
            break

    # Fill remaining slots if needed.
    if len(questions) < 10:
        remaining = [
            q for q in question_bank
            if q[1] not in seen and q not in questions
        ]

        random.shuffle(remaining)

        questions.extend(remaining[:10 - len(questions)])

    # =========================================================
    # If all questions were already used, start a new cycle.
    # =========================================================
    if len(questions) < 10:
        db.execute(
            "DELETE FROM assessment_question_history WHERE user_id=?",
            (user["id"],)
        )
        db.commit()

        random.shuffle(question_bank)
        questions = question_bank[:10]

    # Close GET-selection connection.
    db.close()

    # =========================================================
    # POST - evaluate assessment
    # =========================================================
    if request.method == "POST":

        score = 0
        strong = []
        weak = []

        for i, (skill, question, expected) in enumerate(questions):

            submitted = normalize_answer(
                request.form.get(f"q{i}", "")
            )

            expected = normalize_answer(expected)

            accepted = {expected}

            # Common equivalent answers.
            equivalents = {
                "none": {"null", "none value"},
                "sql": {"structured query language"},
                "http": {"http protocol"},
                "https": {"https protocol"},
                "bfs": {"breadth first search"},
                "dfs": {"depth first search"},
                "supervised learning": {"supervised"},
                "unsupervised learning": {"unsupervised"},
                "html": {"hypertext markup language"},
                "css": {"cascading style sheets"},
                "javascript": {"js"},
                "o(n)": {"on", "linear", "linear time"},
                "o(1)": {"o1", "constant", "constant time"},
                "o(n log n)": {"on log n", "n log n"},
            }

            accepted.update(equivalents.get(expected, set()))

            if submitted in accepted:
                score += 1
                strong.append(skill)
            else:
                weak.append(skill)

        total = len(questions)
        percentage = (score / total) * 100 if total else 0

        # =====================================================
        # Save questions to history only after submission.
        # =====================================================
        db = get_db()

        for skill, question, expected in questions:
            db.execute(
                """
                INSERT OR IGNORE INTO assessment_question_history
                (user_id, question_text)
                VALUES (?, ?)
                """,
                (user["id"], question)
            )

        db.execute(
            """
            INSERT INTO assessments
            (user_id, score, total, percentage, strong_skills, weak_skills)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                user["id"],
                score,
                total,
                percentage,
                ", ".join(dict.fromkeys(strong)),
                ", ".join(dict.fromkeys(weak))
            )
        )

        db.commit()
        db.close()

        add_xp(user["id"], 75)
        record_activity(user["id"], "assessment")
        award_badge(user["id"], "Skill Builder")

        return page("Assessment Result", f"""
        <div class="hero">
            <h1>🎯 Assessment Complete</h1>
            <h2>{score}/{total}</h2>
            <p>{percentage:.0f}%</p>
        </div>

        <div class="grid">

            <div class="card">
                <h3>💪 Strong Skills</h3>
                <p>
                    {", ".join(dict.fromkeys(strong))
                    or "Keep practicing"}
                </p>
            </div>

            <div class="card">
                <h3>📚 Skill Gaps</h3>
                <p>
                    {", ".join(dict.fromkeys(weak))
                    or "No major gaps detected"}
                </p>
            </div>

        </div>

        <div class="card">
            <h3>🔄 Take Another Assessment</h3>
            <p>
                A fresh set of questions will be generated
                for your next attempt.
            </p>

            <a class="btn" href="/assessment">
                Take New Assessment
            </a>

            <a class="btn" href="/roadmap">
                View Roadmap
            </a>

            <a class="btn" href="/coding">
                Practice Coding
            </a>
        </div>
        """)

    # =========================================================
    # GET - display assessment
    # =========================================================
    html = """
    <div class="card">
        <h2>🎯 Skill Assessment</h2>

        <p>
            Answer the following questions.
            Your questions are selected based on your learning profile
            and previous assessment history.
        </p>

        <form method="post">
    """

    for i, (skill, question, expected) in enumerate(questions):

        html += f"""
        <div class="card">
            <strong>
                {i + 1}. {question}
            </strong>

            <input
                name="q{i}"
                required
                placeholder="Your answer"
                autocomplete="off"
            >
        </div>
        """

    html += """
            <button class="btn">
                Submit Assessment
            </button>

        </form>
    </div>
    """

    return page("Assessment", html)


# ============================================================
# ROADMAP
# ============================================================

@app.route("/roadmap")
@login_required
def roadmap():
    user = current_user()
    profile = learner_assignment_profile(user)
    db = get_db()
    rows = db.execute("SELECT * FROM roadmap WHERE career=? ORDER BY order_no", (user["career_goal"],)).fetchall()
    assessment = db.execute("SELECT strong_skills, weak_skills, percentage FROM assessments WHERE user_id=? ORDER BY id DESC LIMIT 1", (user["id"],)).fetchone()
    quiz_rows = db.execute("""
        SELECT topic, AVG(CASE WHEN total>0 THEN CAST(score AS REAL)*100/total END) AS pct
        FROM quiz_results WHERE user_id=? GROUP BY topic
    """, (user["id"],)).fetchall()
    coding_rows = db.execute("""
        SELECT p.topic, AVG(CASE WHEN s.passed=1 THEN 100.0 ELSE 0.0 END) AS pct
        FROM coding_submissions s JOIN coding_problems p ON p.id=s.problem_id
        WHERE s.user_id=? GROUP BY p.topic
    """, (user["id"],)).fetchall()
    db.close()

    def norm(value):
        return " ".join(str(value or "").lower().replace("/", " ").replace("-", " ").split())

    weak = {norm(item) for item in (assessment["weak_skills"] or "").split(",") if item.strip()} if assessment else set()
    strong = {norm(item) for item in (assessment["strong_skills"] or "").split(",") if item.strip()} if assessment else set()
    quiz_scores = {norm(row["topic"]): float(row["pct"]) for row in quiz_rows if row["pct"] is not None}
    coding_scores = {norm(row["topic"]): float(row["pct"]) for row in coding_rows if row["pct"] is not None}
    aliases = {
        "web development": ["javascript", "html css", "react", "flask"],
        "data analysis": ["sql", "statistics", "python", "data analysis"],
        "problem solving": ["arrays", "strings", "searching", "sorting", "stack", "queue", "linked list", "trees", "graphs", "dynamic programming", "math"],
        "deep learning": ["machine learning", "python"],
        "mathematics": ["statistics", "math"],
        "oop": ["oop", "java", "python"],
        "dsa": ["arrays", "strings", "searching", "sorting", "stack", "queue", "linked list", "trees", "graphs", "dynamic programming"],
    }

    prioritized = []
    for row in rows:
        skill = row["skill"]
        key = norm(skill)
        topics = {key, *aliases.get(key, [])}
        signals = []
        if key in weak:
            signals.append(40.0)
        if key in strong:
            signals.append(90.0)
        signals.extend(value for topic, value in quiz_scores.items() if topic in topics)
        signals.extend(value for topic, value in coding_scores.items() if topic in topics)
        if not signals and profile["assessment_percentage"] is not None:
            signals.append(float(profile["assessment_percentage"]))
        score = round(sum(signals) / len(signals)) if signals else None
        prioritized.append((score, row))

    # Put the skills with the clearest evidence of difficulty first; retain curriculum order for ties.
    prioritized.sort(key=lambda item: (item[0] if item[0] is not None else 75, item[1]["order_no"]))
    html = f"""
    <div class="hero"><h1>🗺️ Your Career Roadmap</h1>
    <p>{escape(user['career_goal'])}</p></div>
    <div class="card"><p>Roadmap order uses your assessment, quiz, and accepted coding results. Take more practice to improve these recommendations.</p></div>
    """
    for index, (score, row) in enumerate(prioritized, 1):
        if score is None:
            status, label, width = "Start here", "No performance data yet", min(100, user["level"] * 10)
        elif score < 50:
            status, label, width = "Focus next", f"Current evidence: {score}%", score
        elif score < 75:
            status, label, width = "Practice next", f"Current evidence: {score}%", score
        else:
            status, label, width = "On track", f"Current evidence: {score}%", score
        html += f"""
        <div class="card"><span class="badge">Priority {index} · {escape(status)}</span>
        <h2>{escape(str(row['phase']))} → {escape(str(row['skill']))}</h2>
        <p>{escape(str(row['description']))}</p><p>{escape(label)}</p>
        <div class="progress"><div style="width:{max(0, min(100, int(width)))}%"></div></div></div>
        """
    if not rows:
        html += "<div class='card'><h3>Roadmap coming soon</h3><p>Your selected career currently has no roadmap data.</p></div>"
    return page("Roadmap", html)


# ============================================================
# CODING
# ============================================================

ROLE_CODING_TOPICS = {
    "Full Stack Developer": ["Arrays", "Strings", "Searching", "Sorting", "Stack", "Queue", "Linked List", "Trees", "Graphs", "Dynamic Programming", "Math", "SQL"],
    "Python Developer": ["Arrays", "Strings", "Searching", "Sorting", "Stack", "Queue", "Linked List", "Trees", "Graphs", "Dynamic Programming", "Math", "SQL"],
    "Java Developer": ["Arrays", "Strings", "Searching", "Sorting", "Stack", "Queue", "Linked List", "Trees", "Graphs", "Dynamic Programming", "Math", "SQL"],
    "AI/ML Engineer": ["Arrays", "Strings", "Searching", "Sorting", "Stack", "Queue", "Linked List", "Trees", "Graphs", "Dynamic Programming", "Math", "SQL"],
    "Data Analyst": ["Arrays", "Strings", "Searching", "Math", "SQL"],
}

ROLE_INTERVIEW_CATEGORIES = {
    "Full Stack Developer": ["Full Stack", "JavaScript", "Python", "SQL", "HR"],
    "Python Developer": ["Python", "SQL", "DBMS", "HR"],
    "Java Developer": ["Java", "DBMS", "Computer Networks", "HR"],
    "AI/ML Engineer": ["AI/ML", "Python", "SQL", "HR"],
    "Data Analyst": ["SQL", "Python", "DBMS", "HR"],
}

ROLE_QUIZ_TOPICS = {
    "Full Stack Developer": ["JavaScript", "Python", "SQL", "DSA", "Computer Networks", "DBMS", "Operating Systems", "OOP", "HTML/CSS", "React", "Flask", "Cloud Computing"],
    "Python Developer": ["Python", "Java", "C++", "DSA", "DBMS", "OOP", "SQL", "Statistics", "Flask", "Cloud Computing"],
    "Java Developer": ["Java", "C++", "DSA", "DBMS", "OOP", "Computer Networks", "Operating Systems", "SQL", "Cloud Computing"],
    "AI/ML Engineer": ["Python", "C++", "Machine Learning", "DSA", "DBMS", "Statistics", "Data Analysis", "Cloud Computing"],
    "Data Analyst": ["Python", "SQL", "Statistics", "Data Analysis", "Machine Learning", "DBMS", "Cloud Computing"],
}


def coding_normalize(value):
    if value is None:
        return ""

    return "".join(str(value).split()).lower()


def coding_run_process(command, cwd, input_data=""):
    try:
        result = subprocess.run(
            command,
            input=str(input_data),
            text=True,
            capture_output=True,
            cwd=cwd,
            timeout=5,
            shell=False
        )

        return {
            "ok": result.returncode == 0,
            "output": (result.stdout or "")[:10000].strip(),
            "error": (result.stderr or "")[:10000].strip()
        }

    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "output": "",
            "error": "Time limit exceeded."
        }

    except FileNotFoundError:
        return {
            "ok": False,
            "output": "",
            "error": "Required compiler or runtime is not installed."
        }

    except Exception as e:
        return {
            "ok": False,
            "output": "",
            "error": str(e)[:10000]
        }


def coding_run_python(code, input_data):
    with tempfile.TemporaryDirectory() as temp:

        source = Path(temp) / "solution.py"

        wrapper = f"""
import json

{code}

_functions = []

for _name, _value in globals().items():
    if callable(_value) and not _name.startswith("_"):
        _functions.append(_value)

if not _functions:
    raise Exception("No solution function found.")

_fn = _functions[0]

_raw = {json.dumps(str(input_data))}

try:
    _input = json.loads(_raw)
except Exception:
    _input = _raw

if isinstance(_input, list):
    _result = _fn(*_input)
else:
    _result = _fn(_input)

print(json.dumps(_result))
"""

        source.write_text(wrapper, encoding="utf-8")

        return coding_run_process(
            ["python", str(source)],
            temp
        )


def coding_run_javascript(code, input_data):
    with tempfile.TemporaryDirectory() as temp:

        source = Path(temp) / "solution.js"

        wrapper = f"""
{code}

const rawInput = {json.dumps(str(input_data))};

let input;

try {{
    input = JSON.parse(rawInput);
}} catch {{
    input = rawInput;
}}

let fn = null;

for (const name of Object.keys(globalThis)) {{
    if (
        typeof globalThis[name] === "function" &&
        !name.startsWith("_")
    ) {{
        fn = globalThis[name];
        break;
    }}
}}

if (!fn) {{
    throw new Error("No solution function found.");
}}

let result;

if (Array.isArray(input)) {{
    result = fn(...input);
}} else {{
    result = fn(input);
}}

console.log(JSON.stringify(result));
"""

        source.write_text(wrapper, encoding="utf-8")

        return coding_run_process(
            ["node", str(source)],
            temp
        )


def coding_run_java(code, input_data):
    with tempfile.TemporaryDirectory() as temp:

        source = Path(temp) / "Main.java"

        full_code = f"""
import java.util.*;

public class Main {{
    public static void main(String[] args) throws Exception {{
        {code}
    }}
}}
"""

        source.write_text(full_code, encoding="utf-8")

        compile_result = coding_run_process(
            ["javac", str(source)],
            temp
        )

        if not compile_result["ok"]:
            return compile_result

        return coding_run_process(
            ["java", "-cp", temp, "Main"],
            temp,
            input_data
        )


def coding_run_c(code, input_data):
    with tempfile.TemporaryDirectory() as temp:
        source = Path(temp) / "main.c"
        executable = Path(temp) / "main.exe"
        source.write_text(code, encoding="utf-8")
        compile_result = coding_run_process(
            ["gcc", str(source), "-O2", "-o", str(executable)], temp
        )
        if not compile_result["ok"]:
            return compile_result
        return coding_run_process([str(executable)], temp, input_data)


def coding_run_cpp(code, input_data):
    with tempfile.TemporaryDirectory() as temp:

        source = Path(temp) / "main.cpp"
        executable = Path(temp) / "main.exe"

        source.write_text(code, encoding="utf-8")

        compile_result = coding_run_process(
            [
                "g++",
                str(source),
                "-std=c++17",
                "-O2",
                "-o",
                str(executable)
            ],
            temp
        )

        if not compile_result["ok"]:
            return compile_result

        return coding_run_process(
            [str(executable)],
            temp,
            input_data
        )


def coding_run_sql(code, input_data):
    try:
        fixture = json.loads(str(input_data))
        tables = fixture.get("tables", {})
        connection = sqlite3.connect(":memory:")
        for table_name, table_data in tables.items():
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table_name):
                raise ValueError("Invalid table name in test data.")
            columns = table_data.get("columns", [])
            column_sql = []
            for column_name, column_type in columns:
                if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", column_name) or column_type.upper() not in {"INTEGER", "REAL", "TEXT", "NUMERIC"}:
                    raise ValueError("Invalid column definition in test data.")
                column_sql.append(f'"{column_name}" {column_type.upper()}')
            connection.execute(f'CREATE TABLE "{table_name}" ({", ".join(column_sql)})')
            if table_data.get("rows"):
                marks = ",".join("?" for _ in columns)
                connection.executemany(f'INSERT INTO "{table_name}" VALUES ({marks})', table_data["rows"])
        statement = code.strip()
        if not re.match(r"^(SELECT|WITH)\b", statement, re.IGNORECASE):
            raise ValueError("Only read-only SELECT queries are allowed.")
        connection.execute("PRAGMA query_only = ON")
        steps = [0]
        def limit_sql_work():
            steps[0] += 1
            return 1 if steps[0] > 10000 else 0
        connection.set_progress_handler(limit_sql_work, 1000)
        rows = connection.execute(statement).fetchall()
        output = json.dumps([list(row) for row in rows], ensure_ascii=False, separators=(",", ":"))
        connection.close()
        return {"ok": True, "output": output, "error": ""}
    except Exception as exc:
        try:
            connection.close()
        except Exception:
            pass
        return {"ok": False, "output": "", "error": str(exc)[:1000]}


def coding_execute(language, code, input_data):

    if language == "SQL":
        return coding_run_sql(code, input_data)

    if language == "Python":
        return coding_run_python(code, input_data)

    if language == "JavaScript":
        return coding_run_javascript(code, input_data)

    if language == "Java":
        return coding_run_java(code, input_data)

    if language == "C++":
        return coding_run_cpp(code, input_data)

    if language == "C":
        return coding_run_c(code, input_data)

    return {
        "ok": False,
        "output": "",
        "error": "Unsupported programming language."
    }


@app.route("/coding", methods=["GET", "POST"])
@login_required
def coding():

    user = current_user()
    role = user["career_goal"]

    allowed_topics = ROLE_CODING_TOPICS.get(
        role,
        ROLE_CODING_TOPICS["Full Stack Developer"]
    )

    topic = request.args.get("topic", "").strip()

    if request.method == "POST" and request.form.get("action") == "generate_problem":
        requested_topic = request.form.get("topic", "").strip()
        requirement = request.form.get("requirement", "").strip()[:1200]
        if requested_topic not in allowed_topics or not requirement:
            flash("Choose one of your role topics and describe the practice you need.")
            return redirect(url_for("coding"))
        try:
            generated = skillnova_ai_json(f"""
Create one original programming practice problem for a {role} learner.
The requested topic is {requested_topic}. User requirement: {requirement}
Return JSON with title, difficulty (Easy/Medium/Hard), description,
example_input, example_output, and test_cases (array of objects with input
and expected_output). Include 3 to 5 valid test cases. For SQL, each input
must be a JSON object with a top-level tables property, mapping each table name to columns (pairs of column name and one of INTEGER/TEXT/REAL/NUMERIC) and rows; expected_output is a JSON array of result rows, and the task must be solved by one read-only SELECT query. For other topics, use simple whitespace-separated stdin data that works in Python, JavaScript, Java, C, and C++. For Python and JavaScript, name the function solve; their runner may pass a parsed number for a single-number input, otherwise it passes the input text. Do not repeat a standard title if a more specific title fits.
""")
            title = str(generated.get("title", "")).strip()[:160]
            description = str(generated.get("description", "")).strip()[:4000]
            cases = generated.get("test_cases", [])
            if not title or not description or not isinstance(cases, list) or not cases:
                raise ValueError("The generated problem was incomplete.")
            db = get_db()
            duplicate = db.execute("SELECT 1 FROM coding_problems WHERE lower(title)=lower(?) AND (role=? OR role IS NULL)", (title, role)).fetchone()
            if duplicate:
                db.close()
                raise ValueError("That title already exists. Change your requirement and try again.")
            cur = db.execute("""
                INSERT INTO coding_problems
                (title,topic,difficulty,description,example_input,example_output,xp,role)
                VALUES(?,?,?,?,?,?,?,?)
            """, (title, requested_topic, str(generated.get("difficulty", "Medium"))[:20], description,
                  str(generated.get("example_input", ""))[:1000], str(generated.get("example_output", ""))[:1000], 30, role))
            problem_id = cur.lastrowid
            for case in cases[:5]:
                if not isinstance(case, dict) or "input" not in case or "expected_output" not in case:
                    continue
                db.execute("INSERT INTO coding_test_cases(problem_id,input,expected_output) VALUES(?,?,?)",
                           (problem_id, str(case["input"])[:2000], str(case["expected_output"])[:2000]))
            if db.execute("SELECT COUNT(*) FROM coding_test_cases WHERE problem_id=?", (problem_id,)).fetchone()[0] == 0:
                db.rollback()
                db.close()
                raise ValueError("The generated problem had no usable test cases.")
            db.commit()
            db.close()
            flash("A new practice problem was created for your selected role and topic.")
            return redirect(url_for("coding", topic=requested_topic))
        except Exception as exc:
            flash(f"Could not generate a problem: {exc}")
            return redirect(url_for("coding"))

    db = get_db()

    placeholders = ",".join(["?"] * len(allowed_topics))

    if topic and topic in allowed_topics:

        problems = db.execute(
            """
            SELECT *
            FROM coding_problems
            WHERE topic=? AND (role IS NULL OR role=?)
            ORDER BY id
            """,
            (topic, role)
        ).fetchall()

    else:

        problems = db.execute(
            f"""
            SELECT *
            FROM coding_problems
            WHERE topic IN ({placeholders}) AND (role IS NULL OR role=?)
            ORDER BY id
            """,
            (*allowed_topics, role)
        ).fetchall()

    topics = db.execute(
        f"""
        SELECT DISTINCT topic
        FROM coding_problems
        WHERE topic IN ({placeholders}) AND (role IS NULL OR role=?)
        ORDER BY topic
        """,
        (*allowed_topics, role)
    ).fetchall()

    db.execute("""
        CREATE TABLE IF NOT EXISTS coding_problem_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            problem_id INTEGER NOT NULL,
            seen_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, problem_id)
        )
    """)

    db.commit()
    db.close()

    html = f"""
    <div class="hero">
        <h1>💻 Coding Practice</h1>
        <p>{len(problems)} problems matched to your {escape(role)} career track.</p>
    </div>

    <div class="card">

        <h3>Your coding topics</h3>

        <p>
            {escape(", ".join(allowed_topics))}
        </p>

        <form method="post" class="card">
            <input type="hidden" name="action" value="generate_problem">
            <label>Generate a problem for what you need</label>
            <select name="topic" required>
                {"".join(f"<option value='{escape(t)}'>{escape(t)}</option>" for t in allowed_topics)}
            </select>
            <textarea name="requirement" required maxlength="1200"
                placeholder="Example: Give me an SQL join problem using employee and department tables"></textarea>
            <button class="btn" type="submit">Generate Practice Problem</button>
        </form>

        <a class="badge" href="/coding">
            All
        </a>
    """

    for item in topics:

        html += f"""
        <a class="badge"
           href="/coding?topic={quote(str(item['topic']))}">
            {escape(str(item['topic']))}
        </a>
        """

    html += """
    </div>

    <div class="grid">
    """

    for problem in problems:

        html += f"""
        <div class="card">

            <span class="badge">
                {escape(str(problem['difficulty']))}
            </span>

            <span class="badge">
                {escape(str(problem['topic']))}
            </span>

            <h3>
                {escape(str(problem['title']))}
            </h3>

            <p>
                {escape(str(problem['description']))}
            </p>

            <p>
                <strong>XP:</strong>
                {problem['xp']}
            </p>

            <a class="btn"
               href="/coding/{problem['id']}">
                Solve
            </a>

        </div>
        """

    if not problems:
        html += "<div class='card'><h3>No problems for this topic yet</h3><p>Choose another role topic or request a custom practice problem above.</p></div>"

    html += "</div>"

    return page("Coding Practice", html)


@app.route("/coding/<int:problem_id>", methods=["GET", "POST"])
@login_required
def coding_problem(problem_id):

    user = current_user()

    db = get_db()

    problem = db.execute(
        """
        SELECT *
        FROM coding_problems
        WHERE id=?
        """,
        (problem_id,)
    ).fetchone()

    test_cases = db.execute(
        """
        SELECT *
        FROM coding_test_cases
        WHERE problem_id=?
        ORDER BY id
        """,
        (problem_id,)
    ).fetchall()

    db.execute("""
        CREATE TABLE IF NOT EXISTS coding_problem_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            problem_id INTEGER NOT NULL,
            seen_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, problem_id)
        )
    """)

    db.commit()
    db.close()

    allowed_topics = ROLE_CODING_TOPICS.get(user["career_goal"], ROLE_CODING_TOPICS["Full Stack Developer"])
    if not problem or problem["topic"] not in allowed_topics or problem["role"] not in (None, user["career_goal"]):

        flash("That coding problem is not available for your selected role.")

        return redirect(url_for("coding"))


    # --------------------------------------------------------
    # RUN TESTS
    # --------------------------------------------------------

    if request.method == "POST" and request.form.get("action") == "run":

        language = request.form.get(
            "language",
            "Python"
        )

        code = request.form.get(
            "code",
            ""
        ).strip()

        if not code:

            flash("Please write your solution first.")

            return redirect(
                url_for(
                    "coding_problem",
                    problem_id=problem_id
                )
            )

        results = []

        passed = 0
        total = len(test_cases)

        for tc in test_cases:

            execution = coding_execute(
                language,
                code,
                tc["input"]
            )

            actual = execution.get(
                "output",
                ""
            )

            expected = tc["expected_output"]

            case_passed = (
                execution.get("ok", False)
                and coding_normalize(actual)
                == coding_normalize(expected)
            )

            if case_passed:
                passed += 1

            results.append({
                "input": tc["input"],
                "expected": expected,
                "actual": actual,
                "error": execution.get(
                    "error",
                    ""
                ),
                "passed": case_passed
            })


        # Store only the latest test result.
        session["last_coding_run"] = {
            "problem_id": problem_id,
            "language": language,
            "passed": passed,
            "total": total
        }


        db = get_db()

        db.execute(
            """
            INSERT OR IGNORE INTO coding_problem_history
            (user_id, problem_id)
            VALUES (?,?)
            """,
            (
                user["id"],
                problem_id
            )
        )

        db.commit()
        db.close()


        output_html = f"""
        <div class="card">

            <h2>Test Results</h2>

            <span class="badge">
                {passed}/{total} Tests Passed
            </span>

            <span class="badge">
                {escape(language)}
            </span>

        </div>
        """


        for number, result in enumerate(
            results,
            1
        ):

            if result["passed"]:

                status = "PASSED"
                border = "#22c55e"

            else:

                status = "FAILED"
                border = "#ef4444"


            output_html += f"""
            <div class="card"
                 style="border-left:5px solid {border};">

                <h3>
                    Test Case {number} - {status}
                </h3>

                <p>
                    <b>Input:</b>
                    <code>
                        {escape(str(result["input"]))}
                    </code>
                </p>

                <p>
                    <b>Expected:</b>
                    <code>
                        {escape(str(result["expected"]))}
                    </code>
                </p>

                <p>
                    <b>Your Output:</b>
                    <code>
                        {escape(str(result["actual"]))}
                    </code>
                </p>

            """

            if result["error"]:

                output_html += f"""
                <p>
                    <b>Error:</b>
                    <pre>
                        {escape(str(result["error"]))}
                    </pre>
                </p>
                """

            output_html += "</div>"


        if total == 0:

            output_html = """
            <div class="card">
                <h3>No test cases available</h3>
            </div>
            """


        return page(
            f"Code Arena - {problem['title']}",
            f"""

            <div class="card">

                <span class="badge">
                    {escape(str(problem['difficulty']))}
                </span>

                <span class="badge">
                    {escape(str(problem['topic']))}
                </span>

                <h1>
                    {escape(str(problem['title']))}
                </h1>

                <p>
                    {escape(str(problem['description']))}
                </p>

                <p>
                    <b>Example Input:</b>
                    <code>
                        {escape(str(problem['example_input']))}
                    </code>
                </p>

                <p>
                    <b>Example Output:</b>
                    <code>
                        {escape(str(problem['example_output']))}
                    </code>
                </p>

                <p>Use a function named <code>solve</code> for Python and JavaScript; convert its input with <code>str(data).split()</code> or <code>String(data).trim().split(/\\s+/)</code> when parsing tokens. For Java, write the body of Main.main. For C and C++, submit a complete program that reads standard input. SQL runs a read-only SELECT against the provided test database. Java needs a JDK, C needs GCC, and C++ needs G++ installed.</p>

                <form method="post">

                    <input
                        type="hidden"
                        name="action"
                        value="run"
                    >

                    <label>
                        <b>Programming Language</b>
                    </label>

                    <select
                        name="language"
                        style="width:100%;padding:12px;"
                    >

                        <option>Python</option>
                        <option>Java</option>
                        <option>SQL</option>
                        <option>C</option>
                        <option>C++</option>
                        <option>JavaScript</option>

                    </select>

                    <br><br>

                    <textarea
                        name="code"
                        rows="18"
                        required
                        placeholder="Write your solution here..."
                        style="
                            width:100%;
                            min-height:350px;
                            padding:15px;
                            font-family:monospace;
                            background:#111827;
                            color:#f9fafb;
                        "
                    ></textarea>

                    <br><br>

                    <button
                        type="submit"
                        class="btn"
                    >
                        Run Tests
                    </button>

                </form>

            </div>

            {output_html}

            <div class="card">

                <h2>Submit Solution</h2>

                <p>
                    Your solution is recorded.
                    XP is awarded only when all
                    stored tests pass.
                </p>

                <form method="post">

                    <input
                        type="hidden"
                        name="action"
                        value="submit"
                    >

                    <label><b>Programming Language</b></label>
                    <select name="language" required>
                        <option>Python</option><option>Java</option>
                        <option>SQL</option><option>C</option><option>C++</option><option>JavaScript</option>
                    </select>

                    <textarea
                        name="answer"
                        rows="8"
                        required
                        placeholder="Paste your final solution..."
                    ></textarea>

                    <br><br>

                    <button
                        type="submit"
                        class="btn"
                    >
                        Submit Solution
                    </button>

                </form>

            </div>

            """
        )


    # --------------------------------------------------------
    # SUBMIT
    # --------------------------------------------------------

    if request.method == "POST" and request.form.get("action") == "submit":

        answer = request.form.get(
            "answer",
            ""
        ).strip()

        language = request.form.get(
            "language",
            "Python"
        )

        if not answer:

            flash("Please enter your solution.")

            return redirect(
                url_for(
                    "coding_problem",
                    problem_id=problem_id
                )
            )


        total = len(test_cases)
        passed = 0
        for tc in test_cases:
            execution = coding_execute(language, answer, tc["input"])
            if execution.get("ok") and coding_normalize(execution.get("output", "")) == coding_normalize(tc["expected_output"]):
                passed += 1
        passed_all = total > 0 and passed == total
        session["last_coding_run"] = {
            "problem_id": problem_id, "language": language,
            "passed": passed, "total": total
        }


        status = (
            "Accepted"
            if passed_all
            else "Submitted"
        )


        db = get_db()
        already_earned = db.execute(
            "SELECT 1 FROM coding_submissions WHERE user_id=? AND problem_id=? AND passed=1 LIMIT 1",
            (user["id"], problem_id)
        ).fetchone()

        db.execute(
            """
            INSERT INTO coding_submissions
            (user_id, problem_id, answer, status,
             language, code, passed, output, error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user["id"],
                problem_id,
                answer,
                status,
                language,
                answer,
                1 if passed_all else 0,
                "All tests passed"
                if passed_all
                else "Tests were not all passed.",
                ""
            )
        )

        db.commit()
        db.close()


        if passed_all and not already_earned:

            add_xp(user["id"], problem["xp"])
            record_activity(user["id"], "coding")
            award_badge(user["id"], "Code Explorer")
            flash("Solution accepted. " f"+{problem['xp']} XP")

        elif passed_all:
            flash("Solution accepted. XP was already earned for this problem.")

        else:

            flash(
                "Solution submitted. "
                "Pass all tests to earn XP."
            )


        return redirect(
            url_for("coding")
        )


    # --------------------------------------------------------
    # NORMAL GET PAGE
    # --------------------------------------------------------

    return page(
        f"Code Arena - {problem['title']}",
        f"""

        <div class="card">

            <span class="badge">
                {escape(str(problem['difficulty']))}
            </span>

            <span class="badge">
                {escape(str(problem['topic']))}
            </span>

            <h1>
                {escape(str(problem['title']))}
            </h1>

            <p>
                {escape(str(problem['description']))}
            </p>

            <p>
                <b>Example Input:</b>
                <code>
                    {escape(str(problem['example_input']))}
                </code>
            </p>

            <p>
                <b>Example Output:</b>
                <code>
                    {escape(str(problem['example_output']))}
                </code>
            </p>

            <form method="post">

                <input
                    type="hidden"
                    name="action"
                    value="run"
                >

                <label>
                    <b>Programming Language</b>
                </label>

                <select
                    name="language"
                    style="width:100%;padding:12px;"
                >

                    <option>Python</option>
                    <option>Java</option>
                    <option>C++</option>
                    <option>JavaScript</option>

                </select>

                <br><br>

                <textarea
                    name="code"
                    rows="18"
                    required
                    placeholder="Write your solution here..."
                    style="
                        width:100%;
                        min-height:350px;
                        padding:15px;
                        font-family:monospace;
                        background:#111827;
                        color:#f9fafb;
                    "
                ></textarea>

                <br><br>

                <button
                    type="submit"
                    class="btn"
                >
                    Run Tests
                </button>

            </form>

        </div>

        """
    )


# ============================================================
# QUIZZES
# ============================================================

@app.route("/quizzes")
@login_required
def quizzes():
    user = current_user()
    allowed_topics = ROLE_QUIZ_TOPICS.get(user["career_goal"], ROLE_QUIZ_TOPICS["Full Stack Developer"])
    db = get_db()
    marks = ",".join("?" for _ in allowed_topics)
    topics = db.execute(f"""
        SELECT topic, COUNT(*) total
        FROM quizzes WHERE topic IN ({marks})
        GROUP BY topic
    """, allowed_topics).fetchall()

    db.close()

    html = """
    <div class="hero">
        <h1>🧪 Skill Quizzes</h1>
        <p>Test your knowledge and earn XP.</p>
    </div>

    <div class="grid">
    """

    for t in topics:
        html += f"""
        <div class="card">
            <h2>{t['topic']}</h2>
            <p>{t['total']} questions</p>
            <a class="btn" href="/quiz/{t['topic']}">Start Quiz</a>
        </div>
        """

    html += "</div>"

    return page("Quizzes", html)


@app.route("/quiz/<path:topic>", methods=["GET", "POST"])
@login_required
def quiz(topic):

    user = current_user()
    allowed_topics = ROLE_QUIZ_TOPICS.get(user["career_goal"], ROLE_QUIZ_TOPICS["Full Stack Developer"])
    if topic not in allowed_topics:
        flash("That quiz topic is not part of your selected career track.")
        return redirect(url_for("quizzes"))

    db = get_db()

    # ========================================================
    # CREATE QUESTION HISTORY TABLE
    # ========================================================
    #
    # This stores which questions each student has already
    # received for each subject.
    #
    # It is created automatically if it does not exist.
    #
    db.execute("""
        CREATE TABLE IF NOT EXISTS quiz_question_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            topic TEXT NOT NULL,
            question_id INTEGER NOT NULL,
            seen_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, topic, question_id)
        )
    """)

    db.commit()

    # Number of questions displayed in one quiz
    QUIZ_SIZE = 10

    # ========================================================
    # SUBMIT QUIZ
    # ========================================================

    if request.method == "POST":

        question_ids_text = request.form.get(
            "question_ids",
            ""
        ).strip()

        if not question_ids_text:

            db.close()

            flash("Quiz session expired. Please start again.")

            return redirect(
                url_for("quiz", topic=topic)
            )

        try:

            question_ids = [
                int(x)
                for x in question_ids_text.split(",")
                if x.strip()
            ]

        except ValueError:

            db.close()

            flash("Invalid quiz session.")

            return redirect(
                url_for("quiz", topic=topic)
            )

        # ----------------------------------------------------
        # Retrieve ONLY the questions shown in this quiz
        # ----------------------------------------------------

        placeholders = ",".join(
            ["?"] * len(question_ids)
        )

        questions = db.execute(
            f"""
            SELECT *
            FROM quizzes
            WHERE topic=?
            AND id IN ({placeholders})
            """,
            [topic] + question_ids
        ).fetchall()

        # Preserve the original question order
        question_map = {
            q["id"]: q
            for q in questions
        }

        ordered_questions = [
            question_map[qid]
            for qid in question_ids
            if qid in question_map
        ]

        # ----------------------------------------------------
        # Calculate score
        # ----------------------------------------------------

        score = 0

        for q in ordered_questions:

            selected_answer = request.form.get(
                str(q["id"])
            )

            if selected_answer == q["correct"]:

                score += 1

        total = len(ordered_questions)

        # ----------------------------------------------------
        # Record questions as seen
        # ----------------------------------------------------

        for q in ordered_questions:

            db.execute(
                """
                INSERT OR IGNORE INTO quiz_question_history
                (user_id, topic, question_id)
                VALUES (?, ?, ?)
                """,
                (
                    user["id"],
                    topic,
                    q["id"]
                )
            )

        # ----------------------------------------------------
        # Store quiz result
        # ----------------------------------------------------

        db.execute(
            """
            INSERT INTO quiz_results
            (user_id, topic, score, total)
            VALUES (?, ?, ?, ?)
            """,
            (
                user["id"],
                topic,
                score,
                total
            )
        )

        db.commit()

        # ----------------------------------------------------
        # Find remaining questions
        # ----------------------------------------------------

        remaining = db.execute(
            """
            SELECT COUNT(*)
            FROM quizzes q
            WHERE q.topic=?
            AND q.id NOT IN (
                SELECT question_id
                FROM quiz_question_history
                WHERE user_id=?
                AND topic=?
            )
            """,
            (
                topic,
                user["id"],
                topic
            )
        ).fetchone()[0]

        db.close()

        # ----------------------------------------------------
        # XP / activity / badge
        # ----------------------------------------------------

        earned_xp = score * 20 if total and (score / total) >= 0.7 else 0
        if earned_xp:
            add_xp(user["id"], earned_xp)

        record_activity(
            user["id"],
            "quiz"
        )

        if total > 0 and (score / total) >= 0.85:

            award_badge(
                user["id"],
                "Quiz Master"
            )

        percentage = (
            (score / total) * 100
            if total > 0
            else 0
        )
        course_db = get_db()
        course_db.execute("UPDATE course_progress SET best_score=MAX(best_score,?), status=CASE WHEN ?>=80 THEN 'Completed' ELSE status END, completed_at=CASE WHEN ?>=80 AND completed_at IS NULL THEN CURRENT_TIMESTAMP ELSE completed_at END WHERE user_id=? AND role=? AND topic=?", (percentage, percentage, percentage, user["id"], user["career_goal"], topic))
        course_db.commit()
        course_db.close()

        # ----------------------------------------------------
        # RESULT PAGE
        # ----------------------------------------------------

        if remaining > 0:

            next_message = f"""
            <div class="card">

                <h3>🎯 More Questions Available</h3>

                <p>
                    You still have
                    <strong>{remaining}</strong>
                    unused questions in this subject.
                </p>

                <a class="btn"
                   href="/quiz/{topic}">
                    🔄 Try New Questions
                </a>

            </div>
            """

        else:

            next_message = f"""
            <div class="card">

                <h3>🎉 Question Bank Completed!</h3>

                <p>
                    You have completed every available
                    question for <strong>{topic}</strong>.
                </p>

                <p>
                    Starting another quiz will begin a
                    <strong>new question cycle</strong>.
                </p>

                <a class="btn"
                   href="/quiz/{topic}">
                    🔄 Start New Cycle
                </a>

            </div>
            """

        return page(
            "Quiz Result",
            f"""

            <div class="hero">

                <h1>🧪 Quiz Complete</h1>

                <h2>
                    {score}/{total}
                </h2>

                <h3>
                    {percentage:.0f}%
                </h3>

                <p>
                    {topic}
                </p>

            </div>

            <div class="card">

                <h2>📊 Your Performance</h2>

                <div style="
                    display:grid;
                    grid-template-columns:
                    repeat(auto-fit,minmax(180px,1fr));
                    gap:15px;
                ">

                    <div class="card">
                        <h3>Correct</h3>
                        <h2>✅ {score}</h2>
                    </div>

                    <div class="card">
                        <h3>Incorrect</h3>
                        <h2>❌ {total - score}</h2>
                    </div>

                    <div class="card">
                        <h3>Score</h3>
                        <h2>{percentage:.0f}%</h2>
                    </div>

                    <div class="card">
                        <h3>XP Earned</h3>
                        <h2>⭐ {earned_xp}</h2>
                    </div>

                </div>

            </div>

            {next_message}

            <div class="card">

                <a class="btn"
                   href="/quizzes">
                    📚 More Quizzes
                </a>

                <a class="btn"
                   href="/dashboard">
                    📊 Dashboard
                </a>

            </div>

            """
        )

    # ========================================================
    # GENERATE A NEW QUIZ
    # ========================================================

    # --------------------------------------------------------
    # Get questions the student has NOT seen
    # --------------------------------------------------------

    available_questions = db.execute(
        """
        SELECT *
        FROM quizzes q
        WHERE q.topic=?
        AND q.id NOT IN (
            SELECT question_id
            FROM quiz_question_history
            WHERE user_id=?
            AND topic=?
        )
        """,
        (
            topic,
            user["id"],
            topic
        )
    ).fetchall()

    # --------------------------------------------------------
    # If all questions have been used:
    # start a NEW QUESTION CYCLE
    # --------------------------------------------------------

    if not available_questions:

        db.execute(
            """
            DELETE FROM quiz_question_history
            WHERE user_id=?
            AND topic=?
            """,
            (
                user["id"],
                topic
            )
        )

        db.commit()

        available_questions = db.execute(
            """
            SELECT *
            FROM quizzes
            WHERE topic=?
            """,
            (topic,)
        ).fetchall()

    # --------------------------------------------------------
    # Randomly select questions
    # --------------------------------------------------------

    import random

    if len(available_questions) <= QUIZ_SIZE:

        questions = list(
            available_questions
        )

    else:

        questions = random.sample(
            available_questions,
            QUIZ_SIZE
        )

    # Randomize the order too
    random.shuffle(questions)

    # --------------------------------------------------------
    # Save question IDs in hidden form field
    # --------------------------------------------------------

    question_ids = ",".join(
        str(q["id"])
        for q in questions
    )

    db.close()

    # ========================================================
    # BUILD QUIZ PAGE
    # ========================================================

    html = f"""

    <div class="hero">

        <h1>🧠 {topic} Quiz</h1>

        <p>
            Test your knowledge with
            <strong>new questions</strong>.
        </p>

        <div class="badge">
            📝 {len(questions)} Questions
        </div>

        <div class="badge">
            ⭐ {len(questions) * 20} XP Available
        </div>

    </div>


    <div class="card">

        <h3>🎯 How this quiz works</h3>

        <ul>

            <li>
                Questions are selected randomly.
            </li>

            <li>
                Questions you've already completed
                are excluded.
            </li>

            <li>
                Each correct answer earns
                <strong>20 XP</strong>.
            </li>

            <li>
                After completing the entire question bank,
                a new question cycle begins.
            </li>

        </ul>

    </div>


    <form method="post">

        <input
            type="hidden"
            name="question_ids"
            value="{question_ids}"
        >

    """

    # ========================================================
    # QUESTIONS
    # ========================================================

    for i, q in enumerate(questions, 1):

        html += f"""

        <div class="card">

            <h3>
                {i}. {q["question"]}
            </h3>

            <label>
                <input
                    type="radio"
                    name="{q["id"]}"
                    value="A"
                    required
                >
                <strong>A.</strong>
                {q["option_a"]}
            </label>

            <br><br>

            <label>
                <input
                    type="radio"
                    name="{q["id"]}"
                    value="B"
                >
                <strong>B.</strong>
                {q["option_b"]}
            </label>

            <br><br>

            <label>
                <input
                    type="radio"
                    name="{q["id"]}"
                    value="C"
                >
                <strong>C.</strong>
                {q["option_c"]}
            </label>

            <br><br>

            <label>
                <input
                    type="radio"
                    name="{q["id"]}"
                    value="D"
                >
                <strong>D.</strong>
                {q["option_d"]}
            </label>

        </div>

        """

    html += """

        <button
            class="btn"
            type="submit"
        >
            🚀 Submit Quiz
        </button>

    </form>

    """

    return page(
        f"{topic} Quiz",
        html
    )


# ============================================================
# RESOURCES
# ============================================================

@app.route("/resources")
@login_required
def resources():
    user = current_user()
    skill = request.args.get("skill", "")
    role_skills = {
        "Full Stack Developer": ["JavaScript", "React", "Flask", "Python", "SQL", "Git/GitHub"],
        "Python Developer": ["Python", "Flask", "SQL", "Git/GitHub"],
        "Java Developer": ["Java", "SQL", "Python", "Git/GitHub"],
        "AI/ML Engineer": ["Machine Learning", "Python", "SQL", "Git/GitHub"],
        "Data Analyst": ["SQL", "Python", "Machine Learning", "Git/GitHub"],
    }
    relevant_skills = role_skills.get(user["career_goal"], role_skills["Full Stack Developer"])

    db = get_db()

    if skill and skill in relevant_skills:
        resources = db.execute(
            "SELECT * FROM resources WHERE skill=?",
            (skill,)
        ).fetchall()
    else:
        marks = ",".join("?" for _ in relevant_skills)
        resources = db.execute(
            f"SELECT * FROM resources WHERE skill IN ({marks}) ORDER BY skill,title",
            relevant_skills
        ).fetchall()

    db.close()

    html = f"""
    <div class="hero">
        <h1>📚 Resources for {escape(user['career_goal'])}</h1>
        <p>Curated resources matched to your selected career track.</p>
    </div>
    """

    html += "<div class='card'><strong>Topics for your role</strong><p>" + "".join(
        f"<a class='badge' href='/resources?skill={quote(topic)}'>{escape(topic)}</a>"
        for topic in relevant_skills if any(resource["skill"] == topic for resource in resources)
    ) + "<a class='badge' href='/resources'>Show all role resources</a></p></div><div class='grid'>"

    for r in resources:
        html += f"""
        <div class="card">
            <span class="badge">{r['resource_type']}</span>
            <h3>{r['title']}</h3>
            <p><strong>{r['skill']}</strong></p>
            <p>{r['description']}</p>
            <a class="btn" href="{r['url']}" target="_blank">
                Open Resource
            </a>
        </div>
        """

    if not resources:
        html += "<div class='card'><h3>No resources found for this filter</h3><p>Choose one of your role topics to explore learning material.</p></div>"
    html += "</div>"

    return page("Resources", html)


# ============================================================
# ASSIGNMENTS
# ============================================================

ASSIGNMENT_LEVELS = ["Foundation", "Intermediate", "Advanced", "Challenge"]


def first_skill(value):
    """Pick a usable skill label from the comma-separated assessment fields."""
    if not value:
        return "Python"
    return value.split(",")[0].strip() or "Python"



def learner_assignment_profile(user):
    """Build a progress-aware profile for the next personalized assignment."""

    db = get_db()

    assessment = db.execute(
        """
        SELECT percentage, strong_skills, weak_skills
        FROM assessments
        WHERE user_id=?
        ORDER BY id DESC
        LIMIT 1
        """,
        (user["id"],),
    ).fetchone()

    quiz = db.execute(
        """
        SELECT
            topic,
            AVG(
                CASE
                    WHEN total > 0
                    THEN CAST(score AS REAL) / total * 100
                    ELSE NULL
                END
            ) AS percentage
        FROM quiz_results
        WHERE user_id=?
        GROUP BY topic
        ORDER BY percentage ASC
        LIMIT 1
        """,
        (user["id"],),
    ).fetchone()

    coding = db.execute(
        """
        SELECT
            COUNT(*) AS attempts,
            COALESCE(SUM(passed), 0) AS passed
        FROM coding_submissions
        WHERE user_id=?
        """,
        (user["id"],),
    ).fetchone()

    assignment_count = db.execute(
        """
        SELECT COUNT(*)
        FROM assignment_submissions
        WHERE user_id=?
        """,
        (user["id"],),
    ).fetchone()[0]

    personalized_done = db.execute(
        """
        SELECT COUNT(*)
        FROM personalized_assignments
        WHERE user_id=? AND status='Completed'
        """,
        (user["id"],),
    ).fetchone()[0]

    db.close()

    assessment_percentage = (
        float(assessment["percentage"])
        if assessment and assessment["percentage"] is not None
        else None
    )

    quiz_percentage = (
        float(quiz["percentage"])
        if quiz and quiz["percentage"] is not None
        else None
    )

    coding_attempts = int(coding["attempts"] or 0)
    coding_passed = int(coding["passed"] or 0)

    coding_percentage = (
        coding_passed / coding_attempts * 100
        if coding_attempts
        else None
    )

    weak_text = (
        (assessment["weak_skills"] or "")
        if assessment
        else ""
    )

    # --------------------------------------------------------
    # Normalize skill names
    # --------------------------------------------------------

    skill = first_skill(weak_text)

    if not skill:
        if quiz and quiz["topic"]:
            skill = quiz["topic"]
        else:
            career = (user["career_goal"] or "").lower()

            if "data analyst" in career or "data scientist" in career:
                skill = "Data Analysis"
            elif "java" in career:
                skill = "Java"
            elif "web" in career:
                skill = "Web Development"
            else:
                skill = "Python"

    # --------------------------------------------------------
    # Calculate learning level
    # --------------------------------------------------------

    xp = int(user["xp"] or 0)

    if assessment_percentage is None:
        level = "Foundation"

    elif assessment_percentage < 40:
        level = "Foundation"

    elif assessment_percentage < 65:
        level = "Intermediate"

    elif assessment_percentage < 85:
        level = "Advanced"

    else:
        level = "Challenge"

    # Weak quiz/coding performance can lower difficulty.
    if quiz_percentage is not None and quiz_percentage < 45:
        if level in ("Advanced", "Challenge"):
            level = "Intermediate"

    if coding_percentage is not None and coding_percentage < 40:
        if level == "Challenge":
            level = "Advanced"

    # Strong repeated activity can increase difficulty.
    if (
        assessment_percentage is not None
        and assessment_percentage >= 80
        and quiz_percentage is not None
        and quiz_percentage >= 75
        and coding_percentage is not None
        and coding_percentage >= 70
    ):
        level = "Challenge"

    # --------------------------------------------------------
    # Create a progress summary
    # --------------------------------------------------------

    return {
        "difficulty": level,
        "skill": skill,
        "assessment_percentage": assessment_percentage,
        "quiz_percentage": quiz_percentage,
        "coding_percentage": coding_percentage,
        "coding_count": coding_attempts,
        "assignment_count": assignment_count,
        "personalized_done": personalized_done,
        "xp": xp,
    }


# ============================================================
# Assignment generation
# ============================================================

def create_personalized_assignment(user):
    """
    Generate a new assignment based on the learner's current progress.

    The generator deliberately avoids previous titles/descriptions so that
    repeated requests do not return the same task.
    """

    profile = learner_assignment_profile(user)

    skill = profile["skill"]
    level = profile["difficulty"]

    db = get_db()

    # --------------------------------------------------------
    # Previous personalized assignments
    # --------------------------------------------------------

    previous = db.execute(
        """
        SELECT title, description
        FROM personalized_assignments
        WHERE user_id=?
        ORDER BY id DESC
        """,
        (user["id"],),
    ).fetchall()

    previous_titles = {
        (row["title"] or "").strip().lower()
        for row in previous
    }

    previous_descriptions = {
        (row["description"] or "").strip().lower()
        for row in previous
    }

    # --------------------------------------------------------
    # Assignment variations
    # --------------------------------------------------------

    banks = {

        "Python": {
            "Foundation": [
                (
                    "Python Input Validator",
                    "Build a Python program that accepts student information, "
                    "validates the input, handles invalid values, and displays "
                    "a clean formatted result. Include at least five test cases.",
                    30,
                ),
                (
                    "Python Grade Calculator",
                    "Create a Python program that accepts marks for multiple "
                    "subjects and calculates total, average, grade, and pass/fail "
                    "status. Handle invalid marks and missing input.",
                    30,
                ),
                (
                    "Python File Organizer",
                    "Create a small Python utility that reads a list of file names "
                    "and groups them by extension. Handle empty lists and unknown "
                    "extensions.",
                    35,
                ),
                (
                    "Python Expense Summary",
                    "Build a command-line Python program that accepts daily expenses, "
                    "calculates totals by category, and reports the highest spending "
                    "category.",
                    35,
                ),
            ],

            "Intermediate": [
                (
                    "Python CSV Analytics Tool",
                    "Build a Python and Pandas application that loads a CSV dataset, "
                    "handles missing values, calculates summary statistics, and "
                    "identifies three useful insights.",
                    50,
                ),
                (
                    "Python Student Performance Analyzer",
                    "Create a Python application that analyzes student marks, "
                    "calculates subject-wise averages, identifies weak subjects, "
                    "and produces a summary report.",
                    50,
                ),
                (
                    "Python Sales Analysis Dashboard Data",
                    "Use Python and Pandas to analyze monthly sales data. Clean the "
                    "dataset, calculate KPIs, identify trends, and prepare the "
                    "results for visualization.",
                    55,
                ),
                (
                    "Python Data Cleaning Pipeline",
                    "Create a reusable Python data-cleaning pipeline that handles "
                    "duplicates, missing values, incorrect types, and invalid rows. "
                    "Document the transformations.",
                    55,
                ),
            ],

            "Advanced": [
                (
                    "Python Data Quality Pipeline",
                    "Design a reusable Python data-quality pipeline for a real-world "
                    "dataset. Include validation, missing-value handling, duplicate "
                    "detection, logging, and automated test cases.",
                    75,
                ),
                (
                    "Python ETL Mini Project",
                    "Build a small ETL pipeline using Python that extracts data from "
                    "CSV files, transforms it into an analysis-ready format, and "
                    "generates a final report.",
                    75,
                ),
                (
                    "Python Analytics API",
                    "Create a Flask API that exposes useful analytics from a dataset. "
                    "Include input validation, error handling, reusable functions, "
                    "and documentation.",
                    80,
                ),
            ],

            "Challenge": [
                (
                    "Production-Ready Python Analytics Service",
                    "Design a portfolio-ready Python analytics service. Include a "
                    "clean architecture, validation, error handling, automated tests, "
                    "logging, documentation, and a short discussion of scalability.",
                    100,
                ),
                (
                    "Python Intelligent Data Processing System",
                    "Build a reusable Python data-processing system that accepts raw "
                    "data, validates it, transforms it, calculates useful metrics, "
                    "and produces an explainable report.",
                    100,
                ),
            ],
        },

        "SQL": {
            "Foundation": [
                (
                    "SQL Student Database Basics",
                    "Create a student database and write SQL queries to insert, "
                    "update, delete, filter, and sort student records. Include "
                    "at least ten sample records.",
                    30,
                ),
                (
                    "SQL Employee Query Practice",
                    "Create an employee table and write queries to find employees "
                    "by department, salary range, experience, and joining year.",
                    30,
                ),
                (
                    "SQL Product Database",
                    "Design a simple product table and write SQL queries using "
                    "WHERE, ORDER BY, GROUP BY, and aggregate functions.",
                    35,
                ),
            ],

            "Intermediate": [
                (
                    "SQL Sales Analysis",
                    "Create a sales database and write analytical queries using "
                    "GROUP BY, HAVING, joins, and aggregate functions to identify "
                    "top products and customers.",
                    50,
                ),
                (
                    "SQL Employee Analytics",
                    "Build an employee database and use joins, subqueries, "
                    "aggregations, and CASE expressions to produce an HR analysis.",
                    55,
                ),
                (
                    "SQL E-Commerce Reporting",
                    "Design tables for customers, orders, and products. Write "
                    "queries that calculate revenue, repeat customers, and "
                    "best-selling products.",
                    55,
                ),
            ],

            "Advanced": [
                (
                    "SQL Business Intelligence Report",
                    "Design a relational schema and create analytical SQL queries "
                    "for a business intelligence report. Include joins, CTEs, "
                    "window functions, and performance considerations.",
                    75,
                ),
                (
                    "SQL Customer Analytics System",
                    "Create a customer analytics database and calculate retention, "
                    "purchase frequency, customer value, and monthly trends using "
                    "advanced SQL.",
                    80,
                ),
            ],

            "Challenge": [
                (
                    "Advanced SQL Analytics Platform",
                    "Design a portfolio-ready SQL analytics solution. Include a "
                    "normalized schema, complex analytical queries, indexes, "
                    "window functions, CTEs, edge cases, and documentation.",
                    100,
                ),
            ],
        },

        "Data Analysis": {
            "Foundation": [
                (
                    "Dataset Exploration Report",
                    "Choose a small CSV dataset and perform basic exploration. "
                    "Identify missing values, data types, averages, minimums, "
                    "maximums, and three observations.",
                    35,
                ),
                (
                    "Student Data Analysis",
                    "Analyze a student-performance dataset using Python and Pandas. "
                    "Find averages, highest performers, weak subjects, and missing data.",
                    35,
                ),
                (
                    "Sales Dataset Explorer",
                    "Explore a sales dataset and identify the highest-selling products, "
                    "monthly totals, and basic trends.",
                    35,
                ),
            ],

            "Intermediate": [
                (
                    "Customer Sales Analysis",
                    "Analyze customer and sales data using Pandas. Clean the data, "
                    "calculate KPIs, identify customer segments, and explain the "
                    "most important findings.",
                    55,
                ),
                (
                    "Business Performance Analysis",
                    "Use a business dataset to calculate key performance indicators, "
                    "compare categories, detect trends, and create at least three "
                    "meaningful visualizations.",
                    55,
                ),
                (
                    "Data Cleaning and Insight Report",
                    "Take a messy dataset, clean duplicates and missing values, "
                    "perform exploratory analysis, and produce a concise insight report.",
                    60,
                ),
            ],

            "Advanced": [
                (
                    "End-to-End Data Analysis Project",
                    "Complete an end-to-end data analysis project covering data "
                    "cleaning, exploratory analysis, visualization, insight generation, "
                    "and business recommendations.",
                    80,
                ),
                (
                    "Predictive Analytics Preparation",
                    "Prepare a real-world dataset for predictive modeling. Perform "
                    "cleaning, feature analysis, outlier investigation, and explain "
                    "which features may be useful for modeling.",
                    80,
                ),
            ],

            "Challenge": [
                (
                    "Portfolio Data Analytics Case Study",
                    "Create a portfolio-ready data analytics case study from a real "
                    "dataset. Include cleaning, exploratory analysis, visualizations, "
                    "business insights, recommendations, limitations, and future work.",
                    100,
                ),
            ],
        },

        "Java": {
            "Foundation": [
                (
                    "Java Student Record Manager",
                    "Build a Java console application to add, display, search, "
                    "and update student records using classes and objects.",
                    35,
                ),
                (
                    "Java Bank Account Simulator",
                    "Create a Java application representing bank accounts with "
                    "deposit, withdrawal, balance checking, and input validation.",
                    35,
                ),
                (
                    "Java Library Manager",
                    "Build a simple Java library system that manages books, "
                    "borrow operations, returns, and unavailable-book cases.",
                    35,
                ),
            ],

            "Intermediate": [
                (
                    "Java Inventory Management System",
                    "Build a Java inventory application using OOP principles. "
                    "Include multiple classes, validation, searching, updating, "
                    "and error handling.",
                    55,
                ),
                (
                    "Java Employee Management System",
                    "Create a Java employee-management application using inheritance, "
                    "encapsulation, collections, and meaningful exception handling.",
                    55,
                ),
                (
                    "Java Expense Management Application",
                    "Build a Java application that stores expenses by category, "
                    "calculates totals, and generates a monthly summary.",
                    55,
                ),
            ],

            "Advanced": [
                (
                    "Java Modular Management System",
                    "Design a modular Java application using interfaces, collections, "
                    "exception handling, reusable services, and unit-testable components.",
                    80,
                ),
                (
                    "Java REST Backend Prototype",
                    "Design a small Java backend service with clear models, service "
                    "logic, validation, exception handling, and documented endpoints.",
                    80,
                ),
            ],

            "Challenge": [
                (
                    "Production-Style Java Application",
                    "Build a portfolio-ready Java application with clean architecture, "
                    "OOP, validation, exception handling, testing, documentation, "
                    "and clear separation of responsibilities.",
                    100,
                ),
            ],
        },

        "DSA": {
            "Foundation": [
                (
                    "Array Problem Solving Pack",
                    "Solve five beginner array problems in your preferred programming "
                    "language. Explain the approach and time complexity for each.",
                    35,
                ),
                (
                    "String Algorithms Practice",
                    "Implement string reversal, palindrome checking, character "
                    "frequency, and duplicate detection with explanations.",
                    35,
                ),
                (
                    "Searching Fundamentals",
                    "Implement linear search and binary search, compare their "
                    "complexities, and test them on multiple inputs.",
                    35,
                ),
            ],

            "Intermediate": [
                (
                    "Stack and Queue Challenge",
                    "Implement stack and queue operations and solve at least three "
                    "practical problems using them. Explain time complexity.",
                    55,
                ),
                (
                    "Linked List Problem Set",
                    "Implement insertion, deletion, searching, and reversal for "
                    "a singly linked list and discuss edge cases.",
                    55,
                ),
                (
                    "Tree Traversal Practice",
                    "Implement preorder, inorder, and postorder traversal for a "
                    "binary tree and explain the complexity of each.",
                    55,
                ),
            ],

            "Advanced": [
                (
                    "Graph Algorithm Challenge",
                    "Implement BFS and DFS on a graph and solve a practical graph "
                    "problem. Explain complexity and discuss disconnected graphs.",
                    80,
                ),
                (
                    "Dynamic Programming Challenge",
                    "Solve two related dynamic-programming problems, compare "
                    "memoization and tabulation, and explain state transitions.",
                    80,
                ),
            ],

            "Challenge": [
                (
                    "Advanced Algorithm Portfolio Challenge",
                    "Solve a complex algorithmic problem, compare at least two "
                    "approaches, analyze time and space complexity, and document "
                    "edge cases and trade-offs.",
                    100,
                ),
            ],
        },

        "HTML/CSS": {
            "Foundation": [
                (
                    "Responsive Profile Page",
                    "Create a responsive personal profile page using HTML and CSS. "
                    "Include semantic structure, cards, navigation, and mobile layout.",
                    30,
                ),
                (
                    "College Event Web Page",
                    "Build a responsive college-event webpage with sections for "
                    "event details, schedule, speakers, and registration.",
                    35,
                ),
            ],

            "Intermediate": [
                (
                    "Responsive Dashboard UI",
                    "Create a responsive dashboard interface using HTML and CSS "
                    "with cards, navigation, tables, forms, and mobile support.",
                    50,
                ),
                (
                    "Student Portfolio Website",
                    "Build a professional portfolio website with responsive "
                    "sections for skills, projects, education, and contact.",
                    50,
                ),
            ],

            "Advanced": [
                (
                    "Interactive Web Interface",
                    "Design a polished responsive interface with reusable components, "
                    "form validation, accessibility considerations, and responsive layouts.",
                    75,
                ),
            ],

            "Challenge": [
                (
                    "Portfolio-Ready Frontend System",
                    "Build a complete responsive frontend experience with reusable "
                    "components, accessibility, responsive behavior, clear UX, "
                    "and documentation.",
                    100,
                ),
            ],
        },

        "JavaScript": {
            "Foundation": [
                (
                    "JavaScript Interactive Form",
                    "Create an interactive form using JavaScript with validation, "
                    "error messages, and dynamic feedback.",
                    30,
                ),
                (
                    "JavaScript To-Do List",
                    "Build a browser-based to-do list that supports adding, "
                    "completing, deleting, and filtering tasks.",
                    35,
                ),
            ],

            "Intermediate": [
                (
                    "JavaScript API Dashboard",
                    "Build a JavaScript dashboard that retrieves data from an API, "
                    "handles loading and errors, and displays useful information.",
                    55,
                ),
                (
                    "JavaScript Expense Tracker",
                    "Create an expense tracker with categories, totals, filtering, "
                    "validation, and browser storage.",
                    55,
                ),
            ],

            "Advanced": [
                (
                    "JavaScript Modular Application",
                    "Build a modular JavaScript application using reusable functions, "
                    "async operations, error handling, and clean state management.",
                    80,
                ),
            ],

            "Challenge": [
                (
                    "Production-Style JavaScript App",
                    "Build a portfolio-ready JavaScript application with modular "
                    "architecture, asynchronous data handling, validation, "
                    "error states, and documentation.",
                    100,
                ),
            ],
        },

        "Flask": {
            "Foundation": [
                (
                    "Flask Student Portal",
                    "Build a basic Flask application with routes, templates, "
                    "forms, and a simple student information page.",
                    40,
                ),
                (
                    "Flask Notes Application",
                    "Create a Flask notes application that allows users to create, "
                    "view, edit, and delete notes.",
                    40,
                ),
            ],

            "Intermediate": [
                (
                    "Flask Task Management API",
                    "Build a Flask REST API for task management with CRUD operations, "
                    "validation, and meaningful error responses.",
                    60,
                ),
                (
                    "Flask Student Management System",
                    "Create a Flask application with student records, search, "
                    "update, delete, validation, and SQLite integration.",
                    60,
                ),
            ],

            "Advanced": [
                (
                    "Flask Authentication System",
                    "Build a Flask authentication module with registration, login, "
                    "password hashing, session management, validation, and protected routes.",
                    80,
                ),
                (
                    "Flask Analytics Dashboard",
                    "Create a Flask dashboard backed by SQLite that calculates "
                    "useful metrics and presents them through a clean interface.",
                    80,
                ),
            ],

            "Challenge": [
                (
                    "Production-Ready Flask Module",
                    "Build a portfolio-ready Flask feature with authentication-aware "
                    "routes, database operations, validation, error handling, testing, "
                    "and documentation.",
                    100,
                ),
            ],
        },

        "Web Development": {
            "Foundation": [
                (
                    "Personal Portfolio Website",
                    "Create a responsive portfolio website containing about, skills, "
                    "projects, education, and contact sections.",
                    35,
                ),
                (
                    "College Club Website",
                    "Build a responsive website for a college club with events, "
                    "members, announcements, and contact information.",
                    35,
                ),
            ],

            "Intermediate": [
                (
                    "Student Productivity Web App",
                    "Build a web application that manages tasks, deadlines, "
                    "categories, and progress using a frontend and backend.",
                    60,
                ),
                (
                    "Mini E-Commerce Interface",
                    "Create a small e-commerce web application with products, "
                    "categories, cart behavior, validation, and responsive design.",
                    60,
                ),
            ],

            "Advanced": [
                (
                    "Full-Stack Management Module",
                    "Design and implement a full-stack management module with "
                    "authentication, CRUD operations, validation, responsive UI, "
                    "and database persistence.",
                    85,
                ),
            ],

            "Challenge": [
                (
                    "Portfolio-Ready Full-Stack Application",
                    "Build a complete full-stack application with authentication, "
                    "database persistence, validation, error handling, testing, "
                    "responsive design, and deployment documentation.",
                    100,
                ),
            ],
        },
    }

    # Fallback bank for skills not explicitly listed.
    fallback = {
        "Foundation": [
            (
                f"{skill} Fundamentals Practice",
                f"Create a small practical {skill} exercise that demonstrates "
                f"fundamental concepts. Include validation, at least five test cases, "
                f"expected results, and a short explanation.",
                30,
            ),
            (
                f"{skill} Beginner Problem Solver",
                f"Build a beginner-friendly {skill} solution for a real-world problem. "
                f"Document your approach, assumptions, normal cases, and edge cases.",
                35,
            ),
        ],

        "Intermediate": [
            (
                f"{skill} Applied Mini Project",
                f"Build a practical {skill} mini project with at least two related "
                f"features, validation, error handling, and five test cases.",
                55,
            ),
            (
                f"{skill} Data or Logic Challenge",
                f"Use {skill} to solve a practical problem. Include reusable components, "
                f"input validation, testing, and a short explanation of design choices.",
                60,
            ),
        ],

        "Advanced": [
            (
                f"{skill} Integration Challenge",
                f"Design an advanced {skill} project with reusable components, "
                f"error handling, testing, edge-case handling, and documentation.",
                80,
            ),
            (
                f"{skill} Real-World Problem Solver",
                f"Build a realistic {skill} solution that combines multiple concepts. "
                f"Explain architecture, assumptions, limitations, and improvements.",
                85,
            ),
        ],

        "Challenge": [
            (
                f"{skill} Portfolio Challenge",
                f"Build a portfolio-ready {skill} project with clean architecture, "
                f"validation, testing, edge-case handling, documentation, trade-offs, "
                f"and future improvements.",
                100,
            ),
            (
                f"Advanced {skill} Capstone",
                f"Design an end-to-end {skill} solution for a realistic use case. "
                f"Include implementation details, testing strategy, limitations, "
                f"and scalability considerations.",
                100,
            ),
        ],
    }

    candidates = banks.get(skill, fallback).get(
        level,
        fallback[level]
    )

    # --------------------------------------------------------
    # Choose an unused assignment
    # --------------------------------------------------------

    selected = None

    for candidate in candidates:
        title, description, xp = candidate

        if title.lower() not in previous_titles:
            if description.lower() not in previous_descriptions:
                selected = candidate
                break

    # --------------------------------------------------------
    # If all fixed variations are used, generate a numbered
    # variation instead of repeating the exact same assignment.
    # --------------------------------------------------------

    if selected is None:
        base_title, base_description, base_xp = candidates[
            len(previous) % len(candidates)
        ]

        variation_number = len(previous) + 1

        selected = (
            f"{base_title} — Variation {variation_number}",
            (
                f"{base_description} "
                f"For this variation, use a different dataset, input format, "
                f"or real-world scenario than your previous attempts. "
                f"Explicitly mention what changed and why."
            ),
            base_xp,
        )

    title, description, xp = selected

    # --------------------------------------------------------
    # Store the assignment
    # --------------------------------------------------------

    db.execute(
        """
        INSERT INTO personalized_assignments
        (user_id, difficulty, title, skill, description, xp, status)
        VALUES (?, ?, ?, ?, ?, ?, 'Open')
        """,
        (
            user["id"],
            level,
            title,
            skill,
            description,
            xp,
        ),
    )

    assignment_id = db.execute(
        "SELECT last_insert_rowid()"
    ).fetchone()[0]

    db.commit()

    assignment = db.execute(
        """
        SELECT *
        FROM personalized_assignments
        WHERE id=?
        """,
        (assignment_id,),
    ).fetchone()

    db.close()

    return assignment, True


def create_personalized_assignment(user):
    """Generate one next-step assignment from all recorded learner progress."""
    db = get_db()
    existing = db.execute(
        "SELECT * FROM personalized_assignments WHERE user_id=? AND status='Open' ORDER BY id DESC LIMIT 1",
        (user["id"],),
    ).fetchone()
    if existing:
        db.close()
        return existing, False
    db.close()

    profile = learner_assignment_profile(user)
    level = profile["difficulty"]
    skill = profile["skill"]
    templates = {
        "Foundation": (
            f"{skill} Foundations Practice",
            f"Build a small {skill} exercise that solves one clear problem. Include sample input/output, explain your approach in 3–5 sentences, and test at least three normal cases.",
            30,
        ),
        "Intermediate": (
            f"{skill} Applied Mini Project",
            f"Create a focused {skill} mini project with two related features, input validation, and at least five test cases. Briefly explain how it improves the skill identified from your prior progress.",
            50,
        ),
        "Advanced": (
            f"{skill} Integration Challenge",
            f"Design and implement a {skill} project with reusable functions or modules, error handling, and a short test plan covering edge cases. Document your design decisions and one improvement you would make next.",
            75,
        ),
        "Challenge": (
            f"{skill} Portfolio Challenge",
            f"Deliver a portfolio-ready {skill} feature: define requirements, implement a clean solution, test happy paths and edge cases, and write a concise README-style explanation of trade-offs and future work.",
            100,
        ),
    }
    title, description, xp = templates[level]
    db = get_db()
    db.execute(
        """INSERT INTO personalized_assignments
           (user_id, difficulty, title, skill, description, xp)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (user["id"], level, title, skill, description, xp),
    )
    assignment_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.commit()
    assignment = db.execute(
        "SELECT * FROM personalized_assignments WHERE id=?", (assignment_id,)
    ).fetchone()
    db.close()
    return assignment, True



@app.route("/assignments", methods=["GET", "POST"])
@login_required
def assignments():
    """Display the learner's current personalized assignment."""

    user = current_user()

    if request.method == "POST":

        assignment, created = create_personalized_assignment(user)

        if created:
            flash("A new personalized assignment has been generated.")
        else:
            flash("Your current assignment is still open. Complete it before generating another.")

        return redirect(url_for("assignments"))

    db = get_db()

    current = db.execute(
        """
        SELECT *
        FROM personalized_assignments
        WHERE user_id=? AND status='Open'
        ORDER BY id DESC
        LIMIT 1
        """,
        (user["id"],),
    ).fetchone()

    history = db.execute(
        """
        SELECT *
        FROM personalized_assignments
        WHERE user_id=?
        ORDER BY id DESC
        LIMIT 8
        """,
        (user["id"],),
    ).fetchall()

    db.close()

    # --------------------------------------------------------
    # Automatically create the first assignment
    # --------------------------------------------------------

    if not current:
        current, _ = create_personalized_assignment(user)

        db = get_db()
        history = db.execute(
            """
            SELECT *
            FROM personalized_assignments
            WHERE user_id=?
            ORDER BY id DESC
            LIMIT 8
            """,
            (user["id"],),
        ).fetchall()
        db.close()

    profile = learner_assignment_profile(user)

    history_html = ""

    for item in history:
        status = item["status"] or "Open"

        history_html += f"""
        <div class="card">
            <h3>{item["title"]}</h3>
            <p>
                <span class="badge">{item["skill"]}</span>
                <span class="badge">{item["difficulty"]}</span>
                <span class="badge">{status}</span>
            </p>
            <p>{item["description"]}</p>
            <p>⭐ {item["xp"]} XP</p>
        </div>
        """

    html = f"""
    <div class="hero">
        <h1>📋 Personalized Assignment</h1>
        <p>
            Your assignment is generated from your recent learning progress.
        </p>
    </div>

    <div class="grid">

        <div class="card">
            <h3>📊 Your Current Learning Profile</h3>

            <p>
                <strong>Target Skill:</strong>
                {profile["skill"]}
            </p>

            <p>
                <strong>Difficulty:</strong>
                {profile["difficulty"]}
            </p>

            <p>
                <strong>Assessment:</strong>
                {
                    f'{profile["assessment_percentage"]:.1f}%'
                    if profile["assessment_percentage"] is not None
                    else "Not attempted"
                }
            </p>

            <p>
                <strong>Quiz Performance:</strong>
                {
                    f'{profile["quiz_percentage"]:.1f}%'
                    if profile["quiz_percentage"] is not None
                    else "Not attempted"
                }
            </p>

            <p>
                <strong>Coding Performance:</strong>
                {
                    f'{profile["coding_percentage"]:.1f}%'
                    if profile["coding_percentage"] is not None
                    else "Not attempted"
                }
            </p>
        </div>

        <div class="card">
            <h2>{current["title"]}</h2>

            <p>
                <span class="badge">{current["skill"]}</span>
                <span class="badge">{current["difficulty"]}</span>
            </p>

            <p>{current["description"]}</p>

            <p>
                <strong>Reward:</strong> ⭐ {current["xp"]} XP
            </p>

            <a class="btn" href="/personalized-assignment/{current["id"]}">
                Open Assignment
            </a>
        </div>

    </div>

    <div class="card">
        <h2>🔄 Generate Next Assignment</h2>

        <p>
            Complete your current assignment before generating another.
            The next assignment will use your latest available progress.
        </p>

        <form method="post">
            <button class="btn">
                Generate Next Personalized Assignment
            </button>
        </form>
    </div>

    <div class="hero">
        <h2>📚 Assignment History</h2>
        <p>Your recent personalized learning tasks.</p>
    </div>

    <div class="grid">
        {history_html}
    </div>
    """

    return page("Personalized Assignments", html)


@app.route("/personalized-assignment/<int:assignment_id>", methods=["GET", "POST"])
@login_required
def personalized_assignment(assignment_id):

    user = current_user()

    db = get_db()

    assignment = db.execute(
        """
        SELECT *
        FROM personalized_assignments
        WHERE id=? AND user_id=?
        """,
        (assignment_id, user["id"]),
    ).fetchone()

    db.close()

    if not assignment:
        flash("Assignment not found.")
        return redirect(url_for("assignments"))

    # --------------------------------------------------------
    # Submission
    # --------------------------------------------------------

    if request.method == "POST":

        answer = (request.form.get("answer") or "").strip()

        if not answer:
            flash("Please enter your submission before submitting.")
            return redirect(
                url_for(
                    "personalized_assignment",
                    assignment_id=assignment_id,
                )
            )

        db = get_db()

        db.execute(
            """
            INSERT INTO personalized_assignment_submissions
            (user_id, personalized_assignment_id, answer)
            VALUES (?, ?, ?)
            """,
            (
                user["id"],
                assignment_id,
                answer,
            ),
        )

        db.commit()
        db.close()

        flash("Assignment submitted and added to your history for review.")

        return redirect(url_for("assignments"))

    html = f"""
    <div class="hero">
        <h1>{assignment["title"]}</h1>

        <p>
            <span class="badge">{assignment["skill"]}</span>
            <span class="badge">{assignment["difficulty"]}</span>
        </p>
    </div>

    <div class="card">

        <h2>📌 Assignment</h2>

        <p>{assignment["description"]}</p>

        <p>
            <strong>Reward:</strong>
            ⭐ {assignment["xp"]} XP
        </p>

        <form method="post">

            <label>
                Your Submission
            </label>

            <textarea
                name="answer"
                required
                rows="12"
                placeholder="Describe your solution, paste your code, provide your project link, or explain your completed work..."
            ></textarea>

            <br><br>

            <button class="btn">
                Submit Assignment
            </button>

        </form>

    </div>

    <br>

    <a class="btn" href="/assignments">
        ← Back to Assignments
    </a>
    """

    return page("Assignment", html)


@app.route("/assignment/<int:assignment_id>", methods=["GET", "POST"])
@login_required
def assignment(assignment_id):
    user = current_user()

    db = get_db()
    a = db.execute(
        "SELECT * FROM assignments WHERE id=?",
        (assignment_id,)
    ).fetchone()
    db.close()

    if request.method == "POST":
        answer = request.form["answer"]

        db = get_db()
        db.execute("""
            INSERT INTO assignment_submissions
            (user_id,assignment_id,answer)
            VALUES(?,?,?)
        """, (user["id"], assignment_id, answer))
        db.commit()
        db.close()

        flash("Assignment submitted and added to your history for review.")
        return redirect(url_for("assignments"))

    return page("Assignment", f"""
    <div class="card">
        <h1>{a['title']}</h1>
        <span class="badge">{a['skill']}</span>

        <p>{a['description']}</p>

        <form method="post">
            <label>Submission</label>
            <textarea name="answer" required
                placeholder="Describe your solution, project link, or submission..."></textarea>

            <button class="btn">Submit Assignment</button>
        </form>
    </div>
    """)


# ============================================================
# MOCK INTERVIEW
# ============================================================

@app.route("/interview", methods=["GET", "POST"])
@login_required
def interview():
    return redirect(url_for("ai_tutor"))


# ============================================================
# RESUME BUILDER
# ============================================================

@app.route("/resume", methods=["GET", "POST"])
@login_required
def resume():
    user = current_user()

    db = get_db()
    existing = db.execute(
        "SELECT * FROM resumes WHERE user_id=?",
        (user["id"],)
    ).fetchone()
    db.close()

    if request.method == "POST":
        data = (
            user["id"],
            request.form.get("phone", ""),
            request.form.get("location", ""),
            request.form.get("summary", ""),
            request.form.get("education", ""),
            request.form.get("skills", ""),
            request.form.get("projects", ""),
            request.form.get("experience", ""),
            request.form.get("certifications", "")
        )

        db = get_db()

        db.execute("""
        INSERT INTO resumes
        (user_id,phone,location,summary,education,skills,projects,experience,certifications)
        VALUES(?,?,?,?,?,?,?,?,?)
        ON CONFLICT(user_id) DO UPDATE SET
        phone=excluded.phone,
        location=excluded.location,
        summary=excluded.summary,
        education=excluded.education,
        skills=excluded.skills,
        projects=excluded.projects,
        experience=excluded.experience,
        certifications=excluded.certifications,
        updated_at=CURRENT_TIMESTAMP
        """, data)

        db.commit()
        db.close()

        record_activity(user["id"], "resume")

        return redirect(url_for("resume_preview"))

    def value(field):
        return existing[field] if existing else ""

    return page("Resume Builder", f"""
    <div class="card">
        <h1>📄 Resume Builder</h1>

        <form method="post">

            <label>Phone</label>
            <input name="phone" value="{value('phone')}">

            <label>Location</label>
            <input name="location" value="{value('location')}">

            <label>Professional Summary</label>
            <textarea name="summary">{value('summary')}</textarea>

            <label>Education</label>
            <textarea name="education"
                placeholder="B.Tech CSE - College - Year">{value('education')}</textarea>

            <label>Skills</label>
            <textarea name="skills"
                placeholder="Python, Java, SQL, DSA...">{value('skills')}</textarea>

            <label>Projects</label>
            <textarea name="projects">{value('projects')}</textarea>

            <label>Experience</label>
            <textarea name="experience">{value('experience')}</textarea>

            <label>Certifications</label>
            <textarea name="certifications">{value('certifications')}</textarea>

            <button class="btn">Save Resume</button>
        </form>
    </div>
    """)


@app.route("/resume/preview")
@login_required
def resume_preview():
    user = current_user()

    db = get_db()
    r = db.execute(
        "SELECT * FROM resumes WHERE user_id=?",
        (user["id"],)
    ).fetchone()
    db.close()

    if not r:
        return redirect(url_for("resume"))

    return page("Resume Preview", f"""
    <div class="card">

        <div class="no-print">
            <button class="btn" onclick="window.print()">🖨️ Print / Save PDF</button>
        </div>

        <hr>

        <h1>{user['name']}</h1>

        <p>
            {r['phone']} • {r['location']} • {user['email']}
        </p>

        <hr>

        <h2>Professional Summary</h2>
        <p>{r['summary']}</p>

        <h2>Education</h2>
        <p>{r['education']}</p>

        <h2>Skills</h2>
        <p>{r['skills']}</p>

        <h2>Projects</h2>
        <p>{r['projects']}</p>

        <h2>Experience</h2>
        <p>{r['experience']}</p>

        <h2>Certifications</h2>
        <p>{r['certifications']}</p>
    </div>
    """)


# ============================================================
# LEADERBOARD
# ============================================================

@app.route("/leaderboard")
@login_required
def leaderboard():
    db = get_db()

    users = db.execute("""
        SELECT name,career_goal,xp,level,streak
        FROM users
        ORDER BY xp DESC
        LIMIT 50
    """).fetchall()

    db.close()

    rows = ""

    for i, u in enumerate(users, 1):
        rows += f"""
        <tr>
            <td>{i}</td>
            <td>🏆 {u['name']}</td>
            <td>{u['career_goal']}</td>
            <td><strong>{u['xp']}</strong></td>
            <td>{u['level']}</td>
            <td>🔥 {u['streak']}</td>
        </tr>
        """

    return page("Leaderboard", f"""
    <div class="hero">
        <h1>🏆 Leaderboard</h1>
        <p>Students ranked by learning XP.</p>
    </div>

    <div class="card">
        <table>
            <tr>
                <th>Rank</th>
                <th>Student</th>
                <th>Career</th>
                <th>XP</th>
                <th>Level</th>
                <th>Streak</th>
            </tr>
            {rows}
        </table>
    </div>
    """)


# ============================================================
# CERTIFICATES
# ============================================================

@app.route("/courses")
@login_required
def courses():
    user = current_user()
    role = user["career_goal"] or "Full Stack Developer"
    topics = ROLE_QUIZ_TOPICS.get(role, ROLE_QUIZ_TOPICS["Full Stack Developer"])
    db = get_db()
    html = f"<div class='hero'><h1>📚 {escape(role)} Courses</h1><p>Complete a course quiz with at least 80% to mark it complete and unlock its certificate.</p></div>"
    for topic in topics:
        if not db.execute("SELECT 1 FROM quizzes WHERE topic=? LIMIT 1", (topic,)).fetchone():
            continue
        title = f"{role}: {topic}"
        row = db.execute("SELECT * FROM course_progress WHERE user_id=? AND course_title=?", (user["id"], title)).fetchone()
        status = row["status"] if row else "Not Started"
        score = row["best_score"] if row else 0
        html += f"<div class='card'><h3>{escape(topic)}</h3><p>Status: <strong>{escape(status)}</strong> · Best score: {score:.0f}%</p><a class='btn' href='/courses/start/{quote(topic)}'>Study and take quiz</a>"
        if status == "Completed":
            html += f"<form method='post' action='/certificates/generate' style='display:inline'><input type='hidden' name='course_title' value='{escape(title)}'><button class='btn'>Issue course certificate</button></form>"
        html += "</div>"
    db.close()
    return page("Courses", html)


@app.route("/courses/start/<path:topic>")
@login_required
def start_course(topic):
    user = current_user()
    role = user["career_goal"] or "Full Stack Developer"
    if topic not in ROLE_QUIZ_TOPICS.get(role, []):
        return page("Course unavailable", "<div class='card'>That course is not part of your selected role.</div>")
    db = get_db()
    if not db.execute("SELECT 1 FROM quizzes WHERE topic=? LIMIT 1", (topic,)).fetchone():
        db.close()
        flash("No course quiz is available for this topic yet.")
        return redirect(url_for("courses"))
    title = f"{role}: {topic}"
    db.execute("INSERT OR IGNORE INTO course_progress(user_id,role,course_title,topic,status) VALUES(?,?,?,?, 'In Progress')", (user["id"], role, title, topic))
    db.execute("UPDATE course_progress SET status='In Progress' WHERE user_id=? AND course_title=? AND status='Not Started'", (user["id"], title))
    db.commit()
    db.close()
    return redirect(url_for("quiz", topic=topic))


@app.route("/history")
@login_required
def submission_history():
    user = current_user()
    db = get_db()
    rows = []
    for r in db.execute("SELECT p.title, s.status, s.submitted_at, '' detail FROM coding_submissions s LEFT JOIN coding_problems p ON p.id=s.problem_id WHERE s.user_id=? ORDER BY s.id DESC", (user["id"],)):
        rows.append((r["submitted_at"], "Coding", r["title"] or "Coding task", r["status"] or "Submitted", r["detail"]))
    for r in db.execute("SELECT topic,score,total,created_at FROM quiz_results WHERE user_id=? ORDER BY id DESC", (user["id"],)):
        rows.append((r["created_at"], "Quiz", r["topic"], f"{r['score']}/{r['total']} ({(100*r['score']/r['total'] if r['total'] else 0):.0f}%)", ""))
    for r in db.execute("SELECT a.title,s.status,s.submitted_at FROM assignment_submissions s LEFT JOIN assignments a ON a.id=s.assignment_id WHERE s.user_id=? ORDER BY s.id DESC", (user["id"],)):
        rows.append((r["submitted_at"], "Assignment", r["title"] or "Assignment", r["status"] or "Submitted", ""))
    for r in db.execute("SELECT a.title,s.submitted_at FROM personalized_assignment_submissions s LEFT JOIN personalized_assignments a ON a.id=s.personalized_assignment_id WHERE s.user_id=? ORDER BY s.id DESC", (user["id"],)):
        rows.append((r["submitted_at"], "Personalized assignment", r["title"] or "Assignment", "Submitted for review", ""))
    for r in db.execute("SELECT course_title,status,completed_at FROM course_progress WHERE user_id=? ORDER BY id DESC", (user["id"],)):
        rows.append((r["completed_at"] or "", "Course", r["course_title"], r["status"], ""))
    db.close()
    rows.sort(key=lambda x: x[0] or "", reverse=True)
    table = "<div class='card'><h3>No task activity yet</h3></div>" if not rows else "<div class='card'><table><tr><th>Date</th><th>Type</th><th>Task</th><th>Result</th></tr>" + "".join(f"<tr><td>{escape(str(d))}</td><td>{escape(t)}</td><td>{escape(str(n))}</td><td>{escape(str(s))}</td></tr>" for d,t,n,s,_ in rows) + "</table></div>"
    return page("Submission History", f"<div class='hero'><h1>📜 Your Progress History</h1><p>Submissions, quiz scores, and course progress for {escape(user['career_goal'])}.</p></div>{table}")

@app.route("/certificates")
@login_required
def certificates():
    user = current_user()

    db = get_db()
    certs = db.execute("""
        SELECT * FROM certificates
        WHERE user_id=?
        ORDER BY issued_at DESC
    """, (user["id"],)).fetchall()
    db.close()

    html = """
    <div class="hero">
        <h1>🏅 Certificates</h1>
        <p>Earn certificates for completing learning milestones.</p>
    </div>
    """

    if not certs:
        html += """
        <div class="card">
            <h3>No certificates yet.</h3>
            <p>Certificates unlock only after you complete a role-specific course with at least 80%.</p>
            <a class="btn" href="/courses">View your courses</a>
        </div>
        """
    else:
        for c in certs:
            html += f"""
            <div class="card">
                <h2>🏅 {c['title']}</h2>
                <p>Certificate ID:
                    <strong>{c['certificate_id']}</strong>
                </p>
                <p>Issued: {c['issued_at']}</p>

                <a class="btn"
                    href="/certificate/{c['certificate_id']}">
                    View Certificate
                </a>
            </div>
            """

    return page("Certificates", html)


@app.route("/certificates/generate", methods=["POST"])
@login_required
def generate_certificate():
    user = current_user()
    course_title = request.form.get("course_title", "").strip()
    db = get_db()
    completed = db.execute("SELECT * FROM course_progress WHERE user_id=? AND role=? AND course_title=? AND status='Completed' AND best_score>=80", (user["id"], user["career_goal"], course_title)).fetchone()
    if not completed:
        db.close()
        flash("Complete the course quiz with at least 80% before requesting a certificate.")
        return redirect(url_for("courses"))

    certificate_id = (
        "SN-" +
        datetime.now().strftime("%Y%m%d") +
        "-" +
        secrets.token_hex(4).upper()
    )

    db.execute("""
        INSERT INTO certificates(user_id,title,certificate_id)
        VALUES(?,?,?)
    """, (
        user["id"],
        course_title,
        certificate_id
    ))

    db.commit()
    db.close()

    return redirect(
        url_for("certificate", certificate_id=certificate_id)
    )


@app.route("/certificate/<certificate_id>")
def certificate(certificate_id):
    db = get_db()

    c = db.execute("""
        SELECT c.*,u.name,u.career_goal
        FROM certificates c
        JOIN users u ON c.user_id=u.id
        WHERE c.certificate_id=?
    """, (certificate_id,)).fetchone()

    db.close()

    if not c:
        return page("Certificate", """
        <div class="card">
            <h2>❌ Certificate Not Found</h2>
            <p>The certificate ID could not be verified.</p>
        </div>
        """)

    return page("Certificate", f"""
    <div class="card"
         style="text-align:center;border:8px solid #4f46e5;padding:50px">

        <h1>🏅 CERTIFICATE OF ACHIEVEMENT</h1>

        <p>This certificate is proudly presented to</p>

        <h1>{c['name']}</h1>

        <p>for completing</p>

        <h2>{c['title']}</h2>

        <p>Career Track: {c['career_goal']}</p>

        <br>

        <p>Certificate ID</p>
        <h3>{c['certificate_id']}</h3>

        <p>Issued: {c['issued_at']}</p>

        <br>

        <button class="btn no-print" onclick="window.print()">
            🖨️ Print Certificate
        </button>
    </div>
    """)


@app.route("/verify")
def verify():
    certificate_id = request.args.get("id", "")

    if not certificate_id:
        return page("Verify Certificate", """
        <div class="card">
            <h1>🔎 Verify Certificate</h1>

            <form>
                <input name="id" placeholder="Enter Certificate ID" required>
                <button class="btn">Verify</button>
            </form>
        </div>
        """)

    db = get_db()

    c = db.execute("""
        SELECT c.*,u.name,u.career_goal
        FROM certificates c
        JOIN users u ON c.user_id=u.id
        WHERE c.certificate_id=?
    """, (certificate_id,)).fetchone()

    db.close()

    if c:
        return page("Certificate Verified", f"""
        <div class="card">
            <h1>✅ Certificate Verified</h1>
            <h2>{c['name']}</h2>
            <p>{c['title']}</p>
            <p>Career: {c['career_goal']}</p>
            <p>Certificate ID: <strong>{c['certificate_id']}</strong></p>
            <p>Issued: {c['issued_at']}</p>
        </div>
        """)

    return page("Verification Failed", """
    <div class="card">
        <h1>❌ Verification Failed</h1>
        <p>No certificate was found with that ID.</p>
    </div>
    """)


# ============================================================
# PROFILE
# ============================================================

@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    user = current_user()

    if request.method == "POST":
        name = request.form["name"]
        career = request.form["career"]

        db = get_db()
        db.execute("""
            UPDATE users
            SET name=?,career_goal=?
            WHERE id=?
        """, (name, career, user["id"]))
        db.commit()
        db.close()

        flash("Profile updated.")
        return redirect(url_for("profile"))

    return page("Profile", f"""
    <div class="card">
        <h1>👤 My Profile</h1>

        <form method="post">
            <label>Name</label>
            <input name="name" value="{user['name']}">

            <label>Email</label>
            <input value="{user['email']}" disabled>

            <label>Career Goal</label>
            <select name="career">
                {"".join(
                    f"<option {'selected' if c == user['career_goal'] else ''}>{c}</option>"
                    for c in [
                        "Full Stack Developer",
                        "Python Developer",
                        "Java Developer",
                        "AI/ML Engineer",
                        "Data Analyst"
                    ]
                )}
            </select>

            <button class="btn">Update Profile</button>
        </form>
    </div>
    """)


# ============================================================
# AI TUTOR
# ============================================================

@app.route("/ai-tutor", methods=["GET", "POST"])
@login_required
def ai_tutor():
    user = current_user()
    key = f"tutor_history_{user['id']}"
    history = session.get(key, [])
    if request.method == "POST":
        question = request.form.get("question", "").strip()[:4000]
        if question:
            history.append({"role": "user", "content": question[:800]})
            try:
                answer = skillnova_ai_chat(history[-4:], user["career_goal"] or "")
            except Exception:
                app.logger.exception("AI tutor provider request failed")
                answer = "I could not reach the learning assistant just now. Please check the AI provider configuration and try again."
            history.append({"role": "assistant", "content": answer[:800]})
            session[key] = history[-4:]
    transcript = "".join(
        f"<div class='card'><strong>{'You' if item['role'] == 'user' else 'SkillNova AI'}</strong><p>{escape(item['content']).replace(chr(10), '<br>')}</p></div>"
        for item in history[-12:]
    )
    return page("AI Tutor", f"""
    <div class="hero"><h1>🤖 SkillNova AI Tutor</h1>
    <p>Ask questions about your studies and {escape(user['career_goal'])} career track.</p></div>
    <div class="card"><form method="post"><label>Ask a question</label>
    <textarea name="question" required maxlength="4000" placeholder="Ask a question or share a doubt..."></textarea>
    <button class="btn">Ask SkillNova AI</button></form></div>{transcript}
    """)


# ============================================================
# ADMIN
# ============================================================

@app.route("/admin")
@login_required
def admin():
    user = current_user()

    if user["role"] != "admin":
        return page("Access Denied", """
        <div class="card">
            <h2>⛔ Admin access required.</h2>
        </div>
        """)

    db = get_db()

    users = db.execute(
        "SELECT COUNT(*) c FROM users"
    ).fetchone()["c"]

    problems = db.execute(
        "SELECT COUNT(*) c FROM coding_problems"
    ).fetchone()["c"]

    quizzes = db.execute(
        "SELECT COUNT(*) c FROM quizzes"
    ).fetchone()["c"]

    certificates = db.execute(
        "SELECT COUNT(*) c FROM certificates"
    ).fetchone()["c"]

    db.close()

    return page("Admin", f"""
    <div class="hero">
        <h1>👨‍💼 Admin Dashboard</h1>
        <p>Manage and monitor SkillNova.</p>
    </div>

    <div class="grid">
        <div class="card">
            <div class="stat">{users}</div>
            <p>Users</p>
        </div>

        <div class="card">
            <div class="stat">{problems}</div>
            <p>Coding Problems</p>
        </div>

        <div class="card">
            <div class="stat">{quizzes}</div>
            <p>Quiz Questions</p>
        </div>

        <div class="card">
            <div class="stat">{certificates}</div>
            <p>Certificates</p>
        </div>
    </div>
    """)


# ============================================================
# ERROR HANDLING
# ============================================================

@app.errorhandler(404)
def not_found(error):
    return page("Page Not Found", """
    <div class="card">
        <h1>404</h1>
        <p>The page you requested does not exist.</p>
        <a class="btn" href="/">Go Home</a>
    </div>
    """), 404


# ============================================================
# START
# ============================================================

if __name__ == "__main__":
    init_db()
    seed_data()

    print("=" * 60)
    print("🚀 SkillNova AI is starting...")
    print("🌐 http://127.0.0.1:5000")
    print("=" * 60)

    app.run(debug=False)
