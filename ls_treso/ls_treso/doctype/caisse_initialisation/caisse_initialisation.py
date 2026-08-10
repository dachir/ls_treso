# Copyright (c) 2022, Kossivi Amouzou and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import now, getdate 
from ls_treso.ls_treso.utils.payment_utils import (
	ACTIVE_MODES,
	get_ls_treso_mode,
	make_internal_transfer_payment_entry,
)


class CaisseInitialisation(Document):

	def before_save(self):
		delta = 0
		solde = 0
		self.status = "Vert"
		solde_final = 0

		if  self.solde_plancher != None :
			solde = float(self.solde_plancher)
		
		if self.delta_plancher != None :
			delta = float(self.delta_plancher)
		
		if self.solde_final != None:
			solde_final = float(self.solde_final)

		if solde != 0.0 and  delta != 0.0 :
			min =  solde * (100 -  delta)

			if solde_final >= solde :
				self.status = "Vert"
			elif solde_final >= min and solde_final < solde :
				self.status = "Jaune"
			else:
				self.status = "Rouge"
		


	def validate(self):
		nb = frappe.db.count('Caisse Initialisation', {"docstatus": 0, "caisse": self.caisse, "name": ["!=", self.name]})
		#test = frappe.db.get_list('Caisse Initialisation', filters = {"docstatus": 0, "caisse": self.caisse}, fields=["name"])
		if nb > 0 :
			#frappe.msgprint(str(test))
			frappe.throw("Veuillez cloturer la journée précédente")

		nb = frappe.db.count('Caisse Initialisation', 
				{
					"date_initialisation": [">", self.date_initialisation], 
					"caisse": self.caisse, 
				}
			)
		if nb > 0 :
			frappe.throw("Une date ultérieure à la date choisie existe déjà. Veuillez choisir une date plus récente")

		#nb = frappe.db.sql(
		#	"""
		#	SELECT count(*) nb
		#	FROM `tabCaisse Initialisation`
		#	WHERE DATE(date_initialisation) = DATE(%s) AND caisse = %s 
		#	""", (self.date_initialisation,self.caisse),
		#	as_dict = 1
		#)[0].nb
		#if nb > 0:
		#	msg = ("Vous avez déjà initialisé la caisse <b>{}</b> pour la date du <b>{}</b>.").format(	self.caisse, getdate(self.date_initialisation))
		#	frappe.throw(msg)
	
	def before_submit(self):
		self.date_fermeture = getdate()
		operations = frappe.db.get_list("Encaissement", fields = ["name"], filters = {"docstatus": 0, 'initialisation': self.name})
		for o in operations:
			operation = frappe.get_doc("Encaissement", o.name)
			operation.submit()
				

		operations = frappe.db.get_list("Decaissement", fields = ["name"], filters = {"docstatus": 0, 'initialisation': self.name})
		for o in operations:
			operation = frappe.get_doc("Decaissement", o.name)
			operation.submit()
				

		self.recalcul2()

		billetage = frappe.db.get_value('Societe', self.societe , 'billetage')
		if billetage == 1 :
			if len(self.billetage) == 0:
				frappe.throw("Veuillez saisir le billetage")

			total = 0
			for b in self.billetage:
				total += b.valeur_finale
			if self.solde_final != total:
				frappe.throw("Le solde physique de la caisse est différent du solde final. Veuillez recompter!") 
		
		frappe.db.sql(
			"""
				UPDATE tabCaisse c 
				SET c.solde = %(solde)s, c.date_solde = %(date)s
				WHERE c.name = %(caisse)s
			""",{"caisse": self.caisse, "name": self.name, "solde": self.solde_final, "date": now()}, as_dict = 1
		)

	def on_cancel(self):
		nb = frappe.db.count('Caisse Initialisation', 
				{
					"date_initialisation": [">", self.date_initialisation], 
					"caisse": self.caisse, 
				}
			)
		if nb > 0 :
			frappe.throw("Vous ne pouvez annuler cette journée de caisse alors que des dates plus récentes existes. prière d'annuler d'abord les entrées plus récentes!")
		
		self.solde_final = float(self.solde_initial)

		frappe.db.sql(
			"""
				UPDATE tabCaisse c 
				SET c.solde = c.solde - %(solde)s, c.date_solde = (SELECT MAX(date_initialisation) as Date FROM `tabCaisse Initialisation` WHERE docstatus = 1 and caisse = %(caisse)s)
				WHERE c.name = %(caisse)s
			""",{"caisse": self.caisse, "name": self.name, "solde": self.solde_final}, as_dict = 1
		)

	def recalcul2(self):
		mt = frappe.db.sql(
		"""
			SELECT SUM(montant) AS montant
			FROM(
				SELECT SUM(montant) AS montant
				FROM `tabEncaissement`
				WHERE initialisation =  %(name)s AND docstatus = 1
				UNION
				SELECT -SUM(montant) AS montant
				FROM `tabDecaissement`
				WHERE initialisation =  %(name)s AND docstatus = 1
			) AS t
		""" , {"name": self.name}, as_dict = 1
		)[0].montant
		if mt == None:
			#frappe.throw("Il n'y a aucune opération valide sur cette journée!")
			self.solde_final = self.solde_initial
		else:
			if self.type_caisse == 'Caisse' and float(mt) < 0.0 and float(self.solde_initial) < float(abs(mt)) :
				frappe.throw("Vous avez une inconsistance dans les montants saisis, veuillez appeler l'administrateur!") 
			else:
				self.solde_final = float(self.solde_initial) + float(mt)

	@frappe.whitelist()
	def recalcul(self):
		if self.docstatus == 1:
			frappe.throw("Vous ne pouvez pas recalculer une journée déjà fermée!") 
		mt = frappe.db.sql(
		"""
			SELECT SUM(montant) AS montant
			FROM(
				SELECT SUM(montant) AS montant
				FROM `tabEncaissement`
				WHERE initialisation =  %(name)s AND docstatus = 1
				UNION
				SELECT -SUM(montant) AS montant
				FROM `tabDecaissement`
				WHERE initialisation =  %(name)s AND docstatus = 1
			) AS t
		""" , {"name": self.name}, as_dict = 1
		)[0].montant
		if mt == None:
			frappe.throw("Il n'y a aucune opération valide sur cette journée!")
		else:
			if self.type_caisse == 'Caisse' and float(mt) < 0.0 and float(self.solde_initial) < float(abs(mt)) :
				frappe.throw("Vous avez une inconsistance dans les montants saisis, veuillez appeler l'administrateur!") 
			else:
				self.solde_final = float(self.solde_initial) + float(mt)
				self.save()
				#frappe.db.sql(
				#"""
				#	UPDATE `tabCaisse Initialisation` SET solde_final = solde_initial + %(mt)s
				#	WHERE name = %(name)s AND docstatus = 0
				#""" , {"name": self.name, "mt": float(mt)}, as_dict = 1
				#)

	@frappe.whitelist()
	def cloture(self):
		operations = frappe.db.get_list('Encaissement',
			filters={
				'docstatus': 0,
				'initialisation': self.name
			},
			fields=['name'],
			order_by='name',
		)

		for o in operations:
			op_doc = frappe.get_doc('Encaissement', o.name)
			op_doc.submit()

		operations = frappe.db.get_list('Decaissement',
			filters={
				'docstatus': 0,
				'initialisation': self.name
			},
			fields=['name'],
			order_by='name',
		)

		for o in operations:
			op_doc = frappe.get_doc('Decaissement', o.name)
			op_doc.submit()

