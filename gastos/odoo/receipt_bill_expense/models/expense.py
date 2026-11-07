"""Optional HR report over an existing canonical bill, using nominal ACLs."""
from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from . import expense_logic as logic

IMMUTABLE = {
    "receipt_bill_source_id", "receipt_bill_contract", "invoice_id", "company_id",
    "employee_id", "product_id", "date", "currency_id", "payment_mode", "name",
    "analytic_distribution", "quantity", "tax_ids", "price_unit",
}


class HrExpense(models.Model):
    _inherit = "hr.expense"

    receipt_bill_source_id = fields.Many2one(
        "account.move", string="Comprobante canónico", readonly=True, copy=False,
        ondelete="restrict", check_company=True)
    receipt_bill_contract = fields.Integer(compute="_compute_receipt_bill_contract")
    _sql_constraints = [(
        "receipt_bill_report_unique", "UNIQUE(receipt_bill_source_id)",
        "El comprobante ya tiene un gasto de rendición.",
    )]

    @api.depends("receipt_bill_source_id")
    def _compute_receipt_bill_contract(self):
        for expense in self:
            expense.receipt_bill_contract = 2 if expense.receipt_bill_source_id else 0

    def _receipt_bill_validate(self):
        for expense in self:
            bill = expense.receipt_bill_source_id
            if not bill:
                if expense.invoice_id and expense.invoice_id.receipt_bill_contract == 2:
                    raise ValidationError(_("La factura canónica requiere su vínculo de origen de rendición."))
                continue
            bill.check_access("read")
            lines = bill.invoice_line_ids.filtered(lambda line: line.display_type == "product")
            try:
                logic.validate({
                    "source_id": bill.id, "invoice_id": expense.invoice_id.id,
                    "company_id": expense.company_id.id, "currency_id": expense.currency_id.id,
                    "date": expense.date, "payment": expense.payment_mode,
                    "total": expense.total_amount_currency, "analytic": expense.analytic_distribution,
                    "name": expense.name,
                }, {
                    "id": bill.id, "contract": bill.receipt_bill_contract, "state": bill.state,
                    "move_type": bill.move_type, "company_id": bill.company_id.id,
                    "currency_id": bill.currency_id.id, "date": bill.invoice_date,
                    "payment": bill.receipt_bill_payment, "total": bill.amount_total,
                    "original_total": bill.receipt_bill_original_total,
                    "original_analytic": bill.receipt_bill_original_analytic,
                    "lines": [line.analytic_distribution for line in lines],
                    "lot": bill.receipt_bill_lot, "photo": bill.receipt_bill_photo,
                })
            except logic.ExpenseContractError as exc:
                raise ValidationError(str(exc)) from exc
            if (expense.employee_id.company_id != expense.company_id
                    or not expense.product_id.can_be_expensed
                    or (expense.product_id.company_id and expense.product_id.company_id != expense.company_id)):
                raise ValidationError(_("Empleado y categoría deben pertenecer a la compañía o ser una categoría compartida."))
        return True

    @api.model_create_multi
    def create(self, vals_list):
        # No context flag or elevated accounting operation can authorize this path.
        for field in ("receipt_bill_source_id", "receipt_bill_contract", "invoice_id"):
            if "default_" + field in self.env.context:
                raise AccessError(_("Declará el vínculo explícito; no se admiten defaults de origen o factura."))
        if any("receipt_bill_contract" in vals for vals in vals_list):
            raise AccessError(_("El contrato de rendición se calcula a partir de su origen."))
        for vals in vals_list:
            source_id = vals.get("receipt_bill_source_id")
            if source_id:
                if not logic.positive_id(source_id) or vals.get("invoice_id") != source_id:
                    raise AccessError(_("Declará la misma factura canónica como origen y factura de rendición."))
                bill = self.env["account.move"].browse(source_id).exists()
                bill.check_access("read")
                if not bill or bill.receipt_bill_contract != 2:
                    raise AccessError(_("El origen debe ser una factura visible de contrato 2."))
                company = bill.company_id
                # Only protected configuration is read with elevated access, never bills or HR data.
                config = company.sudo()
                database = self.env["ir.config_parameter"].sudo().get_param("receipt_bill.database")
                employee_id = vals.get("employee_id")
                if not logic.positive_id(employee_id):
                    raise AccessError(_("La rendición requiere un empleado explícito por compañía."))
                employee = self.env["hr.employee"].browse(employee_id).exists()
                employee.check_access("read")
                if (not employee or company not in self.env.companies
                        or database != self.env.cr.dbname or not config.receipt_bill_enabled
                        or self.env.user not in config.receipt_bill_user_ids
                        or bill.receipt_bill_source_uid.id != self.env.uid
                        or employee.user_id != self.env.user or employee.company_id != company):
                    raise AccessError(_("La rendición requiere el usuario nominal de origen y su empleado en una compañía habilitada."))
                if vals.get("company_id") != company.id:
                    raise AccessError(_("La compañía explícita debe coincidir con el comprobante."))
        expenses = super().create(vals_list)
        expenses._receipt_bill_validate()
        return expenses

    def write(self, vals):
        if "receipt_bill_source_id" in vals or "receipt_bill_contract" in vals:
            raise AccessError(_("El vínculo de origen de rendición no puede agregarse, quitarse ni reemplazarse."))
        if self.filtered("receipt_bill_source_id") and IMMUTABLE & vals.keys():
            raise AccessError(_("Los datos de origen de la rendición son inmutables; revisá la divergencia sin duplicar el gasto."))
        result = super().write(vals)
        self._receipt_bill_validate()
        return result

    @api.constrains("receipt_bill_source_id", "invoice_id", "company_id", "employee_id",
                    "date", "currency_id", "total_amount_currency", "analytic_distribution",
                    "payment_mode", "product_id", "name")
    def _check_receipt_bill_source(self):
        self._receipt_bill_validate()

    def copy(self, default=None):
        if self.filtered("receipt_bill_source_id") or (default or {}).get("receipt_bill_source_id"):
            raise UserError(_("No se puede duplicar un gasto vinculado al comprobante canónico."))
        return super().copy(default=default)

    def copy_data(self, default=None):
        if self.filtered("receipt_bill_source_id") or (default or {}).get("receipt_bill_source_id"):
            raise UserError(_("No se puede convertir un gasto de origen canónico en una copia ordinaria."))
        return super().copy_data(default=default)

    def unlink(self):
        if self.filtered("receipt_bill_source_id"):
            raise UserError(_("Conservá el gasto de rendición y su origen, incluso cuando sea rechazado."))
        return super().unlink()

    def action_expense_create_invoice(self):
        if self.filtered("receipt_bill_source_id"):
            raise UserError(_("Este gasto ya tiene su factura canónica; no corresponde crear otra."))
        return super().action_expense_create_invoice()


