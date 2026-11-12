"""Small API for storing and reading notes in PostgreSQL."""

import os
import psycopg
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="KubeOps Demo API")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://demo_user:demo_password@localhost:5432/demo_db")

class Note(BaseModel):
    text: str

@app.on_event("startup")
def setup_database():
    with psycopg.connect(DATABASE_URL) as database:
        database.execute("CREATE TABLE IF NOT EXISTS notes (id SERIAL PRIMARY KEY, text TEXT NOT NULL, created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP)")

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/notes")
def add_note(note: Note):
    with psycopg.connect(DATABASE_URL) as database:
        row = database.execute("INSERT INTO notes (text) VALUES (%s) RETURNING id, text, created_at", (note.text,)).fetchone()
    return {"id": row[0], "text": row[1], "created_at": row[2]}

@app.get("/notes")
def list_notes():
    with psycopg.connect(DATABASE_URL) as database:
        rows = database.execute("SELECT id, text, created_at FROM notes ORDER BY id DESC").fetchall()
    return [{"id": row[0], "text": row[1], "created_at": row[2]} for row in rows]

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("demo_backend:app", host="0.0.0.0", port=8000)
