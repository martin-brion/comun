"""Borrador de factura de proveedor por cada gasto de la carga por foto: crear borradores, nunca enviar,
aprobar ni contabilizar sin revisión."""
from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from . import receipt_logic as logic

SILENT = {
    "tracking_disable": True,
    "mail_create_nosubscribe": True,
    "mail_auto_subscribe_no_notify": True,
    "mail_notify_force_send": False,
}
ORIGIN_FIELDS = {"receipt_bill_expense_id", "receipt_bill_marker", "receipt_bill_payload_hash"}
REVIEW_FIELDS = {"receipt_bill_reviewed_by_id", "receipt_bill_reviewed_at", "receipt_bill_review_hash"}
SENSITIVE_FIELDS = {"partner_id", "company_id", "journal_id", "currency_id", "invoice_date", "date",
                    "ref", "invoice_line_ids", "line_ids"}


class ResCompany(models.Model):
    _inherit = "res.company"

    receipt_bill_enabled = fields.Boolean(
        string="Borradores de proveedor desde gastos por foto", default=False,
        groups="base.group_system")
    receipt_bill_user_ids = fields.Many2many(
        "res.users", "receipt_bill_company_user_rel", "company_id", "user_id",
        string="Usuarios habilitados", groups="base.group_system")
    receipt_bill_journal_id = fields.Many2one(
        "account.journal", string="Diario de compras de los borradores", check_company=True,
        domain="[('company_id', '=', id), ('type', '=', 'purchase')]", groups="base.group_system")


