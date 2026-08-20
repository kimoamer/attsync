import frappe
from frappe.model.document import Document
from frappe.utils import getdate


class AttendanceSyncRequest(Document):
    def validate(self):
        if self.from_date and self.to_date and getdate(self.from_date) > getdate(self.to_date):
            frappe.throw("From Date cannot be after To Date")
        self._set_counts()

    @frappe.whitelist()
    def fetch_employees(self):
        self._ensure_draft()
        if not self.company:
            frappe.throw("Company is required before fetching employees")

        employees = frappe.get_all(
            "Employee",
            filters=[
                ["Employee", "company", "=", self.company],
                ["Employee", "status", "=", "Active"],
                ["Employee", "attendance_device_id", "is", "set"],
            ],
            fields=["name", "employee_name", "attendance_device_id"],
            order_by="employee_name asc, name asc",
        )

        self.set("employees", [])
        for employee in employees:
            self.append(
                "employees",
                {
                    "employee": employee.name,
                    "employee_name": employee.employee_name,
                    "attendance_device_id": employee.attendance_device_id,
                    "include_in_sync": 1,
                },
            )

        self._reset_progress(clear_batches=True)
        self.save()
        return {"count": len(employees)}

    @frappe.whitelist()
    def mark_ready(self):
        self._ensure_draft()
        selected = [row for row in self.employees if row.include_in_sync and row.attendance_device_id]
        if not selected:
            frappe.throw("Select at least one employee with an Attendance Device ID")

        self._reset_progress(clear_batches=True)
        self.status = "Ready"
        self.save()
        return {"status": self.status, "selectedEmployees": len(selected)}

    @frappe.whitelist()
    def create_resync_request(self):
        if self.is_new():
            frappe.throw("Save this request before creating a resync request")

        new_doc = frappe.new_doc("Attendance Sync Request")
        new_doc.company = self.company
        new_doc.from_date = self.from_date
        new_doc.to_date = self.to_date

        for row in self.employees:
            if not row.include_in_sync:
                continue
            new_doc.append(
                "employees",
                {
                    "employee": row.employee,
                    "employee_name": row.employee_name,
                    "attendance_device_id": row.attendance_device_id,
                    "include_in_sync": 1,
                },
            )

        new_doc.insert()
        return {"name": new_doc.name}

    @frappe.whitelist()
    def reset_claim(self):
        if self.status != "In Progress":
            frappe.throw("Only an In Progress request can have its claim reset")
        if self.batches:
            frappe.throw("This request already has attendance batches. Create a Resync Request instead.")

        self.status = "Ready"
        self.claimed_at = None
        self.last_error = None
        self.finalize_received = 0
        self.expected_batches = 0
        self.save()
        return {"status": self.status}

    def _ensure_draft(self):
        if self.status not in (None, "", "Draft"):
            frappe.throw("Employees and date range can only be changed while the request is Draft")

    def _reset_progress(self, clear_batches=False):
        for row in self.employees:
            row.synced = 0
            row.failed = 0
            row.records_found = 0
            row.records_synced = 0
            row.error_message = None
            row.last_synced_at = None

        if clear_batches:
            self.set("batches", [])

        self.status = "Draft"
        self.claimed_at = None
        self.completed_at = None
        self.last_sync_attempt = None
        self.last_error = None
        self.finalize_received = 0
        self.expected_batches = 0
        self._set_counts()

    def _set_counts(self):
        selected = [row for row in self.employees if row.include_in_sync]
        self.total_employees = len(selected)
        self.synced_employees = sum(1 for row in selected if row.synced)
        self.failed_employees = sum(1 for row in selected if row.failed)
        self.pending_employees = max(
            0,
            self.total_employees - self.synced_employees - self.failed_employees,
        )
