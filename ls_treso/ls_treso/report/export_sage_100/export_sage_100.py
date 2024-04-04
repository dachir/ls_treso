# Copyright (c) 2023, Kossivi Amouzou and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from erpnext.setup.utils import get_exchange_rate

def execute(filters=None):
	filters = frappe._dict(filters or {})
	columns = get_columns(filters)
	data = get_data(filters)
	return columns, data


def get_columns(filters):
	columns = [
		{ "label": _("Date"), "fieldtype": "Data",	"fieldname": "date", "width": 100, },
		{ "label": _("N° Pièce"), "fieldtype": "Data",	"fieldname": "name", "width": 100, },
		{ "label": _("Compte"), "fieldtype": "Link", "fieldname": "compte", "options": "Compte General", "width": 100, },
		{ "label": _("Tiers"), "fieldtype": "Link", "fieldname": "tiers", "options": "Tiers", "width": 100, },
		{ "label": _("Libellé"), "fieldtype": "Data", "fieldname": "designation", "width": 100, },
		{ "label": _("Parité|cours"), "fieldtype": "Float", "fieldname": "cours", "width": 100, },
		{ "label": _("Montant devise"), "fieldtype": "Currency", "fieldname": "montant", "options": "devise", "width": 100, },
		{ "label": _("Code Journal"), "fieldtype": "Data", "fieldname": "journal", "width": 100, },
		{ "label": _("Débit"), "fieldtype": "Currency", "fieldname": "debit", "options": "company_currency", "width": 100, },
		{ "label": _("Crédit"), "fieldtype": "Currency", "fieldname": "credit", "options": "company_currency", "width": 100, },
		{ "label": _("Devise Societe"), "fieldtype": "Link", "fieldname": "company_currency", "options": "Devise", "width": 100, "hidden": 1, },

		{ "label": _("Imputation Analytique"), "fieldtype": "Link", "fieldname": "compte_analytique", "options": "Compte Analytique", "width": 100, "hidden": 0, },
		{ "label": _("Imputation Analytique 2"), "fieldtype": "Link", "fieldname": "compte_analytique_2", "options": "Compte Analytique", "width": 100, "hidden": 0, },
		{ "label": _("Imputation Analytique 3"), "fieldtype": "Link", "fieldname": "compte_analytique_3", "options": "Compte Analytique", "width": 100, "hidden": 0, },
		{ "label": _("Imputation Analytique 4"), "fieldtype": "Link", "fieldname": "compte_analytique_4", "options": "Compte Analytique", "width": 100, "hidden": 0, },
		{ "label": _("Imputation Analytique 5"), "fieldtype": "Link", "fieldname": "compte_analytique_5", "options": "Compte Analytique", "width": 100, "hidden": 0, },
	]
	return columns


def get_data(filters):

	#frappe.msgprint(str(filters))
	if filters.societe:
		company_currency = frappe.db.get_value("Societe",filters.societe,"devise_de_base") 
	else:
		frappe.throw("Selectionnez une société!")

	data = frappe.db.sql(
        """
        SELECT DATE_FORMAT(o.date, "%%d/%%m/%%Y") AS date, 
		o.name, 
		SUBSTRING_INDEX(d.compte,' - ',1) AS compte,
		d.tiers,
		o.designation,
		d.cours,
		d.montant,
		o.journal,
		o.devise,
		d.compte_analytique,
		d.compte_analytique_2,
		d.compte_analytique_3,
		d.compte_analytique_4,
		d.compte_analytique_5,
		CASE WHEN d.sens = 'Debit' THEN d.montant * d.cours else NULL END AS debit,
		CASE WHEN d.sens = 'Credit' THEN d.montant * d.cours else NULL END AS credit,
		%(currency)s as company_currency 
		FROM (
			SELECT site, societe, journal, date, devise, name, caisse, docstatus, designation, cours
			FROM tabEncaissement
			WHERE docstatus = 1
			UNION
			SELECT site, societe, journal, date, devise, name, caisse, docstatus, designation, cours
			FROM tabDecaissement
			WHERE docstatus = 1
			) o 
		INNER JOIN `tabComptabilisation` d on o.name = d.parent
		WHERE o.date >= %(date_debut)s AND o.date <= %(date_fin)s AND o.caisse LIKE %(caisse)s
        """,{"date_debut": filters.date_debut, "date_fin": filters.date_fin, "currency":company_currency,"caisse": filters.caisse if filters.caisse else '%' }, as_dict = 1
    )

	#for entry in data:
	#	exchange_rate = get_exchange_rate(entry['devise'], company_currency, entry['date'])
	#	entry['exchange_rate'] = exchange_rate

	return data

