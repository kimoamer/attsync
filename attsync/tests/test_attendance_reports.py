"""Fast checks for attendance calculation logic; runs inside an installed Frappe bench."""

from unittest import TestCase

from frappe import _dict

from attsync.attendance_reports import _leave_bucket, calculated_columns, filter_by_view, summarize


class TestAttendanceReports(TestCase):
    def test_spreadsheet_columns(self):
        self.assertEqual(
            [c["fieldname"] for c in calculated_columns()][:12],
            [
                "device_id", "employee_name", "late", "early", "absent", "business_trip",
                "sick_leave", "unpaid_leave", "unpaid_half_day", "unpaid_quarter_day",
                "maternity_leave", "unpaid_three_quarter_day",
            ],
        )

    def test_part_day_unpaid_leave(self):
        for fraction, result in (
            (0.25, "unpaid_quarter_day"), (0.5, "unpaid_half_day"),
            (0.75, "unpaid_three_quarter_day"), (1.0, "unpaid_leave"),
        ):
            self.assertEqual(
                _leave_bucket({"status": "On Leave", "leave_type": "Unpaid",
                               "leave_is_lwp": True, "leave_fraction": fraction}),
                result,
            )

    def test_no_record_does_not_count_as_absent(self):
        employee = _dict(name="EMP-001", employee_name="Test", department="HR", attendance_device_id="001")
        record = {
            "employee": "EMP-001", "late_entry": 0, "early_exit": 0, "absent": 0,
            "missing_in": 0, "missing_out": 0, "late_minutes": 0, "early_minutes": 0,
            "working_hours": 0, "overtime_minutes": 0, "status": "No Record",
            "holiday": 0, "leave_type": "", "leave_fraction": 0, "leave_is_lwp": False,
        }
        totals = summarize([record], [employee])[0]
        self.assertEqual(totals["absent"], 0)
        self.assertEqual(totals["unrecorded_days"], 1)

    def test_exception_views(self):
        a = dict(missing_in=1, missing_out=0, late_entry=0, early_exit=0,
                 absent=0, status="Present", exception="Missing")
        b = dict(missing_in=0, missing_out=0, late_entry=1, early_exit=0,
                 absent=0, status="Present", exception="Late")
        self.assertEqual(filter_by_view([a, b], "Clock In/Out Exceptions"), [a])
        self.assertEqual(filter_by_view([a, b], "Shift Exceptions"), [b])
