try:
    from app.models import Vacancy
    print("Import succeeded")
except Exception as e:
    print(f"Import failed: {e}")
    import traceback
    traceback.print_exc()