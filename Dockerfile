from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os

# On HF Spaces, DATABASE_PATH=/data/shariahease.db (set as a Space Variable)
# Locally, falls back to ./shariahease.db at project root
DB_PATH = os.getenv("DATABASE_PATH", "./shariahease.db")
DATABASE_URL = f"sqlite:///{DB_PATH}"

# Ensure the directory exists (e.g. /data/ on HF Spaces)
os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False}  # needed for SQLite only
)

# Each request gets its own session, closed after
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# All ORM models inherit from this
Base = declarative_base()


# Dependency — inject into FastAPI routes with Depends()
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()