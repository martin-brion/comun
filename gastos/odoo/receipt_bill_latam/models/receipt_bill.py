from odoo import _, api, models


class AccountMove(models.Model):
    _inherit = "account.move"

    @api.model
    def _receipt_bill_direct_extra_values(self):
        return {**super()._receipt_bill_direct_extra_values(),
                "l10n_latam_document_type_id": False, "l10n_latam_document_number": False}

    def _receipt_bill_direct_sensitive_fields(self):
        return super()._receipt_bill_direct_sensitive_fields() | {
            "l10n_latam_document_type_id", "l10n_latam_document_number"}

    def _receipt_bill_direct_review_missing(self):
        missing = super()._receipt_bill_direct_review_missing()
        if missing:
            return missing
        if self.l10n_latam_use_documents and (not self.l10n_latam_document_type_id or not self.l10n_latam_document_number):
            return _("Completá tipo y número de documento LATAM antes de confirmar la revisión.")
        return ""

    def _receipt_bill_direct_review_snapshot(self):
        uses_documents = bool(self.l10n_latam_use_documents)
        return {**super()._receipt_bill_direct_review_snapshot(),
                "latam_use_documents": uses_documents,
                "latam_document_type_id": self.l10n_latam_document_type_id.id,
                # Sin documentos, LATAM calcula el número desde la secuencia al contabilizar;
                # esa secuencia no es documento fiscal revisado.
                "latam_document_number": self.l10n_latam_document_number if uses_documents else False}

    @api.depends("l10n_latam_available_document_type_ids")
    def _compute_l10n_latam_document_type(self):
        pending = self.filtered(lambda move: move.receipt_bill_lot and move.state == "draft"
                                and not move.l10n_latam_document_type_id)
        super(AccountMove, self - pending)._compute_l10n_latam_document_type()
        pending.l10n_latam_document_type_id = False
