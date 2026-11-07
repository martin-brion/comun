"""Documentos fiscales LATAM en los borradores de la carga por foto: tipo y número vacíos hasta la revisión."""
from odoo import _, api, models

LATAM_FIELDS = {"l10n_latam_document_type_id", "l10n_latam_document_number"}


class AccountMove(models.Model):
    _inherit = "account.move"

    @api.model
    def _receipt_bill_extra_values(self):
        return {**super()._receipt_bill_extra_values(), "l10n_latam_document_type_id": False}

    def _receipt_bill_sensitive_fields(self):
        return super()._receipt_bill_sensitive_fields() | LATAM_FIELDS

    def _receipt_bill_altered_by_defaults(self, expense):
        return super()._receipt_bill_altered_by_defaults(expense) or bool(self.l10n_latam_document_type_id)

    def _receipt_bill_review_missing(self):
        missing = super()._receipt_bill_review_missing()
        if not missing and self.l10n_latam_use_documents and (
                not self.l10n_latam_document_type_id or not self.l10n_latam_document_number):
            return _("Completá tipo y número de documento fiscal antes de revisar.")
        return missing

    def _receipt_bill_review_snapshot(self):
        snapshot = super()._receipt_bill_review_snapshot()
        uses_documents = bool(self.l10n_latam_use_documents)
        # LATAM calcula el número desde la secuencia contable aun en diarios sin documentos: esa secuencia aparece
        # al contabilizar y no es número fiscal. Con documentos, el número queda protegido por la huella.
        snapshot.update({
            "uses_latam_documents": uses_documents,
            "document_type": self.l10n_latam_document_type_id.id,
            "document_number": self.l10n_latam_document_number if uses_documents else False,
        })
        return snapshot

    @api.depends("l10n_latam_available_document_type_ids")
    def _compute_l10n_latam_document_type(self):
        pending = self.filtered(lambda move: move.receipt_bill_expense_id and move.state == "draft"
                                and not move.l10n_latam_document_type_id)
        super(AccountMove, self - pending)._compute_l10n_latam_document_type()
        pending.l10n_latam_document_type_id = False
