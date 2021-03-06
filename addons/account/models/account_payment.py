# -*- coding: utf-8 -*-

from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError

from collections import defaultdict

MAP_INVOICE_TYPE_PARTNER_TYPE = {
    'out_invoice': 'customer',
    'out_refund': 'customer',
    'out_receipt': 'customer',
    'in_invoice': 'supplier',
    'in_refund': 'supplier',
    'in_receipt': 'supplier',
}


class account_payment_method(models.Model):
    _name = "account.payment.method"
    _description = "Payment Methods"

    name = fields.Char(required=True, translate=True)
    code = fields.Char(required=True)  # For internal identification
    payment_type = fields.Selection([('inbound', 'Inbound'), ('outbound', 'Outbound')], required=True)


class account_payment(models.Model):
    _name = "account.payment"
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = "Payments"
    _order = "payment_date desc, name desc"
    _check_company_auto = True

    name = fields.Char(readonly=True, copy=False)  # The name is attributed upon post()
    payment_reference = fields.Char(copy=False, readonly=True, help="Reference of the document used to issue this payment. Eg. check number, file name, etc.")
    move_name = fields.Char(string='Journal Entry Name', readonly=True,
        default=False, copy=False,
        help="Technical field holding the number given to the journal entry, automatically set when the statement line is reconciled then stored to set the same number again if the line is cancelled, set to draft and re-processed again.")

    # Money flows from the journal_id's default_debit_account_id or default_credit_account_id to the destination_account_id
    destination_account_id = fields.Many2one('account.account', compute='_compute_destination_account_id', readonly=True)

    invoice_ids = fields.Many2many('account.move', 'account_invoice_payment_rel', 'payment_id', 'invoice_id', string="Invoices", copy=False, readonly=True,
                                   help="""Technical field containing the invoice for which the payment has been generated.
                                   This does not especially correspond to the invoices reconciled with the payment,
                                   as it can have been generated first, and reconciled later""")
    reconciled_invoice_ids = fields.Many2many('account.move', string='Reconciled Invoices', compute='_compute_reconciled_invoice_ids', help="Invoices whose journal items have been reconciled with these payments.")
    has_invoices = fields.Boolean(compute="_compute_reconciled_invoice_ids", help="Technical field used for usability purposes")
    reconciled_invoices_count = fields.Integer(compute="_compute_reconciled_invoice_ids")

    move_line_ids = fields.One2many('account.move.line', 'payment_id', readonly=True, copy=False, ondelete='restrict')
    move_reconciled = fields.Boolean(compute="_get_move_reconciled", readonly=True)

    state = fields.Selection([('draft', 'Draft'), ('posted', 'Validated'), ('sent', 'Sent'), ('reconciled', 'Reconciled'), ('cancelled', 'Cancelled'), ('invoicing_legacy', 'Invoicing App Legacy')], readonly=True, default='draft', copy=False, string="Status", tracking=True)
    payment_type = fields.Selection([('outbound', 'Send Money'), ('inbound', 'Receive Money')], string='Payment Type', required=True, readonly=True, states={'draft': [('readonly', False)]})
    _payment_methods = fields.Many2many('account.payment.method', compute='_compute_payment_methods')
    payment_method_id = fields.Many2one('account.payment.method', string='Payment Method', required=True, readonly=True, states={'draft': [('readonly', False)]},
        domain="""[
            ('payment_type', '=', ('inbound' if payment_type == 'inbound' else 'outbound')),
            ('id', 'in', _payment_methods),
        ]""",
        help="Manual: Get paid by cash, check or any other method outside of Odoo.\n"\
        "Electronic: Get paid automatically through a payment acquirer by requesting a transaction on a card saved by the customer when buying or subscribing online (payment token).\n"\
        "Check: Pay bill by check and print it from Odoo.\n"\
        "Batch Deposit: Encase several customer checks at once by generating a batch deposit to submit to your bank. When encoding the bank statement in Odoo, you are suggested to reconcile the transaction with the batch deposit.To enable batch deposit, module account_batch_payment must be installed.\n"\
        "SEPA Credit Transfer: Pay bill from a SEPA Credit Transfer file you submit to your bank. To enable sepa credit transfer, module account_sepa must be installed ")
    payment_method_code = fields.Char(related='payment_method_id.code',
        help="Technical field used to adapt the interface to the payment type selected.", readonly=True)

    partner_type = fields.Selection([('customer', 'Customer'), ('supplier', 'Vendor')], tracking=True, readonly=True, states={'draft': [('readonly', False)]})
    partner_id = fields.Many2one('res.partner', string='Partner', tracking=True, readonly=True, states={'draft': [('readonly', False)]}, domain="['|', ('company_id', '=', False), ('company_id', '=', company_id)]")
    is_internal_transfer = fields.Boolean(compute="_compute_is_internal_transfer", store=True)
    amount = fields.Monetary(string='Amount', required=True, readonly=True, states={'draft': [('readonly', False)]}, tracking=True)
    currency_id = fields.Many2one('res.currency', string='Currency', required=True, readonly=True, states={'draft': [('readonly', False)]}, default=lambda self: self.env.company.currency_id)
    payment_date = fields.Date(string='Date', default=fields.Date.context_today, required=True, readonly=True, states={'draft': [('readonly', False)]}, copy=False, tracking=True)
    communication = fields.Char(string='Memo', readonly=True, states={'draft': [('readonly', False)]})
    journal_id = fields.Many2one('account.journal', string='Journal', required=True, readonly=True, states={'draft': [('readonly', False)]}, tracking=True,
        check_company=True, domain="[('id', 'in', _suitable_journal_ids)]")
    _suitable_journal_ids = fields.Many2many('account.journal', compute='_compute_suitable_journal_ids')
    company_id = fields.Many2one('res.company', related='journal_id.company_id', string='Company', readonly=True, store=True, default=lambda self: self.env.company)

    hide_payment_method = fields.Boolean(compute='_compute_hide_payment_method',
                                         help="Technical field used to hide the payment method if the "
                                         "selected journal has only one available which is 'manual'")

    payment_difference = fields.Monetary(compute='_compute_payment_difference', readonly=True)
    payment_difference_handling = fields.Selection([('open', 'Keep open'), ('reconcile', 'Mark invoice as fully paid')], default='open', string="Payment Difference Handling", copy=False)
    writeoff_account_id = fields.Many2one('account.account', string="Difference Account", domain="[('deprecated', '=', False), ('company_id', '=', company_id)]", copy=False)
    writeoff_label = fields.Char(
        string='Journal Item Label',
        help='Change label of the counterpart that will hold the payment difference',
        default='Write-Off')
    partner_bank_account_id = fields.Many2one('res.partner.bank', string="Recipient Bank Account", readonly=True, states={'draft': [('readonly', False)]}, domain="['|', ('company_id', '=', False), ('company_id', '=', company_id), ('partner_id', 'in', possible_bank_partner_ids)]", check_company=True)
    possible_bank_partner_ids = fields.Many2many('res.partner', compute='_compute_possible_bank_partners')
    show_partner_bank_account = fields.Boolean(compute='_compute_show_partner_bank', help='Technical field used to know whether the field `partner_bank_account_id` needs to be displayed or not in the payments form views')
    require_partner_bank_account = fields.Boolean(compute='_compute_show_partner_bank', help='Technical field used to know whether the field `partner_bank_account_id` needs to be required or not in the payments form views')

    @api.model
    def default_get(self, default_fields):
        rec = super(account_payment, self).default_get(default_fields)
        active_ids = self._context.get('active_ids') or self._context.get('active_id')
        active_model = self._context.get('active_model')

        # Check for selected invoices ids
        if not active_ids or active_model != 'account.move':
            return rec

        invoices = self.env['account.move'].browse(active_ids).filtered(lambda move: move.is_invoice(include_receipts=True))

        # Check all invoices are open
        if not invoices or any(invoice.state != 'posted' for invoice in invoices):
            raise UserError(_("You can only register payments for open invoices"))
        # Check if, in batch payments, there are not negative invoices and positive invoices
        dtype = invoices[0].move_type
        for inv in invoices[1:]:
            if inv.move_type != dtype:
                if ((dtype == 'in_refund' and inv.move_type == 'in_invoice') or
                        (dtype == 'in_invoice' and inv.move_type == 'in_refund')):
                    raise UserError(_("You cannot register payments for vendor bills and supplier refunds at the same time."))
                if ((dtype == 'out_refund' and inv.move_type == 'out_invoice') or
                        (dtype == 'out_invoice' and inv.move_type == 'out_refund')):
                    raise UserError(_("You cannot register payments for customer invoices and credit notes at the same time."))

        amount = self._compute_payment_amount(invoices, invoices[0].currency_id, invoices[0].journal_id, rec.get('payment_date') or fields.Date.today())
        rec.update({
            'currency_id': invoices[0].currency_id.id,
            'amount': abs(amount),
            'payment_type': 'inbound' if amount > 0 else 'outbound',
            'partner_id': invoices[0].commercial_partner_id.id,
            'partner_type': MAP_INVOICE_TYPE_PARTNER_TYPE[invoices[0].move_type],
            'communication': invoices[0].invoice_payment_ref or invoices[0].ref or invoices[0].name,
            'invoice_ids': [(6, 0, invoices.ids)],
        })
        return rec

    @api.depends('partner_id', 'journal_id')
    def _compute_is_internal_transfer(self):
        for payment in self:
            payment.is_internal_transfer = payment.partner_id == payment.journal_id.company_id.partner_id

    @api.depends('amount', 'currency_id', 'payment_type', 'company_id', 'invoice_ids.company_id', )
    def _compute_suitable_journal_ids(self):
        for p in self:
            domain = [('company_id', '=', p.invoice_ids[:1].company_id.id or p.company_id.id)]
            if p.currency_id.is_zero(p.amount) and p.invoice_ids:
                domain.append(('type', '=', 'general'))
            else:
                domain.append(('type', 'in', ['bank', 'cash']))
                if p.payment_type == 'inbound':
                    domain.append(('at_least_one_inbound', '=', True))
                else:
                    domain.append(('at_least_one_outbound', '=', True))

            p._suitable_journal_ids = self.env['account.journal'].search(domain)

    @api.depends('partner_id.commercial_partner_id')
    def _compute_possible_bank_partners(self):
        for r in self:
            r.possible_bank_partner_ids = \
                r.partner_id | r.partner_id.commercial_partner_id

    @api.depends('payment_type', 'journal_id.inbound_payment_method_ids', 'journal_id.outbound_payment_method_ids')
    @api.depends_context('default_payment_method_id')
    def _compute_payment_methods(self):
        # Ensure the domain will accept the provided default value
        self._payment_methods = default = self.env['account.payment.method'].browse(self.env.context.get('default_payment_method_id'))
        for p in self.filtered(lambda p: p.journal_id):
            if p.payment_type == 'inbound':
                payment_methods = p.journal_id.inbound_payment_method_ids
            else:
                payment_methods = p.journal_id.outbound_payment_method_ids
            p._payment_methods = default | payment_methods

    @api.constrains('amount')
    def _check_amount(self):
        for payment in self:
            if payment.amount < 0:
                raise ValidationError(_('The payment amount cannot be negative.'))

    @api.model
    def _get_method_codes_using_bank_account(self):
        return []

    @api.model
    def _get_method_codes_needing_bank_account(self):
        return []

    @api.depends('payment_method_code')
    def _compute_show_partner_bank(self):
        """ Computes if the destination bank account must be displayed in the payment form view. By default, it
        won't be displayed but some modules might change that, depending on the payment type."""
        for payment in self:
            payment.show_partner_bank_account = payment.payment_method_code in self._get_method_codes_using_bank_account()
            payment.require_partner_bank_account = payment.state == 'draft' and payment.payment_method_code in self._get_method_codes_needing_bank_account()

    @api.depends('payment_type', 'journal_id')
    def _compute_hide_payment_method(self):
        for payment in self:
            if not payment.journal_id or payment.journal_id.type not in ['bank', 'cash']:
                payment.hide_payment_method = True
                continue
            journal_payment_methods = payment.payment_type == 'inbound'\
                and payment.journal_id.inbound_payment_method_ids\
                or payment.journal_id.outbound_payment_method_ids
            payment.hide_payment_method = len(journal_payment_methods) == 1 and journal_payment_methods[0].code == 'manual'

    @api.depends('invoice_ids', 'amount', 'payment_date', 'currency_id', 'payment_type')
    def _compute_payment_difference(self):
        draft_payments = self.filtered(lambda p: p.invoice_ids and p.state == 'draft')
        for pay in draft_payments:
            payment_amount = -pay.amount if pay.payment_type == 'outbound' else pay.amount
            pay.payment_difference = pay._compute_payment_amount(pay.invoice_ids, pay.currency_id, pay.journal_id, pay.payment_date) - payment_amount
        (self - draft_payments).payment_difference = 0

    @api.onchange('journal_id')
    def _onchange_journal(self):
        if not self.journal_id:
            return

        if self.journal_id.currency_id:
            self.currency_id = self.journal_id.currency_id

        if self.payment_method_id not in self._payment_methods._origin:
            self.payment_method_id = self._payment_methods[:1]._origin

        if self.env.context.get('active_model') == 'account.move':
            active_ids = self._context.get('active_ids')
            invoices = self.env['account.move'].browse(active_ids)
            self.amount = abs(self._compute_payment_amount(invoices, self.currency_id, self.journal_id, self.payment_date))

    @api.onchange('partner_id')
    def _onchange_partner_id(self):
        if self.invoice_ids and self.invoice_ids[0].invoice_partner_bank_id:
            self.partner_bank_account_id = self.invoice_ids[0].invoice_partner_bank_id
        elif self.partner_id != self.partner_bank_account_id.partner_id:
            # This condition ensures we use the default value provided into
            # context for partner_bank_account_id properly when provided with a
            # default partner_id. Without it, the onchange recomputes the bank account
            # uselessly and might assign a different value to it.
            if self.partner_id and len(self.partner_id.bank_ids) > 0:
                self.partner_bank_account_id = self.partner_id.bank_ids[0]
            elif self.partner_id and len(self.partner_id.commercial_partner_id.bank_ids) > 0:
                self.partner_bank_account_id = self.partner_id.commercial_partner_id.bank_ids[0]
            else:
                self.partner_bank_account_id = False

    @api.onchange('payment_type')
    def _onchange_payment_type(self):
        if not self.invoice_ids and not self.partner_type:
            # Set default partner type for the payment type
            if self.payment_type == 'inbound':
                self.partner_type = 'customer'
            else:  # -> self.payment_type == 'outbound'
                self.partner_type = 'supplier'
        self._onchange_journal()
        if self.currency_id.is_zero(self.amount) and self.has_invoices:
            self.payment_difference_handling = 'reconcile'

    @api.onchange('amount', 'currency_id')
    def _onchange_amount(self):
        journal_types = ['bank', 'cash']
        if self.currency_id.is_zero(self.amount) and self.has_invoices:
            # In case of payment with 0 amount, allow to select a journal of type 'general' like
            # 'Miscellaneous Operations' and set this journal by default.
            journal_types = ['general']
            self.payment_difference_handling = 'reconcile'
        domain_on_types = [('type', 'in', journal_types)]
        if self.invoice_ids:
            domain_on_types.append(('company_id', '=', self.invoice_ids[0].company_id.id))
        if self.journal_id.type not in journal_types or (self.invoice_ids and self.journal_id.company_id != self.invoice_ids[0].company_id):
            self.journal_id = self.env['account.journal'].search(domain_on_types, limit=1)

    @api.onchange('currency_id')
    def _onchange_currency(self):
        self.amount = abs(self._compute_payment_amount(self.invoice_ids, self.currency_id, self.journal_id, self.payment_date))

        if self.journal_id:  # TODO: only return if currency differ?
            return

        # Set by default the first liquidity journal having this currency if exists.
        domain = [('type', 'in', ('bank', 'cash')), ('currency_id', '=', self.currency_id.id)]
        if self.invoice_ids:
            domain.append(('company_id', '=', self.invoice_ids[0].company_id.id))
        journal = self.env['account.journal'].search(domain, limit=1)
        if journal:
            return {'value': {'journal_id': journal.id}}

    @api.model
    def _compute_payment_amount(self, invoices, currency, journal, date):
        '''Compute the total amount for the payment wizard.

        :param invoices:    Invoices on which compute the total as an account.invoice recordset.
        :param currency:    The payment's currency as a res.currency record.
        :param journal:     The payment's journal as an account.journal record.
        :param date:        The payment's date as a datetime.date object.
        :return:            The total amount to pay the invoices.
        '''
        company = journal.company_id
        currency = currency or journal.currency_id or company.currency_id
        date = date or fields.Date.today()

        if not invoices:
            return 0.0

        self.env['account.move'].flush(['move_type', 'currency_id'])
        self.env['account.move.line'].flush(['amount_residual', 'amount_residual_currency', 'move_id', 'account_id'])
        self.env['account.account'].flush(['user_type_id'])
        self.env['account.account.type'].flush(['type'])
        self._cr.execute('''
            SELECT
                move.move_type AS type,
                move.currency_id AS currency_id,
                SUM(line.amount_residual) AS amount_residual,
                SUM(line.amount_residual_currency) AS residual_currency
            FROM account_move move
            LEFT JOIN account_move_line line ON line.move_id = move.id
            LEFT JOIN account_account account ON account.id = line.account_id
            LEFT JOIN account_account_type account_type ON account_type.id = account.user_type_id
            WHERE move.id IN %s
            AND account_type.type IN ('receivable', 'payable')
            GROUP BY move.id, move.move_type
        ''', [tuple(invoices.ids)])
        query_res = self._cr.dictfetchall()

        total = 0.0
        for res in query_res:
            move_currency = self.env['res.currency'].browse(res['currency_id'])
            if move_currency == currency and move_currency != company.currency_id:
                total += res['residual_currency']
            else:
                total += company.currency_id._convert(res['amount_residual'], currency, company, date)
        return total

    def name_get(self):
        return [(payment.id, payment.name or _('Draft Payment')) for payment in self]

    @api.depends('move_line_ids.reconciled')
    def _get_move_reconciled(self):
        for payment in self:
            rec = True
            for aml in payment.move_line_ids.filtered(lambda x: x.account_id.reconcile):
                if not aml.reconciled:
                    rec = False
                    break
            payment.move_reconciled = rec

    @api.depends('invoice_ids', 'payment_type', 'partner_type', 'partner_id', 'is_internal_transfer')
    def _compute_destination_account_id(self):
        for payment in self:
            payment = payment.with_company(payment.company_id)
            if payment.invoice_ids:
                payment.destination_account_id = payment.invoice_ids[0].mapped(
                    'line_ids.account_id').filtered(
                        lambda account: account.user_type_id.type in ('receivable', 'payable'))[0]
            elif payment.is_internal_transfer:
                if not payment.company_id.transfer_account_id.id:
                    raise UserError(_('There is no Transfer Account defined in the accounting settings. Please define one to be able to confirm this transfer.'))
                payment.destination_account_id = payment.company_id.transfer_account_id.id
            elif payment.partner_id:
                if payment.partner_type == 'customer':
                    payment.destination_account_id = payment.partner_id.property_account_receivable_id.id
                else:
                    payment.destination_account_id = payment.partner_id.property_account_payable_id.id
            elif payment.partner_type == 'customer':
                default_account = self.env['ir.property'].get('property_account_receivable_id', 'res.partner')
                payment.destination_account_id = default_account.id
            elif payment.partner_type == 'supplier':
                default_account = self.env['ir.property'].get('property_account_payable_id', 'res.partner')
                payment.destination_account_id = default_account.id

    @api.depends('move_line_ids.matched_debit_ids', 'move_line_ids.matched_credit_ids')
    def _compute_reconciled_invoice_ids(self):
        for record in self:
            reconciled_moves = record.move_line_ids.mapped('matched_debit_ids.debit_move_id.move_id')\
                               + record.move_line_ids.mapped('matched_credit_ids.credit_move_id.move_id')
            record.reconciled_invoice_ids = reconciled_moves.filtered(lambda move: move.is_invoice())
            record.has_invoices = bool(record.reconciled_invoice_ids)
            record.reconciled_invoices_count = len(record.reconciled_invoice_ids)

    def action_register_payment(self):
        active_ids = self.env.context.get('active_ids')
        if not active_ids:
            return ''

        return {
            'name': _('Register Payment'),
            'res_model': len(active_ids) == 1 and 'account.payment' or 'account.payment.register',
            'view_mode': 'form',
            'view_id': len(active_ids) != 1 and self.env.ref('account.view_account_payment_form_multi').id or self.env.ref('account.view_account_payment_invoice_form').id,
            'context': self.env.context,
            'target': 'new',
            'type': 'ir.actions.act_window',
        }

    def button_journal_entries(self):
        return {
            'name': _('Journal Items'),
            'view_mode': 'tree,form',
            'res_model': 'account.move.line',
            'view_id': False,
            'type': 'ir.actions.act_window',
            'domain': [('payment_id', 'in', self.ids)],
        }

    def button_invoices(self):
        return {
            'name': _('Paid Invoices'),
            'view_mode': 'tree,form',
            'res_model': 'account.move',
            'view_id': False,
            'views': [(self.env.ref('account.view_move_tree').id, 'tree'), (self.env.ref('account.view_move_form').id, 'form')],
            'type': 'ir.actions.act_window',
            'domain': [('id', 'in', [x.id for x in self.reconciled_invoice_ids])],
            'context': {'create': False},
        }

    def unreconcile(self):
        """ Set back the payments in 'posted' or 'sent' state, without deleting the journal entries.
            Called when cancelling a bank statement line linked to a pre-registered payment.
        """
        for payment in self:
            if payment.payment_reference:
                payment.write({'state': 'sent'})
            else:
                payment.write({'state': 'posted'})

    def cancel(self):
        self.write({'state': 'cancelled'})

    def unlink(self):
        if any(bool(rec.move_line_ids) for rec in self):
            raise UserError(_("You cannot delete a payment that is already posted."))
        if any(rec.move_name for rec in self):
            raise UserError(_('It is not allowed to delete a payment that already created a journal entry since it would create a gap in the numbering. You should create the journal entry again and cancel it thanks to a regular revert.'))
        return super(account_payment, self).unlink()

    def _prepare_payment_moves(self):
        ''' Prepare the creation of journal entries (account.move) by creating a list of python dictionary to be passed
        to the 'create' method.

        Example : outbound with write-off:

        Account             | Debit     | Credit
        ---------------------------------------------------------
        BANK                |   900.0   |
        RECEIVABLE          |           |   1000.0
        WRITE-OFF ACCOUNT   |   100.0   |

        :return: A list of Python dictionary to be passed to env['account.move'].create.
        '''
        all_move_vals = []
        for payment in self:
            company_currency = payment.company_id.currency_id

            # Compute amounts.
            write_off_amount = payment.payment_difference_handling == 'reconcile' and -payment.payment_difference or 0.0
            if payment.payment_type == 'outbound':
                counterpart_amount = payment.amount
                liquidity_line_account = payment.journal_id.default_debit_account_id
            else:
                counterpart_amount = -payment.amount
                liquidity_line_account = payment.journal_id.default_credit_account_id

            # Manage currency.
            if payment.currency_id == company_currency:
                # Single-currency.
                balance = counterpart_amount
                write_off_balance = write_off_amount
                counterpart_amount = write_off_amount = 0.0
                currency_id = False
            else:
                # Multi-currencies.
                balance = payment.currency_id._convert(counterpart_amount, company_currency, payment.company_id, payment.payment_date)
                write_off_balance = payment.currency_id._convert(write_off_amount, company_currency, payment.company_id, payment.payment_date)
                currency_id = payment.currency_id.id

            # Manage custom currency on journal for liquidity line.
            if payment.journal_id.currency_id and payment.currency_id != payment.journal_id.currency_id:
                # Custom currency on journal.
                if payment.journal_id.currency_id == company_currency:
                    # Single-currency
                    liquidity_line_currency_id = False
                else:
                    liquidity_line_currency_id = payment.journal_id.currency_id.id
                liquidity_amount = company_currency._convert(
                    balance, payment.journal_id.currency_id, payment.company_id, payment.payment_date)
            else:
                # Use the payment currency.
                liquidity_line_currency_id = currency_id
                liquidity_amount = counterpart_amount

            # Compute 'name' to be used in receivable/payable line.
            rec_pay_line_name = ''
            if payment.is_internal_transfer:
                rec_pay_line_name = payment.name
            else:
                if payment.partner_type == 'customer':
                    if payment.payment_type == 'inbound':
                        rec_pay_line_name += _("Customer Payment")
                    elif payment.payment_type == 'outbound':
                        rec_pay_line_name += _("Customer Credit Note")
                elif payment.partner_type == 'supplier':
                    if payment.payment_type == 'inbound':
                        rec_pay_line_name += _("Vendor Credit Note")
                    elif payment.payment_type == 'outbound':
                        rec_pay_line_name += _("Vendor Payment")
                if payment.invoice_ids:
                    rec_pay_line_name += ': %s' % ', '.join(payment.invoice_ids.mapped('name'))

            # Compute 'name' to be used in liquidity line.
            if payment.is_internal_transfer:
                if payment.payment_type == 'inbound':
                    liquidity_line_name = _('Transfer to %s') % payment.journal_id.name
                elif payment.payment_type == 'outbound':
                    liquidity_line_name = _('Transfer from %s') % payment.journal_id.name
            else:
                liquidity_line_name = payment.name

            # ==== 'inbound' / 'outbound' ====

            move_vals = {
                'date': payment.payment_date,
                'ref': payment.communication,
                'journal_id': payment.journal_id.id,
                'currency_id': payment.journal_id.currency_id.id or payment.company_id.currency_id.id,
                'partner_id': payment.partner_id.id,
                'line_ids': [
                    # Receivable / Payable / Transfer line.
                    (0, 0, {
                        'name': rec_pay_line_name,
                        'amount_currency': counterpart_amount + write_off_amount if currency_id else 0.0,
                        'currency_id': currency_id,
                        'debit': balance + write_off_balance > 0.0 and balance + write_off_balance or 0.0,
                        'credit': balance + write_off_balance < 0.0 and -balance - write_off_balance or 0.0,
                        'date_maturity': payment.payment_date,
                        'partner_id': payment.partner_id.commercial_partner_id.id,
                        'account_id': payment.destination_account_id.id,
                        'payment_id': payment.id,
                    }),
                    # Liquidity line.
                    (0, 0, {
                        'name': liquidity_line_name,
                        'amount_currency': -liquidity_amount if liquidity_line_currency_id else 0.0,
                        'currency_id': liquidity_line_currency_id,
                        'debit': balance < 0.0 and -balance or 0.0,
                        'credit': balance > 0.0 and balance or 0.0,
                        'date_maturity': payment.payment_date,
                        'partner_id': payment.partner_id.commercial_partner_id.id,
                        'account_id': liquidity_line_account.id,
                        'payment_id': payment.id,
                    }),
                ],
            }
            if write_off_balance:
                # Write-off line.
                move_vals['line_ids'].append((0, 0, {
                    'name': payment.writeoff_label,
                    'amount_currency': -write_off_amount,
                    'currency_id': currency_id,
                    'debit': write_off_balance < 0.0 and -write_off_balance or 0.0,
                    'credit': write_off_balance > 0.0 and write_off_balance or 0.0,
                    'date_maturity': payment.payment_date,
                    'partner_id': payment.partner_id.commercial_partner_id.id,
                    'account_id': payment.writeoff_account_id.id,
                    'payment_id': payment.id,
                }))

            if payment.move_name:
                move_vals['name'] = payment.move_name

            all_move_vals.append(move_vals)

        return all_move_vals

    def post(self):
        """ Create the journal items for the payment and update the payment's state to 'posted'.
            A journal entry is created containing an item in the source liquidity account (selected journal's default_debit or default_credit)
            and another in the destination reconcilable account (see _compute_destination_account_id).
            If invoice_ids is not empty, there will be one reconcilable move line per invoice to reconcile with.
            If the payment is a transfer, a second journal entry is created in the destination journal to receive money from the transfer account.
        """
        AccountMove = self.env['account.move'].with_context(default_move_type='entry')
        for rec in self:

            if rec.state != 'draft':
                raise UserError(_("Only a draft payment can be posted."))

            if any(inv.state != 'posted' for inv in rec.invoice_ids):
                raise ValidationError(_("The payment cannot be processed because the invoice is not open!"))

            # keep the name in case of a payment reset to draft
            if not rec.name:
                if rec.is_internal_transfer:
                    sequence_code = 'account.payment.transfer'
                else:
                    if rec.partner_type == 'customer':
                        if rec.payment_type == 'inbound':
                            sequence_code = 'account.payment.customer.invoice'
                        elif rec.payment_type == 'outbound':
                            sequence_code = 'account.payment.customer.refund'
                    elif rec.partner_type == 'supplier':
                        if rec.payment_type == 'inbound':
                            sequence_code = 'account.payment.supplier.refund'
                        elif rec.payment_type == 'outbound':
                            sequence_code = 'account.payment.supplier.invoice'
                rec.name = self.env['ir.sequence'].next_by_code(sequence_code, sequence_date=rec.payment_date)
                if not rec.name and not rec.is_internal_transfer:
                    raise UserError(_("You have to define a sequence for %s in your company.") % (sequence_code,))

            move = AccountMove.create(rec._prepare_payment_moves())
            if move.journal_id.post_at != 'bank_rec':
                move.post()

            # Update the state / move before performing any reconciliation.
            rec.write({'state': 'posted', 'move_name': move.name})

            if rec.invoice_ids:
                (move + rec.invoice_ids).line_ids \
                    .filtered(lambda line: not line.reconciled and line.account_id == rec.destination_account_id)\
                    .reconcile()

        return True

    def action_draft(self):
        moves = self.mapped('move_line_ids.move_id')
        moves.filtered(lambda move: move.state == 'posted').button_draft()
        moves.with_context(force_delete=True).unlink()
        self.write({'state': 'draft'})

    def _get_invoice_payment_amount(self, inv):
        """
        Computes the amount covered by the current payment in the given invoice.

        :param inv: an invoice object
        :returns: the amount covered by the payment in the invoice
        """
        self.ensure_one()
        return sum([
            data['amount']
            for data in inv._get_reconciled_info_JSON_values()
            if data['account_payment_id'] == self.id
        ])