class HrExpenseSheet(models.Model):
    _inherit = "hr.expense.sheet"

    @api.model_create_multi
    def create(self, vals_list):
        sheets = super().create(vals_list)
        for sheet in sheets:
            if logic.sheet_lot(sheet.name) and sheet._receipt_bill_allowed():
                sheet.action_receipt_bill_prepare()
        return sheets

    def _receipt_bill_allowed(self):
        self.ensure_one()
        # Sólo se eleva la lectura de la configuración protegida, nunca las operaciones contables.
        opted_in = self.env["ir.config_parameter"].sudo().get_param(logic.DATABASE_PARAMETER)
        if not logic.database_allowed(self.env.cr.dbname, opted_in):
            return False
        company = self.company_id.sudo()
        return bool(
            company.receipt_bill_enabled
            and self.env.user in company.receipt_bill_user_ids
            and self.env.user.has_group("account.group_account_invoice")
            and self.env["account.move"].has_access("create")
            and self.env["account.move"].has_access("read")
        )

    def action_receipt_bill_prepare(self):
        """También retoma una hoja marcada que se creó antes de habilitar la compañía."""
        for sheet in self:
            sheet.check_access("write")
            if not sheet._receipt_bill_allowed():
                raise AccessError(_("Requiere la base habilitada, la compañía habilitada y un usuario contable habilitado."))
            if sheet.state != "draft" or not sheet.expense_line_ids or not logic.sheet_lot(sheet.name):
                raise UserError(_("Sólo se prepara una hoja de la carga por foto, en borrador y con gastos."))
            journal = sheet.company_id.sudo().receipt_bill_journal_id
            if not journal or journal.company_id != sheet.company_id or journal.type != "purchase":
                raise UserError(_("Configurá el diario de compras de la compañía para los borradores."))
            expenses = sheet.expense_line_ids
            expenses.check_access("read")
            expenses.check_access("write")
            # Serializar reintentos sobre los mismos gastos; el índice único protege también marcadores repetidos.
            expenses.flush_recordset()
            self.env.cr.execute("SELECT id FROM hr_expense WHERE id IN %s ORDER BY id FOR UPDATE", [tuple(expenses.ids)])
            expenses.invalidate_recordset()
            for expense in expenses:
                sheet._receipt_bill_one(expense, journal)
        return True

    def _receipt_bill_one(self, expense, journal):
        self.ensure_one()
        if (expense.company_id != self.company_id or expense.employee_id != self.employee_id
                or expense.sheet_id != self or expense.state != "reported"):
            raise UserError(_("Cambió la compañía, el empleado o la hoja del gasto."))
        data = {
            "id": expense.id, "name": expense.name, "company_id": expense.company_id.id,
            "employee_id": expense.employee_id.id, "currency_id": expense.currency_id.id,
            "date": expense.date, "account_id": expense.account_id.id,
            "quantity": expense.quantity, "tax_ids": expense.tax_ids.ids,
            "payment_mode": expense.payment_mode, "total_amount_currency": expense.total_amount_currency,
            "analytic_distribution": expense.analytic_distribution,
        }
        moves = self.env["account.move"].with_company(self.company_id).with_context(**SILENT)
        try:
            values = logic.invoice_values(data, self.name, self.expense_line_ids.mapped("payment_mode"), journal.id)
        except logic.ReceiptContractError as exc:
            raise UserError(str(exc)) from exc
        values.update(moves._receipt_bill_extra_values())
        sources = self.env["ir.attachment"].search([("res_model", "=", "hr.expense"), ("res_id", "=", expense.id)])
        if not sources or any(source.type != "binary" or not source.checksum for source in sources):
            raise UserError(_("El gasto necesita su comprobante binario con checksum antes de crear la factura."))
        existing = moves.search([
            ("company_id", "=", self.company_id.id),
            ("receipt_bill_marker", "=", values["receipt_bill_marker"]),
        ])
        if expense.invoice_id and existing != expense.invoice_id:
            raise UserError(_("El gasto ya está vinculado a otra factura. Revisalo sin crear otra."))
        if existing:
            if (len(existing) != 1 or existing.receipt_bill_expense_id != expense
                    or existing.state != "draft" or existing.move_type != "in_invoice"
                    or existing.receipt_bill_payload_hash != values["receipt_bill_payload_hash"]
                    or existing.currency_id != expense.currency_id
                    or existing.invoice_date != expense.date
                    or existing.currency_id.compare_amounts(existing.amount_total, expense.total_amount_currency)):
                raise UserError(_("El borrador existente no coincide con el gasto; el reintento se detuvo."))
            bill = existing
        else:
            bill = moves._receipt_bill_create(values)
            # Ningún default heredado puede inventar identidad, documento o impuestos, ni cambiar el importe.
            if bill._receipt_bill_altered_by_defaults(expense):
                raise UserError(_("Un default de Odoo alteró el borrador pendiente de clasificación fiscal."))
        if not expense.invoice_id:
            expense.with_context(**SILENT).write({"invoice_id": bill.id})
        self._receipt_bill_attachments(expense, bill)

    def _receipt_bill_attachments(self, expense, bill):
        attachments = self.env["ir.attachment"].with_context(**SILENT)
        sources = attachments.search([("res_model", "=", "hr.expense"), ("res_id", "=", expense.id)])
        for source in sources:
            existing = attachments.search([
                ("res_model", "=", "account.move"), ("res_id", "=", bill.id),
                ("receipt_bill_source_attachment_id", "=", source.id),
            ])
            if existing:
                if len(existing) != 1 or existing.checksum != source.checksum:
                    raise UserError(_("La copia del comprobante difiere del adjunto original."))
                continue
            copied = source._receipt_bill_copy_for(bill)
            if copied.checksum != source.checksum:
                raise UserError(_("El checksum de la copia del comprobante no coincide."))