@frappe.whitelist()
def get_caisse_devise(caisse):
	return frappe.db.get_value('Caisse', caisse, 'devise')

@frappe.whitelist()
def get_solde_final(caisse, date):
	init = frappe.db.sql(
		"""
			SELECT solde_final
				FROM(
				SELECT max(date_initialisation) AS date_initialisation, caisse
				FROM `tabCaisse Initialisation`
				WHERE caisse = %s AND date_initialisation < %s
				GROUP BY caisse
			) d INNER JOIN `tabCaisse Initialisation` i ON i.date_initialisation = d.date_initialisation AND i.caisse = d.caisse
			WHERE i.docstatus = 1
		""", (caisse,date)
	)
	if len(init) > 0:
		return init[0][0]
	else:
		return 0


@frappe.whitelist()
def get_last_billetage(caisse, date):
	billetage = frappe.db.sql(
		"""
			SELECT b.nom, b.nombre_final, b.valeur_finale
				FROM(
				SELECT max(date_initialisation) AS date_initialisation, caisse
				FROM `tabCaisse Initialisation`
				WHERE caisse = %s AND date_initialisation < %s
				GROUP BY caisse
			) d INNER JOIN `tabCaisse Initialisation` i ON i.date_initialisation = d.date_initialisation  AND i.caisse = d.caisse
				INNER JOIN `tabBilletage` b ON b.parent = i.name
		""" , (caisse,date), as_dict = 1
	)
	return billetage
	#for b in billetage:
	#	for b2 in doc.billetage:
	#		if b.nom == b2.nom:
	#			b2.nombre_initial = b.nombre_final
	#			b2.valeur_initiale = b.valeur_finale

