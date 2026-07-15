import frappe


def calculate_discount_loss(book_id, seller_id, selling_price, mrp=None):
    if not mrp:
        policy = frappe.db.get_value(
            "PricePolicy",
            {"book": book_id, "publisher_profile": seller_id, "is_active": 1},
            ["mrp", "min_seller_price", "max_discount_pct", "allow_discount_below_min"],
            as_dict=True,
        )
        if not policy:
            return {"can_sell": True, "loss": 0}
        mrp = policy.mrp
        min_price = policy.min_seller_price or mrp * 0.5
        max_discount = policy.max_discount_pct or 0
    else:
        min_price = mrp * 0.5
        max_discount = 0
    discount_pct = ((mrp - selling_price) / mrp) * 100
    loss = 0
    can_sell = True
    warning = None
    if max_discount and discount_pct > max_discount:
        loss = selling_price - (mrp * (1 - max_discount / 100))
        warning = f"Discount {discount_pct:.1f}% exceeds policy limit {max_discount}%"
    if selling_price < min_price:
        loss = min_price - selling_price
        can_sell = False
        warning = f"Price below minimum ({min_price})"
    return {"can_sell": can_sell, "loss": round(loss, 2), "discount_pct": round(discount_pct, 1), "warning": warning}
