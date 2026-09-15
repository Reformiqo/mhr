import frappe
from frappe import _


@frappe.whitelist()
def submit_delivery_note_in_background(name):
    """Queue the Delivery Note submit on a background worker so a large note
    does not run past the request timeout."""
    if not name:
        frappe.throw(_("Delivery Note name is required."))

    doc = frappe.get_doc("Delivery Note", name)
    if doc.docstatus != 0:
        frappe.throw(
            _("Delivery Note {0} is not a draft. Current docstatus={1}.").format(name, doc.docstatus)
        )

    # The worker runs outside the request, so the Desk's own Submit permission
    # check never reaches it.
    doc.check_permission("submit")

    frappe.enqueue(
        method="mhr.delivery_note_background._submit_delivery_note_worker",
        queue="long",
        timeout=3600,
        job_id=f"mhr-submit-delivery-note-{name}",
        deduplicate=True,
        name=name,
        notify_user=frappe.session.user,
    )
    return {"queued": True, "name": name}


def _submit_delivery_note_worker(name, notify_user):
    ok = False
    error = ""
    try:
        doc = frappe.get_doc("Delivery Note", name)
        if doc.docstatus == 0:
            doc.submit()
            frappe.db.commit()
        ok = True
    except Exception as exc:
        frappe.db.rollback()
        error = str(exc)
        frappe.log_error(
            frappe.get_traceback(),
            f"mhr submit_delivery_note_worker failed for {name}",
        )
    frappe.publish_realtime(
        event="mhr_delivery_note_submitted",
        message={"name": name, "ok": ok, "error": error},
        user=notify_user,
    )
