"""Tool definitions and execution against the Pune hotel catalog.

Hotels, bookings, and users are stored in MongoDB (see db.py). There is no
local hotel JSON file.

Structure matches the reference tools.py:
1. FIELD_MAPPINGS for response projection
2. TOOL_DEFINITIONS (name, description, input_schema)
3. Helpers
4. execute_tool dispatcher
5. One private handler per tool
"""

import logging
import math
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional

import db as catalog_db

logger = logging.getLogger(__name__)

# ============================================================================
# CATALOG FIELD MAPPINGS
# ============================================================================

FIELD_MAPPINGS = {
    "hotels": [
        "hotel_id",
        "name",
        "city",
        "area",
        "star_rating",
        "guest_rating",
        "review_count",
        "starting_price_inr",
        "latitude",
        "longitude",
        "image_url",
        "images",
    ],
    "hotel_details": [
        "hotel_id",
        "name",
        "city",
        "area",
        "address",
        "latitude",
        "longitude",
        "star_rating",
        "guest_rating",
        "review_count",
        "description",
        "amenities",
        "check_in_time",
        "check_out_time",
        "policies",
        "rooms",
        "image_url",
        "images",
    ],
    "rooms": [
        "room_id",
        "name",
        "max_guests",
        "size_sqft",
        "bed",
        "amenities",
        "inventory",
        "base_price_inr",
        "rate_plans",
    ],
    "bookings": [
        "booking_id",
        "hotel_id",
        "hotel_name",
        "room_id",
        "room_name",
        "rate_id",
        "check_in",
        "check_out",
        "nights",
        "guests",
        "guest_name",
        "guest_email",
        "guest_phone",
        "customer_id",
        "status",
        "total_inr",
        "currency",
        "razorpay_order_id",
        "razorpay_payment_id",
        "paid_at",
        "payment",
        "receipt",
    ],
    "reviews": [
        "review_id",
        "guest_name",
        "rating",
        "title",
        "comment",
        "stay_date",
    ],
}

# ============================================================================
# TOOL DEFINITIONS
# ============================================================================

