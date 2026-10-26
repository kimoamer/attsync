// Regression: execute all report JS files with Frappe filter defaults stubbed.
// Run: node --test attsync/tests/test_attendance_report_filters.cjs
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");

const reports = [
  ["Attendance Calculation Detail", "attendance_calculation_detail"],
  ["Attendance Calculation Exceptions", "attendance_calculation_exceptions"],
  ["Attendance Calculated Items", "attendance_calculated_items"],
];

for (const [reportName, slug] of reports) {
  test(`${reportName} initializes required filters`, () => {
    const filename = path.resolve(__dirname, `../attsync/report/${slug}/${slug}.js`);
    const source = fs.readFileSync(filename, "utf8");
    const frappe = {
      query_reports: {},
      defaults: { get_user_default: () => "SEKEM" },
      datetime: { month_start: () => "2026-10-01", get_today: () => "2026-10-26" },
    };
    vm.runInNewContext(source, { frappe }, { filename });
    const config = frappe.query_reports[reportName];
    assert.ok(config && Array.isArray(config.filters), "Report filters must register");
    const getFilter = (fieldname) => config.filters.find((f) => f.fieldname === fieldname);
    for (const name of ["company", "from_date", "to_date"]) {
      const filter = getFilter(name);
      assert.ok(filter, `${name} filter must exist`);
      assert.equal(filter.reqd, 1);
      assert.ok(filter.default, `${name} must have a default`);
    }
    if (slug === "attendance_calculation_exceptions") {
      const view = getFilter("exception_view");
      assert.equal(view.default, "All Exceptions");
      assert.ok(view.options.split("\n").includes(view.default));
    }
  });
}
