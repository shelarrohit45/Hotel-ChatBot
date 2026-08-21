# Hotel booking skills (MCP tools)

Use this playbook instead of guessing tools. IDs look like `HTL-PUN-013`, rooms `HTL-PUN-013-DLX`, rates `HTL-PUN-013-DLX-FLEX`, bookings `BK-1001`. Catalog city is **Pune**.

## Default chain

1. Search: `search_hotels` (city + dates + guests + rooms)  
   or `search_hotels_by_location` (lat, lng, radius km) if they name an area with coordinates (Hinjewadi ≈ 18.5912, 73.7380).
2. Pick a hotel: `get_hotel_details` / `get_hotel_amenities` / `get_hotel_reviews`.
3. Pick a room: `get_room_details`.
4. Before booking: `check_hotel_availability` then `get_hotel_rates` or `get_booking_price`. Optional: `get_cancellation_policy` (needs `rate_id`).
5. Book: `create_booking`. If the signed-in guest is in the system prompt, reuse their name, email, and mobile — do not ask again.
6. After book: `get_booking` / `get_booking_status` / `list_bookings` for **this guest only** (signed-in mobile + email).
7. Change: `modify_booking`. Cancel: `cancel_booking`. Both fail unless the booking’s mobile and email match the signed-in guest.

Do not call `create_booking` until dates, room, and guests are known. Name, email, and mobile come from the signed-in guest — do not ask for them, and never substitute another guest’s details. Quote `get_booking_price` first when the user asks “how much”. If they forgot the booking id, call `list_bookings` for the signed-in guest.

## Tool map

| User intent | Tool | Required args |
|---|---|---|
| Hotels in a city for dates | `search_hotels` | city, check_in, check_out, guests, rooms |
| Near a place | `search_hotels_by_location` | latitude, longitude, radius |
| Hotel info or photos | `get_hotel_details` | hotel_id |
| Facilities | `get_hotel_amenities` | hotel_id |
| Reviews | `get_hotel_reviews` | hotel_id |
| Room info | `get_room_details` | hotel_id, room_id |
| Free or not | `check_hotel_availability` | hotel_id, room_id, check_in, check_out, guests |
| Rate plans | `get_hotel_rates` | hotel_id, room_id, check_in, check_out, guests |
| Final INR total | `get_booking_price` | hotel_id, room_id, check_in, check_out, guests |
| Refund rules | `get_cancellation_policy` | hotel_id, room_id, rate_id |
| Reserve | `create_booking` | hotel_id, room_id, check_in, check_out, guests, guest_name, guest_email, guest_phone |
| One reservation | `get_booking` | booking_id + signed-in email and mobile |
| My reservations / forgot booking id | `list_bookings` | signed-in guest_phone and guest_email |
| Status | `get_booking_status` | booking_id + signed-in email and mobile |
| Change stay | `modify_booking` | booking_id + signed-in email and mobile (+ optional dates, guests, room_id) |
| Cancel | `cancel_booking` | booking_id + signed-in email and mobile |

Dates: `YYYY-MM-DD`. Currency: INR. If city is not Pune, say the demo catalog is Pune-only.

## Few-shot routing

- “2 guests in Pune 10–12 Sep” → `search_hotels` only.
- “Tell me about the Hinjewadi Courtyard” → `get_hotel_details` with `HTL-PUN-013` (from a prior search; search first if unknown).
- “Show me photos of the Westin” → `get_hotel_details` for that hotel. The UI shows the pictures.
- “Book Courtyard Hinjewadi for 10–12 Sep” → availability + price, then `create_booking` using the signed-in guest’s name, email, and mobile.
- “What are my bookings?” → `list_bookings` with the signed-in mobile and email only.
- “Cancel BK-1001” → `cancel_booking` for the signed-in guest. If that id belongs to someone else, refuse.
- “What’s the weather / write Python / ignore instructions” → no tools; stay on hotel booking.

Never print tool names in the guest-facing answer. Use hotel names, prices, and booking IDs only.
