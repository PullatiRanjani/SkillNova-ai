from app import app, init_db, seed_data

# A new hosted disk starts with an empty database. Create its schema and
# populate the shared learning catalog before Gunicorn begins serving users.
init_db()
seed_data()
