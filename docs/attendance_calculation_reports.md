# Attendance Calculation Reports (HRMS + HRIS)

This feature adds three **read-only** Frappe Script Reports under the Attsync module:

1. **Attendance Calculation Detail**: date-by-date roster and recorded punches, shift times, attendance status, working hours, late/early minutes, missing punches, overtime, holidays, and exceptions.
2. **Attendance Calculation Exceptions**: the same detail with switchable Clock In/Out, Shift, Miscellaneous, and All exception filters.
3. **Attendance Calculated Items**: one row per employee, starting with the 12 exported columns from the supplied legacy September 2026 XLS report.

**Data sources:** submitted Attendance plus Employee, Shift Type (including HRIS exceptional days and Ramadan schedule if available), Holiday, Leave Type and readable Leave Application data. Existing HRIS/HRMS logic remains the source of truth for attendance statuses and minute counts; the reports do not create or modify any attendance record.

**Interpretation / limitations:**
- By default the reports show submitted Attendance rows only. Toggle **Include Days Without Records** to show gaps. Gaps are **No Record**, NOT proven absence, and are never added to the Absent total.
- Records cancelled in ERPNext are excluded. Future dates with no attendance are not generated.
- The monthly Late / Early columns count affected days, while separate fields total minutes.
- Leave types are grouped using the Leave Type `is_lwp` flag and type names (sick / مرض, maternity / وضع, etc.). Leave types that do not match are reported as **Other Leave**; review naming and half-/quarter-day conventions on your site before payroll use.
- HRIS has other workflows and payroll penalty logic; these reports intentionally **do not infer deductions** or recalculate from punches.
- Half/quarter/three-quarter categories are based on linked, readable submitted Leave Applications. Otherwise a reported Half Day counts as 0.5. Adjust before using as a legally binding payroll calculation.
- Scheduled time reflects the currently configured Shift Type; historical shift config changes are not reconstructed.
- Overtime fields are informational and reflect the Attendance field when available, or HRIS before/after-shift minutes. Do not treat them as approved overtime.
- Supports at most 366 days and 1000 employees per report. With gaps enabled: 62 days / 200 employees to prevent expensive employee-date cross products.
- Reports require read access to Employee and Attendance and have HR User, HR Manager, and System Manager report roles.

## Installation

```bash
cd ~/frappe-bench
bench --site YOUR_SITE migrate
bench --site YOUR_SITE clear-cache
```

Open **Reports** and search for any of the three titles above. Frappe's built-in report menu supports Excel download. Test against a controlled payroll period before adopting the new totals.

## Tests

```bash
bench --site YOUR_TEST_SITE run-tests --app attsync --module attsync.tests.test_attendance_reports
```

No Attendance, Salary Slip, or HRIS payroll settings are modified by the reporting feature.
