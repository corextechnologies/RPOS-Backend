from fastapi import FastAPI, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text
from database import init_db, get_db

app = FastAPI()

@app.on_event("startup")
def on_startup():
    init_db()

@app.get("/health")
def health_check(db: Session = Depends(get_db)):
    # Confirm the DB connection actually works, not just that the app is up
    db.execute(text("SELECT 1"))
    return {"status": "ok", "database": "connected"}