TOOL_DEFINITIONS: List[Dict] = [
    {
        "name": "search_hotels",
        "description": "Search available hotels in a city for given check-in/check-out dates, guest count, and room count. Reads the Pune hotel catalog in MongoDB and returns matching hotels with area, star rating, guest rating, and starting price. Use this first in a booking flow before get_hotel_details or create_booking.",
        "input_schema": {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "City where the user wants to stay."},
                "check_in": {"type": "string", "format": "date", "description": "Check-in date in YYYY-MM-DD. Must be today or a future date. Never a past date."},
                "check_out": {"type": "string", "format": "date", "description": "Check-out date in YYYY-MM-DD format."},
                "guests": {"type": "integer", "minimum": 1, "description": "Number of guests."},
                "rooms": {"type": "integer", "minimum": 1, "description": "Number of rooms required."},
            },
            "required": ["city", "check_in", "check_out", "guests", "rooms"],
        },
    },
    {
        "name": "get_hotel_details",
        "description": "Get complete information about a specific hotel including address, description, amenities, photos, check-in/out times, policies, and a summary of room types. Use after search_hotels when the guest has picked a hotel_id, and whenever they ask to see pictures or images of a hotel.",
        "input_schema": {
            "type": "object",
            "properties": {
                "hotel_id": {"type": "string", "description": "Unique identifier of the hotel."},
            },
            "required": ["hotel_id"],
        },
    },
    {
        "name": "get_room_details",
        "description": "Get room types, amenities, bed configuration, occupancy, base pricing, rate plans, and room policies for one room in a hotel. Use before check_hotel_availability or create_booking.",
        "input_schema": {
            "type": "object",
            "properties": {
                "hotel_id": {"type": "string", "description": "Unique identifier of the hotel."},
                "room_id": {"type": "string", "description": "Unique identifier of the room."},
            },
            "required": ["hotel_id", "room_id"],
        },
    },
    {
        "name": "check_hotel_availability",
        "description": "Check whether a particular hotel room is available for the requested dates and guests. Compares room inventory against overlapping confirmed bookings. Returns remaining rooms and whether the stay can be booked.",
        "input_schema": {
            "type": "object",
            "properties": {
                "hotel_id": {"type": "string", "description": "Unique identifier of the hotel."},
                "room_id": {"type": "string", "description": "Unique identifier of the room."},
                "check_in": {"type": "string", "format": "date", "description": "Check-in date."},
                "check_out": {"type": "string", "format": "date", "description": "Check-out date."},
                "guests": {"type": "integer", "minimum": 1, "description": "Number of guests."},
            },
            "required": ["hotel_id", "room_id", "check_in", "check_out", "guests"],
        },
    },
    {
        "name": "get_hotel_rates",
        "description": "Get current room prices and available rate plans for a hotel stay. Returns nightly and stay-total amounts per rate plan (Flexible, Saver, Bed & Breakfast) before tax. Use before get_booking_price or create_booking.",
        "input_schema": {
            "type": "object",
            "properties": {
                "hotel_id": {"type": "string", "description": "Unique identifier of the hotel."},
                "room_id": {"type": "string", "description": "Unique identifier of the room."},
                "check_in": {"type": "string", "format": "date", "description": "Check-in date."},
                "check_out": {"type": "string", "format": "date", "description": "Check-out date."},
                "guests": {"type": "integer", "minimum": 1, "description": "Number of guests."},
            },
            "required": ["hotel_id", "room_id", "check_in", "check_out", "guests"],
        },
    },
    {
        "name": "create_booking",
        "description": "Create a hotel reservation after availability and price are confirmed. Stores a pending_payment booking in MongoDB. The guest must complete Razorpay checkout in the UI before the stay is confirmed. Do not tell them the stay is confirmed until they pay. Returns booking_id, status pending_payment, and total payable.",
        "input_schema": {
            "type": "object",
            "properties": {
                "hotel_id": {"type": "string", "description": "Unique identifier of the hotel."},
                "room_id": {"type": "string", "description": "Unique identifier of the room."},
                "check_in": {"type": "string", "format": "date", "description": "Check-in date."},
                "check_out": {"type": "string", "format": "date", "description": "Check-out date."},
                "guests": {"type": "integer", "minimum": 1, "description": "Number of guests."},
                "guest_name": {"type": "string", "description": "Full name of the primary guest."},
                "guest_email": {"type": "string", "format": "email", "description": "Email address of the guest."},
                "guest_phone": {"type": "string", "description": "10-digit mobile number. Required so the guest can look up bookings later without a booking_id."},
            },
            "required": [
                "hotel_id",
                "room_id",
                "check_in",
                "check_out",
                "guests",
                "guest_name",
                "guest_email",
                "guest_phone",
            ],
        },
    },
    {
        "name": "get_booking",
        "description": "Retrieve one hotel booking. Only returns it if guest_phone and guest_email match the person who booked it.",
        "input_schema": {
            "type": "object",
            "properties": {
                "booking_id": {"type": "string", "description": "Unique identifier of the booking."},
                "guest_email": {"type": "string", "format": "email", "description": "Signed-in guest email. Must match the booking."},
                "guest_phone": {"type": "string", "description": "Signed-in 10-digit mobile. Must match the booking."},
            },
            "required": ["booking_id", "guest_email", "guest_phone"],
        },
    },
    {
        "name": "list_bookings",
        "description": "Show reservations for the signed-in guest only. Requires both guest_phone and guest_email; they must match the same person.",
        "input_schema": {
            "type": "object",
            "properties": {
                "guest_email": {"type": "string", "format": "email", "description": "Signed-in guest email."},
                "guest_phone": {"type": "string", "description": "Signed-in 10-digit mobile number."},
                "customer_id": {"type": "string", "description": "Optional alias for guest_email."},
            },
            "required": ["guest_email", "guest_phone"],
        },
    },
    {
        "name": "modify_booking",
        "description": "Change dates, guests, or room on a booking the signed-in guest owns. Rejected if mobile or email does not match the reservation.",
        "input_schema": {
            "type": "object",
            "properties": {
                "booking_id": {"type": "string", "description": "Unique identifier of the booking."},
                "guest_email": {"type": "string", "format": "email", "description": "Signed-in guest email. Must match the booking."},
                "guest_phone": {"type": "string", "description": "Signed-in 10-digit mobile. Must match the booking."},
                "check_in": {"type": "string", "format": "date", "description": "New check-in date."},
                "check_out": {"type": "string", "format": "date", "description": "New check-out date."},
                "guests": {"type": "integer", "minimum": 1, "description": "New number of guests."},
                "room_id": {"type": "string", "description": "New room ID."},
            },
            "required": ["booking_id", "guest_email", "guest_phone"],
        },
    },
    {
        "name": "cancel_booking",
        "description": "Cancel a reservation the signed-in guest owns. Rejected if mobile or email does not match the person who booked it.",
        "input_schema": {
            "type": "object",
            "properties": {
                "booking_id": {"type": "string", "description": "Unique identifier of the booking."},
                "guest_email": {"type": "string", "format": "email", "description": "Signed-in guest email. Must match the booking."},
                "guest_phone": {"type": "string", "description": "Signed-in 10-digit mobile. Must match the booking."},
                "cancellation_reason": {"type": "string", "description": "Reason for cancelling the booking."},
            },
            "required": ["booking_id", "guest_email", "guest_phone"],
        },
    },
    {
        "name": "get_cancellation_policy",
        "description": "Check cancellation and refund rules for a hotel room and rate plan. Returns whether the rate is refundable, free-cancellation window in hours, and the full policy text.",
        "input_schema": {
            "type": "object",
            "properties": {
                "hotel_id": {"type": "string", "description": "Unique identifier of the hotel."},
                "room_id": {"type": "string", "description": "Unique identifier of the room."},
                "rate_id": {"type": "string", "description": "Unique identifier of the rate plan."},
            },
            "required": ["hotel_id", "room_id", "rate_id"],
        },
    },
    {
        "name": "get_booking_price",
        "description": "Calculate the final booking price including room cost, GST, city fee, and stay length. Defaults to the Flexible rate plan. Use this to quote a total before create_booking.",
        "input_schema": {
            "type": "object",
            "properties": {
                "hotel_id": {"type": "string", "description": "Unique identifier of the hotel."},
                "room_id": {"type": "string", "description": "Unique identifier of the room."},
                "check_in": {"type": "string", "format": "date", "description": "Check-in date."},
                "check_out": {"type": "string", "format": "date", "description": "Check-out date."},
                "guests": {"type": "integer", "minimum": 1, "description": "Number of guests."},
            },
            "required": ["hotel_id", "room_id", "check_in", "check_out", "guests"],
        },
    },
    {
        "name": "get_booking_status",
        "description": "Check status of a booking the signed-in guest owns. Mobile and email must match the reservation.",
        "input_schema": {
            "type": "object",
            "properties": {
                "booking_id": {"type": "string", "description": "Unique identifier of the booking."},
                "guest_email": {"type": "string", "format": "email", "description": "Signed-in guest email. Must match the booking."},
                "guest_phone": {"type": "string", "description": "Signed-in 10-digit mobile. Must match the booking."},
            },
            "required": ["booking_id", "guest_email", "guest_phone"],
        },
    },
    {
        "name": "get_hotel_reviews",
        "description": "Retrieve customer reviews and ratings for a hotel from the catalog, including guest name, score, title, comment, and stay date.",
        "input_schema": {
            "type": "object",
            "properties": {
                "hotel_id": {"type": "string", "description": "Unique identifier of the hotel."},
            },
            "required": ["hotel_id"],
        },
    },
    {
        "name": "search_hotels_by_location",
        "description": "Search for hotels near a specific geographic location using latitude, longitude, and radius in kilometres. Reads MongoDB catalog coordinates and returns hotels within the radius, nearest first. Useful when the guest names an area such as Hinjewadi or Koregaon Park.",
        "input_schema": {
            "type": "object",
            "properties": {
                "latitude": {"type": "number", "description": "Latitude of the location."},
                "longitude": {"type": "number", "description": "Longitude of the location."},
                "radius": {"type": "number", "description": "Search radius in kilometers."},
            },
            "required": ["latitude", "longitude", "radius"],
        },
    },
    {
        "name": "get_hotel_amenities",
        "description": "Get the facilities and amenities available at a hotel (pool, spa, gym, parking, WiFi, and similar). Fetched from the MongoDB hotel record.",
        "input_schema": {
            "type": "object",
            "properties": {
                "hotel_id": {"type": "string", "description": "Unique identifier of the hotel."},
            },
            "required": ["hotel_id"],
        },
    },
]


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================


