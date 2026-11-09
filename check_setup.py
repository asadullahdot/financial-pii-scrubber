import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()
print("Groq key loaded:", bool(os.getenv("GROQ_API_KEY")))

engine = create_engine(os.getenv("DATABASE_URL"))
with engine.connect() as conn:
    print("DB says:", conn.execute(text("SELECT 1")).scalar())

import pdfplumber, docx, openpyxl, pandas, fastapi, groq
print("All libraries import fine")