// Copyright (c) 2022, Kossivi Amouzou and contributors
// For license information, please see license.txt

frappe.ui.form.on('Caisse Initialisation', {
	refresh: function(frm) {
		frm.add_custom_button(
			__("Ouverture"),
			function () {
				if(frm.is_new()) return;
				let d = new frappe.ui.Dialog({
					title: 'Ouverture de Caisse',
					fields: [
						{
							label: 'Caisse',
							fieldname: 'caisse',
							fieldtype: 'Link',
							options: "Caisse",
							default: frm.doc.caisse,
						},
						{
							label: 'Devise',
							fieldname: 'devise',
							fieldtype: 'Data',
							read_only: 1,
							default: frm.doc.devise,
						},
						{
							fieldname: "column_break_01",
							fieldtype: "Column Break"
						},
						{
							label: 'Date',
							fieldname: 'date',
							fieldtype: 'Date',
							default: frm.doc.date_initialisation,
						},
					],
					primary_action_label: __('Ouvrir'),
					primary_action(values) {
						//console.log(values);
						frm.events.get_billetage(frm,values);
						//if(frappe.has_route_options()) frm.events.get_billetage(frm,values);
						d.hide();
					}
				});
				d.show();
			},
			__("Utilitaires")
		);

		frm.add_custom_button(
			__("Recalcul"),
			function () {
				let d = new frappe.ui.Dialog({
					title: 'Recalculer les montants déjà validés!',
					fields: [
						{
							label: 'Numero',
							fieldname: 'initialisation',
							fieldtype: 'Data',
							read_only: 1,
							default: cur_frm.docname,
						},
						{
							label: 'Caisse',
							fieldname: 'caisse',
							fieldtype: 'Link',
							options: "Caisse",
							read_only: 1,
							default: cur_frm.doc.caisse,
						},
						{
							fieldname: "column_break_01",
							fieldtype: "Column Break"
						},
						{
							label: 'Date',
							fieldname: 'date',
							fieldtype: 'Date',
							read_only: 1,
							default: cur_frm.doc.date_initialisation,
						},
					],
					primary_action_label: __('Recalculer'),
					primary_action(values) {
						cur_frm.call('recalcul');
						cur_frm.refresh();
						//console.log(values);
						d.hide();
					}
				});
				d.show();
			},
			__("Utilitaires")
		);

		frm.add_custom_button(
			__("Transfert"),
			function () {
				let d = new frappe.ui.Dialog({
					title: 'Transfert',
					fields: [
						{
							label: 'De: ',
							fieldname: 'caisse_de',
							fieldtype: 'Link',
							options: "Caisse",
							read_only: 1,
							default: cur_frm.doc.caisse,
						},
						{
							label: 'Date',
							fieldname: 'date_de',
							fieldtype: 'Date',
							read_only: 1,
							default: cur_frm.doc.date_initialisation,
						},
						{
							label: 'Devise',
							fieldname: 'devise_de',
							fieldtype: 'Data',
							read_only: 1,
							default: cur_frm.doc.devise,
						},
						{
							label: 'Montant',
							fieldname: 'montant_de',
							fieldtype: 'Data',
							onchange: function() {
								const mt = flt(d.get_value('montant_de'));
								if (d.get_value('tiers')) {
									d.set_value('devise_a', d.get_value('devise_de'));
									d.set_value('montant_a', mt);
								} else if (mt > 0 && d.get_value('caisse_a')) {
									frm.events.cours(d.get_value('devise_de'), d.get_value('devise_a'));
								}
							}
						},
						{
							label: 'Salarié',
							fieldname: 'tiers',
							fieldtype: 'Link',
							options: 'Tiers',
							get_query: function() {
								return { filters: { type: 'SALARIE' } };
							},
							onchange: function() {
								if (d.get_value('tiers')) {
									d.set_value('caisse_a', '');
									d.set_value('devise_a', d.get_value('devise_de'));
									d.set_value('montant_a', flt(d.get_value('montant_de')));
								}
							}
						},
						{
							fieldname: "column_break_01",
							fieldtype: "Column Break"
						},
						
						{
							label: 'A: ',
							fieldname: 'caisse_a',
							fieldtype: 'Link',
							options: "Caisse",
							onchange: function() {
								if (d.get_value('caisse_a')) {
									d.set_value('tiers', '');
									frm.events.devise(d.get_value('caisse_a'));
									const mt = flt(d.get_value('montant_de'));
									if (mt > 0) {
										frm.events.cours(d.get_value('devise_de'), d.get_value('devise_a'));
									}
								}
							}
						},
						{
							label: 'Date',
							fieldname: 'date_a',
							fieldtype: 'Date',
							read_only: 1,
							default: cur_frm.doc.date_initialisation,
						},
						{
							label: 'Devise',
							fieldname: 'devise_a',
							fieldtype: 'Data',
							read_only: 1,
							//default: "CDF",
						},
						{
							label: 'Montant',
							fieldname: 'montant_a',
							fieldtype: 'Data',
							//default: "2000000",
							read_only: 1,
						},
					],
					primary_action_label: __('Transférer'),
					primary_action(values) {
						if (!values.caisse_a && !values.tiers) {
							frappe.msgprint(__('Veuillez sélectionner une caisse destination ou un salarié.'));
							return;
						}
						frappe.call({
							method: 'ls_treso.ls_treso.doctype.caisse_initialisation.caisse_initialisation.save_operation',
							args: {
								caisse_de : values.caisse_de,
								caisse_a : values.caisse_a,
								tiers : values.tiers,
								date : values.date_de,
								montant_de : values.montant_de,
								montant_a : values.montant_a,
								devise : values.devise_de,
							},
							callback: function(r){
								frm.refresh();
							}
						});
						//console.log(values);
						d.hide();
					}
				});
				d.show();
			},
			__("Utilitaires")
		);
		/*frm.add_custom_button(
			__("Clôture"),
			function () {
				let d = new frappe.ui.Dialog({
					title: 'Clôturer toutes les opérations ouvertes!',
					fields: [
						{
							label: 'Numero',
							fieldname: 'initialisation',
							fieldtype: 'Data',
							read_only: 1,
							default: cur_frm.docname,
						},
						{
							label: 'Caisse',
							fieldname: 'caisse',
							fieldtype: 'Link',
							options: "Caisse",
							read_only: 1,
							default: cur_frm.doc.caisse,
						},
						{
							fieldname: "column_break_01",
							fieldtype: "Column Break"
						},
						{
							label: 'Date',
							fieldname: 'date',
							fieldtype: 'Date',
							read_only: 1,
							default: cur_frm.doc.date_initialisation,
						},
					],
					primary_action_label: __('Clôturer'),
					primary_action(values) {
						frm.call("cloture");
						//console.log(values);
						d.hide();
					}
				});
				d.show();
			},
			__("Utilitaires")
		);*/

		frm.add_custom_button(__("Encaissement"), function() {
			var local_docname = frappe.model.make_new_doc_and_get_name('Encaissement');
			frappe.route_options = {
				'caisse': cur_frm.doc.caisse,
				'date_initialisation': cur_frm.doc.date,
				'devise': cur_frm.doc.devise,
				'initialisation': cur_frm.doc.name,
				//'state': cur_frm.doc.docstatus,
			};
			if(frappe.has_route_options()){
				frappe.set_route("List", "Encaissement");
			}
		}, __("Opérations de Caisse"));
		frm.add_custom_button(__("Decaissement"), function() {
			var local_docname = frappe.model.make_new_doc_and_get_name('Decaissement');
			frappe.route_options = {
				'caisse': cur_frm.doc.caisse,
				'date_initialisation': cur_frm.doc.date,
				'devise': cur_frm.doc.devise,
				'initialisation': cur_frm.doc.name,
				//'state': cur_frm.doc.docstatus,
			};
			frappe.set_route("List", "Decaissement");
		}, __("Opérations de Caisse"));
		if(frm.doc.docstatus === 1 || frm.doc.docstatus === 2){
			frm.remove_custom_button("Ouverture", "Utilitaires");
			frm.remove_custom_button("Recalcul", "Utilitaires");
			frm.remove_custom_button("Transfert", "Utilitaires");
			/*frm.remove_custom_button("Encaissement", "Opérations de Caisse");
			frm.remove_custom_button("Decaissement", "Opérations de Caisse");*/
		}
		
	},
	init_billetage: function(frm) {
        frappe.call({
			method: "ls_treso.ls_treso.doctype.devise.devise.get_billetage",
			args: {
				devise: frm.doc.devise,
			},
			callback: function (r) {
				if (r.message) {
                    frm.clear_table("billetage");
					console.log(r.message);
                    r.message.forEach(e => {
                        var row = frm.add_child('billetage');
						row.image = e.image,
                        row.nom = e.nom,
						row.unite = e.unite;
                        row.nombre_initial = 0;
                        row.valeur_initiale = 0;
                        row.nombre_final = 0;
                        row.valeur_finale = 0;
                    });

                    frm.refresh_field('billetage');
                    frm.dirty();
                    //frm.refresh();
                }
			}
		});
	},
	get_billetage: function(frm,values) {
		var caisse = null;
		var date_init = null;
		/*if (!frm.is_new()) {
			caisse = values.caisse;
			date_init = values.date;
			var local_docname = frappe.model.make_new_doc_and_get_name('Caisse Initialisation')
			frappe.route_options = {
				'caisse': values.caisse,
				'date_initialisation': values.date,
				'devise': values.devise,
			};
			frappe.set_route('Form', 'Caisse Initialisation', local_docname)
		}
		else{
			caisse = values.caisse; //frm.doc.caisse;
			date_init = values.date; //frm.doc.date_initialisation;
		}
		//var test = frappe.get_route();
		//var caisse = frm.is_new() ? frm.doc.caisse : values.caisse;
		//var date_init = frm.is_new() ? frm.doc.date_initialisation : values.date;*/
		frappe.call({
			method: "ls_treso.ls_treso.doctype.caisse_initialisation.caisse_initialisation.get_solde_final",
			args: {
				caisse : values.caisse,
				date: values.date,
			},
			callback: function (r) {
				frm.events.init_billetage(frm);
				if (r.message) {
					frm.set_value('solde_initial', r.message);
					frm.set_value('solde_final', r.message);

					frappe.call({
						method: "ls_treso.ls_treso.doctype.caisse_initialisation.caisse_initialisation.get_last_billetage",
						args: {
							caisse : values.caisse,
							date: values.date,
						},
						callback: function (r) {
							if (r.message) {
								if(r.message.length > 0) {
									r.message.forEach(e => {
										frm.doc.billetage.forEach(f => {
											if (f.nom == e.nom){
												if(e.nombre_final > 0) {
													f.nombre_initial = e.nombre_final;
													f.valeur_initiale = e.valeur_finale;
												}
											}
										})
									});
								}
							}
							frm.dirty();
							frm.refresh_field('billetage');
							frm.save();
						}
					});

				}
				/*else{
					frm.events.init_billetage(frm);
				}*/
			}
		});
	},
	cours: function(from, to) {
		frappe.call({
			method: "ls_treso.ls_treso.doctype.devise.devise.get_cours",
			args: {
				reference: from,
				devise: to,
			},
			callback: function (r) {
				if (r.message) {
                    if(r.message.length > 0) {
						let mt = 0;
						if (to == 'CDF') mt = flt(cur_dialog.get_value('montant_de') * r.message[0].cours,0);
						else mt = flt(cur_dialog.get_value('montant_de') * r.message[0].cours,2);
						cur_dialog.set_value('montant_a',cur_frm.events.roundnum(to, mt));
					}
					else{
						cur_dialog.set_value('montant_a',0);
					}
                }
			}
		});
	},
	roundnum: function(devise,num){
		var amount = 0;
		if(devise == "CDF") amount = Math.round(num / 50)*50;
		else amount = Math.round(num);
		return amount;
	},

	devise: function(caisse) {
		frappe.call({
			method: "ls_treso.ls_treso.doctype.caisse_initialisation.caisse_initialisation.get_caisse_devise",
			args: {
				caisse: caisse,
			},
			callback: function (r) {
				if (r.message) {
                    if(r.message.length > 0) cur_dialog.set_value('devise_a',r.message);
					else cur_dialog.set_value('devise_a','');
                }
			}
		});
	},
});

frappe.ui.form.on('Billetage', {
	
    nombre_final(frm, cdt, cdn) {
		var row = locals[cdt][cdn];
        const old_value =  row.valeur_finale;
        if(row.nombre_final){
			row.valeur_finale = row.nombre_final * row.nom / (row.unite == "Centime" ? 100 : 1);
		}
		else{
			row.valeur_finale = 0;
		}
        frm.doc.total = frm.doc.total - old_value + row.valeur_finale;
        frm.refresh_field('billetage');
        frm.refresh_field('total');
        frm.refresh();
    },
    valeur_finale(frm, cdt, cdn) {
		alert("OK");
    },
});