def _load_hotels() -> List[Dict]:
    """Fetch the hotel catalog from MongoDB."""
    catalog_db.init_db()
    return catalog_db.list_hotels()


def _project(record: Dict, mapping_key: str) -> Dict:
    """Return only catalog-required fields, same idea as tools.py FIELD_MAPPINGS."""
    fields = FIELD_MAPPINGS.get(mapping_key)
    if not fields:
        return dict(record)
    return {field: record.get(field) for field in fields if field in record}


_NOT_OWNER = (
    "This booking does not match the signed-in mobile number and email. "
    "You can only view or change reservations you booked."
)


def _guest_identity(tool_input: Dict) -> tuple[Optional[str], Optional[str], Optional[Dict]]:
    phone = catalog_db.normalize_phone(tool_input.get("guest_phone"))
    email = str(tool_input.get("guest_email") or "").strip().lower()
    if not email:
        customer_id = str(tool_input.get("customer_id") or "").strip()
        if customer_id and not catalog_db.looks_like_phone(customer_id):
            email = customer_id.lower()
    if len(phone) != 10:
        return None, None, {"error": "A 10-digit mobile number is required to access this booking"}
    if "@" not in email:
        return None, None, {"error": "The signed-in email is required to access this booking"}
    return phone, email, None


def _booking_owned_by(booking: Dict, phone: str, email: str) -> bool:
    booked_phone = catalog_db.normalize_phone(booking.get("guest_phone"))
    booked_email = str(booking.get("guest_email") or booking.get("customer_id") or "").strip().lower()
    return booked_phone == phone and booked_email == email


