import itertools
import math
import re

import frappe
import json
from frappe.model.document import Document
from frappe.utils import get_url


class Book(Document):
    def autoname(self):
        """Title is the name while it is free. A second book with the same title (different author
        or edition) gets 'title - author', then 'title - author - publisher', then a counter.
        Existing books keep their names."""
        base = (self.title or "").strip()
        if not base:
            frappe.throw("Title is required")
        if not frappe.db.exists("Book", base):
            self.name = base
            return
        author = (self.author or "").strip()
        candidate = f"{base} - {author}" if author else base
        if not frappe.db.exists("Book", candidate):
            self.name = candidate
            return
        if self.publisher:
            candidate = f"{candidate} - {self.publisher}"
            if not frappe.db.exists("Book", candidate):
                self.name = candidate
                return
        n = 2
        while frappe.db.exists("Book", f"{candidate} ({n})"):
            n += 1
        self.name = f"{candidate} ({n})"

    def before_save(self):
        self.sync_primary_language()
        self.detect_duplicates()
        self.validate_volumes()
        self.validate_textbook()
        self.validate_sharh_of()
        self.validate_variants()

    def after_save(self):
        self.generate_qr_code()
        self.apply_variant_renames()

    def validate_variants(self):
        """Like ERPNext item variants. Each variant row has a key; its attribute rows are in Variant Attributes.
        A variant with attributes gets its name and code built from them (e.g. 'Hard، Glossy', 'HD-GL').
        Rows without attributes keep the typed name (older rows). Each combination is unique per book."""
        old = self.get_doc_before_save()
        old_names = {r.name: r.variant_name for r in (old.get("variants") or [])} if old else {}
        renames = {}

        for row in self.get("variants") or []:
            if not row.variant_key:
                row.variant_key = frappe.generate_hash(length=10)

        keys = {r.variant_key for r in self.get("variants") or []}
        groups = {}
        for a in self.get("variant_attributes") or []:
            if a.variant_key not in keys:
                frappe.throw(f"Variant Attributes کی قطار {a.attribute_value} کسی ویریئنٹ سے نہیں جڑی۔")
            groups.setdefault(a.variant_key, []).append(a)

        seen = set()
        for row in self.get("variants") or []:
            attrs = sorted(groups.get(row.variant_key, []), key=lambda a: a.attribute)
            if not attrs:
                if not (row.variant_name or "").strip():
                    frappe.throw("ویریئنٹ کا نام لازمی ہے، یا اس کی نوعیت (Variant Attributes) منتخب کریں۔")
                row.variant_code = None
                continue
            codes, names, key = [], [], []
            for a in attrs:
                abbr = frappe.db.get_value(
                    "Book Attribute Value",
                    {"parent": a.attribute, "parenttype": "Book Attribute", "attribute_value": a.attribute_value},
                    "abbr",
                )
                if not abbr:
                    frappe.throw(f"{a.attribute_value} نوعیت {a.attribute} کی قدر نہیں ہے۔")
                codes.append(abbr)
                names.append(a.attribute_value)
                key.append((a.attribute, a.attribute_value))
            key = tuple(key)
            if key in seen:
                frappe.throw("ایک ہی کتاب میں ایک جیسی نوعیتوں کا ویریئنٹ دو بار نہیں ہو سکتا۔")
            seen.add(key)
            row.variant_code = "-".join(codes)
            row.variant_name = "، ".join(names)
            old_name = old_names.get(row.name)
            if old_name and old_name != row.variant_name:
                renames[old_name] = row.variant_name
        self.flags.variant_renames = renames

    def apply_variant_renames(self):
        """When a variant's name changes, its publisher rows (Business Catalog Item) follow the new name."""
        for old, new in (self.flags.get("variant_renames") or {}).items():
            frappe.db.sql(
                "update `tabBusiness Catalog Item` set variant_name=%s where book=%s and variant_name=%s",
                (new, self.name, old),
            )

    def sync_primary_language(self):
        rows = [r.language for r in (self.get("languages") or []) if r.language]
        if len(set(rows)) > 1:
            frappe.throw("Each language is a separate product (one product per language). Add it as another book.")
        if rows:
            self.language = rows[0]

    def detect_duplicates(self):
        filters = {"name": ["!=", self.name]} if self.name else {}
        if self.isbn:
            filters["isbn"] = self.isbn
            existing = frappe.db.get_value("Book", filters, "name")
            if existing:
                frappe.msgprint(f"Warning: ISBN already exists for Book <b>{existing}</b>")
                return
        if self.title and self.author and self.publisher:
            filters = {
                "title": self.title,
                "author": self.author,
                "publisher": self.publisher,
                "name": ["!=", self.name or ""],
            }
            existing = frappe.db.get_value("Book", filters, "name")
            if existing:
                frappe.msgprint(f"Warning: Similar book exists: <b>{existing}</b>")

    def validate_sharh_of(self):
        if self.sharh_of and self.sharh_of == self.name:
            frappe.throw("ایک کتاب اپنی ہی شرح نہیں ہو سکتی۔")
        if self.sharh_of and frappe.db.get_value("Book", self.sharh_of, "sharh_of") == self.name:
            frappe.throw("دو کتابیں ایک دوسرے کی شرح نہیں ہو سکتیں۔")

    def validate_textbook(self):
        """Textbook rules: boards and Dars-e-Nizami darja. Wifaq implies Dars-e-Nizami.
        Board and darja nodes are also written to the classification table, so department search finds the book."""
        if not self.is_textbook:
            self.set("exam_boards", [])
            self.is_dars_e_nizami = 0
            self.darja = None
        boards = [r.board for r in (self.get("exam_boards") or []) if r.board]
        if self.is_textbook and any(self._is_wifaq(b) for b in boards):
            self.is_dars_e_nizami = 1
        if not self.is_dars_e_nizami:
            self.darja = None
        if self.is_dars_e_nizami and not self.darja:
            frappe.throw("درجہ لازمی ہے: درسِ نظامی کی کتاب کے لیے درجہ منتخب کریں۔")
        if self.darja and frappe.db.get_value("Book Category", self.darja, "category_type") != "Department":
            frappe.throw("درجہ صرف شعبہ جات (Department) میں سے ہو سکتا ہے۔")
        self.sync_textbook_classifications(boards)

    @staticmethod
    def _is_wifaq(board):
        label = frappe.db.get_value("Book Category", board, ["category_name", "category_name_ur"], as_dict=True) or {}
        return "وفاق" in f"{label.get('category_name') or ''} {label.get('category_name_ur') or ''}"

    def sync_textbook_classifications(self, boards):
        old = self.get_doc_before_save()
        old_managed = set()
        if old:
            old_managed = {r.board for r in (old.get("exam_boards") or []) if r.board}
            if old.darja:
                old_managed.add(old.darja)
        new_managed = set(boards)
        if self.darja:
            new_managed.add(self.darja)
        kept = [r for r in (self.get("classifications") or [])
                if not (r.category in old_managed and r.category not in new_managed)]
        self.set("classifications", kept)
        present = {r.category for r in kept}
        for cat in sorted(new_managed - present):
            self.append("classifications", {"category": cat})

    def validate_volumes(self):
        if self.book_type != "Multi Volume":
            self.set("volumes", [])
            return
        volumes = self.get("volumes", [])
        if not volumes:
            frappe.throw("Multi-volume books must have at least one volume entry")

    def generate_qr_code(self):
        if self.qr_code:
            return
        try:
            url = get_url(f"/books/{self.name}")
            from frappe.utils import get_files_path
            import os, qrcode
            from io import BytesIO
            img = qrcode.make(url)
            file_name = f"qr_{self.name}.png"
            file_path = get_files_path(file_name, is_private=0)
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            img.save(file_path)
            file_doc = frappe.get_doc({
                "doctype": "File",
                "file_name": file_name,
                "file_url": f"/files/{file_name}",
                "attached_to_doctype": "Book",
                "attached_to_name": self.name,
                "is_private": 0,
            })
            file_doc.insert(ignore_permissions=True)
            self.db_set("qr_code", file_doc.file_url)
        except ImportError:
            pass


