import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

print("Connecting to database...")

conn = psycopg2.connect(
    DATABASE_URL,
    sslmode="require"
)

cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    email VARCHAR(100) UNIQUE NOT NULL,
    password TEXT NOT NULL
);
""")

conn.commit()

cursor.close()
conn.close()

print("✅ users table created successfully!")