class HrExpenseSheet(models.Model):
    _inherit = "hr.expense.sheet"

    def _receipt_bill_validate_sources(self):
        # Guard before OCA excludes linked bills from the ordinary expense posting path.
        self.expense_line_ids._receipt_bill_validate()

    def _receipt_bill_guard_canonical_moves(self):
        # A canonical invoice is never a journal entry generated by the HR report.
        # Comparing relational IDs does not elevate or read accounting contents.
        for sheet in self:
            canonical = sheet.expense_line_ids.mapped("receipt_bill_source_id")
            if set(canonical.ids) & set(sheet.account_move_ids.ids):
                raise UserError(_("La factura canónica no puede ser un asiento generado o anulado por la rendición."))

    def _do_refuse(self, reason):
        self._receipt_bill_guard_canonical_moves()
        return super()._do_refuse(reason)

    def _do_reverse_moves(self):
        self._receipt_bill_guard_canonical_moves()
        return super()._do_reverse_moves()

    def _do_create_moves(self):
        self._receipt_bill_validate_sources()
        return super()._do_create_moves()

    def _prepare_bills_vals(self):
        self._receipt_bill_validate_sources()
        return super()._prepare_bills_vals()

    def action_sheet_move_post(self):
        self._receipt_bill_validate_sources()
        return super().action_sheet_move_post()