class IrAttachment(models.Model):
    _inherit = "ir.attachment"

    receipt_bill_source_attachment_id = fields.Many2one("ir.attachment", copy=False, readonly=True)
    _sql_constraints = [(
        "receipt_bill_attachment_unique",
        "UNIQUE(res_model, res_id, receipt_bill_source_attachment_id)",
        "El comprobante original ya está adjunto a este borrador.",
    )]

    @api.model_create_multi
    def create(self, vals_list):
        if (any("receipt_bill_source_attachment_id" in values for values in vals_list)
                or "default_receipt_bill_source_attachment_id" in self.env.context):
            raise AccessError(_("El origen de la copia del comprobante sólo lo establece este módulo."))
        return super().create(vals_list)

    def _receipt_bill_copy_for(self, bill):
        self.ensure_one()
        self.check_access("read")
        bill.check_access("write")
        if ("default_receipt_bill_source_attachment_id" in self.env.context
                or self.res_model != "hr.expense" or self.res_id != bill.receipt_bill_expense_id.id
                or bill.state != "draft" or not bill.receipt_bill_expense_id.sheet_id._receipt_bill_allowed()):
            raise AccessError(_("La copia requiere el comprobante del gasto y su borrador habilitado."))
        values = self.copy_data({"res_model": "account.move", "res_id": bill.id,
                                 "receipt_bill_source_attachment_id": self.id,
                                 "public": False, "access_token": False})[0]
        # copy() del core lleva raw explícito; se reproduce eso salteando sólo la guarda pública de origen.
        values["raw"] = self.raw
        return super(IrAttachment, self.env["ir.attachment"]).create([values])

    def _receipt_bill_moves(self):
        copies = self.filtered(lambda attachment: attachment.res_model == "account.move"
                               and attachment.receipt_bill_source_attachment_id)
        return self.env["account.move"].browse(copies.mapped("res_id"))

    def write(self, vals):
        if "receipt_bill_source_attachment_id" in vals:
            raise AccessError(_("El origen de la copia del comprobante no se puede alterar."))
        bills = self._receipt_bill_moves()
        result = super().write(vals)
        if {"datas", "raw", "store_fname", "checksum", "name", "res_model", "res_id", "public",
                "access_token"} & vals.keys():
            (bills | self._receipt_bill_moves())._receipt_bill_invalidate_review()
        return result

    def unlink(self):
        bills = self._receipt_bill_moves()
        result = super().unlink()
        bills._receipt_bill_invalidate_review()
        return result


