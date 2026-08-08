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

	def make_payment_entry(self):
		if flt(self.montant) <= 0:
			return

		caisse_account = frappe.db.get_value("Caisse", self.caisse, "compte_comptable")
		if not caisse_account:
			frappe.throw(_("Veuillez renseigner le compte comptable de la caisse {0}").format(self.caisse))

		details = {}
		advances = {}
		first_reference = None

		for row in self.advance_allocation or []:
			if row.payment_entry and row.document_type and row.invoice and flt(row.allocated_amount):
				key = (row.document_type, row.invoice)
				advances[key] = flt(advances.get(key)) + flt(row.allocated_amount)

		for row in self.details_operation_de_caisse:
			is_advance = frappe.db.get_value("Nature Operations", row.nature_operations, "is_advance")
			if row.document_type and row.invoice:
				if row.document_type not in ("Purchase Invoice", "Purchase Order"):
					frappe.throw(_("Ligne {0}: type de document {1} invalide").format(row.idx, row.document_type))
				key = (row.document_type, row.invoice)
				details[key] = flt(details.get(key)) + flt(row.montant_devise)
				if not first_reference:
					first_reference = key
			elif not is_advance:
				frappe.throw(_("Ligne {0}: un document est obligatoire pour une nature non avance").format(row.idx))

		for key in advances:
			if key not in details:
				frappe.throw(_("Le document {0} {1} utilisé dans les avances doit être présent dans les détails").format(key[0], key[1]))

		allocations = {}
		for key, amount in details.items():
			amount = flt(amount) - flt(advances.get(key))
			if amount < 0:
				frappe.throw(_("Les avances affectées à {0} {1} dépassent le montant du détail").format(key[0], key[1]))
			if amount:
				allocations[key] = amount

		if flt(sum(allocations.values()), 2) > flt(self.montant, 2):
			frappe.throw(_("Le montant affecté aux documents dépasse le montant de l'opération"))

		if first_reference:
			pe = get_payment_entry(
				first_reference[0],
				first_reference[1],
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
				frappe.throw(_("Une avance sans document doit avoir un Supplier ERPNext valide dans Tiers"))
			company = frappe.db.get_value("Account", caisse_account, "company")
			pe = frappe.new_doc("Payment Entry")
			pe.payment_type = "Pay"
			pe.company = company
			pe.party_type = "Supplier"
			pe.party = party
			pe.paid_from = caisse_account
			pe.paid_to = get_party_account("Supplier", party, company)
			pe.paid_amount = flt(self.montant_reference or self.montant)
			pe.received_amount = flt(self.montant)

		for key, amount in allocations.items():
			pe.append("references", {
				"reference_doctype": key[0],
				"reference_name": key[1],
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
		processed = set()
		for row in self.advance_allocation or []:
			if not row.payment_entry or not row.document_type or not row.invoice or flt(row.allocated_amount) <= 0:
				continue

			if row.document_type != "Purchase Invoice":
				frappe.throw(_("Les avances d'un decaissement doivent être rapprochées avec une Purchase Invoice"))

			key = (row.payment_entry, row.document_type, row.invoice)
			if key in processed:
				frappe.throw(_("Regroupez l'avance {0} et la facture {1} sur une seule ligne").format(row.payment_entry, row.invoice))
			processed.add(key)

			if frappe.db.exists("Payment Entry Reference", {
				"parent": row.payment_entry,
				"reference_doctype": row.document_type,
				"reference_name": row.invoice,
				"docstatus": 1,
			}):
				frappe.throw(_("L'avance {0} est déjà liée à {1}").format(row.payment_entry, row.invoice))

			invoice = frappe.get_doc(row.document_type, row.invoice)
			recon = frappe.new_doc("Payment Reconciliation")
			recon.company = invoice.company
			recon.party_type = "Supplier"
			recon.party = invoice.get("supplier")
			recon.receivable_payable_account = invoice.get("credit_to")
			recon.payment_name = row.payment_entry
			recon.invoice_name = row.invoice
			recon.get_unreconciled_entries()

			payment = next((d for d in recon.payments if d.reference_type == "Payment Entry" and d.reference_name == row.payment_entry), None)
			inv = next((d for d in recon.invoices if d.invoice_type == row.document_type and d.invoice_number == row.invoice), None)
			if not payment or not inv:
				frappe.throw(_("Impossible de rapprocher l'avance {0} avec {1}").format(row.payment_entry, row.invoice))

			row.available_amount = flt(payment.amount)
			amount = flt(row.allocated_amount)
			if amount > flt(payment.amount) or amount > flt(inv.outstanding_amount):
				frappe.throw(_("Le montant alloué dépasse le montant disponible ou le solde de la facture"))

			payment.unreconciled_amount = payment.amount
			exchange_map = recon.get_invoice_exchange_map([inv], [payment])
			inv.exchange_rate = exchange_map.get(inv.invoice_number)
			allocation = recon.get_allocated_entry(payment, inv, amount)
			allocation.difference_amount = recon.get_difference_amount(payment, inv, amount)
			allocation.difference_account = frappe.db.get_value("Company", invoice.company, "exchange_gain_loss_account")
			allocation.exchange_rate = inv.exchange_rate
			allocation.gain_loss_posting_date = self.date
			recon.append("allocation", allocation)
			recon.reconcile()

	def unreconcile_advances(self):
		if not self.advance_allocation:
			return

		from erpnext.accounts.doctype.unreconcile_payment.unreconcile_payment import create_unreconcile_doc_for_selection

		selections = []
		for row in self.advance_allocation:
			if row.payment_entry and row.document_type and row.invoice and flt(row.allocated_amount) > 0:
				company = frappe.db.get_value("Payment Entry", row.payment_entry, "company")
				selections.append({
					"company": company,
					"voucher_type": "Payment Entry",
					"voucher_no": row.payment_entry,
					"against_voucher_type": row.document_type,
					"against_voucher_no": row.invoice,
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
