// Copyright (c) 2023, Kossivi Amouzou and contributors
// For license information, please see license.txt

function update_advance_totals(frm) {
	let advance_total = 0;
	(frm.doc.advance_allocation || []).forEach(row => {
		advance_total += flt(row.allocated_amount);
	});
	frm.set_value("montant_avances_utilisees", advance_total);
	frm.set_value("montant_total_operation", flt(frm.doc.montant) + advance_total);
}

function load_advance_payment(frm, cdt, cdn) {
	let row = locals[cdt][cdn];
	if (!row.payment_entry) {
		frappe.model.set_value(cdt, cdn, "available_amount", 0);
		frappe.model.set_value(cdt, cdn, "invoices", "");
		update_advance_totals(frm);
		return;
	}

	frappe.db.get_doc("Payment Entry", row.payment_entry).then(payment => {
		if (payment.docstatus !== 1 || payment.payment_type !== "Receive" || payment.party_type !== "Customer" || flt(payment.unallocated_amount) <= 0) {
			frappe.msgprint(__("Ce Payment Entry n'est pas une avance valide pour cette opération."));
			frappe.model.set_value(cdt, cdn, "payment_entry", "");
			return;
		}

		let references = (payment.references || [])
			.filter(ref => ref.reference_doctype === "Sales Invoice" && flt(ref.allocated_amount) > 0)
			.map(ref => `{${ref.reference_name}:${flt(ref.allocated_amount)}}`);

		frappe.model.set_value(cdt, cdn, "available_amount", flt(payment.unallocated_amount));
		frappe.model.set_value(cdt, cdn, "invoices", references.length ? `[${references.join("; ")}]` : "[]");

		if (flt(row.allocated_amount) > flt(payment.unallocated_amount)) {
			frappe.model.set_value(cdt, cdn, "allocated_amount", 0);
		}
		update_advance_totals(frm);
	});
}


function load_invoice_details(frm, cdt, cdn) {
	let row = locals[cdt][cdn];
	if (!row.invoice) return;

	frappe.call({
		method: "ls_treso.ls_treso.utils.payment_utils.get_invoice_details",
		args: {
			document_type: row.document_type,
			invoice: row.invoice,
			societe: frm.doc.societe
		},
		callback: function(r) {
			if (!r.message) return;

			frappe.model.set_value(cdt, cdn, "type_tiers", r.message.type_tiers || "");
			frappe.model.set_value(cdt, cdn, "tiers", r.message.tiers || "");

			if (r.message.nature_operations) {
				frappe.model.set_value(cdt, cdn, "nature_operations", r.message.nature_operations);
			}

			for (let i = 1; i <= 10; i++) {
				let fieldname = i === 1 ? "imputation_analytique" : `imputation_analytique_${i}`;
				frappe.model.set_value(cdt, cdn, fieldname, r.message[fieldname] || "");
			}
		}
	});
}

