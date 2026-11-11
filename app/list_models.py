"""Print the Groq models your API key can use.   python -m app.list_models"""
from app.analysis import MODEL, PREFERRED_MODELS, get_client

if __name__ == "__main__":
    ids = sorted(m.id for m in get_client().models.list().data)
    print("Models available to your key:")
    for i in ids:
        print("  ", i, "  <-- default" if i == MODEL else "")
    usable = [m for m in PREFERRED_MODELS if m in ids]
    print("\nWill be used:", usable[0] if usable else "none of the preferred models; set GROQ_MODEL in .env")
