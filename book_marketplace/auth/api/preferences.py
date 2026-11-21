"""A signed-in account keeps its own look (theme and colors) and language. Other accounts never see them."""
import json

from zoneinfo import available_timezones

import frappe
from frappe import _
from frappe.utils import get_system_timezone, now_datetime

THEMES = ("white", "indigo", "ink-gold", "custom")
# an account that has not picked a zone reads times in Pakistan time
DEFAULT_ZONE = "Asia/Karachi"


def _me():
    if not frappe.session.user or frappe.session.user == "Guest":
        frappe.throw(_("Please log in."), frappe.AuthenticationError)
    return frappe.session.user


# the capital or largest city first, for countries with many zones
MAIN_ZONE = {"United States": "America/New_York", "Canada": "America/Toronto", "Australia": "Australia/Sydney",
             "Russia": "Europe/Moscow", "Brazil": "America/Sao_Paulo", "Indonesia": "Asia/Jakarta",
             "Mexico": "America/Mexico_City", "China": "Asia/Shanghai", "Kazakhstan": "Asia/Almaty",
             "India": "Asia/Kolkata", "Malaysia": "Asia/Kuala_Lumpur", "Spain": "Europe/Madrid"}


def zones_of_country(country):
    """Time zones of a country, from the Country list (Pakistan: Asia/Karachi; United States: many)."""
    raw = frappe.db.get_value("Country", country, "time_zones") if country else ""
    known = available_timezones()
    zones = [z for z in dict.fromkeys((raw or "").split()) if z in known]
    main = MAIN_ZONE.get(country)
    if main in zones:
        zones = [main] + [z for z in zones if z != main]
    return zones


def _profile_country(user):
    return frappe.db.get_value("BusinessProfile", {"owner": user}, "country") or ""


def _clocks(raw):
    try:
        zones = json.loads(raw or "[]")
    except ValueError:
        zones = []
    known = available_timezones()
    return [z for z in zones if isinstance(z, str) and z in known][:12]


@frappe.whitelist()
def my_preferences():
    user = _me()
    row = frappe.db.get_value("Account Preference", user,
                              ["language", "theme_name", "colors", "brand_display", "time_zone", "country",
                               "hijri_adjust", "show_world_clocks", "world_clocks"], as_dict=True) or frappe._dict()
    # server_time_zone: the zone the server writes its times in, so the panel can show them in the account's zone
    server_zone = get_system_timezone()
    # no zone chosen: the main zone of the account's country (its own choice, else its business's), else Pakistan
    country = row.get("country") or _profile_country(user)
    zone = row.get("time_zone") or (zones_of_country(country) or [DEFAULT_ZONE])[0]
    try:
        colors = json.loads(row.get("colors") or "{}")
    except ValueError:
        colors = {}
    return {"language": row.get("language") or "ur", "theme_name": row.get("theme_name") or "white", "colors": colors,
            "brand_display": row.get("brand_display") or "both", "time_zone": zone,
            "server_time_zone": server_zone, "country": country or "",
            "hijri_adjust": int(row.get("hijri_adjust") or 0),
            "show_world_clocks": int(row.get("show_world_clocks") or 0),
            "world_clocks": _clocks(row.get("world_clocks"))}


@frappe.whitelist()
def country_time_zones(country):
    """The zones of one country, the first is its main zone."""
    _me()
    return zones_of_country(country)


@frappe.whitelist()
def list_countries():
    _me()
    return frappe.get_all("Country", pluck="name", order_by="name asc", limit_page_length=0)


@frappe.whitelist()
def save_clock_settings(country=None, time_zone=None, hijri_adjust=None, show_world_clocks=None, world_clocks=None):
    """Country (its main zone becomes the account's zone unless a zone is sent), Hijri adjustment and world clocks."""
    user = _me()
    if frappe.db.exists("Account Preference", user):
        doc = frappe.get_doc("Account Preference", user)
    else:
        doc = frappe.get_doc({"doctype": "Account Preference", "user": user, "language": "ur", "theme_name": "white"})
    if country is not None:
        if country and not frappe.db.exists("Country", country):
            frappe.throw(_("Unknown country."))
        doc.country = country or None
        if country and not time_zone:
            zones = zones_of_country(country)
            if zones:
                doc.time_zone = zones[0]
    if time_zone:
        if time_zone not in available_timezones():
            frappe.throw(_("Unknown time zone."))
        doc.time_zone = time_zone
    if hijri_adjust is not None:
        h = int(hijri_adjust or 0)
        if h < -2 or h > 2:
            frappe.throw(_("The Hijri adjustment is between -2 and +2 days."))
        doc.hijri_adjust = h
    if show_world_clocks is not None:
        doc.show_world_clocks = 1 if str(show_world_clocks) in ("1", "true", "True") else 0
    if world_clocks is not None:
        zones = json.loads(world_clocks) if isinstance(world_clocks, str) else (world_clocks or [])
        bad = [z for z in zones if z not in available_timezones()]
        if bad:
            frappe.throw(_("Unknown time zone: {0}").format(bad[0]))
        doc.world_clocks = json.dumps(list(dict.fromkeys(zones))[:12])
    doc.save(ignore_permissions=True)
    return my_preferences()


@frappe.whitelist()
def save_preferences(language="ur", theme_name="white", colors=None, brand_display=None, time_zone=None):
    user = _me()
    if language not in ("ur", "en"):
        frappe.throw(_("Unknown language."))
    if theme_name not in THEMES:
        frappe.throw(_("Unknown theme."))
    data = json.loads(colors) if isinstance(colors, str) else (colors or {})
    clean = {}
    for key, value in (data or {}).items():
        v = str(value or "").strip()
        if len(key) > 40 or not v.startswith("#") or len(v) not in (4, 7, 9):
            frappe.throw(_("Colors must be hex values like #1f6f6b."))
        clean[key] = v
    if frappe.db.exists("Account Preference", user):
        doc = frappe.get_doc("Account Preference", user)
    else:
        doc = frappe.get_doc({"doctype": "Account Preference", "user": user})
    doc.language = language
    doc.theme_name = theme_name
    doc.colors = json.dumps(clean)
    if brand_display is not None:
        if brand_display not in ("both", "logo", "name"):
            frappe.throw(_("Unknown header display."))
        doc.brand_display = brand_display
    if time_zone is not None:
        # changed only when sent; must be a real zone name such as Asia/Karachi
        if time_zone not in available_timezones():
            frappe.throw(_("Unknown time zone."))
        doc.time_zone = time_zone
    doc.save(ignore_permissions=True)
    return {"language": language, "theme_name": theme_name, "colors": clean, "brand_display": doc.brand_display or "both",
            "time_zone": doc.time_zone or DEFAULT_ZONE, "saved_on": str(now_datetime())}