def _load_owned_booking(tool_input: Dict) -> tuple[Optional[Dict], Optional[Dict]]:
    phone, email, error = _guest_identity(tool_input)
    if error:
        return None, error
    booking_id = tool_input.get("booking_id")
    if not booking_id:
        return None, {"error": "Missing booking_id"}
    booking = catalog_db.get_booking(booking_id)
    if not booking:
        return None, {"error": f"Booking not found: {booking_id}"}
    if not _booking_owned_by(booking, phone, email):
        return None, {"error": _NOT_OWNER}
    return booking, None


IST = timezone(timedelta(hours=5, minutes=30))


def _today() -> date:
    return datetime.now(IST).date()


def _parse_date(value: str) -> Optional[date]:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _validate_stay(check_in: str, check_out: str) -> Dict:
    start = _parse_date(check_in)
    end = _parse_date(check_out)
    if not start or not end:
        return {"error": "Dates must be YYYY-MM-DD"}
    today = _today()
    if start < today:
        return {
            "error": (
                f"Check-in cannot be in the past. Today is {today.isoformat()}. "
                "Please choose today or a later date."
            )
        }
    if end <= start:
        return {"error": "check_out must be after check_in"}
    if end <= today:
        return {
            "error": (
                f"Check-out cannot be in the past. Today is {today.isoformat()}. "
                "Please choose a future check-out date."
            )
        }
    nights = (end - start).days
    return {"check_in": start, "check_out": end, "nights": nights}


def _find_hotel(hotel_id: str) -> Optional[Dict]:
    catalog_db.init_db()
    return catalog_db.find_hotel(hotel_id)


def _find_room(hotel: Dict, room_id: str) -> Optional[Dict]:
    for room in hotel.get("rooms", []):
        if room.get("room_id") == room_id:
            return room
    return None


def _find_rate(room: Dict, rate_id: str) -> Optional[Dict]:
    for rate in room.get("rate_plans", []):
        if rate.get("rate_id") == rate_id:
            return rate
    return None


def _default_rate(room: Dict) -> Optional[Dict]:
    plans = room.get("rate_plans") or []
    for rate in plans:
        if str(rate.get("rate_id", "")).endswith("-FLEX"):
            return rate
    return plans[0] if plans else None


def _starting_price(hotel: Dict) -> int:
    prices = [room.get("base_price_inr", 0) for room in hotel.get("rooms", [])]
    return min(prices) if prices else 0


def _booked_count(hotel_id: str, room_id: str, check_in: str, check_out: str, skip_booking_id: str = None) -> int:
    return catalog_db.overlapping_count(hotel_id, room_id, check_in, check_out, skip_booking_id)


def _availability(hotel: Dict, room: Dict, check_in: str, check_out: str, guests: int, skip_booking_id: str = None) -> Dict:
    stay = _validate_stay(check_in, check_out)
    if "error" in stay:
        return stay
    if guests < 1:
        return {"error": "guests must be at least 1"}
    if guests > room.get("max_guests", 0):
        return {
            "available": False,
            "reason": f"Room sleeps {room.get('max_guests')} guests; {guests} requested.",
            "remaining_rooms": 0,
        }
    booked = _booked_count(hotel["hotel_id"], room["room_id"], check_in, check_out, skip_booking_id)
    remaining = max(room.get("inventory", 0) - booked, 0)
    return {
        "available": remaining > 0,
        "remaining_rooms": remaining,
        "inventory": room.get("inventory"),
        "booked_overlapping": booked,
        "nights": stay["nights"],
        "reason": None if remaining > 0 else "No rooms left for these dates.",
    }


