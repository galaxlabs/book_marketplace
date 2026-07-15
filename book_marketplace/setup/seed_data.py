import frappe


def seed():
    if frappe.db.get_all("SubjectMaster", limit=1):
        print("Data already seeded, skipping.")
        return

    terms = frappe.get_doc({
        "doctype": "SignupTerms",
        "title": "Marketplace Terms & Conditions",
        "version": "v1.0",
        "is_active": 1,
        "content": "<h3>Book Marketplace Terms & Conditions</h3><p>By registering, you agree to:</p><ul><li>Provide accurate business information</li><li>Comply with all applicable laws</li><li>Maintain the confidentiality of your account</li><li>Not engage in fraudulent activities</li></ul><p>The marketplace reserves the right to suspend accounts that violate these terms.</p>",
    })
    terms.insert(ignore_permissions=True)
    print(f"SignupTerms: {terms.name}")

    subjects = ["Urdu", "English", "Mathematics", "Science", "Islamiat", "Pak Studies", "Computer", "Physics", "Chemistry", "Biology"]
    for subj in subjects:
        s = frappe.get_doc({"doctype": "SubjectMaster", "subject_name": subj})
        s.insert(ignore_permissions=True)
    print(f"Subjects: {len(subjects)} created")

    publishers = [
        {"business_name": "Ilm Publishers", "profile_type": "Publisher", "email": "ilm@publisher.com", "city": "Lahore"},
        {"business_name": "Kitab Distributors", "profile_type": "Distributor", "email": "info@kitabdist.com", "city": "Karachi"},
        {"business_name": "Campus Bookseller", "profile_type": "Bookseller", "email": "campus@books.com", "city": "Islamabad"},
    ]
    for pd in publishers:
        if not frappe.db.exists("User", pd["email"]):
            user = frappe.get_doc({
                "doctype": "User",
                "email": pd["email"],
                "first_name": pd["business_name"],
                "new_password": "SeedPass123!",
                "roles": [{"role": pd["profile_type"]}],
            })
            user.insert(ignore_permissions=True)
        else:
            user = frappe.get_doc("User", pd["email"])
        profile = frappe.get_doc({
            "doctype": "BusinessProfile",
            "user": user.name,
            "profile_type": pd["profile_type"],
            "business_name": pd["business_name"],
            "email": pd["email"],
            "city": pd["city"],
            "status": "Approved",
            "approved_by": "Administrator",
            "approved_on": frappe.utils.today(),
        })
        profile.insert(ignore_permissions=True)
        user.add_roles(pd["profile_type"])
        print(f"BusinessProfile: {profile.name} ({pd['business_name']})")

        store = frappe.get_doc({
            "doctype": "BusinessStore",
            "business_profile": profile.name,
            "store_name": f"{pd['business_name']} Store",
            "slug": pd["business_name"].lower().replace(" ", "-"),
            "is_published": 1,
        })
        store.insert(ignore_permissions=True)
        print(f"BusinessStore: {store.name}")

    subject_masters = frappe.get_all("SubjectMaster", pluck="name")
    publisher_profiles = frappe.get_all("BusinessProfile", filters={"profile_type": "Publisher"}, pluck="name")

    books_data = [
        {"title": "Urdu Ka Guldasta", "author": "Prof. Ahmad", "isbn": "978-969-1234-01-1", "language": "Urdu", "subject": "Urdu", "barcode": "SKU-UR-001"},
        {"title": "English Grammar & Composition", "author": "Wren & Martin", "isbn": "978-969-1234-02-8", "language": "English", "subject": "English", "barcode": "SKU-EN-001"},
        {"title": "Mathematics for Class 9", "author": "Punjab Textbook Board", "isbn": "978-969-1234-03-5", "language": "Urdu", "subject": "Mathematics", "barcode": "SKU-MA-001"},
        {"title": "General Science", "author": "Dr. Rashid", "isbn": "978-969-1234-04-2", "language": "Urdu", "subject": "Science", "barcode": "SKU-SC-001"},
        {"title": "Islamiat (Compulsory)", "author": "Hafiz Nazar", "isbn": "978-969-1234-05-9", "language": "Urdu", "subject": "Islamiat", "barcode": "SKU-IS-001"},
    ]

    for bd in books_data:
        subject_name = frappe.db.get_value("SubjectMaster", {"subject_name": bd["subject"]}, "name")
        publisher = publisher_profiles[0] if publisher_profiles else None
        book = frappe.get_doc({
            "doctype": "Book",
            "title": bd["title"],
            "author": bd["author"],
            "publisher": publisher,
            "isbn": bd["isbn"],
            "barcode": bd["barcode"],
            "language": bd["language"],
            "book_type": "Single Volume",
            "set_mrp": 450 + (books_data.index(bd) * 50),
            "global_visibility": 1,
        })
        book.insert(ignore_permissions=True)
        if subject_name:
            book.append("subjects", {"subject": subject_name})
            book.save()
        print(f"Book: {book.name} ({bd['title']})")

    print("\nSeed data complete!")
    print(f"  SignupTerms: 1")
    print(f"  Subjects: {len(subjects)}")
    print(f"  BusinessProfiles: {len(publishers)}")
    print(f"  Books: {len(books_data)}")
