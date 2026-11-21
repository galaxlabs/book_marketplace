"""What a shop sells (only those item types show in its panel) and when it is open.

- item_types: the kinds of goods it sells (Book, Stationery, Perfume & Attar ...). Empty means books only.
- weekly_hours: each day open or closed, with from / to times. A day not listed is open all day.
- holidays: dates the shop is closed (Eid ...), or open with its own hours when "open anyway" is ticked.
- shop_status: open or closed now, in the owner's time zone, with a notice for buyers and the next opening.
"""
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import frappe
from frappe import _
from frappe.utils import get_datetime, getdate

from book_marketplace.auth.api.auth import _assert_profile_owner

DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
DAYS_UR = {"Monday": "پیر", "Tuesday": "منگل", "Wednesday": "بدھ", "Thursday": "جمعرات", "Friday": "جمعہ",
           "Saturday": "ہفتہ", "Sunday": "اتوار"}


def _types():
    return (frappe.get_meta("Book").get_field("item_type").options or "Book").split("\n")


def item_types_of(profile_id):
    raw = frappe.db.get_value("BusinessProfile", profile_id, "item_types") or ""
    allowed = _types()
    chosen = [t for t in raw.split("\n") if t.strip() in allowed]
    return chosen or ["Book"]


def _t(v):
    """Time field value as HH:MM."""
    if not v:
        return ""
    s = str(v)
    parts = s.split(":")
    return f"{int(parts[0]):02d}:{parts[1][:2]}" if len(parts) >= 2 else ""


@frappe.whitelist()
def get_shop_settings(profile_id):
    _assert_profile_owner(profile_id)
    doc = frappe.get_doc("BusinessProfile", profile_id)
    hours = {h.day: h for h in doc.get("weekly_hours") or []}
    return {
        "item_types": item_types_of(profile_id),
        "item_types_set": bool((doc.get("item_types") or "").strip()),
        "all_types": _types(),
        "closed_note": doc.get("closed_note") or "",
        "weekly_hours": [{"day": d, "is_open": int(hours[d].is_open) if d in hours else 1,
                          "open_from": _t(hours[d].open_from) if d in hours else "",
                          "open_to": _t(hours[d].open_to) if d in hours else ""} for d in DAYS],
        "holidays": [{"holiday_date": str(h.holiday_date), "note": h.note or "", "open_anyway": int(h.open_anyway or 0),
                      "open_from": _t(h.open_from), "open_to": _t(h.open_to)}
                     for h in sorted(doc.get("holidays") or [], key=lambda x: str(x.holiday_date))],
    }


@frappe.whitelist()
def save_item_types(profile_id, item_types):
    _assert_profile_owner(profile_id)
    chosen = json.loads(item_types) if isinstance(item_types, str) else (item_types or [])
    allowed = _types()
    bad = [t for t in chosen if t not in allowed]
    if bad:
        frappe.throw(_("Unknown item type: {0}").format(bad[0]))
    if not chosen:
        frappe.throw(_("Choose at least one kind of item."))
    frappe.db.set_value("BusinessProfile", profile_id, "item_types", "\n".join(dict.fromkeys(chosen)))
    return {"item_types": item_types_of(profile_id)}


def _valid_time(v):
    if not v:
        return None
    try:
        datetime.strptime(v, "%H:%M")
    except ValueError:
        frappe.throw(_("Write the time as HH:MM, for example 09:30."))
    return v


