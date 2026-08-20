frappe.ui.form.on("Attendance Sync Request", {
	refresh(frm) {
		const isDraft = (frm.doc.status || "Draft") === "Draft";
		const selected = (frm.doc.employees || []).some(
			(row) => row.include_in_sync && row.attendance_device_id
		);

		frm.set_df_property("company", "read_only", !isDraft);
		frm.set_df_property("from_date", "read_only", !isDraft);
		frm.set_df_property("to_date", "read_only", !isDraft);
		frm.set_df_property("employees", "read_only", !isDraft);

		if (!frm.is_new() && isDraft) {
			frm.add_custom_button(__("Fetch Employees"), () => {
				frm.call("fetch_employees").then((response) => {
					frm.reload_doc();
					frappe.show_alert({
						message: __("Fetched {0} active employees with device IDs", [
							response.message?.count || 0,
						]),
						indicator: "green",
					});
				});
			});

			if (selected) {
				frm.add_custom_button(__("Ready for Sync"), () => {
					frm.call("mark_ready").then(() => frm.reload_doc());
				});
			}
		}

		if (!frm.is_new() && frm.doc.status === "In Progress" && !(frm.doc.batches || []).length) {
			frm.add_custom_button(__("Reset Claim"), () => {
				frm.call("reset_claim").then(() => frm.reload_doc());
			});
		}

		if (!frm.is_new() && !isDraft) {
			frm.add_custom_button(__("Create Resync Request"), () => {
				frm.call("create_resync_request").then((response) => {
					if (response.message?.name) {
						frappe.set_route("Form", "Attendance Sync Request", response.message.name);
					}
				});
			});
		}
	},
});