def _price_breakdown(hotel: Dict, room: Dict, rate: Dict, nights: int) -> Dict:
    room_subtotal = int(round(room["base_price_inr"] * rate.get("multiplier", 1.0) * nights))
    gst_percent = float(hotel.get("gst_percent", 12))
    city_fee = int(hotel.get("city_fee_inr", 0)) * nights
    gst_amount = int(round(room_subtotal * gst_percent / 100))
    total = room_subtotal + gst_amount + city_fee
    return {
        "nights": nights,
        "nightly_rate_inr": int(round(room["base_price_inr"] * rate.get("multiplier", 1.0))),
        "room_subtotal_inr": room_subtotal,
        "gst_percent": gst_percent,
        "gst_inr": gst_amount,
        "city_fee_inr": city_fee,
        "total_inr": total,
        "currency": "INR",
        "rate_id": rate.get("rate_id"),
        "rate_name": rate.get("name"),
    }


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return radius * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _next_booking_id() -> str:
    return catalog_db.next_booking_id()


def _require_hotel_room(tool_input: Dict):
    hotel_id = tool_input.get("hotel_id")
    room_id = tool_input.get("room_id")
    if not hotel_id:
        return None, None, {"error": "Missing hotel_id"}
    hotel = _find_hotel(hotel_id)
    if not hotel:
        return None, None, {"error": f"Hotel not found: {hotel_id}"}
    if not room_id:
        return hotel, None, {"error": "Missing room_id"}
    room = _find_room(hotel, room_id)
    if not room:
        return hotel, None, {"error": f"Room not found: {room_id}"}
    return hotel, room, None


# ============================================================================
# EXECUTE
# ============================================================================


def execute_tool(tool_name: str, tool_input: Dict) -> Dict:
    """Execute a tool against MongoDB hotels / bookings / users."""
    try:
        catalog_db.init_db()
        if tool_name == "search_hotels":
            result = _search_hotels(tool_input)
        elif tool_name == "get_hotel_details":
            result = _get_hotel_details(tool_input)
        elif tool_name == "get_room_details":
            result = _get_room_details(tool_input)
        elif tool_name == "check_hotel_availability":
            result = _check_hotel_availability(tool_input)
        elif tool_name == "get_hotel_rates":
            result = _get_hotel_rates(tool_input)
        elif tool_name == "create_booking":
            result = _create_booking(tool_input)
        elif tool_name == "get_booking":
            result = _get_booking(tool_input)
        elif tool_name == "list_bookings":
            result = _list_bookings(tool_input)
        elif tool_name == "modify_booking":
            result = _modify_booking(tool_input)
        elif tool_name == "cancel_booking":
            result = _cancel_booking(tool_input)
        elif tool_name == "get_cancellation_policy":
            result = _get_cancellation_policy(tool_input)
        elif tool_name == "get_booking_price":
            result = _get_booking_price(tool_input)
        elif tool_name == "get_booking_status":
            result = _get_booking_status(tool_input)
        elif tool_name == "get_hotel_reviews":
            result = _get_hotel_reviews(tool_input)
        elif tool_name == "search_hotels_by_location":
            result = _search_hotels_by_location(tool_input)
        elif tool_name == "get_hotel_amenities":
            result = _get_hotel_amenities(tool_input)
        else:
            result = {"error": f"Unknown tool: {tool_name}"}
        return result
    except Exception as exc:
        logger.error("Error executing tool %s: %s", tool_name, exc)
        return {"error": str(exc)}


# ============================================================================
# TOOL HANDLERS
# ============================================================================


def _search_hotels(tool_input: Dict) -> Dict:
    city = (tool_input.get("city") or "").strip()
    guests = tool_input.get("guests")
    rooms_needed = tool_input.get("rooms")
    if not city:
        return {"error": "Missing city"}
    if guests is None or rooms_needed is None:
        return {"error": "Missing guests or rooms"}

    stay = _validate_stay(tool_input.get("check_in"), tool_input.get("check_out"))
    if "error" in stay:
        return stay

    matches = []
    for hotel in catalog_db.hotels_by_city(city):
        suitable = [
            room
            for room in hotel.get("rooms", [])
            if room.get("max_guests", 0) * rooms_needed >= guests
            and room.get("inventory", 0) >= rooms_needed
        ]
        if not suitable:
            continue
        card = _project(hotel, "hotels")
        card["starting_price_inr"] = _starting_price(hotel)
        matches.append(card)

    return {
        "city": city,
        "check_in": tool_input.get("check_in"),
        "check_out": tool_input.get("check_out"),
        "guests": guests,
        "rooms": rooms_needed,
        "nights": stay["nights"],
        "count": len(matches),
        "hotels": matches,
    }


