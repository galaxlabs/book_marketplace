"""Saved buyers and sellers from past purchase orders, so each business finds its old trading partners listed."""
import frappe

from book_marketplace.trading.doc_numbers import remember_party


def execute():
    for po in frappe.get_all("Purchase Order Request", fields=["buyer_profile", "seller_profile"], limit_page_length=0):
        if not (po.buyer_profile and po.seller_profile):
            continue
        if not (frappe.db.exists("BusinessProfile", po.buyer_profile) and frappe.db.exists("BusinessProfile", po.seller_profile)):
            continue
        remember_party(po.buyer_profile, po.seller_profile, "Seller")
        remember_party(po.seller_profile, po.buyer_profile, "Buyer")
