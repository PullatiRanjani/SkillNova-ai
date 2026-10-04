# SkillNova AI — role-based learning project

SkillNova helps learners practice for their selected career track.

## Included features

- Role-specific coding, quizzes, learning resources, and performance-informed roadmap
- Coding submissions checked against stored test cases; XP is awarded only for accepted solutions
- Role-aware AI tutor for learning questions (configure `SARVAM_API_KEY`)
- Role-specific courses; scoring 80% or higher on a course quiz marks it complete and enables its certificate
- Submission and learning history, leaderboard, and resume builder
- Existing assessment flow and assessment records retained

## Setup on Windows

1. Install Python 3.10 or later, plus Node.js for JavaScript submissions. Java, C, and C++ submissions also need a JDK, GCC, and G++ compiler installed and available on PATH.
2. Create and activate a virtual environment: `python -m venv venv` then `venv\\Scripts\\Activate.ps1`.
3. Install dependencies: `pip install -r requirements.txt`.
4. Copy `.env.example` to `.env`, set `SARVAM_API_KEY`, and replace `SECRET_KEY` with a private random value.
5. Start the app with `python app.py`, then open http://127.0.0.1:5000.

The provided `skillnova.db` contains the project learning data. The app initializes any newer tables when started. Keep a separate backup before replacing this database with another copy.
