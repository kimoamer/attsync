import frappe


CUSTOM_FIELDS = (
    "Payroll Entry-sync_attendance",
    "Payroll Entry-synced",
)


def execute():
    for custom_field in CUSTOM_FIELDS:
        if frappe.db.exists("Custom Field", custom_field):
            frappe.delete_doc("Custom Field", custom_field, force=True, ignore_permissions=True)

    frappe.clear_cache(doctype="Payroll Entry")