class AccountMove(models.Model):
    _inherit = "account.move"

    receipt_bill_expense_id = fields.Many2one("hr.expense", copy=False, readonly=True, ondelete="restrict")
    receipt_bill_marker = fields.Char(copy=False, readonly=True)
    receipt_bill_payload_hash = fields.Char(copy=False, readonly=True)
    receipt_bill_reviewed_by_id = fields.Many2one("res.users", copy=False, readonly=True)
    receipt_bill_reviewed_at = fields.Datetime(copy=False, readonly=True)
    receipt_bill_review_hash = fields.Char(copy=False, readonly=True)
    _sql_constraints = [
        ("receipt_bill_expense_unique", "UNIQUE(receipt_bill_expense_id)", "El gasto ya tiene un borrador de proveedor."),
        ("receipt_bill_marker_unique", "UNIQUE(company_id, receipt_bill_marker)", "La foto del lote ya tiene un borrador de proveedor."),
    ]

    # Ganchos para la localización de cada país (ver el módulo *_latam): este módulo no conoce ninguna.
    @api.model
    def _receipt_bill_extra_values(self):
        return {}

    def _receipt_bill_sensitive_fields(self):
        return set(SENSITIVE_FIELDS)

    def _receipt_bill_altered_by_defaults(self, expense):
        self.ensure_one()
        return bool(self.partner_id or self.ref or self.invoice_line_ids.tax_ids or self.state != "draft"
                    or self.currency_id != expense.currency_id
                    or self.currency_id.compare_amounts(self.amount_total, expense.total_amount_currency))

    def _receipt_bill_review_missing(self):
        """Texto de lo que falta revisar, o vacío si el borrador está completo para la revisión."""
        self.ensure_one()
        if not self.partner_id or not self.ref:
            return _("Completá proveedor y referencia fiscal en el borrador antes de revisar.")
        if not self.invoice_line_ids or any(not line.account_id for line in self.invoice_line_ids):
            return _("Revisá las cuentas e impuestos del comprobante.")
        return ""

    def _receipt_bill_review_snapshot(self):
        self.ensure_one()
        receipts = self.env["ir.attachment"].search([("res_model", "=", "account.move"), ("res_id", "=", self.id)])
        return {
            "company_id": self.company_id.id, "journal_id": self.journal_id.id,
            "partner_id": self.partner_id.id, "currency_id": self.currency_id.id,
            "invoice_date": self.invoice_date, "date": self.date, "ref": self.ref,
            "receipts": [{"id": receipt.id, "checksum": receipt.checksum, "name": receipt.name,
                          "source_id": receipt.receipt_bill_source_attachment_id.id,
                          "public": receipt.public, "has_access_token": bool(receipt.access_token)}
                         for receipt in receipts.sorted("id")],
            "amount_total": self.amount_total, "amount_untaxed": self.amount_untaxed, "amount_tax": self.amount_tax,
            "lines": [{"id": line.id, "name": line.name, "account_id": line.account_id.id,
                       "tax_ids": sorted(line.tax_ids.ids), "quantity": line.quantity,
                       "price_unit": line.price_unit, "discount": line.discount,
                       "analytic_distribution": line.analytic_distribution}
                      for line in self.invoice_line_ids.sorted("id")],
            "journal_items": [{"id": line.id, "display_type": line.display_type, "account_id": line.account_id.id,
                               "currency_id": line.currency_id.id, "partner_id": line.partner_id.id,
                               "debit": line.debit, "credit": line.credit, "amount_currency": line.amount_currency,
                               "tax_ids": sorted(line.tax_ids.ids), "tax_line_id": line.tax_line_id.id,
                               "tax_repartition_line_id": line.tax_repartition_line_id.id}
                              for line in self.line_ids.sorted("id")],
        }

    def _receipt_bill_review_fingerprint(self):
        return logic.review_fingerprint(self._receipt_bill_review_snapshot())

    @api.model_create_multi
    def create(self, vals_list):
        if any(ORIGIN_FIELDS & vals.keys() for vals in vals_list) or any(
                "default_" + field in self.env.context for field in ORIGIN_FIELDS | REVIEW_FIELDS):
            raise AccessError(_("El origen del borrador sólo lo establece este módulo, con la compañía habilitada."))
        if any(vals.get("receipt_bill_reviewed_by_id") or vals.get("receipt_bill_reviewed_at")
               or vals.get("receipt_bill_review_hash") for vals in vals_list):
            raise AccessError(_("La revisión del comprobante requiere la acción explícita del área contable."))
        return super().create(vals_list)

    @api.model
    def _receipt_bill_create(self, values):
        """Sin RPC: conserva el create heredado y las ACL, sin bandera de contexto que autorice."""
        if any("default_" + field in self.env.context for field in ORIGIN_FIELDS | REVIEW_FIELDS):
            raise AccessError(_("El llamador no puede introducir defaults de origen o de revisión fiscal."))
        expense = self.env["hr.expense"].browse(values.get("receipt_bill_expense_id"))
        expense.check_access("read")
        expense.check_access("write")
        sheet = expense.sheet_id
        if (not sheet or not sheet._receipt_bill_allowed() or sheet.state != "draft"
                or expense.state != "reported" or expense.company_id != self.env.company
                or expense.company_id != sheet.company_id or expense.employee_id != sheet.employee_id
                or not logic.expense_marker(expense.name)):
            raise AccessError(_("El origen requiere una hoja de la carga por foto y un llamador habilitado."))
        if REVIEW_FIELDS & values.keys():
            raise AccessError(_("No se puede crear metadata de revisión fiscal."))
        clean_context = {key: value for key, value in self.env.context.items()
                         if key not in {"default_" + field for field in ORIGIN_FIELDS | REVIEW_FIELDS}}
        return super(AccountMove, self.with_context(clean_context)).create([values])

    def write(self, vals):
        if REVIEW_FIELDS & vals.keys():
            raise AccessError(_("La revisión del comprobante requiere la acción explícita del área contable."))
        if ORIGIN_FIELDS & vals.keys():
            raise AccessError(_("El origen del borrador no se puede alterar."))
        bills = self.filtered("receipt_bill_expense_id")
        res = super().write(vals)
        if bills and bills[:1]._receipt_bill_sensitive_fields() & vals.keys():
            bills._receipt_bill_invalidate_review()
        return res

    def _receipt_bill_invalidate_review(self):
        # El contexto sólo evita invalidar los cambios internos de _post; la huella se verifica antes de _post
        # aunque un llamador haya inyectado ese contexto en otra escritura.
        if self.env.context.get("receipt_bill_posting"):
            return
        reviewed_drafts = self.filtered(lambda bill: bill.receipt_bill_expense_id and bill.state == "draft"
                                       and bill.receipt_bill_reviewed_by_id)
        if reviewed_drafts:
            super(AccountMove, reviewed_drafts).write({"receipt_bill_reviewed_by_id": False,
                                                     "receipt_bill_reviewed_at": False,
                                                     "receipt_bill_review_hash": False})

    def action_receipt_bill_review(self):
        """Sólo registra la revisión contable humana; no contabiliza ni paga."""
        if not self.env.user.has_group("account.group_account_invoice"):
            raise AccessError(_("La revisión fiscal corresponde al área contable."))
        for bill in self:
            bill.check_access("write")
            if not bill.receipt_bill_expense_id or bill.state != "draft":
                raise UserError(_("Sólo se revisa un borrador que viene de un gasto."))
            missing = bill._receipt_bill_review_missing()
            if missing:
                raise UserError(missing)
            super(AccountMove, bill).write({
                "receipt_bill_reviewed_by_id": self.env.user.id,
                "receipt_bill_reviewed_at": fields.Datetime.now(),
                "receipt_bill_review_hash": bill._receipt_bill_review_fingerprint(),
            })
        return True

    def _post(self, soft=True):
        for move in self.filtered("receipt_bill_expense_id"):
            if (not move.receipt_bill_reviewed_by_id
                    or move.receipt_bill_review_hash != move._receipt_bill_review_fingerprint()):
                raise UserError(_("El borrador del gasto está pendiente de revisión fiscal."))
        return super(AccountMove, self.with_context(receipt_bill_posting=True))._post(soft=soft)

    @api.constrains("receipt_bill_expense_id", "company_id", "move_type", "state",
                    "receipt_bill_reviewed_by_id", "receipt_bill_review_hash")
    def _check_receipt_origin(self):
        for bill in self.filtered("receipt_bill_expense_id"):
            if bill.move_type != "in_invoice" or bill.company_id != bill.receipt_bill_expense_id.company_id:
                raise ValidationError(_("El borrador debe ser de proveedor y de la misma compañía que el gasto."))
            if bill.state == "posted" and (not bill.receipt_bill_reviewed_by_id
                    or bill.receipt_bill_review_hash != bill._receipt_bill_review_fingerprint()):
                raise ValidationError(_("El comprobante requiere revisión fiscal antes de contabilizar."))


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        lines.move_id._receipt_bill_invalidate_review()
        return lines

    def write(self, vals):
        bills = self.move_id
        res = super().write(vals)
        if {"account_id", "tax_ids", "price_unit", "quantity", "discount", "analytic_distribution", "debit", "credit",
                "amount_currency", "currency_id", "partner_id", "move_id"} & vals.keys():
            (bills | self.move_id)._receipt_bill_invalidate_review()
        return res

    def unlink(self):
        bills = self.move_id
        res = super().unlink()
        bills._receipt_bill_invalidate_review()
        return res
