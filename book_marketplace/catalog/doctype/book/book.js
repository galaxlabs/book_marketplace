// Copyright (c) 2026, Galaxylabs  and contributors
// For license information, please see license.txt

frappe.ui.form.on("Book", {
	refresh(frm) {
		if (frm.is_new()) return;
		frm.add_custom_button(__("Create Variants"), () => open_create_variants(frm), __("Variants"));
	},
});

// Like ERPNext's Create Multiple Variants: pick several values for each attribute,
// one variant is made for every combination that does not exist yet.
function open_create_variants(frm) {
	frappe.call({
		method: "book_marketplace.catalog.doctype.book.book.get_attribute_options",
		callback(r) {
			const attrs = r.message || [];
			if (!attrs.length) {
				frappe.msgprint(__("Create a Book Attribute with its values first."));
				return;
			}
			const fields = attrs.map((a) => ({
				fieldtype: "MultiCheck",
				fieldname: a.attribute_name,
				label: a.attribute_name,
				options: a.values.map((v) => ({ label: v, value: v, checked: 0 })),
			}));
			const d = new frappe.ui.Dialog({
				title: __("Create Variants"),
				fields,
				primary_action_label: __("Create"),
				primary_action(values) {
					const args = {};
					attrs.forEach((a) => {
						const v = values[a.attribute_name];
						const list = Array.isArray(v) ? v : String(v || "").split("\n").filter(Boolean);
						if (list.length) args[a.attribute_name] = list;
					});
					if (!Object.keys(args).length) {
						frappe.msgprint(__("Select at least one value."));
						return;
					}
					frappe.call({
						method: "book_marketplace.catalog.doctype.book.book.create_multiple_variants",
						args: { book: frm.doc.name, args: JSON.stringify(args) },
						freeze: true,
						callback(res) {
							d.hide();
							frappe.show_alert({ message: __("{0} variants added", [res.message || 0]), indicator: "green" });
							frm.reload_doc();
						},
					});
				},
			});
			d.show();
		},
	});
}
