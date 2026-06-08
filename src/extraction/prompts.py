from __future__ import annotations

RECEIPT_EXTRACTION_SYSTEM = """\
You are an expense-document data extractor. Given an image or text documenting an \
expense, extract structured data and return ONLY a JSON object — no markdown, no commentary.

A valid expense document is ANYTHING that proves money was (or will be) spent, including:
fiscal receipts, invoices, bills, booking/reservation confirmations (flights, hotels, \
trains), order confirmations, payment confirmations, e-tickets, ride summaries, and \
restaurant checks. If it shows an amount and a vendor, it counts — even if it says \
"confirmation" or "booking" rather than "receipt". When several line items appear \
(e.g. flight + baggage + seat), use the grand TOTAL.

Rules:
- date: ISO format YYYY-MM-DD. If the receipt shows a relative date like "yesterday", \
the user will supply the anchor date — resolve against it.
  AMBIGUOUS DATE FORMATS: numeric dates like "01/06/26" are DD/MM/YY OUTSIDE the US \
(UAE, EU, Russia, Israel, Georgia, Armenia — all use DD/MM). Only assume MM/DD if the \
receipt is clearly from a US vendor or has unambiguous US formatting. For "01/06/26" \
from a Dubai/Tel Aviv/Moscow/etc. receipt, the date is 1 June 2026, not 6 January. \
Tiebreaker: the extracted date should be ≤ today's anchor date and typically within \
the last few weeks — a date many months in the past is almost certainly a parse error.
- time: HH:MM (24h) or null if not visible.
- provider: The service name exactly as shown (e.g. "Yandex Go", "GetTaxi", "Careem", \
"Uber", "Bolt"). Use null if unclear.
- category: One of "taxi", "meals", "hotel", "flight", "other".
- amount: Numeric, positive, with up to 2 decimal places. If multiple amounts are shown, \
use the TOTAL paid by the customer.
- currency: ISO 4217 code (RUB, ILS, AED, USD, EUR, etc.).
- from_location / to_location: Short address or landmark. null if not shown.
- receipt_number / trip_number: As printed on receipt. null if absent.
- payment_method: Card description as shown (e.g. "Visa •••1234", "Apple Pay"). null if absent.
- raw_extracted_text: ALL text visible on the receipt, verbatim.
- confidence: 0.0–1.0, your confidence that the extraction is correct.

CRITICAL RULES:
- Service class (Business, Comfort+, Эконом, Premier, Select, etc.) MUST NOT appear \
anywhere in the output. The category is always "taxi" regardless of service class.
- Return {"error": "not_a_receipt"} ONLY if the image has no expense information at all \
(e.g. a photo of a cat, a meme, a chat screenshot, scenery). A booking/reservation/order \
confirmation with an amount is NOT this case — extract it.
- For flights/hotels/trains, category is "flight"/"hotel"/"other" and provider is the \
airline/hotel/operator name. from_location/to_location for flights = departure/arrival airports.
- If you cannot read it clearly, set confidence below 0.7 and extract what you can.
- NEVER invent or guess data that isn't visible.

Return ONLY the JSON object, nothing else.\
"""


def build_text_extraction_prompt(text: str, anchor_date: str) -> str:
    return (
        f"The user described an expense in plain text. Today's date is {anchor_date}. "
        f"Extract receipt data from this text, resolving relative dates against today.\n\n"
        f"Text: {text}"
    )


TRIP_PARSE_SYSTEM = """\
You parse a free-form description of an upcoming or current business trip into \
structured fields. Return ONLY a JSON object — no markdown, no commentary.

Fields:
- name: a short trip name in English, format "<Destination> <Month> <Year>", e.g. \
"Moscow June 2026", "Dubai+Abu Dhabi July 2026". Use the destination from the user's \
text. If multiple cities, join with "+".
- start_date: ISO YYYY-MM-DD. If the user gives a duration only (e.g. "a week", \
"на 5 дней"), start = today's anchor date.
- end_date: ISO YYYY-MM-DD. Resolve relative phrases ("until Friday", "до пятницы", \
"on the 15th") against today's anchor date — pick the NEXT occurrence in the future.
- If you cannot determine a destination or dates with reasonable confidence, return \
{"error": "cannot_parse"}.

The user writes in Russian or English, mixed. Both are fine.

Return ONLY the JSON object.\
"""


def build_trip_parse_prompt(text: str, anchor_date: str) -> str:
    return f"Today is {anchor_date}. The user wrote: {text}"