def _get_hotel_details(tool_input: Dict) -> Dict:
    hotel_id = tool_input.get("hotel_id")
    if not hotel_id:
        return {"error": "Missing hotel_id"}
    hotel = _find_hotel(hotel_id)
    if not hotel:
        return {"error": f"Hotel not found: {hotel_id}"}

    details = _project(hotel, "hotel_details")
    details["rooms"] = [
        {
            "room_id": room.get("room_id"),
            "name": room.get("name"),
            "max_guests": room.get("max_guests"),
            "base_price_inr": room.get("base_price_inr"),
        }
        for room in hotel.get("rooms", [])
    ]
    return details


def _get_room_details(tool_input: Dict) -> Dict:
    hotel, room, error = _require_hotel_room(tool_input)
    if error:
        return error
    payload = _project(room, "rooms")
    payload["hotel_id"] = hotel["hotel_id"]
    payload["hotel_name"] = hotel["name"]
    payload["policies"] = hotel.get("policies", {})
    return payload


def _check_hotel_availability(tool_input: Dict) -> Dict:
    hotel, room, error = _require_hotel_room(tool_input)
    if error:
        return error
    guests = tool_input.get("guests")
    if guests is None:
        return {"error": "Missing guests"}
    result = _availability(
        hotel,
        room,
        tool_input.get("check_in"),
        tool_input.get("check_out"),
        guests,
    )
    if "error" in result and "available" not in result:
        return result
    return {
        "hotel_id": hotel["hotel_id"],
        "hotel_name": hotel["name"],
        "room_id": room["room_id"],
        "room_name": room["name"],
        "check_in": tool_input.get("check_in"),
        "check_out": tool_input.get("check_out"),
        "guests": guests,
        **result,
    }


def _get_hotel_rates(tool_input: Dict) -> Dict:
    hotel, room, error = _require_hotel_room(tool_input)
    if error:
        return error
    stay = _validate_stay(tool_input.get("check_in"), tool_input.get("check_out"))
    if "error" in stay:
        return stay
    guests = tool_input.get("guests")
    if guests is None:
        return {"error": "Missing guests"}
    if guests > room.get("max_guests", 0):
        return {"error": f"Room sleeps {room.get('max_guests')} guests; {guests} requested."}

    rates = []
    for plan in room.get("rate_plans", []):
        breakdown = _price_breakdown(hotel, room, plan, stay["nights"])
        rates.append(
            {
                "rate_id": plan.get("rate_id"),
                "name": plan.get("name"),
                "refundable": plan.get("refundable"),
                "free_cancellation_hours": plan.get("free_cancellation_hours"),
                "nightly_rate_inr": breakdown["nightly_rate_inr"],
                "stay_subtotal_inr": breakdown["room_subtotal_inr"],
            }
        )
    return {
        "hotel_id": hotel["hotel_id"],
        "hotel_name": hotel["name"],
        "room_id": room["room_id"],
        "room_name": room["name"],
        "check_in": tool_input.get("check_in"),
        "check_out": tool_input.get("check_out"),
        "nights": stay["nights"],
        "guests": guests,
        "currency": "INR",
        "rates": rates,
    }


def _create_booking(tool_input: Dict) -> Dict:
    hotel, room, error = _require_hotel_room(tool_input)
    if error:
        return error
    for field in ("guest_name", "guest_email", "guest_phone"):
        if not tool_input.get(field):
            return {"error": f"Missing {field}"}
    phone = catalog_db.normalize_phone(tool_input.get("guest_phone"))
    if len(phone) != 10:
        return {"error": "guest_phone must be a 10-digit mobile number"}

    guests = tool_input.get("guests")
    check_in = tool_input.get("check_in")
    check_out = tool_input.get("check_out")
    availability = _availability(hotel, room, check_in, check_out, guests)
    if "error" in availability and "available" not in availability:
        return availability
    if not availability.get("available"):
        return {"error": availability.get("reason") or "Room not available"}

    rate = _default_rate(room)
    if not rate:
        return {"error": "No rate plan on this room"}
    price = _price_breakdown(hotel, room, rate, availability["nights"])
    booking_id = _next_booking_id()
    customer_id = str(tool_input["guest_email"]).strip().lower()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    booking = {
        "booking_id": booking_id,
        "hotel_id": hotel["hotel_id"],
        "hotel_name": hotel["name"],
        "hotel_area": hotel.get("area"),
        "hotel_address": hotel.get("address"),
        "room_id": room["room_id"],
        "room_name": room["name"],
        "rate_id": rate["rate_id"],
        "check_in": check_in,
        "check_out": check_out,
        "nights": availability["nights"],
        "guests": guests,
        "guest_name": tool_input["guest_name"],
        "guest_email": customer_id,
        "guest_phone": phone,
        "customer_id": customer_id,
        "status": "pending_payment",
        "total_inr": price["total_inr"],
        "currency": "INR",
        "price": price,
        "razorpay_order_id": "",
        "razorpay_payment_id": "",
        "razorpay_signature": "",
        "paid_at": "",
        "payment": {
            "provider": "razorpay",
            "status": "pending",
            "order_id": "",
            "payment_id": "",
            "signature": "",
            "amount_paise": int(round(float(price["total_inr"]) * 100)),
            "currency": "INR",
        },
        "created_at": now,
    }
    catalog_db.insert_booking(booking)
    catalog_db.upsert_user(
        name=tool_input["guest_name"],
        email=customer_id,
        phone=phone,
        booking_id=booking_id,
    )
    return {"booking": _project(booking, "bookings")}