MAX_VARIANTS_AT_ONCE = 500


@frappe.whitelist()
def get_attribute_options():
    """Attributes and their values for the Create Variants dialog."""
    frappe.has_permission("Book", ptype="read", throw=True)
    out = []
    for name in frappe.get_all("Book Attribute", filters={"disabled": 0}, pluck="name", order_by="name asc"):
        values = frappe.get_all(
            "Book Attribute Value", filters={"parent": name, "parenttype": "Book Attribute"},
            pluck="attribute_value", order_by="idx asc",
        )
        out.append({"attribute_name": name, "values": values})
    return out


@frappe.whitelist(methods=["POST"])
def create_multiple_variants(book, args):
    """args = {attribute: [value, ...], ...}. Adds one variant for every combination not yet present."""
    frappe.has_permission("Book", ptype="write", doc=book, throw=True)
    args = frappe.parse_json(args) or {}
    args = {k: [v for v in (vals or []) if v] for k, vals in args.items()}
    args = {k: v for k, v in args.items() if v}
    if not args:
        frappe.throw("کم از کم ایک نوعیت کی قدر منتخب کریں۔")
    total = math.prod(len(v) for v in args.values())
    if total > MAX_VARIANTS_AT_ONCE:
        frappe.throw(f"ایک وقت میں {MAX_VARIANTS_AT_ONCE} سے زیادہ ویریئنٹ نہیں بنائے جا سکتے۔")

    doc = frappe.get_doc("Book", book)
    groups = {}
    for a in doc.get("variant_attributes") or []:
        groups.setdefault(a.variant_key, []).append((a.attribute, a.attribute_value))
    existing = {tuple(sorted(v)) for v in groups.values()}

    added = 0
    pairs = [[(k, val) for val in vals] for k, vals in args.items()]
    for combo in itertools.product(*pairs):
        key = tuple(sorted(combo))
        if key in existing:
            continue
        existing.add(key)
        variant_key = frappe.generate_hash(length=10)
        doc.append("variants", {"variant_key": variant_key})
        for attribute, value in combo:
            doc.append("variant_attributes", {"variant_key": variant_key, "attribute": attribute, "attribute_value": value})
        added += 1
    if added:
        doc.save()
    return added


_DIACRITICS = re.compile("[ً-ٰٟۖ-ۭؐ-ؚ​-‏۔ـ]")
_UNIFY = str.maketrans({"ي": "ی", "ى": "ی", "ك": "ک", "ۃ": "ہ", "ة": "ہ", "ۂ": "ہ", "ؒ": "", "ؓ": "", "۔": "."})


def normalize_title(text):
    """Spelling-insensitive key for matching titles: unifies Arabic and Urdu letter forms, drops harakat and
    honorifics, and collapses spaces. 'صحیح البخاری' and 'صحیح بخاری' give different keys only if the letters differ."""
    t = _DIACRITICS.sub("", (text or "").translate(_UNIFY))
    t = re.sub(r"[()\[\]\-–—,،:;!?\"'«»]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def find_book_by_alias(title):
    """The original Book for a title that is its name or one of its aliases, else None."""
    key = normalize_title(title)
    if not key:
        return None
    for name in frappe.get_all("Book", pluck="name", limit_page_length=0):
        if normalize_title(name) == key:
            return name
    for row in frappe.get_all("Book Alias", fields=["alias_name", "parent"], limit_page_length=0):
        if normalize_title(row.alias_name) == key:
            return row.parent
    return None
