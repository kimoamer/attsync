frappe.query_reports["Attendance Calculation Detail"] = {
  filters: [
    {"fieldname": "company", "label": "Company", "fieldtype": "Link", "options": "Company", "reqd": 1, "default": frappe.defaults.get_user_default('Company')},
    {"fieldname": "from_date", "label": "From Date", "fieldtype": "Date", "reqd": 1, "default": frappe.datetime.month_start()},
    {"fieldname": "to_date", "label": "To Date", "fieldtype": "Date", "reqd": 1, "default": frappe.datetime.get_today()},
    {"fieldname":"department","label":"Department","fieldtype":"Link","options":"Department"},
    {"fieldname":"employee","label":"Employee","fieldtype":"Link","options":"Employee"},
    {"fieldname": "include_unrecorded", "label": "Include Days Without Records", "fieldtype": "Check", "default": 0},
  ]
};
