# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class EventTemplateTicket(models.Model):
    _inherit = 'event.type.ticket'

    def _default_product_id(self):
        return self.env.ref('event_sale.product_product_event', raise_if_not_found=False)

    description = fields.Text(compute='_compute_description', copy=True, readonly=False, store=True)
    # product
    product_id = fields.Many2one(
        'product.product', string='Product', required=True,
        domain=[("event_ok", "=", True)], default=_default_product_id)
    price = fields.Float(
        string='Price', compute='_compute_price',
        digits='Product Price', copy=True, readonly=False, store=True)
    price_reduce = fields.Float(
        string="Price Reduce", compute="_compute_price_reduce",
        digits='Product Price')

    @api.depends('product_id')
    def _compute_price(self):
        for ticket in self:
            if ticket.product_id and ticket.product_id.lst_price:
                ticket.price = ticket.product_id.lst_price or 0
            elif not ticket.price:
                ticket.price = 0

    @api.depends('product_id')
    def _compute_description(self):
        for ticket in self:
            if ticket.product_id and ticket.product_id.description_sale:
                ticket.description = ticket.product_id.description_sale
            # initialize, i.e for embedded tree views
            if not ticket.description:
                ticket.description = False

    @api.depends('product_id', 'price')
    def _compute_price_reduce(self):
        for ticket in self:
            product = ticket.product_id
            discount = product.lst_price and (product.lst_price - product.price) / product.lst_price or 0.0
            ticket.price_reduce = (1.0 - discount) * ticket.price

    def _init_column(self, column_name):
        if column_name != "product_id":
            return super(EventTemplateTicket, self)._init_column(column_name)

        # fetch void columns
        self.env.cr.execute("SELECT id FROM %s WHERE product_id IS NULL" % self._table)
        ticket_type_ids = self.env.cr.fetchall()
        if not ticket_type_ids:
            return

        # update existing columns
        _logger.debug("Table '%s': setting default value of new column %s to unique values for each row",
                      self._table, column_name)
        default_event_product = self.env.ref('event_sale.product_product_event', raise_if_not_found=False)
        if default_event_product:
            product_id = default_event_product.id
        else:
            product_id = self.env['product.product'].create({
                'name': 'Generic Registration Product',
                'list_price': 0,
                'standard_price': 0,
                'type': 'service',
                'default_code': 'EVENT_REG',
                'type': 'service',
            }).id
            self.env['ir.model.data'].create({
                'name': 'product_product_event',
                'module': 'event_sale',
                'model': 'product.product',
                'res_id': product_id,
            })
        self.env.cr._obj.execute(
            f'UPDATE {self._table} SET product_id = %s WHERE id IN %s;',
            (product_id, tuple(ticket_type_ids))
        )

    @api.model
    def _get_event_ticket_fields_whitelist(self):
        """ Add sale specific fields to copy from template to ticket """
        return super(EventTemplateTicket, self)._get_event_ticket_fields_whitelist() + ['product_id', 'price']


class EventTicket(models.Model):
    _inherit = 'event.event.ticket'
    _order = "event_id, price"

    # product
    price_reduce_taxinc = fields.Float(string='Price Reduce Tax inc', compute='_compute_price_reduce_taxinc')

    def _compute_price_reduce_taxinc(self):
        for record in self:
            # sudo necessary here since the field is most probably accessed through the website
            tax_ids = record.sudo().product_id.taxes_id.filtered(lambda r: r.company_id == record.event_id.company_id)
            taxes = tax_ids.compute_all(record.price_reduce, record.event_id.company_id.currency_id, 1.0, product=record.product_id)
            record.price_reduce_taxinc = taxes['total_included']

    @api.depends('product_id.active')
    def _compute_sale_available(self):
        inactive_product_tickets = self.filtered(lambda ticket: not ticket.product_id.active)
        for ticket in inactive_product_tickets:
            ticket.sale_available = False
        super(EventTicket, self - inactive_product_tickets)._compute_sale_available()

    def _get_ticket_multiline_description(self):
        """ If people set a description on their product it has more priority
        than the ticket name itself for the SO description. """
        self.ensure_one()
        if self.product_id.description_sale:
            return '%s\n%s' % (self.product_id.description_sale, self.event_id.display_name)
        return super(EventTicket, self)._get_ticket_multiline_description()