class payment_register(models.TransientModel):
    _name = 'account.payment.register'
    _description = 'Register Payment'

    payment_date = fields.Date(required=True, default=fields.Date.context_today)
    journal_id = fields.Many2one('account.journal', required=True, domain="[('type', 'in', ('bank', 'cash')), ('company_id', '=', invoice_company_id)]")
    payment_method_id = fields.Many2one('account.payment.method', string='Payment Method Type', required=True,
                                        domain="[('id', 'in', available_payment_methods)]",
                                        help="Manual: Get paid by cash, check or any other method outside of Odoo.\n"
                                        "Electronic: Get paid automatically through a payment acquirer by requesting a transaction on a card saved by the customer when buying or subscribing online (payment token).\n"
                                        "Check: Pay bill by check and print it from Odoo.\n"
                                        "Batch Deposit: Encase several customer checks at once by generating a batch deposit to submit to your bank. When encoding the bank statement in Odoo, you are suggested to reconcile the transaction with the batch deposit.To enable batch deposit, module account_batch_payment must be installed.\n"
                                        "SEPA Credit Transfer: Pay bill from a SEPA Credit Transfer file you submit to your bank. To enable sepa credit transfer, module account_sepa must be installed ")
    invoice_ids = fields.Many2many('account.move', 'account_invoice_payment_rel_transient', 'payment_id', 'invoice_id', string="Invoices", copy=False, readonly=True)
    group_payment = fields.Boolean(help="Only one payment will be created by partner (bank)/ currency.")

    invoice_company_id = fields.Many2one(related='invoice_ids.company_id')
    available_payment_methods = fields.Many2many('account.payment.method', compute='_compute_available_payment_methods')

    @api.model
    def default_get(self, fields):
        rec = super(payment_register, self).default_get(fields)
        active_ids = self._context.get('active_ids')
        if not active_ids:
            return rec
        invoices = self.env['account.move'].browse(active_ids)

        # Check all invoices are open
        if any(invoice.state != 'posted' or invoice.payment_state not in ('not_paid', 'partial') or not invoice.is_invoice() for invoice in invoices):
            raise UserError(_("You can only register payments for open invoices"))
        # Check all invoices are inbound or all invoices are outbound
        outbound_list = [invoice.is_outbound() for invoice in invoices]
        first_outbound = invoices[0].is_outbound()
        if any(x != first_outbound for x in outbound_list):
            raise UserError(_("You can only register at the same time for payment that are all inbound or all outbound"))
        if any(inv.company_id != invoices[0].company_id for inv in invoices):
            raise UserError(_("You can only register at the same time for payment that are all from the same company"))
        # Check the destination account is the same
        destination_account = invoices.line_ids.filtered(lambda line: line.account_internal_type in ('receivable', 'payable')).mapped('account_id')
        if len(destination_account) > 1:
            raise UserError(_('There is more than one receivable/payable account in the concerned invoices. You cannot group payments in that case.'))
        if 'invoice_ids' not in rec:
            rec['invoice_ids'] = [(6, 0, invoices.ids)]
        if 'journal_id' not in rec:
            rec['journal_id'] = self.env['account.journal'].search([('company_id', '=', self.env.company.id), ('type', 'in', ('bank', 'cash'))], limit=1).id
        if 'payment_method_id' not in rec:
            if invoices[0].is_inbound():
                domain = [('payment_type', '=', 'inbound')]
            else:
                domain = [('payment_type', '=', 'outbound')]
            rec['payment_method_id'] = self.env['account.payment.method'].search(domain, limit=1).id
        return rec

    @api.depends('invoice_ids', 'journal_id.inbound_payment_method_ids', 'journal_id.outbound_payment_method_ids')
    def _compute_available_payment_methods(self):
        for p in self:
            invoice = p.invoice_ids[:1]
            if not invoice:
                p.available_payment_methods = self.env['account.payment.method']
            elif invoice.is_inbound():
                p.available_payment_methods = self.journal_id.inbound_payment_method_ids._origin
            else:
                p.available_payment_methods = self.journal_id.outbound_payment_method_ids._origin

    def _prepare_payment_vals(self, invoices):
        '''Create the payment values.

        :param invoices: The invoices/bills to pay. In case of multiple
            documents, they need to be grouped by partner, bank, journal and
            currency.
        :return: The payment values as a dictionary.
        '''
        amount = self.env['account.payment']._compute_payment_amount(invoices, invoices[0].currency_id, self.journal_id, self.payment_date)
        values = {
            'journal_id': self.journal_id.id,
            'payment_method_id': self.payment_method_id.id,
            'payment_date': self.payment_date,
            'communication': " ".join(i.invoice_payment_ref or i.ref or i.name for i in invoices),
            'invoice_ids': [(6, 0, invoices.ids)],
            'payment_type': ('inbound' if amount > 0 else 'outbound'),
            'amount': abs(amount),
            'currency_id': invoices[0].currency_id.id,
            'partner_id': invoices[0].commercial_partner_id.id,
            'partner_type': MAP_INVOICE_TYPE_PARTNER_TYPE[invoices[0].move_type],
            'partner_bank_account_id': invoices[0].invoice_partner_bank_id.id,
        }
        return values

    def get_payments_vals(self):
        '''Compute the values for payments.

        :return: a list of payment values (dictionary).
        '''
        grouped = defaultdict(lambda: self.env["account.move"])
        for inv in self.invoice_ids:
            if self.group_payment:
                grouped[(inv.commercial_partner_id, inv.currency_id, inv.invoice_partner_bank_id, MAP_INVOICE_TYPE_PARTNER_TYPE[inv.move_type])] += inv
            else:
                grouped[inv.id] += inv
        return [self._prepare_payment_vals(invoices) for invoices in grouped.values()]

    def create_payments(self):
        '''Create payments according to the invoices.
        Having invoices with different commercial_partner_id or different type
        (Vendor bills with customer invoices) leads to multiple payments.
        In case of all the invoices are related to the same
        commercial_partner_id and have the same type, only one payment will be
        created.

        :return: The ir.actions.act_window to show created payments.
        '''
        Payment = self.env['account.payment']
        payments = Payment.create(self.get_payments_vals())
        payments.post()

        action_vals = {
            'name': _('Payments'),
            'domain': [('id', 'in', payments.ids), ('state', '=', 'posted')],
            'res_model': 'account.payment',
            'view_id': False,
            'type': 'ir.actions.act_window',
        }
        if len(payments) == 1:
            action_vals.update({'res_id': payments[0].id, 'view_mode': 'form'})
        else:
            action_vals['view_mode'] = 'tree,form'
        return action_vals