frappe.ui.form.on('Encaissement', {
	setup: function(frm) {
		frm.set_query("initialisation", function() {
			return {
				"filters": {
					"docstatus": 0
				}
			};
		});

		frm.set_query("nature_operations","details_operation_de_caisse", function() {
			return {
				"filters": {
					"type_operation": frm.doc.type_operation || 'N/A',
					"est_valide": 1,
					//"account_currency": frm.doc.devise_caisse,
				}
			};
		});

		frm.set_query("imputation_analytique","details_operation_de_caisse", function() {
			return {
				"filters": {
					"type": 'Axe 1',
				}
			};
		});
		frm.set_query("imputation_analytique_2","details_operation_de_caisse", function() {
			return {
				"filters": {
					"type": 'Axe 2',
				}
			};
		});
		frm.set_query("imputation_analytique_3","details_operation_de_caisse", function() {
			return {
				"filters": {
					"type": 'Axe 3',
				}
			};
		});
		frm.set_query("imputation_analytique_4","details_operation_de_caisse", function() {
			return {
				"filters": {
					"type": 'Axe 4',
				}
			};
		});
		frm.set_query("imputation_analytique_5","details_operation_de_caisse", function() {
			return {
				"filters": {
					"type": 'Axe 5',
				}
			};
		});

		frm.set_query("imputation_analytique_6","details_operation_de_caisse", function() {
			return {
				"filters": {
					"type": 'Axe 6',
				}
			};
		});
		frm.set_query("imputation_analytique_7","details_operation_de_caisse", function() {
			return {
				"filters": {
					"type": 'Axe 7',
				}
			};
		});
		frm.set_query("imputation_analytique_8","details_operation_de_caisse", function() {
			return {
				"filters": {
					"type": 'Axe 8',
				}
			};
		});
		frm.set_query("imputation_analytique_9","details_operation_de_caisse", function() {
			return {
				"filters": {
					"type": 'Axe 9',
				}
			};
		});
		frm.set_query("imputation_analytique_10","details_operation_de_caisse", function() {
			return {
				"filters": {
					"type": 'Axe 10',
				}
			};
		});

		frm.set_query("invoice", "details_operation_de_caisse", function() {
			return {
				filters: {
					docstatus: 1,
					outstanding_amount: [">", 0]
				}
			};
		});

		frm.set_query("payment_entry", "advance_allocation", function() {
			return {
				filters: {
					docstatus: 1,
					payment_type: "Receive",
					party_type: "Customer",
					unallocated_amount: [">", 0]
				}
			};
		});

		frm.set_value('type_operation', 'Encaissement');
	},
	refresh: function(frm){
		if (frm.fields_dict.details_operation_de_caisse) {
			frm.fields_dict.details_operation_de_caisse.grid.update_docfield_property("document_type", "read_only", 1);
		}
		if (frm.doc.docstatus === 0) {
			(frm.doc.details_operation_de_caisse || []).forEach(row => {
				if (row.document_type !== "Sales Invoice") {
					frappe.model.set_value(row.doctype, row.name, "document_type", "Sales Invoice");
				}
			});
			update_advance_totals(frm);
		}
		/*
		if(frappe.has_route_options()){
			if(frappe.route_options.state === 1){
				frm.page.btn_primary.hide();
				frm.page.btn_secondary.hide();
				frm.page.clear_primary_action();

				var span;
				var a;
				var li;
				span = document.querySelector('[data-label="New%20Encaissement"]');
				if(span){
					a = span.parentElement;
					li = a.parentElement;
					li.style.display = "None";
				}
				span = document.querySelector('[data-label="Duplicate"]');
				if(span){
					a = span.parentElement;
					li = a.parentElement;
					li.style.display = "None";
				}
				span = document.querySelector('[data-label="Rename"]');
				if(span){
					a = span.parentElement;
					li = a.parentElement;
					li.style.display = "None";
				}
			}
		}*/
		if (!frm.customFlag){
			var grid = frm.get_field('details_operation_de_caisse');
			// Add a new empty row to the grid
			grid.grid.add_new_row();
			frm.customFlag = true;
		}
	},
	devise: function(frm) {
		if(!frm.doc.devise_caisse) return;
		frappe.call({
			method: "ls_treso.ls_treso.doctype.devise.devise.get_cours",
			args: {
				reference: frm.doc.devise_caisse,
				devise: frm.doc.devise,
			},
			callback: function (r) {
				if (r.message) {
                    if(r.message.length > 0) {
						frm.set_value('cours', r.message[0].cours);
						if(frm.doc.montant) frm.set_value('montant_reference', frm.doc.montant / r.message[0].cours);
					}
					else{
						frm.set_value('cours', 0);
						frm.set_value('montant_reference', 0);
					}
                }
			}
		});
	},
	montant: function(frm) {
		if(frm.doc.cours) frm.set_value('montant_reference', frm.doc.montant / frm.doc.cours);
		update_advance_totals(frm);
	},
});

frappe.ui.form.on('Details Operation de Caisse', {
	
	invoice(frm, cdt, cdn) {
		load_invoice_details(frm, cdt, cdn);
	},
    montant_devise(frm, cdt, cdn) {
		var row = locals[cdt][cdn];
        if(row.montant_devise && frm.doc.cours){
			row.montant_devise_ref = row.montant_devise / frm.doc.cours;
		}
		else{
			row.montant_devise_ref = 0;
		}
        frm.refresh_field('montant_devise_ref');
        frm.refresh();
    },
	details_operation_de_caisse_add:(frm, cdt, cdn) =>{
		var total = 0;
		var row = locals[cdt][cdn];
		frappe.model.set_value(cdt, cdn, "document_type", "Sales Invoice");

		frm.doc.details_operation_de_caisse.forEach(e => {
			total += e.montant_devise ? e.montant_devise : 0;
		});
		
		let operation_total = flt(frm.doc.montant_total_operation || frm.doc.montant);
		if (operation_total){
			if (operation_total > total){
				row.montant_devise = operation_total - total;
				row.montant_devise_ref = operation_total - total;
			}
			else {
				row.montant_devise = 0;
				row.montant_devise_ref = 0;
			}
			frm.refresh_field('montant_devise');
			frm.refresh_field('montant_devise_ref');
			frm.refresh_field('details_operation_de_caisse');
		}
	},
});

frappe.ui.form.on('Advance Allocation', {
	payment_entry(frm, cdt, cdn) {
		load_advance_payment(frm, cdt, cdn);
	},
	allocated_amount(frm, cdt, cdn) {
		let row = locals[cdt][cdn];
		if (flt(row.allocated_amount) > flt(row.available_amount)) {
			frappe.msgprint(__("Le montant alloué ne peut pas dépasser le montant disponible."));
			frappe.model.set_value(cdt, cdn, "allocated_amount", 0);
		}
		update_advance_totals(frm);
	},
	advance_allocation_remove(frm) {
		update_advance_totals(frm);
	}
});