def transfert(caisse_de, caisse_a, montant, devise, caisse_doc, type_operation, skip_payment_entry=False, tiers=None):
	nature_doc = frappe.db.sql(
		"""
		SELECT name
		FROM `tabNature Operations`
		WHERE echange = 1 AND type_operation = %s
		""", (type_operation),
		as_dict = 1
	)

	if not nature_doc:
		frappe.throw("Veuillez configurer une Nature Operations avec Echange activé pour {}".format(type_operation))

	if len(caisse_doc) > 0:
		#if caisse_doc[0].solde_final >= montant :
		detail = {
			"nature_operations": nature_doc[0].name,
			"montant_devise": montant,
			"montant_devise_ref": montant,
		}
		if tiers:
			detail["tiers"] = tiers
			detail["type_tiers"] = "Employe"

		args = frappe._dict(
			{
				"doctype": type_operation,
				"caisse": caisse_de,
				"initialisation": caisse_doc[0].name,
				"designation": 'Envoi de fond' if type_operation == 'Decaissement' else 'Reception de fond',
				"date": caisse_doc[0].date_initialisation,
				"devise": devise,
				"montant": montant,
				"montant_reference": montant,
				"remettant": tiers or caisse_a,
				"details_operation_de_caisse": [detail],
			}
		)

		op_doc = frappe.get_doc(args)
		if skip_payment_entry:
			op_doc.flags.skip_ls_treso_payment_entry = True
		op_doc.submit()
		return op_doc

