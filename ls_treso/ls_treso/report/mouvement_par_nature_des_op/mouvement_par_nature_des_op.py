# Copyright (c) 2023, Kossivi Amouzou and contributors
# For license information, please see license.txt

import frappe
from frappe import _

def execute(filters=None):
	filters = frappe._dict(filters or {})
	columns = get_columns(filters)
	data = get_data(filters)
	return columns, data


def get_columns(filters):
	columns = [
		{ "label": _("N° Pièce"), "fieldtype": "Data",	"fieldname": "name", "width": 100, },
		{ "label": _("Caisse"), "fieldtype": "Link", "fieldname": "caisse", "options": "Caisse", "width": 100, },
		{ "label": _("Bénéficiaire"), "fieldtype": "Data", "fieldname": "remettant", "width": 100, },
		{ "label": _("Désignation"), "fieldtype": "Data", "fieldname": "designation", "width": 100, },
		{ "label": _("Nature"), "fieldtype": "Link", "fieldname": "nature_operations", "options": "Nature Operations", "width": 100, },
		{ "label": _("Montant"), "fieldtype": "Currency", "fieldname": "montant", "options": "devise", "width": 100, },
	]
	return columns


def get_data(filters):

	#frappe.msgprint(str(filters))

	data = frappe.db.sql(
        """
        SELECT o.date, 
		o.name, 
		o.remettant,
		o.designation,
		d.montant_devise AS montant,
		o.devise, o.caisse, d.nature_operations
		FROM (
			SELECT c.name,c.remettant,c.designation,c.reference,c.devise,c.creation,c.caisse,c.montant,c.date
			FROM tabEncaissement c
			UNION
			SELECT c.name,c.remettant,c.designation,c.reference,c.devise,c.creation,c.caisse,c.montant,c.date
			FROM tabDecaissement c
			) o 
		INNER JOIN `tabDetails Operation de Caisse` d on o.name = d.parent
		WHERE o.date >= %(date_debut)s AND o.date <= %(date_fin)s AND d.nature_operations LIKE %(nature)s
        """,{"date_debut": filters.date_debut, "date_fin": filters.date_fin, "nature": filters.nature if filters.nature !=  None else "%"}, as_dict = 1
    )

	return data