@frappe.whitelist()
def save_shop_hours(profile_id, weekly_hours=None, holidays=None, closed_note=""):
    _assert_profile_owner(profile_id)
    week = json.loads(weekly_hours) if isinstance(weekly_hours, str) else (weekly_hours or [])
    days = json.loads(holidays) if isinstance(holidays, str) else (holidays or [])
    doc = frappe.get_doc("BusinessProfile", profile_id)
    doc.set("weekly_hours", [])
    for r in week:
        if r.get("day") not in DAYS:
            frappe.throw(_("Unknown day."))
        a, b = _valid_time(r.get("open_from")), _valid_time(r.get("open_to"))
        if a and b and a == b:
            frappe.throw(_("{0}: the opening and closing times are the same.").format(r["day"]))
        doc.append("weekly_hours", {"day": r["day"], "is_open": 1 if r.get("is_open") else 0, "open_from": a, "open_to": b})
    doc.set("holidays", [])
    seen = set()
    for h in days:
        d = h.get("holiday_date")
        if not d:
            continue
        d = str(getdate(d))
        if d in seen:
            frappe.throw(_("The date {0} is written twice.").format(d))
        seen.add(d)
        doc.append("holidays", {"holiday_date": d, "note": (h.get("note") or "")[:140],
                                "open_anyway": 1 if h.get("open_anyway") else 0,
                                "open_from": _valid_time(h.get("open_from")), "open_to": _valid_time(h.get("open_to"))})
    doc.closed_note = (closed_note or "").strip()[:140]
    doc.save(ignore_permissions=True)
    return get_shop_settings(profile_id)


def _zone_of(profile_id):
    owner = frappe.db.get_value("BusinessProfile", profile_id, "owner")
    z = frappe.db.get_value("Account Preference", owner, "time_zone") if owner else None
    return z or "Asia/Karachi"


def _plan_for(doc, day_date):
    """(open?, from, to, note) for one date: holiday first, then the weekly day."""
    for h in doc.get("holidays") or []:
        if str(h.holiday_date) == str(day_date):
            if h.open_anyway:
                return True, _t(h.open_from), _t(h.open_to), h.note or ""
            return False, "", "", h.note or "holiday"
    name = DAYS[day_date.weekday()]
    for w in doc.get("weekly_hours") or []:
        if w.day == name:
            return bool(w.is_open), _t(w.open_from), _t(w.open_to), ""
    return True, "", "", ""


@frappe.whitelist(allow_guest=True)
def shop_status(profile_id):
    """Open or closed now in the shop's own time zone, with a notice for buyers. Anyone can read it."""
    if not profile_id or not frappe.db.exists("BusinessProfile", profile_id):
        return None
    doc = frappe.get_doc("BusinessProfile", profile_id)
    zone = _zone_of(profile_id)
    now = datetime.now(ZoneInfo(zone))
    today = now.date()
    is_open, a, b, note = _plan_for(doc, today)
    hhmm = now.strftime("%H:%M")
    open_now = is_open and (not a or hhmm >= a) and (not b or hhmm < b)
    # the next time it opens, within two weeks
    nxt = None
    for i in range(0, 15):
        d = today + timedelta(days=i)
        o, fa, _fb, _n = _plan_for(doc, d)
        if not o:
            continue
        if i == 0 and (open_now or (fa and hhmm >= fa)):
            continue
        nxt = {"date": str(d), "day": DAYS[d.weekday()], "day_ur": DAYS_UR[DAYS[d.weekday()]], "from": fa}
        break
    if open_now:
        msg = f"دکان کھلی ہے{f'، {b} تک' if b else ''}۔ / Open now{f' until {b}' if b else ''}."
    elif not is_open:
        why = f" ({note})" if note and note != "holiday" else ""
        msg = f"آج دکان بند ہے{why}۔ / Closed today{why}."
    else:
        msg = "دکان ابھی بند ہے۔ / Closed now."
    if not open_now and nxt:
        when = "آج" if nxt["date"] == str(today) else nxt["day_ur"]
        msg += f" {when} {nxt['from'] or ''} کھلے گی۔ / Opens {('today' if nxt['date'] == str(today) else nxt['day'])} {nxt['from'] or ''}.".replace("  ", " ")
    if not open_now and (doc.get("closed_note") or "").strip():
        msg += " " + doc.closed_note.strip()
    return {"open": bool(open_now), "today_open": bool(is_open), "from": a, "to": b, "note": note,
            "message": msg, "next_open": nxt, "time_zone": zone, "now": hhmm}