def _get_or_create_employee_caisse(source_caisse, tiers, date):
	tiers_doc = frappe.get_doc("Tiers", tiers)
	if tiers_doc.type != "SALARIE":
		frappe.throw("Le tiers sélectionné doit être de type SALARIE")

	source = frappe.get_doc("Caisse", source_caisse)
	caisse_name = frappe.db.get_value(
		"Caisse",
		{"tiers": tiers, "societe": source.societe, "devise": source.devise},
		"name",
	)

	if caisse_name:
		caisse = frappe.get_doc("Caisse", caisse_name)
	else:
		if not source.compte_comptable or not frappe.db.exists("Account", source.compte_comptable):
			frappe.throw("Veuillez renseigner un Account ERPNext valide sur la caisse source")

		source_account = frappe.get_doc("Account", source.compte_comptable)
		if not source_account.parent_account:
			frappe.throw("Le compte de la caisse source doit avoir un compte parent")

		account_name = "Caisse Salarié {} {}".format(tiers_doc.code or tiers_doc.name, source.devise or "")
		account = frappe.db.get_value(
			"Account",
			{"account_name": account_name, "company": source_account.company},
			"name",
		)
		if not account:
			account_doc = frappe.get_doc({
				"doctype": "Account",
				"account_name": account_name,
				"parent_account": source_account.parent_account,
				"company": source_account.company,
				"is_group": 0,
				"account_type": "Cash",
				"account_currency": source_account.account_currency,
			})
			account_doc.insert(ignore_permissions=True)
			account = account_doc.name

		code_caisse = None
		while not code_caisse or frappe.db.exists("Caisse", code_caisse):
			code_caisse = ("S" + frappe.generate_hash(length=4)).upper()

		caisse = frappe.get_doc({
			"doctype": "Caisse",
			"code_caisse": code_caisse,
			"designation": "Caisse - {} - {}".format(tiers_doc.intitule, source.devise or ""),
			"tiers": tiers,
			"societe": source.societe,
			"site": source.site,
			"code_journal": source.code_journal,
			"devise": source.devise,
			"compte_comptable": account,
			"type_caisse": "Caisse",
			"date_lancement": date,
			"cours": source.cours,
			"solde_initial": 0,
		})
		caisse.insert(ignore_permissions=True)
		caisse.submit()

	# A fictive employee cashbox must have an open cash day to receive the generated Encaissement.
	initialisation = frappe.db.get_value(
		"Caisse Initialisation",
		{"caisse": caisse.name, "docstatus": 0},
		"name",
	)
	if not initialisation:
		init = frappe.get_doc({
			"doctype": "Caisse Initialisation",
			"caisse": caisse.name,
			"initialisation": 1,
			"date_initialisation": date,
			"devise": caisse.devise,
			"cours": caisse.cours,
			"solde_initial": 0,
			"solde_final": 0,
			"societe": caisse.societe,
			"site": caisse.site,
		})
		init.insert(ignore_permissions=True)

	return caisse.name


@frappe.whitelist()
def save_operation(caisse_de, caisse_a=None, date=None, montant_de=0, montant_a=0, devise=None, tiers=None):
	#frappe.msgprint("1")
	try:
		if not caisse_a and not tiers:
			frappe.throw("Veuillez sélectionner une caisse destination ou un salarié")

		if tiers:
			caisse_a = _get_or_create_employee_caisse(caisse_de, tiers, date)
			montant_a = montant_de

		caisse_doc_de = frappe.db.sql(
			"""
			SELECT name, solde_final, DATE(date_initialisation) AS date_initialisation
			FROM `tabCaisse Initialisation`
			WHERE DATE(date_initialisation) = DATE(%s) AND caisse = %s AND docstatus = 0
			""", (date,caisse_de),
			as_dict = 1
		)

		caisse_doc_a = frappe.db.sql(
			"""
			SELECT name, solde_final, DATE(date_initialisation) AS date_initialisation
			FROM `tabCaisse Initialisation`
			WHERE DATE(date_initialisation) = DATE(%s) AND caisse = %s AND docstatus = 0
			""", (date,caisse_a),
			as_dict = 1
		)

		if len(caisse_doc_de) > 0 :
			if len(caisse_doc_a) > 0 :
				use_payment_entry = get_ls_treso_mode() in ACTIVE_MODES

				type_operation = 'Decaissement'
				decaissement = transfert(
					caisse_de, caisse_a, montant_de, devise, caisse_doc_de, type_operation, use_payment_entry, tiers
				)

				type_operation = 'Encaissement'
				encaissement = transfert(
					caisse_a, caisse_de, montant_a, devise, caisse_doc_a, type_operation, use_payment_entry, tiers
				)

				if use_payment_entry:
					make_internal_transfer_payment_entry(decaissement, encaissement)
			else:
				frappe.throw("Caisse Réceptrice non ouverte pour cette date")
		else:
			frappe.throw("Caisse Emétrice non ouverte pour cette date")
	except Exception as e:
		frappe.msgprint(str(e))
		frappe.db.rollback()

	

	