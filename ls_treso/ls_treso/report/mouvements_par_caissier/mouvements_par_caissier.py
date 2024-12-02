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
		{ "label": _("Date"), "fieldtype": "Date",	"fieldname": "date", "width": 100, },
		{ "label": _("Caisse"), "fieldtype": "Link", "fieldname": "caisse", "options": "Caisse", "width": 100, },
		{ "label": _("N° Pièce"), "fieldtype": "Data",	"fieldname": "name", "width": 100, },
		{ "label": _("Désignation"), "fieldtype": "Data", "fieldname": "designation", "width": 100, },
		{ "label": _("Recette"), "fieldtype": "Currency", "fieldname": "recette", "options": "devise", "width": 100, },
		{ "label": _("Dépense"), "fieldtype": "Currency", "fieldname": "depense", "options": "devise", "width": 100, },
		{ "label": _("Devise"), "fieldtype": "Link", "fieldname": "devise", "options": "Devise", "width": 100, },
	]
	return columns


def get_data(filters):

	#frappe.msgprint(str(filters))

	data = frappe.db.sql(
        """
        select o.date, 
		o.name, 
		o.designation,
		case when n.type_operation = 'Encaissement' THEN o.montant ELSE 0 END as 'recette',
		case when n.type_operation <> 'Encaissement' THEN o.montant ELSE 0 END as 'depense', 
		o.devise, o.caisse
		from (
			SELECT c.name,c.remettant,c.designation,c.reference,c.devise,c.creation,c.caisse,c.montant,c.date, c.owner
			FROM tabEncaissement c
			UNION
			SELECT c.name,c.remettant,c.designation,c.reference,c.devise,c.creation,c.caisse,c.montant,c.date, c.owner
			FROM tabDecaissement c
			) o 
		INNER JOIN `tabDetails Operation de Caisse` d on o.name = d.parent
		INNER JOIN `tabNature Operations` n on d.nature_operations = n.name
		where o.date >= %(date_debut)s and o.date <= %(date_fin)s and o.owner LIKE %(caissier)s and o.caisse LIKE %(caisse)s

        """,{"date_debut": filters.date_debut, "date_fin": filters.date_fin, "caissier": filters.caissier if filters.caissier !=  None else "%", "caisse": filters.caisse if filters.caisse !=  None else "%"}, as_dict = 1
    )

	return data

