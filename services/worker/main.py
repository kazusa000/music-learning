from music_learning.database import Database
from music_learning.settings import Settings
from music_learning.worker import Worker

if __name__ == "__main__":
    db = Database(Settings.load())
    db.migrate()
    try:
        Worker(db).run()
    finally:
        db.close()
