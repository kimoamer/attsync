from attsync.attendance_reports import detailed_columns, filter_by_view, get_rows


def execute(filters=None):
    rows, _employees = get_rows(filters)
    view = (filters or {}).get("exception_view") or "All Exceptions"
    return detailed_columns(), filter_by_view(rows, view)
