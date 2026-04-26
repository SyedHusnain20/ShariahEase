from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os

# SQLite file will be created at root of your project
DATABASE_URL = "sqlite:///./shariahease.db"

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