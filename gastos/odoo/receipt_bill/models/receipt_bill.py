"""Ingreso y revisión contable sin dependencia de rendiciones ni de empleados."""
from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from . import receipt_logic as logic

ORIGIN_FIELDS = logic.ORIGIN_FIELDS
REVIEW_FIELDS = logic.REVIEW_FIELDS
SENSITIVE_FIELDS = {
    "partner_id", "company_id", "journal_id", "currency_id", "invoice_date", "date", "ref",
    "invoice_line_ids", "line_ids", "move_type", "auto_post",
}


class ResCompany(models.Model):
    _inherit = "res.company"

    receipt_bill_enabled = fields.Boolean(string="Comprobantes por foto: ingreso contable", default=False,
                                          groups="base.group_system")
    receipt_bill_user_ids = fields.Many2many(
        "res.users", "receipt_bill_company_user_rel", "company_id", "user_id",
        string="Usuarios habilitados para el ingreso", groups="base.group_system")
    receipt_bill_journal_id = fields.Many2one(
        "account.journal", string="Diario de compras del ingreso", check_company=True,
        domain="[('company_id', '=', id), ('type', '=', 'purchase')]", groups="base.group_system")


class AccountMove(models.Model):
    _inherit = "account.move"

    receipt_bill_contract = fields.Integer(compute="_compute_receipt_bill_contract", readonly=True)
    receipt_bill_lot = fields.Char(copy=False, readonly=True, index=True)
    receipt_bill_photo = fields.Integer(copy=False, readonly=True)
    receipt_bill_payment = fields.Selection(
        [("own_account", "Pagado por la persona"), ("company_account", "Pagado por la compañía")],
        copy=False, readonly=True)
    receipt_bill_payload_hash = fields.Char(copy=False, readonly=True)
    receipt_bill_source_uid = fields.Many2one("res.users", default=lambda self: self.env.uid,
                                             copy=False, readonly=True, ondelete="restrict")
    receipt_bill_original_total = fields.Monetary(currency_field="currency_id", copy=False, readonly=True)
    receipt_bill_original_analytic = fields.Json(copy=False, readonly=True)
    receipt_bill_direct_review_by_id = fields.Many2one("res.users", copy=False, readonly=True)
    receipt_bill_direct_review_at = fields.Datetime(copy=False, readonly=True)
    receipt_bill_direct_review_hash = fields.Char(copy=False, readonly=True)

    _sql_constraints = [
        ("receipt_bill_direct_origin_unique", "UNIQUE(company_id, receipt_bill_lot, receipt_bill_photo)",
         "La foto de este lote ya tiene una factura en esta compañía."),
    ]

    @api.depends("receipt_bill_lot")
    def _compute_receipt_bill_contract(self):
        for move in self:
            move.receipt_bill_contract = 2 if move.receipt_bill_lot else 0

    @api.model
    def _receipt_bill_direct_allowed(self, company):
        # Sólo se eleva la lectura de configuración protegida, nunca las operaciones contables.
        if "receipt_bill_expense_id" in self._fields:
            raise AccessError(_("El creador directo no puede coexistir con el creador fiscal legado instalado."))
        configured = self.env["ir.config_parameter"].sudo().get_param(logic.DATABASE_PARAMETER)
        settings = company.sudo()
        journal = settings.receipt_bill_journal_id
        if (company not in self.env.companies or not logic.database_allowed(self.env.cr.dbname, configured)
                or not settings.receipt_bill_enabled or self.env.user not in settings.receipt_bill_user_ids
                or not self.env.user.has_group("account.group_account_invoice")
                or not self.has_access("create") or not self.has_access("read")):
            raise AccessError(_("Requiere base y compañía habilitadas y un usuario contable nominal habilitado."))
        if not journal or journal.type != "purchase" or journal.company_id != company:
            raise UserError(_("Configurá un diario de compras de la misma compañía."))
        return journal

    @api.model
    def _receipt_bill_direct_extra_values(self):
        """Campos que una localización exige vacíos al ingresar, sin identidad fiscal inferida."""
        return {}

    def _receipt_bill_direct_sensitive_fields(self):
        return set(SENSITIVE_FIELDS)

    def _receipt_bill_direct_lock(self):
        bills = self.filtered("receipt_bill_lot")
        if bills:
            bills.flush_recordset()
            self.env.cr.execute("SELECT id FROM account_move WHERE id IN %s ORDER BY id FOR UPDATE", [tuple(bills.ids)])
            bills.invalidate_recordset()

    @api.model_create_multi
    def create(self, vals_list):
        if any("default_" + name in self.env.context for name in ORIGIN_FIELDS | REVIEW_FIELDS):
            raise AccessError(_("No se admiten defaults de origen ni revisión en el contexto."))
        clean, evidence = [], []
        for original in vals_list:
            values = dict(original)
            if REVIEW_FIELDS & values.keys():
                raise AccessError(_("La revisión fiscal requiere una acción explícita y el contrato es de sólo lectura."))
            if ORIGIN_FIELDS & values.keys():
                try:
                    snapshot = logic.validate_create(values, self.env.uid)
                except logic.ReceiptContractError as exc:
                    raise UserError(str(exc)) from exc
                company = self.env["res.company"].browse(values["company_id"]).exists()
                journal = self._receipt_bill_direct_allowed(company)
                if values["journal_id"] != journal.id:
                    raise UserError(_("El diario no coincide con el configurado para el ingreso."))
                extras = self._receipt_bill_direct_extra_values()
                if any(values.get(name) not in (None, False, "") for name in extras):
                    raise UserError(_("Los datos fiscales de localización se completan durante la revisión."))
                values.update(extras)
                values.update(receipt_bill_source_uid=self.env.uid,
                              receipt_bill_original_total=snapshot["total"],
                              receipt_bill_original_analytic=snapshot["analytic"])
                evidence.append(snapshot)
            else:
                evidence.append(None)
            clean.append(values)
        bills = super().create(clean)
        for bill, snapshot, values in zip(bills, evidence, clean):
            if snapshot is None:
                continue
            line = bill.invoice_line_ids
            # Revisar también el resultado heredado: un default de producto/proveedor/impuestos no se acepta.
            if (bill.state != "draft" or bill.move_type != "in_invoice" or bill.partner_id or bill.ref
                    or bill.auto_post != "no" or bill.company_id.id != values["company_id"]
                    or bill.currency_id.id != values["currency_id"] or bill.journal_id.id != values["journal_id"]
                    or len(line) != 1 or line.product_id or line.tax_ids or line.quantity != 1 or line.discount
                    or line.account_id.id != values["invoice_line_ids"][0][2]["account_id"]
                    or line.analytic_distribution != snapshot["analytic"]
                    or bill.currency_id.compare_amounts(bill.amount_total, snapshot["total"])
                    or any(bill[name] for name in bill._receipt_bill_direct_extra_values())):
                raise UserError(_("Un default alteró el ingreso contable pendiente de clasificación fiscal."))
        return bills

    def write(self, vals):
        if (ORIGIN_FIELDS | REVIEW_FIELDS) & vals.keys():
            raise AccessError(_("El origen es inmutable y la revisión sólo se registra por su acción explícita."))
        bills = self.filtered("receipt_bill_lot")
        if bills and {"company_id", "currency_id", "move_type"} & vals.keys():
            raise AccessError(_("No se puede cambiar compañía, moneda o tipo de un comprobante de origen."))
        bills._receipt_bill_direct_lock()
        res = super().write(vals)
        if bills and bills[:1]._receipt_bill_direct_sensitive_fields() & vals.keys():
            bills._receipt_bill_direct_invalidate_review()
        return res

    def unlink(self):
        if self.filtered("receipt_bill_lot"):
            raise AccessError(_("La evidencia de origen no se elimina; un borrador se conserva para su resolución."))
        return super().unlink()

    def _receipt_bill_direct_invalidate_review(self):
        # El contexto no autoriza contabilizar: la huella se coteja siempre antes de _post.
        if self.env.context.get("receipt_bill_direct_posting"):
            return
        bills = self.filtered(lambda bill: bill.receipt_bill_lot and bill.state == "draft"
                              and bill.receipt_bill_direct_review_by_id)
        if bills:
            super(AccountMove, bills).write({"receipt_bill_direct_review_by_id": False,
                                            "receipt_bill_direct_review_at": False,
                                            "receipt_bill_direct_review_hash": False})

    def _receipt_bill_direct_attachments(self):
        self.ensure_one()
        return self.env["ir.attachment"].search([("res_model", "=", "account.move"), ("res_id", "=", self.id)])

    def _receipt_bill_direct_review_missing(self):
        self.ensure_one()
        if not self.partner_id or not self.ref or not self.invoice_date:
            return _("Completá proveedor, referencia fiscal y fecha antes de revisar.")
        if not self.invoice_line_ids or any(not line.account_id for line in self.invoice_line_ids):
            return _("Revisá las cuentas e impuestos del comprobante.")
        receipts = self._receipt_bill_direct_attachments()
        if not receipts or any(item.type != "binary" or not item.checksum or item.public or item.access_token for item in receipts):
            return _("Falta evidencia binaria privada con checksum verificado.")
        return ""

    def _receipt_bill_direct_review_snapshot(self):
        self.ensure_one()
        return {
            "origin": {name: self[name].id if name == "receipt_bill_source_uid" else self[name]
                       for name in sorted(ORIGIN_FIELDS)},
            "company_id": self.company_id.id, "journal_id": self.journal_id.id,
            "partner_id": self.partner_id.id, "currency_id": self.currency_id.id,
            "invoice_date": self.invoice_date, "date": self.date, "ref": self.ref,
            "amount_total": self.amount_total, "amount_tax": self.amount_tax,
            "amount_untaxed": self.amount_untaxed,
            "receipts": [{"id": item.id, "name": item.name, "checksum": item.checksum,
                          "public": item.public, "has_access_token": bool(item.access_token)}
                         for item in self._receipt_bill_direct_attachments().sorted("id")],
            "lines": [{"id": line.id, "name": line.name, "account_id": line.account_id.id,
                       "quantity": line.quantity, "price_unit": line.price_unit, "discount": line.discount,
                       "product_id": line.product_id.id, "tax_ids": sorted(line.tax_ids.ids),
                       "analytic_distribution": line.analytic_distribution}
                      for line in self.invoice_line_ids.sorted("id")],
            "journal_items": [{"id": line.id, "display_type": line.display_type, "account_id": line.account_id.id,
                               "currency_id": line.currency_id.id, "partner_id": line.partner_id.id,
                               "debit": line.debit, "credit": line.credit, "amount_currency": line.amount_currency,
                               "tax_ids": sorted(line.tax_ids.ids), "tax_line_id": line.tax_line_id.id,
                               "tax_repartition_line_id": line.tax_repartition_line_id.id,
                               "analytic_distribution": line.analytic_distribution}
                              for line in self.line_ids.sorted("id")],
        }

    def _receipt_bill_direct_review_fingerprint(self):
        return logic.review_fingerprint(self._receipt_bill_direct_review_snapshot())

    def action_receipt_bill_direct_review(self):
        if not self.env.user.has_group("account.group_account_invoice"):
            raise AccessError(_("La revisión corresponde al área contable."))
        for bill in self:
            bill.check_access("write")
            bill._receipt_bill_direct_lock()
            if not bill.receipt_bill_lot or bill.state != "draft":
                raise UserError(_("Sólo se revisa un comprobante de origen en borrador."))
            missing = bill._receipt_bill_direct_review_missing()
            if missing:
                raise UserError(missing)
            super(AccountMove, bill).write({
                "receipt_bill_direct_review_by_id": self.env.uid,
                "receipt_bill_direct_review_at": fields.Datetime.now(),
                "receipt_bill_direct_review_hash": bill._receipt_bill_direct_review_fingerprint(),
            })
        return True

    def _post(self, soft=True):
        bills = self.filtered("receipt_bill_lot")
        bills._receipt_bill_direct_lock()
        for bill in bills:
            if (not bill.receipt_bill_direct_review_by_id or bill._receipt_bill_direct_review_missing()
                    or bill.receipt_bill_direct_review_hash != bill._receipt_bill_direct_review_fingerprint()):
                raise UserError(_("El comprobante está pendiente de revisión fiscal vigente."))
        return super(AccountMove, self.with_context(receipt_bill_direct_posting=True))._post(soft=soft)

    @api.constrains("receipt_bill_lot", "receipt_bill_photo", "receipt_bill_payment", "receipt_bill_payload_hash",
                    "company_id", "move_type", "state", "receipt_bill_direct_review_by_id", "receipt_bill_direct_review_hash")
    def _check_receipt_bill_direct_origin(self):
        for bill in self.filtered("receipt_bill_lot"):
            try:
                logic.origin({name: bill[name] for name in ("receipt_bill_lot", "receipt_bill_photo",
                                                          "receipt_bill_payment", "receipt_bill_payload_hash")})
            except logic.ReceiptContractError as exc:
                raise ValidationError(str(exc)) from exc
            if bill.move_type != "in_invoice":
                raise ValidationError(_("El comprobante de origen debe seguir siendo una factura de proveedor."))
            if bill.state == "posted" and (not bill.receipt_bill_direct_review_by_id
                    or bill.receipt_bill_direct_review_hash != bill._receipt_bill_direct_review_fingerprint()):
                raise ValidationError(_("El comprobante requiere revisión vigente para contabilizar."))


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    @api.model_create_multi
    def create(self, vals_list):
        bills = self.env["account.move"].browse([values["move_id"] for values in vals_list if values.get("move_id")])
        bills._receipt_bill_direct_lock()
        lines = super().create(vals_list)
        lines.move_id._receipt_bill_direct_invalidate_review()
        return lines

    def write(self, vals):
        bills = self.move_id
        target = self.env["account.move"].browse(vals.get("move_id", []))
        (bills | target)._receipt_bill_direct_lock()
        res = super().write(vals)
        (bills | self.move_id)._receipt_bill_direct_invalidate_review()
        return res

    def unlink(self):
        bills = self.move_id
        bills._receipt_bill_direct_lock()
        res = super().unlink()
        bills._receipt_bill_direct_invalidate_review()
        return res