def _get_booking(tool_input: Dict) -> Dict:
    booking, error = _load_owned_booking(tool_input)
    if error:
        return error
    return {"booking": _project(booking, "bookings")}


def _list_bookings(tool_input: Dict) -> Dict:
    phone, email, error = _guest_identity(tool_input)
    if error:
        return error
    bookings = [
        _project(booking, "bookings")
        for booking in catalog_db.list_bookings_for(email, phone)
    ]
    return {"customer_id": email, "count": len(bookings), "bookings": bookings}


def _modify_booking(tool_input: Dict) -> Dict:
    booking, error = _load_owned_booking(tool_input)
    if error:
        return error
    if booking.get("status") == "cancelled":
        return {"error": "Cannot modify a cancelled booking"}
    if booking.get("status") == "pending_payment":
        return {"error": "Complete or cancel payment before changing this stay."}
    if booking.get("status") == "payment_failed":
        return {"error": "This stay was not paid. Book again."}

    hotel_id = booking["hotel_id"]
    room_id = tool_input.get("room_id") or booking["room_id"]
    check_in = tool_input.get("check_in") or booking["check_in"]
    check_out = tool_input.get("check_out") or booking["check_out"]
    guests = tool_input.get("guests") if tool_input.get("guests") is not None else booking["guests"]

    hotel = _find_hotel(hotel_id)
    if not hotel:
        return {"error": f"Hotel not found: {hotel_id}"}
    room = _find_room(hotel, room_id)
    if not room:
        return {"error": f"Room not found: {room_id}"}

    availability = _availability(hotel, room, check_in, check_out, guests, skip_booking_id=booking_id)
    if "error" in availability and "available" not in availability:
        return availability
    if not availability.get("available"):
        return {"error": availability.get("reason") or "Room not available for the new stay"}

    rate = _find_rate(room, booking.get("rate_id")) or _default_rate(room)
    price = _price_breakdown(hotel, room, rate, availability["nights"])
    booking.update(
        {
            "room_id": room["room_id"],
            "room_name": room["name"],
            "rate_id": rate["rate_id"],
            "check_in": check_in,
            "check_out": check_out,
            "nights": availability["nights"],
            "guests": guests,
            "status": "modified",
            "total_inr": price["total_inr"],
            "price": price,
            "modified_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
    )
    catalog_db.save_booking(booking)
    return {"booking": _project(booking, "bookings")}


def _cancel_booking(tool_input: Dict) -> Dict:
    booking, error = _load_owned_booking(tool_input)
    if error:
        return error
    booking_id = booking["booking_id"]
    if booking.get("status") == "cancelled":
        return {"error": "Booking is already cancelled"}

    hotel = _find_hotel(booking["hotel_id"])
    room = _find_room(hotel, booking["room_id"]) if hotel else None
    rate = _find_rate(room, booking.get("rate_id")) if room else None
    refund_inr = 0
    policy_note = "Rate plan not found; no refund calculated."
    if rate:
        hours = int(rate.get("free_cancellation_hours") or 0)
        check_in = _parse_date(booking["check_in"])
        hours_until = ((check_in - date.today()).days * 24) if check_in else 0
        if rate.get("refundable") and hours_until >= hours:
            refund_inr = booking.get("total_inr", 0)
            policy_note = f"Full refund. Free cancellation window is {hours} hours before check-in."
        elif rate.get("refundable"):
            policy_note = f"Inside the {hours}-hour window. No refund."
        else:
            policy_note = "Non-refundable rate. No refund."

    booking["status"] = "cancelled"
    booking["cancellation_reason"] = tool_input.get("cancellation_reason")
    booking["refund_inr"] = refund_inr
    booking["cancelled_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    catalog_db.save_booking(booking)
    if booking.get("guest_phone"):
        catalog_db.upsert_user(
            name=booking.get("guest_name") or "",
            email=booking.get("guest_email") or "",
            phone=booking["guest_phone"],
            booking_id=booking_id,
        )
    return {
        "booking_id": booking_id,
        "status": "cancelled",
        "refund_inr": refund_inr,
        "policy": policy_note,
        "cancellation_reason": booking["cancellation_reason"],
    }


def _get_cancellation_policy(tool_input: Dict) -> Dict:
    hotel, room, error = _require_hotel_room(tool_input)
    if error:
        return error
    rate_id = tool_input.get("rate_id")
    if not rate_id:
        return {"error": "Missing rate_id"}
    rate = _find_rate(room, rate_id)
    if not rate:
        return {"error": f"Rate plan not found: {rate_id}"}
    return {
        "hotel_id": hotel["hotel_id"],
        "hotel_name": hotel["name"],
        "room_id": room["room_id"],
        "room_name": room["name"],
        "rate_id": rate["rate_id"],
        "rate_name": rate["name"],
        "refundable": rate.get("refundable"),
        "free_cancellation_hours": rate.get("free_cancellation_hours"),
        "policy": rate.get("policy"),
    }


def _get_booking_price(tool_input: Dict) -> Dict:
    hotel, room, error = _require_hotel_room(tool_input)
    if error:
        return error
    stay = _validate_stay(tool_input.get("check_in"), tool_input.get("check_out"))
    if "error" in stay:
        return stay
    guests = tool_input.get("guests")
    if guests is None:
        return {"error": "Missing guests"}
    if guests > room.get("max_guests", 0):
        return {"error": f"Room sleeps {room.get('max_guests')} guests; {guests} requested."}
    rate = _default_rate(room)
    if not rate:
        return {"error": "No rate plan on this room"}
    breakdown = _price_breakdown(hotel, room, rate, stay["nights"])
    return {
        "hotel_id": hotel["hotel_id"],
        "hotel_name": hotel["name"],
        "room_id": room["room_id"],
        "room_name": room["name"],
        "check_in": tool_input.get("check_in"),
        "check_out": tool_input.get("check_out"),
        "guests": guests,
        **breakdown,
    }


def _get_booking_status(tool_input: Dict) -> Dict:
    booking, error = _load_owned_booking(tool_input)
    if error:
        return error
    booking_id = booking["booking_id"]
    check_in = _parse_date(booking.get("check_in"))
    days_to_check_in = (check_in - date.today()).days if check_in else None
    return {
        "booking_id": booking_id,
        "status": booking.get("status"),
        "hotel_name": booking.get("hotel_name"),
        "check_in": booking.get("check_in"),
        "check_out": booking.get("check_out"),
        "days_to_check_in": days_to_check_in,
    }


def _get_hotel_reviews(tool_input: Dict) -> Dict:
    hotel_id = tool_input.get("hotel_id")
    if not hotel_id:
        return {"error": "Missing hotel_id"}
    hotel = _find_hotel(hotel_id)
    if not hotel:
        return {"error": f"Hotel not found: {hotel_id}"}
    reviews = [_project(review, "reviews") for review in hotel.get("reviews", [])]
    return {
        "hotel_id": hotel["hotel_id"],
        "hotel_name": hotel["name"],
        "guest_rating": hotel.get("guest_rating"),
        "review_count": hotel.get("review_count"),
        "reviews": reviews,
    }


def _search_hotels_by_location(tool_input: Dict) -> Dict:
    try:
        latitude = float(tool_input["latitude"])
        longitude = float(tool_input["longitude"])
        radius = float(tool_input["radius"])
    except (KeyError, TypeError, ValueError):
        return {"error": "latitude, longitude, and radius are required numbers"}
    if radius <= 0:
        return {"error": "radius must be greater than 0"}

    matches = []
    for hotel in _load_hotels():
        distance = _haversine_km(latitude, longitude, hotel["latitude"], hotel["longitude"])
        if distance <= radius:
            card = _project(hotel, "hotels")
            card["starting_price_inr"] = _starting_price(hotel)
            card["distance_km"] = round(distance, 2)
            matches.append(card)
    matches.sort(key=lambda item: item["distance_km"])
    return {
        "latitude": latitude,
        "longitude": longitude,
        "radius_km": radius,
        "count": len(matches),
        "hotels": matches,
    }


def _get_hotel_amenities(tool_input: Dict) -> Dict:
    hotel_id = tool_input.get("hotel_id")
    if not hotel_id:
        return {"error": "Missing hotel_id"}
    hotel = _find_hotel(hotel_id)
    if not hotel:
        return {"error": f"Hotel not found: {hotel_id}"}
    return {
        "hotel_id": hotel["hotel_id"],
        "hotel_name": hotel["name"],
        "amenities": hotel.get("amenities", []),
    }
