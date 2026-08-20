app_name = "attsync"
app_title = "Attsync"
app_publisher = "Hakeem"
app_description = "A custom app for api of attendance"
app_email = "abdoamer19@yahoo.com"
app_license = "mit"

fixtures = [
    {"dt": "Custom Field", "filters": [["module", "in", ["Attsync"]]]},
]

# Attendance Sync Request is operational log data, not permanent audit history.
default_log_clearing_doctypes = {
    "Attendance Sync Request": 30,
}
