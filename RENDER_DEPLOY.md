# Deploy SkillNova for friends (Render)

This setup runs SkillNova independently of your laptop. The app uses SQLite, so a Render persistent disk is configured as `/var/data` and the database is written there. Render requires a paid web service for a persistent disk; free web services can sleep and lose local files.

## Before creating the service

1. Put this project in a private GitHub repository. Do not upload `.env`, `skillnova.db`, database backups, or virtual-environment files. The included `.gitignore` excludes secrets and local databases.
2. Keep `.env` only on your laptop. The hosted service receives its secrets from the Render dashboard.

## Create the Render service

1. In Render, choose **New → Web Service**, connect the repository, and choose **Docker** as the runtime.
2. Choose a paid web-service plan if you want persistent storage and the app to stay available without your laptop. Render free web services do not support persistent disks.
3. In the service's **Disks** settings, add a disk mounted at `/var/data` (1 GB is plenty for this SQLite app at the start).
4. Add these environment variables in **Environment**:
   - `SECRET_KEY`: a new long random secret. Do not reuse the example placeholder.
   - `SARVAM_API_KEY`: your private Sarvam key. Add it in Render's dashboard, never to GitHub.
   - `SKILLNOVA_DB_PATH`: `/var/data/skillnova.db`
5. Deploy. The Dockerfile installs Python requirements and the Java, C, C++, and Node.js tools needed by the coding runners. `wsgi.py` creates the hosted database schema and seeds the shared learning catalog on first start. The hosted database starts with no local learner accounts or progress; each friend registers their own account.
6. Share the HTTPS URL shown by Render when the service is live.

The app uses one Gunicorn worker with SQLite. Keep it at one worker unless the app is migrated to a server database such as PostgreSQL. Save a copy of the database before changing or removing the persistent disk.
