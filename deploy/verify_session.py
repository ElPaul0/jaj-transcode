from app.main import app
from app.encoder import bootstrap_jobs, global_batch_view
from app import session

print("import ok", app.title)
session.load_session()
bootstrap_jobs()
print("batches view jobs", len(global_batch_view().get("jobs") or []))
