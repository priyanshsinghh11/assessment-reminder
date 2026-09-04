"""Remove evaluations made without readable resume text."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.db import store


def main() -> None:
    collection = store.get_db().submissions
    query = {
        "resume_link": {"$nin": [None, ""]},
        "resume_text": {"$in": [None, ""]},
        "evaluation": {"$exists": True},
    }
    before = collection.count_documents(query)
    result = collection.update_many(
        query,
        {
            "$unset": {"evaluation": ""},
            "$set": {
                "decision.status": "pending",
                "decision.reason": "cv_cannot_be_fetched",
                "decision.source": "auto",
                "decision.at": store.now(),
            },
        },
    )
    print(f"matched={before} modified={result.modified_count}")
    print(f"remaining={collection.count_documents(query)}")


if __name__ == "__main__":
    main()