class IrAttachment(models.Model):
    _inherit = "ir.attachment"

    @api.model
    def _receipt_bill_direct_target(self, values):
        if values.get("res_model") == "account.move" and values.get("res_id"):
            return self.env["account.move"].browse(values["res_id"]).exists().filtered("receipt_bill_lot")
        return self.env["account.move"]

    def _receipt_bill_direct_moves(self):
        attachments = self.filtered(lambda item: item.res_model == "account.move" and item.res_id)
        return self.env["account.move"].browse(attachments.mapped("res_id")).exists().filtered("receipt_bill_lot")

    @api.model_create_multi
    def create(self, vals_list):
        for values in vals_list:
            target = self._receipt_bill_direct_target(values)
            if target:
                target.check_access("write")
                target._receipt_bill_direct_lock()
                if target.state != "draft" or values.get("public") or values.get("access_token") or values.get("type", "binary") != "binary":
                    raise AccessError(_("La evidencia se agrega binaria y privada a un borrador accesible."))
        records = super().create(vals_list)
        for item in records:
            bills = item._receipt_bill_direct_moves()
            if bills:
                bills.check_access("write")
                bills._receipt_bill_direct_lock()
                if (bills.state != "draft" or item.type != "binary" or not item.checksum
                        or item.public or item.access_token):
                    raise UserError(_("La evidencia requiere datos binarios privados y checksum."))
                bills._receipt_bill_direct_invalidate_review()
        return records

    def write(self, vals):
        bills = self._receipt_bill_direct_moves()
        targets = self._receipt_bill_direct_target(vals)
        if bills and {"datas", "raw", "store_fname", "checksum", "res_model", "res_id", "public", "access_token", "type"} & vals.keys():
            raise AccessError(_("La evidencia original no puede alterarse ni trasladarse; agregá evidencia adicional."))
        if targets and not bills:
            raise AccessError(_("La evidencia se adjunta mediante una creación nueva, sin trasladar adjuntos."))
        bills._receipt_bill_direct_lock()
        res = super().write(vals)
        bills._receipt_bill_direct_invalidate_review()
        return res

    def unlink(self):
        if self._receipt_bill_direct_moves():
            raise AccessError(_("La evidencia original no puede eliminarse."))
        return super().unlink()
