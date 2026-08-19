"""
SQLite bilan ishlash uchun yordamchi funksiyalar.
Har bir Telegram foydalanuvchisi (user_id) uchun alohida kirim/chiqim yozuvlari saqlanadi.
"""
import sqlite3
from contextlib import closing
from datetime import datetime

DB_PATH = "kassa.db"


def get_connection(db_path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: str = DB_PATH) -> None:
    with closing(get_connection(db_path)) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                type TEXT NOT NULL CHECK (type IN ('income', 'expense')),
                amount REAL NOT NULL CHECK (amount > 0),
                note TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_user_id ON transactions(user_id)"
        )
        conn.commit()


def add_transaction(user_id: int, tx_type: str, amount: float, note: str = "", db_path: str = DB_PATH) -> int:
    """tx_type: 'income' yoki 'expense'. Yaratilgan yozuv id sini qaytaradi."""
    if tx_type not in ("income", "expense"):
        raise ValueError("tx_type 'income' yoki 'expense' bo'lishi kerak")
    if amount <= 0:
        raise ValueError("Summa musbat son bo'lishi kerak")

    created_at = datetime.now().isoformat(timespec="seconds")
    with closing(get_connection(db_path)) as conn:
        cur = conn.execute(
            "INSERT INTO transactions (user_id, type, amount, note, created_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, tx_type, amount, note, created_at),
        )
        conn.commit()
        return cur.lastrowid


def get_balance(user_id: int, db_path: str = DB_PATH) -> dict:
    with closing(get_connection(db_path)) as conn:
        row = conn.execute(
            """
            SELECT
                COALESCE(SUM(CASE WHEN type = 'income' THEN amount ELSE 0 END), 0) AS income,
                COALESCE(SUM(CASE WHEN type = 'expense' THEN amount ELSE 0 END), 0) AS expense
            FROM transactions WHERE user_id = ?
            """,
            (user_id,),
        ).fetchone()
    income = row["income"]
    expense = row["expense"]
    return {"income": income, "expense": expense, "balance": income - expense}


def get_history(user_id: int, limit: int = 10, db_path: str = DB_PATH) -> list:
    with closing(get_connection(db_path)) as conn:
        rows = conn.execute(
            "SELECT id, type, amount, note, created_at FROM transactions "
            "WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def delete_last(user_id: int, db_path: str = DB_PATH) -> dict | None:
    """Foydalanuvchining eng oxirgi yozuvini o'chiradi va o'chirilgan yozuvni qaytaradi (yo'q bo'lsa None)."""
    with closing(get_connection(db_path)) as conn:
        row = conn.execute(
            "SELECT id, type, amount, note, created_at FROM transactions "
            "WHERE user_id = ? ORDER BY id DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        if row is None:
            return None
        conn.execute("DELETE FROM transactions WHERE id = ?", (row["id"],))
        conn.commit()
        return dict(row)


def delete_all(user_id: int, db_path: str = DB_PATH) -> int:
    """Foydalanuvchining barcha yozuvlarini o'chiradi, o'chirilgan yozuvlar sonini qaytaradi."""
    with closing(get_connection(db_path)) as conn:
        cur = conn.execute("DELETE FROM transactions WHERE user_id = ?", (user_id,))
        conn.commit()
        return cur.rowcount
