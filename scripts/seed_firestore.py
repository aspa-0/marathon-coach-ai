from google.cloud import firestore

PROJECT_ID = "qwiklabs-gcp-03-8ae97e4b8104"

def seed_database():
    db = firestore.Client(project=PROJECT_ID)
    logs_ref = db.collection("workout_logs")

    seeded_items = [
        ("log_001", {
            "runner_id": "runner_123",
            "date": "2026-09-18",
            "distance_miles": 6.0,
            "duration_minutes": 51.0,
            "pace_per_mile": "8:30",
            "workout_type": "tempo",
            "notes": "Controlled tempo pace, felt smooth.",
        }),
        ("log_002", {
            "runner_id": "runner_123",
            "date": "2026-09-20",
            "distance_miles": 18.0,
            "duration_minutes": 153.0,
            "pace_per_mile": "8:30",
            "workout_type": "long_run",
            "notes": "Peak long run. Hydrated well every 4 miles.",
        }),
        ("log_003", {
            "runner_id": "runner_123",
            "date": "2026-09-21",
            "distance_miles": 4.0,
            "duration_minutes": 36.0,
            "pace_per_mile": "9:00",
            "workout_type": "easy",
            "notes": "Recovery jog, legs feeling fresh.",
        }),
    ]

    for doc_id, item in seeded_items:
        logs_ref.document(doc_id).set(item)
        print(f"Set workout log {doc_id} for {item['date']}")

    print("Firestore seeded successfully!")

if __name__ == "__main__":
    seed_database()
