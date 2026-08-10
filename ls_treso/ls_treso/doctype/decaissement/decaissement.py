# Copyright (c) 2023, Kossivi Amouzou and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate
from frappe.utils import flt
import json
from ls_treso.ls_treso.doctype.devise.devise import get_cours
from erpnext.setup.utils import get_exchange_rate
from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry
from erpnext.accounts.party import get_party_account

class Decaissement(Document):
	
	def validate(self):
		self.date = frappe.utils.getdate(self.date)
		self.validate_nature()

	def before_save(self):
		if len(self.details_operation_de_caisse) == 0:
			frappe.throw("Veuillez saisir au moins une ligne détail")

		exist = frappe.db.exists({
			"doctype": "Caisse Initialisation", 
			"name": self.initialisation,
			"docstatus": 1
		})
		if exist:
			frappe.throw("Vous ne pouvez enregistrer d'opérations sur le Numéro: " + self.initialisation + ". Veuillez choisir un numéro valide!")
		
		date_init = getdate(frappe.db.get_value('Caisse Initialisation', self.initialisation, 'date_initialisation'))
		date_split = str(date_init).split(":")[0]
		if date_split != str(self.date):
			frappe.throw("La date de saisie " + str(self.date) + " doit être conforme à la date d'initialisation " + date_split)

	def after_save(self):
		for d in self.details_operation_de_caisse:
			if d.demande_paiement :
				frappe.db.sql(
					"""
						UPDATE `tabDemande Paiement` 
						SET positione = 1
						WHERE name = %(name)s
					""",{ "name": d.demande_paiement }, as_dict = 1
				)

	def after_insert(self):
		for d in self.details_operation_de_caisse:
			if d.demande_paiement :
				frappe.db.sql(
					"""
						UPDATE `tabDemande Paiement` 
						SET positione = 1
						WHERE name = %(name)s
					""",{ "name": d.demande_paiement }, as_dict = 1
				)

	@frappe.whitelist()
	def update_demande(self, demande_paiement, type):
		frappe.db.update("Demande Paiement", demande_paiement, "positione", 0 if type == "remove" else 1)
				

	
	def before_submit(self):
		mode = frappe.db.get_single_value("LS Treso Settings", "operating_mode")

		if self.type_caisse == 'Caisse':
			init_doc = frappe.get_doc("Caisse Initialisation", self.initialisation)
			if float(init_doc.solde_final) < float(self.montant):
				frappe.throw("Le montant actuellement en caisse ne permet pas de faire cette opération.\n Il faut augmenter le solde!!!")

		if mode in ("Standalone", "ERPNext Integrated", "External Export"):
			self.set_operation_totals()
			self.make_payment_entry()
			self.reconcile_advances()
		else:
			total = 0.00
			for details in self.details_operation_de_caisse:
				total += float(details.montant_devise_ref)

			if float(total) != float(self.montant_reference):
				frappe.throw("Le montant saisie en entête de l'opération " + str(self.montant_reference) + " est différent du total des montants en détails " + str(total))

			self.generate_journal_entry()
			if self.comptabilite_erpnext == 1:
				self.make_accrual_jv_entry()
	def on_submit(self):
		init_doc = frappe.get_doc("Caisse Initialisation", self.initialisation)
		init_doc.solde_final -= float(self.montant_reference)
		init_doc.save()
		

	def on_cancel(self):
		init_doc = frappe.get_doc("Caisse Initialisation", self.initialisation)
		#if init_doc.docstatus == 0:
		init_doc.solde_final += float(self.montant_reference)
		init_doc.save()
		for d in self.details_operation_de_caisse:
			if d.demande_paiement :
				frappe.db.sql(
					"""
						UPDATE `tabDemande Paiement` 
						SET positione = 0
						WHERE name = %(name)s
					""",{ "name": d.demande_paiement }, as_dict = 1
				)
		
		self.comptabilisation.clear()


		mode = frappe.db.get_single_value("LS Treso Settings", "operating_mode")
		if mode in ("Standalone", "ERPNext Integrated", "External Export"):
			self.unreconcile_advances()
			self.cancel_payment_entry()
		elif self.comptabilite_erpnext == 1:
			nb = frappe.db.count("Journal Entry", {"cheque_no": self.name})
			if nb > 0:
				jv = frappe.get_doc("Journal Entry", {"cheque_no": self.name})
				jv.cancel()
	def after_delete(self):
		for d in self.details_operation_de_caisse:
			if d.demande_paiement :
				frappe.db.sql(
					"""
						UPDATE `tabDemande Paiement` 
						SET positione = 0
						WHERE name = %(name)s
					""",{ "name": d.demande_paiement }, as_dict = 1
				)

	def create_row(self, type, account, cours, amount, type_tiers=None, tiers=None, cc1=None, cc2=None, cc3=None, cc4=None, cc5=None, cc6=None, cc7=None, cc8=None, cc9=None, cc10=None):
		row = {}
		company_currency = frappe.db.get_value("Societe",self.societe,"devise_de_base") 
		#devise_compte = self.get_account("Account",account,"account_currency")
		ex_rate = flt(get_cours(self.devise, company_currency)[0].cours)
		
		if type == 'Encaissement':
			row = {
				"account": account,
				"exchange_rate": ex_rate,
				#"reference_type": self.doctype,
				#"reference_name": self.name,
				"debit_in_account_currency": amount * cours,
				"reference_currency": self.devise,
				"reference_rate": cours,
				"reference_amount": amount,
			}
		else:
			row = {
				"account": account,
				"exchange_rate": ex_rate,
				#"reference_type": self.doctype,
				#"reference_name": self.name,
				"credit_in_account_currency": amount * cours,
				"reference_currency": self.devise,
				"reference_rate": cours,
				"reference_amount": amount,
			}

		if tiers:
			if not type_tiers :
				frappe.throw(_("Veuillez renseigner le type tiers du tiers {}").format(tiers))

			row.update(
				{
					"party_type": "Employee" if type_tiers == "Employe" else ("Customer"if type_tiers == "Client" else "Supplier"),
					"party": tiers,
				}
			)

		main = frappe.db.get_value('Cost Center',  {'name': ['like', '%Main%'],'company': self.societe}, 'name')
		if main :
			row.update(
				{
					'cost_center': main,
				}
			)

		if cc1:
			correspondance = frappe.db.get_value('Axe Analytique',  {'type': 'Axe 1'}, 'correspondance')
			analytique = frappe.db.sql(
				'''
					SELECT fieldname
					FROM tabDocField
					WHERE parent = 'Journal Entry Account' and label = %(correspondance)s
					UNION
					SELECT fieldname
					FROM `tabCustom Field`
					WHERE dt = 'Journal Entry Account' and label = %(correspondance)s
				''', {'correspondance': correspondance}, as_dict = 1
			)
			if len(analytique) == 0 :
				frappe.throw(_("La nature analytique {} n'a pas de correspondance dans les dimensions comptables").format(analytique))
			row.update(
				{
					analytique[0].fieldname: cc1,
				}
			)
		if cc2:
			correspondance = frappe.db.get_value('Axe Analytique',  {'type': 'Axe 2'}, 'correspondance')
			analytique = frappe.db.sql(
				'''
					SELECT fieldname
					FROM tabDocField
					WHERE parent = 'Journal Entry Account' and label = %(correspondance)s
					UNION
					SELECT fieldname
					FROM `tabCustom Field`
					WHERE dt = 'Journal Entry Account' and label = %(correspondance)s
				''', {'correspondance': correspondance}, as_dict = 1
			)
			if len(analytique) == 0 :
				frappe.throw(_("La nature analytique {} n'a pas de correspondance dans les dimensions comptables").format(analytique))
			row.update(
				{
					analytique[0].fieldname: cc2,
				}
			)
		if cc3:
			correspondance = frappe.db.get_value('Axe Analytique',  {'type': 'Axe 3'}, 'correspondance')
			analytique = frappe.db.sql(
				'''
					SELECT fieldname
					FROM tabDocField
					WHERE parent = 'Journal Entry Account' and label = %(correspondance)s
					UNION
					SELECT fieldname
					FROM `tabCustom Field`
					WHERE dt = 'Journal Entry Account' and label = %(correspondance)s
				''', {'correspondance': correspondance}, as_dict = 1
			)
			if len(analytique) == 0 :
				frappe.throw(_("La nature analytique {} n'a pas de correspondance dans les dimensions comptables").format(analytique))
			row.update(
				{
					analytique[0].fieldname: cc3,
				}
			)
		if cc4:
			correspondance = frappe.db.get_value('Axe Analytique',  {'type': 'Axe 4'}, 'correspondance')
			analytique = frappe.db.sql(
				'''
					SELECT fieldname
					FROM tabDocField
					WHERE parent = 'Journal Entry Account' and label = %(correspondance)s
					UNION
					SELECT fieldname
					FROM `tabCustom Field`
					WHERE dt = 'Journal Entry Account' and label = %(correspondance)s
				''', {'correspondance': correspondance}, as_dict = 1
			)
			if len(analytique) == 0 :
				frappe.throw(_("La nature analytique {} n'a pas de correspondance dans les dimensions comptables").format(analytique))
			row.update(
				{
					analytique[0].fieldname: cc4,
				}
			)
		if cc5:
			correspondance = frappe.db.get_value('Axe Analytique',  {'type': 'Axe 5'}, 'correspondance')
			analytique = frappe.db.sql(
				'''
					SELECT fieldname
					FROM tabDocField
					WHERE parent = 'Journal Entry Account' and label = %(correspondance)s
					UNION
					SELECT fieldname
					FROM `tabCustom Field`
					WHERE dt = 'Journal Entry Account' and label = %(correspondance)s
				''', {'correspondance': correspondance}, as_dict = 1
			)
			if len(analytique) == 0 :
				frappe.throw(_("La nature analytique {} n'a pas de correspondance dans les dimensions comptables").format(analytique))
			row.update(
				{
					analytique[0].fieldname: cc5,
				}
			)

		if cc6:
			correspondance = frappe.db.get_value('Axe Analytique',  {'type': 'Axe 6'}, 'correspondance')
			analytique = frappe.db.sql(
				'''
					SELECT fieldname
					FROM tabDocField
					WHERE parent = 'Journal Entry Account' and label = %(correspondance)s
					UNION
					SELECT fieldname
					FROM `tabCustom Field`
					WHERE dt = 'Journal Entry Account' and label = %(correspondance)s
				''', {'correspondance': correspondance}, as_dict = 1
			)
			if len(analytique) == 0 :
				frappe.throw(_("La nature analytique {} n'a pas de correspondance dans les dimensions comptables").format(analytique))
			row.update(
				{
					analytique[0].fieldname: cc6,
				}
			)
		if cc7:
			correspondance = frappe.db.get_value('Axe Analytique',  {'type': 'Axe 7'}, 'correspondance')
			analytique = frappe.db.sql(
				'''
					SELECT fieldname
					FROM tabDocField
					WHERE parent = 'Journal Entry Account' and label = %(correspondance)s
					UNION
					SELECT fieldname
					FROM `tabCustom Field`
					WHERE dt = 'Journal Entry Account' and label = %(correspondance)s
				''', {'correspondance': correspondance}, as_dict = 1
			)
			if len(analytique) == 0 :
				frappe.throw(_("La nature analytique {} n'a pas de correspondance dans les dimensions comptables").format(analytique))
			row.update(
				{
					analytique[0].fieldname: cc7,
				}
			)
		if cc8:
			correspondance = frappe.db.get_value('Axe Analytique',  {'type': 'Axe 8'}, 'correspondance')
			analytique = frappe.db.sql(
				'''
					SELECT fieldname
					FROM tabDocField
					WHERE parent = 'Journal Entry Account' and label = %(correspondance)s
					UNION
					SELECT fieldname
					FROM `tabCustom Field`
					WHERE dt = 'Journal Entry Account' and label = %(correspondance)s
				''', {'correspondance': correspondance}, as_dict = 1
			)
			if len(analytique) == 0 :
				frappe.throw(_("La nature analytique {} n'a pas de correspondance dans les dimensions comptables").format(analytique))
			row.update(
				{
					analytique[0].fieldname: cc8,
				}
			)
		if cc9:
			correspondance = frappe.db.get_value('Axe Analytique',  {'type': 'Axe 9'}, 'correspondance')
			analytique = frappe.db.sql(
				'''
					SELECT fieldname
					FROM tabDocField
					WHERE parent = 'Journal Entry Account' and label = %(correspondance)s
					UNION
					SELECT fieldname
					FROM `tabCustom Field`
					WHERE dt = 'Journal Entry Account' and label = %(correspondance)s
				''', {'correspondance': correspondance}, as_dict = 1
			)
			if len(analytique) == 0 :
				frappe.throw(_("La nature analytique {} n'a pas de correspondance dans les dimensions comptables").format(analytique))
			row.update(
				{
					analytique[0].fieldname: cc9,
				}
			)
		if cc10:
			correspondance = frappe.db.get_value('Axe Analytique',  {'type': 'Axe 10'}, 'correspondance')
			analytique = frappe.db.sql(
				'''
					SELECT fieldname
					FROM tabDocField
					WHERE parent = 'Journal Entry Account' and label = %(correspondance)s
					UNION
					SELECT fieldname
					FROM `tabCustom Field`
					WHERE dt = 'Journal Entry Account' and label = %(correspondance)s
				''', {'correspondance': correspondance}, as_dict = 1
			)
			if len(analytique) == 0 :
				frappe.throw(_("La nature analytique {} n'a pas de correspondance dans les dimensions comptables").format(analytique))
			row.update(
				{
					analytique[0].fieldname: cc10,
				}
			)			

		return row
	

	def create_row2(self, type, compte, cours, montant, tiers=None, cc1=None, cc2=None, cc3=None, cc4=None, cc5=None, cc6=None, cc7=None, cc8=None, cc9=None, cc10=None):
		row = {
				"compte": compte,
				"cours": cours,
				"montant": montant,
				"sens": 'Debit' if type == 'Encaissement' else 'Credit'
			}
		
		
		
		if tiers:
			type_tiers = frappe.db.get_value("Tiers", tiers, "type")
			row.update(
				{
					"type": type_tiers,
					"tiers": tiers,
				}
			)

		if cc1:
			row.update(
				{
					"compte_analytique": cc1,
				}
			)
		if cc2:
			row.update(
				{
					"compte_analytique_2": cc2,
				}
			)
		if cc3:
			row.update(
				{
					"compte_analytique_3": cc3,
				}
			)
		if cc4:
			row.update(
				{
					"compte_analytique_4": cc4,
				}
			)
		if cc5:
			row.update(
				{
					"compte_analytique_5": cc5,
				}
			)

		if cc6:
			row.update(
				{
					"compte_analytique_6": cc6,
				}
			)
		if cc7:
			row.update(
				{
					"compte_analytique_7": cc7,
				}
			)
		if cc8:
			row.update(
				{
					"compte_analytique_8": cc8,
				}
			)
		if cc9:
			row.update(
				{
					"compte_analytique_9": cc9,
				}
			)
		if cc10:
			row.update(
				{
					"compte_analytique_10": cc10,
				}
			)

		#frappe.msgprint(str(row))				

		return frappe._dict(row)
	
	def get_account(self, doctype, docname, champ):
		code = frappe.db.get_value(doctype,docname,champ)
		#id = frappe.db.get_list("Account",fields=['name'],filters={"account_number": code}) 
		return code
	
	def set_operation_totals(self):
		advance_total = sum(flt(d.allocated_amount) for d in (self.advance_allocation or []))
		self.montant_avances_utilisees = advance_total
		self.montant_total_operation = flt(self.montant) + advance_total

		detail_total = sum(flt(d.montant_devise) for d in self.details_operation_de_caisse)
		if flt(detail_total, 2) != flt(self.montant_total_operation, 2):
			frappe.throw(_("Le total des détails {0} doit être égal au montant total de l'opération {1}").format(detail_total, self.montant_total_operation))

	def get_invoice_rows(self, validate_outstanding=True):
		amounts = {}
		order = []
		new_advance_amount = 0

		for row in self.details_operation_de_caisse:
			is_advance = frappe.db.get_value("Nature Operations", row.nature_operations, "is_advance")
			if is_advance:
				if row.invoice:
					frappe.throw(_("Ligne {0}: une nature Avance ne peut pas être liée à une facture").format(row.idx))
				new_advance_amount += flt(row.montant_devise)
				continue

			if row.document_type != "Purchase Invoice":
				frappe.throw(_("Ligne {0}: un décaissement ne peut contenir que des Purchase Invoices").format(row.idx))
			if not row.invoice:
				frappe.throw(_("Ligne {0}: veuillez sélectionner une Purchase Invoice").format(row.idx))

			if row.invoice not in amounts:
				amounts[row.invoice] = 0
				order.append(row.invoice)
			amounts[row.invoice] += flt(row.montant_devise)

		invoice_rows = []
		party = None
		company = None
		for name in order:
			invoice = frappe.get_doc("Purchase Invoice", name)
			if invoice.docstatus != 1:
				frappe.throw(_("La Purchase Invoice {0} doit être soumise").format(name))
			if party and invoice.supplier != party:
				frappe.throw(_("Toutes les Purchase Invoices doivent appartenir au même fournisseur"))
			if company and invoice.company != company:
				frappe.throw(_("Toutes les Purchase Invoices doivent appartenir à la même société"))
			party = party or invoice.supplier
			company = company or invoice.company
			if validate_outstanding and flt(amounts[name], 2) > flt(invoice.outstanding_amount, 2):
				frappe.throw(_("Le montant {0} dépasse le solde disponible de la Purchase Invoice {1} ({2})").format(amounts[name], name, invoice.outstanding_amount))
			invoice_rows.append(frappe._dict({"name": name, "amount": amounts[name], "invoice": invoice}))

		return invoice_rows, new_advance_amount, party, company

	def get_advance_distribution(self, invoice_rows, party=None, company=None, check_available=True):
		remaining_by_invoice = {d.name: flt(d.amount) for d in invoice_rows}
		distribution = []
		seen = set()

		for row in self.advance_allocation or []:
			if not row.payment_entry or flt(row.allocated_amount) <= 0:
				continue
			if row.payment_entry in seen:
				frappe.throw(_("Le Payment Entry {0} ne doit apparaître qu'une seule fois dans les avances").format(row.payment_entry))
			seen.add(row.payment_entry)

			payment = frappe.get_doc("Payment Entry", row.payment_entry)
			if payment.docstatus != 1 or payment.payment_type != "Pay" or payment.party_type != "Supplier":
				frappe.throw(_("Le Payment Entry {0} n'est pas une avance fournisseur valide").format(row.payment_entry))
			if party and payment.party != party:
				frappe.throw(_("Le Payment Entry {0} appartient au fournisseur {1} et non à {2}").format(row.payment_entry, payment.party, party))
			if company and payment.company != company:
				frappe.throw(_("Le Payment Entry {0} appartient à une autre société").format(row.payment_entry))

			available = flt(payment.unallocated_amount)
			row.available_amount = available
			if check_available and flt(row.allocated_amount, 2) > flt(available, 2):
				frappe.throw(_("Le montant alloué sur {0} dépasse le montant disponible {1}").format(row.payment_entry, available))

			remaining = flt(row.allocated_amount)
			allocations = []
			for invoice in invoice_rows:
				if remaining <= 0:
					break
				available_on_invoice = flt(remaining_by_invoice.get(invoice.name))
				if available_on_invoice <= 0:
					continue
				amount = min(remaining, available_on_invoice)
				allocations.append(frappe._dict({"invoice": invoice.name, "amount": amount}))
				remaining_by_invoice[invoice.name] = available_on_invoice - amount
				remaining -= amount

			if flt(remaining, 2) > 0:
				frappe.throw(_("Le montant d'avance sélectionné dépasse le montant des Purchase Invoices à régler"))
			distribution.append(frappe._dict({"row": row, "payment": payment, "allocations": allocations}))

		return distribution

	def make_payment_entry(self):
		if flt(self.montant) <= 0:
			return

		caisse_account = frappe.db.get_value("Caisse", self.caisse, "compte_comptable")
		if not caisse_account:
			frappe.throw(_("Veuillez renseigner le compte comptable de la caisse {0}").format(self.caisse))

		invoice_rows, new_advance_amount, party, company = self.get_invoice_rows()
		distribution = self.get_advance_distribution(invoice_rows, party, company)
		advance_by_invoice = {}
		for advance in distribution:
			for allocation in advance.allocations:
				advance_by_invoice[allocation.invoice] = flt(advance_by_invoice.get(allocation.invoice)) + flt(allocation.amount)

		allocations = {}
		for invoice in invoice_rows:
			amount = flt(invoice.amount) - flt(advance_by_invoice.get(invoice.name))
			if amount > 0:
				allocations[invoice.name] = amount

		current_allocated = sum(allocations.values())
		if flt(current_allocated + new_advance_amount, 2) != flt(self.montant, 2):
			frappe.throw(_("Le montant courant ne correspond pas aux factures et aux nouvelles avances"))

		if invoice_rows:
			first_invoice = invoice_rows[0].name
			pe = get_payment_entry(
				"Purchase Invoice",
				first_invoice,
				party_amount=flt(self.montant),
				bank_account=caisse_account,
				bank_amount=flt(self.montant_reference or self.montant),
				payment_type="Pay",
				reference_date=self.date,
			)
			pe.set("references", [])
		else:
			party = next((d.tiers for d in self.details_operation_de_caisse if d.tiers), None)
			if not party or not frappe.db.exists("Supplier", party):
				frappe.throw(_("Une avance sans facture doit avoir un Supplier ERPNext valide dans Tiers"))
			company = frappe.db.get_value("Account", caisse_account, "company")
			pe = frappe.new_doc("Payment Entry")
			pe.payment_type = "Pay"
			pe.company = company
			pe.party_type = "Supplier"
			pe.party = party
			pe.paid_from = caisse_account
			pe.paid_to = get_party_account("Supplier", party, company)

		for invoice_name, amount in allocations.items():
			pe.append("references", {
				"reference_doctype": "Purchase Invoice",
				"reference_name": invoice_name,
				"allocated_amount": amount,
			})

		pe.posting_date = self.date
		pe.reference_no = self.name
		pe.reference_date = self.date
		pe.remarks = _("{0} LS Tréso {1}").format(self.doctype, self.name)
		pe.paid_amount = flt(self.montant_reference or self.montant)
		pe.received_amount = flt(self.montant)
		pe.submit()

	def reconcile_advances(self):
		invoice_rows, _, party, company = self.get_invoice_rows(validate_outstanding=False)
		distribution = self.get_advance_distribution(invoice_rows, party, company)

		for advance in distribution:
			if not advance.allocations:
				continue

			payment_doc = advance.payment
			recon = frappe.new_doc("Payment Reconciliation")
			recon.company = payment_doc.company
			recon.party_type = "Supplier"
			recon.party = payment_doc.party
			recon.receivable_payable_account = payment_doc.paid_to
			recon.payment_name = payment_doc.name
			recon.get_unreconciled_entries()

			payment = next((d for d in recon.payments if d.reference_type == "Payment Entry" and d.reference_name == payment_doc.name), None)
			if not payment:
				frappe.throw(_("Le Payment Entry {0} n'a plus de montant disponible").format(payment_doc.name))

			allocated_total = sum(flt(d.amount) for d in advance.allocations)
			if flt(allocated_total, 2) > flt(payment.amount, 2):
				frappe.throw(_("Le montant alloué dépasse le montant disponible du Payment Entry {0}").format(payment_doc.name))

			payment.unreconciled_amount = flt(payment.amount)
			exchange_map = recon.get_invoice_exchange_map(recon.invoices, [payment])
			remaining_payment = flt(payment.amount)

			for allocation in advance.allocations:
				if frappe.db.exists("Payment Entry Reference", {
					"parent": payment_doc.name,
					"reference_doctype": "Purchase Invoice",
					"reference_name": allocation.invoice,
					"docstatus": 1,
				}):
					frappe.throw(_("Le Payment Entry {0} est déjà rapproché avec {1}").format(payment_doc.name, allocation.invoice))

				invoice = next((d for d in recon.invoices if d.invoice_type == "Purchase Invoice" and d.invoice_number == allocation.invoice), None)
				if not invoice or flt(allocation.amount, 2) > flt(invoice.outstanding_amount, 2):
					frappe.throw(_("Impossible de rapprocher {0} avec la Purchase Invoice {1}").format(payment_doc.name, allocation.invoice))

				payment.amount = remaining_payment
				invoice.exchange_rate = exchange_map.get(invoice.invoice_number)
				row = recon.get_allocated_entry(payment, invoice, flt(allocation.amount))
				row.unreconciled_amount = payment.unreconciled_amount
				row.difference_amount = recon.get_difference_amount(payment, invoice, flt(allocation.amount))
				row.difference_account = frappe.db.get_value("Company", payment_doc.company, "exchange_gain_loss_account")
				row.exchange_rate = invoice.exchange_rate
				row.gain_loss_posting_date = self.date
				recon.append("allocation", row)
				remaining_payment -= flt(allocation.amount)

			recon.reconcile()

	def unreconcile_advances(self):
		if not self.advance_allocation:
			return

		from erpnext.accounts.doctype.unreconcile_payment.unreconcile_payment import create_unreconcile_doc_for_selection

		invoice_rows, _, party, company = self.get_invoice_rows(validate_outstanding=False)
		distribution = self.get_advance_distribution(invoice_rows, party, company, check_available=False)
		selections = []
		for advance in distribution:
			for allocation in advance.allocations:
				selections.append({
					"company": advance.payment.company,
					"voucher_type": "Payment Entry",
					"voucher_no": advance.payment.name,
					"against_voucher_type": "Purchase Invoice",
					"against_voucher_no": allocation.invoice,
				})

		if selections:
			create_unreconcile_doc_for_selection(json.dumps(selections))

	def cancel_payment_entry(self):
		name = frappe.db.get_value("Payment Entry", {"reference_no": self.name, "docstatus": 1}, "name")
		if name:
			frappe.get_doc("Payment Entry", name).cancel()

	def make_accrual_jv_entry(self):
		precision = frappe.get_precision("Journal Entry Account", "debit_in_account_currency")
		journal_entry = frappe.new_doc("Journal Entry")
		journal_entry.voucher_type = "Journal Entry"
		if self.commentaire : 
			journal_entry.user_remark = self.commentaire
		else :
			journal_entry.user_remark = _("Journal de la caisse {0} pour la journée de {1}").format(self.caisse, self.date)
		journal_entry.company = self.societe #todo
		journal_entry.posting_date = self.date
		journal_entry.cheque_no = self.name
		journal_entry.cheque_date = self.date
		accounts = []
		currencies = set()
		payable_amount = 0
		multi_currency = 0
		company_currency = frappe.db.get_value("Societe",self.societe,"devise_de_base") 
		currencies.add(company_currency)
		caisse_account = self.get_account("Caisse",self.caisse,"compte_comptable")
		account_currency = self.get_account("Account",caisse_account,"account_currency")
		cours = flt(get_cours(self.devise, account_currency)[0].cours)
		currencies.add(self.devise)

		amount = flt(self.montant_reference, precision)
		payable_amount -= amount * cours
		accounting_entry = self.create_row('Decaissement',caisse_account,cours,amount)
		accounts.append(accounting_entry)

		for e in self.details_operation_de_caisse:
			amount = flt(e.montant_devise, precision)
			payable_amount += amount * cours
			account = self.get_account("Nature Operations",e.nature_operations,"compte_comptable")
			devise = self.get_account("Account",account,"account_currency")
			cours = flt(get_cours(self.devise, devise)[0].cours)
			currencies.add(devise)

			tiers = e.tiers
			cc1 = e.imputation_analytique
			cc2 = e.imputation_analytique_2
			cc3 = e.imputation_analytique_3
			cc4 = e.imputation_analytique_4
			cc5 = e.imputation_analytique_5

			cc6 = e.imputation_analytique_6
			cc7 = e.imputation_analytique_7
			cc8 = e.imputation_analytique_8
			cc9 = e.imputation_analytique_9
			cc10 = e.imputation_analytique_10
			accounting_entry = self.create_row('Encaissement',account,cours,amount,e.type_tiers,tiers,cc1,cc2,cc3,cc4,cc5,cc6,cc7,cc8,cc9,cc10)
			accounts.append(accounting_entry)

		if flt(payable_amount, precision) != 0 :
			round_off_account = self.get_account("Company", self.societe,"round_off_account")
			devise = self.get_account("Account",round_off_account,"account_currency")
			cours = flt(get_cours(self.devise, devise)[0].cours)
			accounting_entry = self.create_row('Decaissement',round_off_account,cours,payable_amount)
			accounts.append(accounting_entry)
		
		if len(currencies) > 1:
			multi_currency = 1
			
		journal_entry.multi_currency = multi_currency
		journal_entry.title = caisse_account
		journal_entry.set("accounts", accounts)
		journal_entry.submit()

	def generate_journal_entry(self):
		company_currency = frappe.db.get_value("Societe",self.societe,"devise_de_base") 
		caisse_account = frappe.db.get_value("Caisse",self.caisse,"compte_comptable")
		cours = get_cours(self.devise, company_currency)[0].cours
		payable_amount = 0

		amount = flt(self.montant_reference, 2)
		payable_amount -= amount
		accounting_entry = self.create_row2('Decaissement',caisse_account,cours,amount)
		#frappe.msgprint(str(accounting_entry))
		self.append('comptabilisation', accounting_entry)

		for e in self.details_operation_de_caisse:
			amount = flt(e.montant_devise_ref, 2)
			payable_amount += amount
			#frappe.msgprint(str(e.imputation_analytique))
			account = frappe.db.get_value("Nature Operations",e.nature_operations,"compte_comptable")
			tiers = e.tiers
			cc1 = e.imputation_analytique
			cc2 = e.imputation_analytique_2
			cc3 = e.imputation_analytique_3
			cc4 = e.imputation_analytique_4
			cc5 = e.imputation_analytique_5

			cc6 = e.imputation_analytique_6
			cc7 = e.imputation_analytique_7
			cc8 = e.imputation_analytique_8
			cc9 = e.imputation_analytique_9
			cc10 = e.imputation_analytique_10
			accounting_entry = self.create_row2('Encaissement',account,cours,amount,tiers,cc1,cc2,cc3,cc4,cc5,cc6,cc7,cc8,cc9,cc10)
			#accounting_entry = self.create_row2('Encaissement',account,cours,amount,tiers,cc1,cc2,cc3,cc4,cc5)
			self.append('comptabilisation', accounting_entry)
		
		#frappe.msgprint(str(self.comptabilisation))

		if flt(payable_amount, 2) != 0 :
			compte__arrondi = frappe.db.get_value("Societe",self.societe,"compte__arrondi")
			accounting_entry = self.create_row2('Decaissement',compte__arrondi,cours,payable_amount)
			self.append('comptabilisation', accounting_entry)

	def validate_nature(self):
		for d in self.get("details_operation_de_caisse"):
			justifiable = frappe.db.get_value("Nature Operations", d.nature_operations, "justifiable")
			if justifiable == "Oui":
				if not (d.imputation_analytique):
					frappe.throw(_("Ligne {0}: Veuillez renseigner la nature analytique").format(d.idx))

			tiers = frappe.db.get_value("Nature Operations", d.nature_operations, "tiers")
			if tiers == "Oui":
				if not (d.tiers):
					frappe.throw(_("Ligne {0}: Veuillez renseigner le tiers").format(d.idx))

@frappe.whitelist()
def get_demande_paiement(name = None, designation = None, remettant = None, montant = None, devise = None, filters = None):
	#frappe.msgprint(montant)
	return frappe.db.sql(
			"""
			SELECT name, site,designation, remettant,montant,devise
			FROM `tabDemande Paiement`
			WHERE docstatus = 1 AND site = %(site)s AND name  LIKE %(name)s AND designation LIKE %(designation)s AND remettant  LIKE %(remettant)s AND montant = %(montant)s AND devise LIKE %(devise)s
			""", {
					"site": filters.get("site"),
					"name": f"%{name}%",
					"designation": f"%{designation}%",
					"remettant": f"%{remettant}%",
					"montant": montant if montant !=  None else "%",
					"devise": f"%{devise}%",
				}
			, as_dict = 1
		)


#if filters.caisse !=  None else "%"
