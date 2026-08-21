"""Attach two Unsplash photo URLs to every hotel in MongoDB.

Run from this folder:
  .venv/bin/python seed_hotel_images.py
"""

from __future__ import annotations

import db as catalog_db

# License-free Unsplash hotel photos (hotlinked via images.unsplash.com).
EXTERIORS = [
    "https://images.unsplash.com/photo-1566073771259-6a8506099945?auto=format&fit=crop&w=800&h=520&q=80",
    "https://images.unsplash.com/photo-1542314831-068cd1dbfeeb?auto=format&fit=crop&w=800&h=520&q=80",
    "https://images.unsplash.com/photo-1551882547-ff40c63fe5fa?auto=format&fit=crop&w=800&h=520&q=80",
    "https://images.unsplash.com/photo-1520250497591-112f2f40a3f4?auto=format&fit=crop&w=800&h=520&q=80",
    "https://images.unsplash.com/photo-1564501049412-61c2a3083791?auto=format&fit=crop&w=800&h=520&q=80",
    "https://images.unsplash.com/photo-1571896349842-33c89424de2d?auto=format&fit=crop&w=800&h=520&q=80",
    "https://images.unsplash.com/photo-1445019980597-93fa8acb246c?auto=format&fit=crop&w=800&h=520&q=80",
    "https://images.unsplash.com/photo-1568084680786-a84a8d0ce1b5?auto=format&fit=crop&w=800&h=520&q=80",
    "https://images.unsplash.com/photo-1582719478250-c89cae4dc85b?auto=format&fit=crop&w=800&h=520&q=80",
    "https://images.unsplash.com/photo-1578683010236-d716f9a3f461?auto=format&fit=crop&w=800&h=520&q=80",
    "https://images.unsplash.com/photo-1590490360182-c33d277a7b11?auto=format&fit=crop&w=800&h=520&q=80",
    "https://images.unsplash.com/photo-1549294413-26f195200c16?auto=format&fit=crop&w=800&h=520&q=80",
]
ROOMS = [
    "https://images.unsplash.com/photo-1631049307264-da0ec9d70304?auto=format&fit=crop&w=800&h=520&q=80",
    "https://images.unsplash.com/photo-1618773928121-c32242e63f39?auto=format&fit=crop&w=800&h=520&q=80",
    "https://images.unsplash.com/photo-1595576508898-0ad5c879a061?auto=format&fit=crop&w=800&h=520&q=80",
    "https://images.unsplash.com/photo-1584132967334-10e028bd69f7?auto=format&fit=crop&w=800&h=520&q=80",
    "https://images.unsplash.com/photo-1590381105924-c72589b9ef3f?auto=format&fit=crop&w=800&h=520&q=80",
    "https://images.unsplash.com/photo-1596394516093-50190417d1bf?auto=format&fit=crop&w=800&h=520&q=80",
    "https://images.unsplash.com/photo-1512918728675-ed5a9ecdebfd?auto=format&fit=crop&w=800&h=520&q=80",
    "https://images.unsplash.com/photo-1611892440504-42a792e24d32?auto=format&fit=crop&w=800&h=520&q=80",
    "https://images.unsplash.com/photo-1578683010236-d716f9a3f461?auto=format&fit=crop&w=800&h=520&q=80",
    "https://images.unsplash.com/photo-1505693417378-8d3e03d1d1d2?auto=format&fit=crop&w=800&h=520&q=80",
    "https://images.unsplash.com/photo-1566665797739-1674de7a421a?auto=format&fit=crop&w=800&h=520&q=80",
    "https://images.unsplash.com/photo-1590490360182-c33d277a7b11?auto=format&fit=crop&w=800&h=520&q=80",
]


def main() -> None:
    catalog_db.init_db()
    hotels = list(catalog_db.hotels_col().find({}, {"hotel_id": 1, "name": 1}))
    hotels.sort(key=lambda row: row.get("hotel_id") or "")
    for index, hotel in enumerate(hotels):
        exterior = EXTERIORS[index % len(EXTERIORS)]
        room = ROOMS[index % len(ROOMS)]
        catalog_db.hotels_col().update_one(
            {"hotel_id": hotel["hotel_id"]},
            {"$set": {"image_url": exterior, "images": [exterior, room]}},
        )
        print(f"{hotel['hotel_id']}  {hotel.get('name', '')}")
    print(f"Updated {len(hotels)} hotels with 2 photos each.")


if __name__ == "__main__":
    main()
