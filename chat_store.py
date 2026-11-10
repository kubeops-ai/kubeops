"""Small PostgreSQL store for chat names and ownership."""

import os
from datetime import datetime, timezone

import psycopg


def connection():
    """Open a PostgreSQL connection using the KubeOps environment setting."""
    url = os.getenv("KUBEOPS_POSTGRES_URL", "").strip()
    if not url:
        raise RuntimeError("Set KUBEOPS_POSTGRES_URL in the kubeops .env file.")
    return psycopg.connect(url)


def setup_chat_table():
    """Create the chat table once when the API starts."""
    with connection() as database:
        database.execute(
            """
            CREATE TABLE IF NOT EXISTS kubeops_chats (
                chat_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                name TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL
            )
            """
        )
        database.execute(
            """
            CREATE TABLE IF NOT EXISTS kubeops_messages (
                message_id BIGSERIAL PRIMARY KEY,
                chat_id TEXT NOT NULL REFERENCES kubeops_chats(chat_id) ON DELETE CASCADE,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL
            )
            """
        )


def list_chats(user_id: str) -> list[dict]:
    """Return a user's chats, newest first."""
    with connection() as database:
        rows = database.execute(
            """
            SELECT chat_id, name, created_at, updated_at
            FROM kubeops_chats
            WHERE user_id = %s
            ORDER BY updated_at DESC
            """,
            (user_id,),
        ).fetchall()
    return [
        {"chat_id": row[0], "name": row[1], "created_at": row[2].isoformat(), "updated_at": row[3].isoformat()}
        for row in rows
    ]


def create_chat(chat_id: str, user_id: str, name: str = "New chat"):
    """Save a new chat."""
    now = datetime.now(timezone.utc)
    with connection() as database:
        database.execute(
            """
            INSERT INTO kubeops_chats (chat_id, user_id, name, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (chat_id) DO NOTHING
            """,
            (chat_id, user_id, name, now, now),
        )


def touch_chat(chat_id: str, user_id: str):
    """Update the last-used time for a chat."""
    with connection() as database:
        database.execute(
            """UPDATE kubeops_chats SET updated_at = %s
               WHERE chat_id = %s AND user_id = %s""",
            (datetime.now(timezone.utc), chat_id, user_id),
        )


def rename_chat(chat_id: str, user_id: str, name: str):
    """Change the display name of one chat."""
    with connection() as database:
        database.execute(
            """UPDATE kubeops_chats SET name = %s, updated_at = %s
               WHERE chat_id = %s AND user_id = %s""",
            (name.strip(), datetime.now(timezone.utc), chat_id, user_id),
        )


def get_messages(chat_id: str, user_id: str) -> list[dict]:
    """Load saved messages for one user's chat."""
    with connection() as database:
        rows = database.execute(
            """
            SELECT role, content FROM kubeops_messages
            WHERE chat_id = %s AND EXISTS (
                SELECT 1 FROM kubeops_chats WHERE chat_id = %s AND user_id = %s
            ) ORDER BY message_id
            """,
            (chat_id, chat_id, user_id),
        ).fetchall()
    return [{"role": row[0], "content": row[1]} for row in rows]


def add_message(chat_id: str, user_id: str, role: str, content: str):
    """Save one user or assistant message."""
    with connection() as database:
        database.execute(
            """
            INSERT INTO kubeops_messages (chat_id, role, content, created_at)
            SELECT %s, %s, %s, %s
            WHERE EXISTS (SELECT 1 FROM kubeops_chats WHERE chat_id = %s AND user_id = %s)
            """,
            (chat_id, role, content, datetime.now(timezone.utc), chat_id, user_id),
        )


def delete_chat(chat_id: str, user_id: str):
    """Delete one chat from the metadata table."""
    with connection() as database:
        database.execute(
            "DELETE FROM kubeops_chats WHERE chat_id = %s AND user_id = %s",
            (chat_id, user_id),
        )
